"""P1 safe-exec core: policy (default-deny), env scrub, limits, executor, preflight, CLI.

All stdlib-only. No real model weights, no network. Execution tests shell out to the
running interpreter (allow-listed by basename) with tiny `-c` programs.
"""
import json
import os
import sys
from pathlib import Path

import pytest

from broker_lane_sandbox import SCHEMA_VERSION, __version__
from broker_lane_sandbox import cli
from broker_lane_sandbox.envscrub import build_child_env
from broker_lane_sandbox.executor import SafeExecutor
from broker_lane_sandbox.limits import have_resource, rlimit_spec
from broker_lane_sandbox.policy import PolicyError, SandboxPolicy
from broker_lane_sandbox.preflight import preflight
from broker_lane_sandbox.result import ExecResult, Status

PYBASE = os.path.basename(sys.executable)   # e.g. "python3" / "python3.11"


def _exec_policy(**over) -> SandboxPolicy:
    base = dict(allow_exec=True, allowed_commands=[PYBASE, "echo"], network="offline")
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


def test_command_allowed_by_basename_only():
    p = _exec_policy(allowed_commands=["python3"])
    assert p.is_command_allowed("/usr/bin/python3") is True
    assert p.is_command_allowed("/usr/bin/bash") is False


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
    p = _exec_policy(cpu_seconds=5, address_space_bytes=2**30, max_processes=32)
    spec = rlimit_spec(p)
    if have_resource():
        assert len(spec) == 3
        assert all(isinstance(v, int) and v > 0 for _, v in spec)
    else:                                   # pragma: no cover - Windows
        assert spec == []


# --- executor: default-deny + allow-list + run/exit/timeout/truncate ---------

def test_executor_denies_when_exec_disabled():
    r = SafeExecutor(SandboxPolicy()).run([sys.executable, "-c", "print('hi')"])
    assert r.status == Status.DENIED and "allow_exec" in r.reason
    assert r.exit_code is None and r.ok is False


def test_executor_denies_command_not_allowlisted():
    p = _exec_policy(allowed_commands=["echo"])
    r = SafeExecutor(p).run([sys.executable, "-c", "print(1)"])
    assert r.status == Status.DENIED and "not in allowed_commands" in r.reason


def test_executor_runs_allowed_command():
    r = SafeExecutor(_exec_policy()).run([sys.executable, "-c", "print('hello-sandbox')"])
    assert r.ok and r.status == Status.OK and r.exit_code == 0
    assert "hello-sandbox" in r.stdout
    assert "SANDBOX_NETWORK" in r.env_keys     # scrubbed env was applied


def test_executor_captures_nonzero_exit():
    r = SafeExecutor(_exec_policy()).run([sys.executable, "-c", "import sys; sys.exit(7)"])
    assert r.status == Status.EXIT_NONZERO and r.exit_code == 7 and r.ok is False


def test_executor_child_env_is_scrubbed(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "leak")
    monkeypatch.setenv("OK_VAR", "fine")
    p = _exec_policy(env_allowlist=["PATH", "OK_VAR", "SECRET_TOKEN"])
    prog = "import os,json; print(json.dumps(sorted(os.environ)))"
    r = SafeExecutor(p).run([sys.executable, "-c", prog])
    keys = json.loads(r.stdout)
    assert "OK_VAR" in keys
    assert "SECRET_TOKEN" not in keys          # secret guard dropped it
    assert "SECRET_TOKEN" in r.limits["dropped_secret_env"]


def test_executor_timeout_kills():
    p = _exec_policy(timeout_seconds=1)
    r = SafeExecutor(p).run([sys.executable, "-c", "import time; time.sleep(30)"])
    assert r.status == Status.TIMEOUT
    assert r.duration_ms < 8000                 # killed promptly, not after 30s


def test_executor_truncates_output():
    p = _exec_policy(max_output_bytes=100)
    r = SafeExecutor(p).run([sys.executable, "-c", "print('x'*5000)"])
    assert r.truncated is True and "truncated" in r.stdout
    assert len(r.stdout) < 400


def test_executor_spawn_error_for_bad_cwd():
    p = _exec_policy(working_dir="/no/such/dir/xyz")
    r = SafeExecutor(p).run([sys.executable, "-c", "print(1)"])
    assert r.status == Status.SPAWN_ERROR and "working_dir" in r.reason


@pytest.mark.skipif(not have_resource(), reason="POSIX rlimits required")
def test_executor_cpu_limit_terminates_busy_loop():
    # RLIMIT_CPU=1 should kill a busy loop well before the 8s wall-clock timeout.
    p = _exec_policy(cpu_seconds=1, timeout_seconds=8)
    r = SafeExecutor(p).run([sys.executable, "-c", "\nwhile True:\n  pass\n"])
    assert r.exit_code != 0                      # signal-killed or timed out
    assert r.duration_ms < 8000


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


def test_cli_run_executes(tmp_path, capsys):
    policy = {"schema_version": SCHEMA_VERSION, "allow_exec": True,
              "allowed_commands": [PYBASE], "network": "offline"}
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps(policy))
    rc = cli.main(["run", "--policy", str(pf), "--", sys.executable, "-c", "print('cli-ok')"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and "cli-ok" in out["stdout"]


def test_cli_run_denied_returns_nonzero(tmp_path, capsys):
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps({"schema_version": SCHEMA_VERSION}))   # default-deny
    rc = cli.main(["run", "--policy", str(pf), "--", sys.executable, "-c", "print(1)"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"] == Status.DENIED


def test_cli_preflight(tmp_path, capsys):
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "allow_exec": True,
                              "allowed_commands": [PYBASE]}))
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
