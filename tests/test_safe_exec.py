"""P1 safe-exec core: policy (default-deny), env scrub, limits, executor, preflight, CLI.

All stdlib-only. No real model weights, no network. Execution tests shell out to a
BARE interpreter name (argv[0] must be path-free -- see is_bare_command) with tiny
`-c` programs resolved through PATH.
"""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from broker_lane_sandbox import SCHEMA_VERSION, __version__
from broker_lane_sandbox import cli
from broker_lane_sandbox import executor as executor_mod
from broker_lane_sandbox.envscrub import build_child_env
from broker_lane_sandbox.executor import SafeExecutor
from broker_lane_sandbox.limits import have_resource, rlimit_spec
from broker_lane_sandbox.policy import PolicyError, SandboxPolicy, is_bare_command
from broker_lane_sandbox.preflight import preflight
from broker_lane_sandbox.result import ExecResult, Status


def _bare_interpreter():
    """A bare (path-free) interpreter name resolvable on PATH."""
    for cand in (os.path.basename(sys.executable), "python3", "python"):
        if cand and shutil.which(cand):
            return cand
    return None


PYBIN = _bare_interpreter()
requires_python = pytest.mark.skipif(PYBIN is None, reason="no bare python on PATH")
requires_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")


def _exec_policy(**over) -> SandboxPolicy:
    base = dict(allow_exec=True, allowed_commands=[PYBIN, "echo"], network="offline")
    base.update(over)
    return SandboxPolicy.from_mapping({"schema_version": SCHEMA_VERSION, **base})


# --- policy: validation + default-deny --------------------------------------

def test_policy_defaults_are_default_deny():
    p = SandboxPolicy()
    assert p.allow_exec is False
    assert p.allowed_commands == []
    assert p.network == "offline"
    assert p.is_command_allowed("python3") is False   # nothing runs by default


def test_policy_rejects_unknown_keys():
    with pytest.raises(PolicyError):
        SandboxPolicy.from_mapping({"bogus_key": 1})


def test_policy_rejects_bad_network_and_schema():
    with pytest.raises(PolicyError):
        SandboxPolicy(network="wifi")
    with pytest.raises(PolicyError):
        SandboxPolicy(schema_version=SCHEMA_VERSION + 99)


def test_policy_rejects_nonpositive_limits():
    with pytest.raises(PolicyError):
        SandboxPolicy(timeout_seconds=0)
    with pytest.raises(PolicyError):
        SandboxPolicy(cpu_seconds=0)
    with pytest.raises(PolicyError):
        SandboxPolicy(max_file_size_bytes=0)
    with pytest.raises(PolicyError):
        SandboxPolicy(max_file_size_bytes=-1)


# --- limit-field type hardening: PolicyError, never TypeError / silent coercion --

@pytest.mark.parametrize(
    "fld", ["cpu_seconds", "address_space_bytes", "max_processes",
            "max_file_size_bytes", "max_output_bytes"]
)
def test_policy_rejects_boolean_limits(fld):
    # JSON `true` must be an ERROR, not a silently-coerced RLIMIT of 1
    # (bool is an int subclass in Python).
    with pytest.raises(PolicyError):
        SandboxPolicy(**{fld: True})


@pytest.mark.parametrize(
    "fld", ["cpu_seconds", "address_space_bytes", "max_processes",
            "max_file_size_bytes", "max_output_bytes"]
)
def test_policy_rejects_string_and_fractional_limits(fld):
    with pytest.raises(PolicyError):          # was a raw TypeError before hardening
        SandboxPolicy(**{fld: "10"})
    with pytest.raises(PolicyError):          # was silently truncated before hardening
        SandboxPolicy(**{fld: 1.5})


def test_policy_rejects_non_numeric_timeout():
    with pytest.raises(PolicyError):
        SandboxPolicy(timeout_seconds="30")
    with pytest.raises(PolicyError):
        SandboxPolicy(timeout_seconds=True)


def test_policy_rejects_non_finite_timeout():
    # NaN passes `<= 0` comparisons and inf would let run() block forever --
    # both are malformed and must raise PolicyError.
    import math
    with pytest.raises(PolicyError):
        SandboxPolicy(timeout_seconds=math.nan)
    with pytest.raises(PolicyError):
        SandboxPolicy(timeout_seconds=math.inf)
    assert SandboxPolicy(timeout_seconds=0.5).timeout_seconds == 0.5   # fractional is fine


def test_policy_accepts_integral_float_limits():
    # JSON scientific notation (1e9) arrives as float; integral floats normalize
    # to int so rlimits are exact. Fractional floats are rejected (test above).
    # All five fields route through the same helper -- cover each.
    p = SandboxPolicy(address_space_bytes=1e9, cpu_seconds=10.0, max_file_size_bytes=2.0**20,
                      max_processes=64.0, max_output_bytes=1e6)
    assert p.address_space_bytes == 10**9 and isinstance(p.address_space_bytes, int)
    assert p.cpu_seconds == 10 and isinstance(p.cpu_seconds, int)
    assert p.max_file_size_bytes == 2**20 and isinstance(p.max_file_size_bytes, int)
    assert p.max_processes == 64 and isinstance(p.max_processes, int)
    assert p.max_output_bytes == 10**6 and isinstance(p.max_output_bytes, int)


# --- F1 regression: only BARE command names may pass the gate ----------------

def test_is_bare_command_helper():
    assert is_bare_command("python3") is True
    assert is_bare_command("/usr/bin/python3") is False
    assert is_bare_command("./python3") is False
    assert is_bare_command("dir\\python3") is False
    assert is_bare_command("") is False


def test_command_allowed_requires_bare_name():
    p = _exec_policy(allowed_commands=["python3"])
    assert p.is_command_allowed("python3") is True
    # path-bearing argv[0] whose basename is allow-listed must NOT pass (path bypass)
    assert p.is_command_allowed("/usr/bin/python3") is False
    assert p.is_command_allowed("./python3") is False
    assert p.is_command_allowed("/tmp/evil/python3") is False


def test_executor_denies_path_bearing_argv0(tmp_path):
    # Plant an executable named like an allow-listed command at an arbitrary path.
    evil = tmp_path / "python3"
    evil.write_text("#!/bin/sh\necho PWNED\n")
    evil.chmod(0o755)
    p = _exec_policy(allowed_commands=["python3"])
    r = SafeExecutor(p).run([str(evil), "-c", "print(1)"])
    assert r.status == Status.DENIED
    assert "bare command name" in r.reason
    assert "PWNED" not in r.stdout            # the planted binary never ran


# --- env scrub: empty baseline + secret guard + offline proxy strip ----------

def test_env_scrub_starts_empty_and_allowlists(monkeypatch):
    monkeypatch.setenv("KEEP_ME", "yes")
    monkeypatch.setenv("DROP_ME", "no")
    p = _exec_policy(env_allowlist=["KEEP_ME"])
    child, dropped = build_child_env(p)
    assert child.get("KEEP_ME") == "yes"
    assert "DROP_ME" not in child
    assert dropped == []


def test_env_scrub_drops_secret_even_if_allowlisted(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    p = _exec_policy(env_allowlist=["OPENROUTER_API_KEY"])
    child, dropped = build_child_env(p)
    assert "OPENROUTER_API_KEY" not in child
    assert "OPENROUTER_API_KEY" in dropped


def test_env_scrub_secret_passes_only_with_explicit_optin(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "t")
    p = _exec_policy(env_allowlist=["MY_TOKEN"], allow_secret_env=True)
    child, dropped = build_child_env(p)
    assert child.get("MY_TOKEN") == "t" and dropped == []


def test_env_scrub_offline_strips_proxies(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://corp:8080")
    monkeypatch.setenv("https_proxy", "http://corp:8080")
    p = _exec_policy(env_allowlist=["HTTPS_PROXY", "https_proxy"], network="offline")
    child, _ = build_child_env(p)
    assert "HTTPS_PROXY" not in child and "https_proxy" not in child
    assert child["SANDBOX_NETWORK"] == "offline" and child["NO_PROXY"] == "*"


# --- limits: pure spec builder ----------------------------------------------

def test_rlimit_spec_pure():
    p = _exec_policy(cpu_seconds=5, address_space_bytes=2**30, max_processes=32,
                     max_file_size_bytes=2**20)
    spec = rlimit_spec(p)
    if have_resource():
        assert len(spec) == 4
        assert all(isinstance(v, int) and v > 0 for _, v in spec)
    else:                                   # pragma: no cover - Windows
        assert spec == []


def test_rlimit_spec_empty_when_no_limits_requested():
    # Limits are applied ONLY when explicitly requested: the default policy
    # (all limit fields None) must produce an empty rlimit spec.
    assert rlimit_spec(SandboxPolicy()) == []


def test_rlimit_spec_includes_file_size_only_when_set():
    p = SandboxPolicy(max_file_size_bytes=1_000_000)
    spec = rlimit_spec(p)
    if have_resource():
        import resource
        assert (resource.RLIMIT_FSIZE, 1_000_000) in spec
        assert len(spec) == 1                # nothing else was requested
    else:                                   # pragma: no cover - Windows
        assert spec == []


def test_limits_summary_reports_file_size():
    from broker_lane_sandbox.limits import limits_summary
    s = limits_summary(SandboxPolicy(max_file_size_bytes=12345))
    assert s["max_file_size_bytes"] == 12345
    s_default = limits_summary(SandboxPolicy())
    assert s_default["max_file_size_bytes"] is None


# --- executor: default-deny + allow-list + run/exit/timeout/truncate ---------

@requires_python
def test_executor_denies_when_exec_disabled():
    r = SafeExecutor(SandboxPolicy()).run([PYBIN, "-c", "print('hi')"])
    assert r.status == Status.DENIED and "allow_exec" in r.reason
    assert r.exit_code is None and r.ok is False


@requires_python
def test_executor_denies_command_not_allowlisted():
    p = _exec_policy(allowed_commands=["echo"])
    r = SafeExecutor(p).run([PYBIN, "-c", "print(1)"])
    assert r.status == Status.DENIED and "not in allowed_commands" in r.reason


@requires_python
def test_executor_runs_allowed_command():
    r = SafeExecutor(_exec_policy()).run([PYBIN, "-c", "print('hello-sandbox')"])
    assert r.ok and r.status == Status.OK and r.exit_code == 0
    assert "hello-sandbox" in r.stdout
    assert "SANDBOX_NETWORK" in r.env_keys     # scrubbed env was applied


@requires_python
def test_executor_captures_nonzero_exit():
    r = SafeExecutor(_exec_policy()).run([PYBIN, "-c", "import sys; sys.exit(7)"])
    assert r.status == Status.EXIT_NONZERO and r.exit_code == 7 and r.ok is False


@requires_python
def test_executor_child_env_is_scrubbed(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "leak")
    monkeypatch.setenv("OK_VAR", "fine")
    p = _exec_policy(env_allowlist=["PATH", "OK_VAR", "SECRET_TOKEN"])
    prog = "import os,json; print(json.dumps(sorted(os.environ)))"
    r = SafeExecutor(p).run([PYBIN, "-c", prog])
    keys = json.loads(r.stdout)
    assert "OK_VAR" in keys
    assert "SECRET_TOKEN" not in keys          # secret guard dropped it
    assert "SECRET_TOKEN" in r.limits["dropped_secret_env"]


@requires_python
def test_executor_timeout_kills():
    p = _exec_policy(timeout_seconds=1)
    r = SafeExecutor(p).run([PYBIN, "-c", "import time; time.sleep(30)"])
    assert r.status == Status.TIMEOUT
    assert r.duration_ms < 8000                 # killed promptly, not after 30s


@requires_python
def test_executor_truncates_output():
    p = _exec_policy(max_output_bytes=100)
    r = SafeExecutor(p).run([PYBIN, "-c", "print('x'*5000)"])
    assert r.truncated is True and "truncated" in r.stdout
    assert len(r.stdout) < 400


@requires_python
def test_executor_spawn_error_for_bad_cwd():
    p = _exec_policy(working_dir="/no/such/dir/xyz")
    r = SafeExecutor(p).run([PYBIN, "-c", "print(1)"])
    assert r.status == Status.SPAWN_ERROR and "working_dir" in r.reason


@requires_python
@pytest.mark.skipif(not have_resource(), reason="POSIX rlimits required")
def test_executor_cpu_limit_terminates_busy_loop():
    # RLIMIT_CPU=1 should kill a busy loop well before the 8s wall-clock timeout.
    p = _exec_policy(cpu_seconds=1, timeout_seconds=8)
    r = SafeExecutor(p).run([PYBIN, "-c", "\nwhile True:\n  pass\n"])
    assert r.exit_code != 0                      # signal-killed or timed out
    assert r.duration_ms < 8000


@requires_python
@pytest.mark.skipif(not have_resource(), reason="POSIX rlimits required")
def test_executor_file_size_limit_blocks_big_write(tmp_path):
    # RLIMIT_FSIZE=64KiB: a 1MiB write must fail (SIGXFSZ or an IOError in the
    # child), never complete. The on-disk file, if any, stays within the cap.
    p = _exec_policy(max_file_size_bytes=64 * 1024, working_dir=str(tmp_path))
    prog = "open('big.bin','wb').write(b'x'*(1024*1024)); print('WROTE-ALL')"
    r = SafeExecutor(p).run([PYBIN, "-c", prog])
    assert r.exit_code != 0 and "WROTE-ALL" not in r.stdout
    big = tmp_path / "big.bin"
    assert (not big.exists()) or big.stat().st_size <= 64 * 1024


@requires_python
@pytest.mark.skipif(not have_resource(), reason="POSIX rlimits required")
def test_executor_file_size_limit_allows_small_write(tmp_path):
    # The same limit must NOT interfere with a write below the cap.
    p = _exec_policy(max_file_size_bytes=64 * 1024, working_dir=str(tmp_path))
    prog = "open('small.bin','wb').write(b'x'*1024); print('OK-SMALL')"
    r = SafeExecutor(p).run([PYBIN, "-c", prog])
    assert r.ok and "OK-SMALL" in r.stdout
    assert (tmp_path / "small.bin").stat().st_size == 1024


# --- F2 regression: an escaped descendant cannot pin run() past the timeout ---

@requires_python
@requires_fork
def test_executor_timeout_bounded_despite_escaped_child(monkeypatch):
    monkeypatch.setattr(executor_mod, "_KILL_GRACE", 0.5)
    # Parent exits immediately; a forked grandchild calls setsid() (escapes the
    # process group) and sleeps while holding the inherited stdout pipe open.
    prog = (
        "import os,sys,time\n"
        "if os.fork()==0:\n"
        "    os.setsid()\n"
        "    time.sleep(6)\n"
        "    os._exit(0)\n"
        "sys.exit(0)\n"
    )
    p = _exec_policy(timeout_seconds=1)
    r = SafeExecutor(p).run([PYBIN, "-c", prog])
    assert r.status == Status.TIMEOUT
    assert r.duration_ms < 4500                  # bounded: ~1s + grace, NOT 6s
    assert "abandoned" in r.stderr


# --- F3 regression: rlimit above the host hard ceiling -> SPAWN_ERROR, no crash --

@requires_python
@pytest.mark.skipif(not have_resource(), reason="POSIX rlimits required")
def test_executor_rlimit_above_hard_is_spawn_error():
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
    if hard == resource.RLIM_INFINITY:
        pytest.skip("NPROC hard limit is unlimited; cannot exceed it")
    p = _exec_policy(max_processes=hard + 1000)   # impossible on this host
    r = SafeExecutor(p).run([PYBIN, "-c", "print(1)"])
    assert r.status == Status.SPAWN_ERROR         # a result, not a raised exception
    assert "could not start process" in r.reason


# --- preflight: inspect only, never executes --------------------------------

def test_preflight_reports_default_deny_posture():
    rep = preflight(SandboxPolicy())
    assert rep["execution"]["default_deny"] is True
    assert rep["network"] == "offline"


def test_preflight_warns_on_secret_allowlist_and_missing_command():
    p = _exec_policy(allowed_commands=["definitely-not-a-real-binary-xyz"],
                     env_allowlist=["API_KEY"])
    rep = preflight(p)
    assert rep["ok"] is False
    joined = " ".join(rep["warnings"])
    assert "not found on PATH" in joined and "looks secret" in joined


# --- ExecResult shape -------------------------------------------------------

def test_exec_result_to_dict_is_json_serializable():
    r = ExecResult(status=Status.OK, argv=["echo", "x"], exit_code=0)
    d = r.to_dict()
    assert d["ok"] is True and d["status"] == "ok"
    json.dumps(d)        # must not raise


# --- CLI seam ---------------------------------------------------------------

def test_cli_version(capsys):
    rc = cli.main(["version"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["version"] == __version__ and out["schema_version"] == SCHEMA_VERSION


@requires_python
def test_cli_run_executes(tmp_path, capsys):
    policy = {"schema_version": SCHEMA_VERSION, "allow_exec": True,
              "allowed_commands": [PYBIN], "network": "offline"}
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps(policy))
    rc = cli.main(["run", "--policy", str(pf), "--", PYBIN, "-c", "print('cli-ok')"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and "cli-ok" in out["stdout"]


def test_cli_run_denied_returns_nonzero(tmp_path, capsys):
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps({"schema_version": SCHEMA_VERSION}))   # default-deny
    rc = cli.main(["run", "--policy", str(pf), "--", "echo", "hi"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"] == Status.DENIED


def test_cli_preflight(tmp_path, capsys):
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "allow_exec": True,
                              "allowed_commands": ["echo"]}))
    rc = cli.main(["preflight", "--policy", str(pf)])
    out = json.loads(capsys.readouterr().out)
    assert out["schema_version"] == SCHEMA_VERSION
    assert "passthrough_names" in out["env_plan"]
    assert rc in (0, 1)


def test_cli_models_json_catalog(tmp_path, capsys):
    cat = tmp_path / "models.json"
    cat.write_text(json.dumps({
        "schema_version": 1, "cache_dir_env": "SANDBOX_MODEL_DIR",
        "profiles": {"demo": {"runner": "llama.cpp", "source": "https://x.invalid/m.gguf",
                              "sha256": "0" * 64, "license": "X", "relative_path": "demo/m.gguf"}},
    }))
    rc = cli.main(["models", "--catalog", str(cat)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["count"] == 1 and "demo" in out["profiles"]
    assert out["profiles"]["demo"]["runner"] == "llama.cpp"


def test_example_policy_loads():
    repo = Path(__file__).resolve().parent.parent
    p = SandboxPolicy.from_file(repo / "policy.example.json")
    assert p.allow_exec is True and "python3" in p.allowed_commands
