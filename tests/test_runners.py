"""P3 runner-surface tests (runners/base.py, runners/llama_cpp.py, fake_runner).

Contract: the accepted P3 runner-surface contract (v2).
Clauses cited inline as [D1], [D3], [D5], [D7], [D9], [D10], [F2], [F9],
[F11], [L4-F1], [L4-F2].

No network, no real weights: the "model" fixture is small text bytes with a
computed sha256 (INVARIANT-1), and the llama binary is a shell-script STUB
placed on a fake child PATH. The stub-binary end-to-end test proves the
`-f /dev/stdin` prompt channel and that the prompt never touches argv or env.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os

import pytest

from broker_lane_sandbox.policy import SandboxPolicy, is_bare_command
from broker_lane_sandbox.runners.base import (
    RUNNER_FAMILIES,
    RunnerError,
    resolve_runner_family,
)
from broker_lane_sandbox.runners.fake_runner import FakeRunner
from broker_lane_sandbox.runners.llama_cpp import (
    CANDIDATE_BINARIES,
    build_argv,
    probe_version,
    recorded_argv,
    resolve_binary,
    run_llama,
)

# ResolvedModel is written by a parallel lane agent (modelcache.py). If it has
# not landed yet, use a local frozen test double mirroring the PINNED dataclass
# exactly; test_resolved_model_interlock skips (and the synthesizer re-runs the
# full suite after both land, at which point the real class is exercised).
try:
    from broker_lane_sandbox.modelcache import ResolvedModel

    HAVE_MODELCACHE = True
except ImportError:  # pragma: no cover - only pre-integration
    HAVE_MODELCACHE = False

    @dataclasses.dataclass(frozen=True)
    class ResolvedModel:  # type: ignore[no-redef]  # pinned-shape test double
        profile: str
        runner: str
        abs_path: str
        relative_path: str
        sha256: str
        sha256_verified: str
        size_bytes: int
        quantization: str | None = None
        context_length: int | None = None
        license: str | None = None


POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="real-runner path is POSIX-only [D10]"
)


# --------------------------------------------------------------------------
# helpers / fixtures (tmp_path only; nothing touches the repo or the network)
# --------------------------------------------------------------------------

def _policy(**overrides) -> SandboxPolicy:
    data = {
        "allow_exec": True,
        "allowed_commands": ["llama-completion", "llama-cli"],
        "env_allowlist": ["PATH"],
        "timeout_seconds": 20,
    }
    data.update(overrides)
    return SandboxPolicy.from_mapping(data)


def _stub(bin_dir, name, body="#!/bin/sh\nexit 0\n", mode=0o755):
    p = bin_dir / name
    p.write_text(body)
    p.chmod(mode)
    return p


def _capture_stub(bin_dir, capture_dir, tail="input=$(cat)\nprintf 'STUB[%s]' \"$input\"\n"):
    """A stub llama-completion that records its argv + env, then runs `tail`."""
    body = (
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > '{capture_dir}/argv.txt'\n"
        f"env > '{capture_dir}/env.txt'\n"
        f"{tail}"
    )
    return _stub(bin_dir, "llama-completion", body)


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "fakebin"
    d.mkdir()
    return d


@pytest.fixture
def capture(tmp_path):
    d = tmp_path / "capture"
    d.mkdir()
    return d


def _resolved(tmp_path) -> ResolvedModel:
    # Fixture weights are small TEXT bytes with a real computed sha256 --
    # never a real model (INVARIANT-1).
    root = tmp_path / "modelcache"
    (root / "models").mkdir(parents=True, exist_ok=True)
    weights = b"tiny-fake-gguf-weights (small text bytes, not a real model)\n"
    f = root / "models" / "tiny.gguf"
    f.write_bytes(weights)
    return ResolvedModel(
        profile="tiny",
        runner="llama.cpp",
        abs_path=str(f),
        relative_path="models/tiny.gguf",
        sha256=hashlib.sha256(weights).hexdigest(),
        sha256_verified="full",
        size_bytes=len(weights),
    )


# --------------------------------------------------------------------------
# registry: closed set [D1]
# --------------------------------------------------------------------------

def test_runner_families_is_the_exact_closed_set():
    # [D1]/[D2]: fake + llama.cpp ONLY; ollama/transformers are deferred,
    # never silently present.
    assert RUNNER_FAMILIES == ("fake", "llama.cpp")


def test_resolve_runner_family_accepts_registered_families():
    assert resolve_runner_family("fake") == "fake"
    assert resolve_runner_family("llama.cpp") == "llama.cpp"


@pytest.mark.parametrize(
    "bad", ["ollama", "transformers", "", "LLAMA.CPP", "llama_cpp", "fake "]
)
def test_resolve_runner_family_rejects_unknown_strings(bad):
    # [D1]: unknown/unregistered family -> RunnerError(unsupported_runner);
    # matching is exact and case-sensitive (closed set, no normalization).
    with pytest.raises(RunnerError) as ei:
        resolve_runner_family(bad)
    assert ei.value.reason_code == "unsupported_runner"


@pytest.mark.parametrize("bad", [None, 7, 1.5, True, ["fake"], b"fake", {"f": 1}])
def test_resolve_runner_family_rejects_non_strings(bad):
    # Negative control [D1]: type confusion fails loud with the SAME code.
    with pytest.raises(RunnerError) as ei:
        resolve_runner_family(bad)
    assert ei.value.reason_code == "unsupported_runner"


def test_runner_error_is_fail_loud_valueerror():
    # House style: RunnerError is a ValueError subclass carrying reason_code.
    err = RunnerError("boom", reason_code="runner_missing")
    assert isinstance(err, ValueError)
    assert err.reason_code == "runner_missing"
    assert "boom" in str(err)


# --------------------------------------------------------------------------
# binary identity + resolution on the scrubbed child PATH [D9]/[F11]/[L4-F1]
# --------------------------------------------------------------------------

def test_candidate_binaries_ordered_exactly():
    # [L4-F1]: ordered code-owned candidate set -- llama-completion first
    # (upstream discussion #17618), completion-mode llama-cli as fallback.
    assert CANDIDATE_BINARIES == ("llama-completion", "llama-cli")


def test_resolve_binary_prefers_llama_completion(bin_dir, monkeypatch):
    _stub(bin_dir, "llama-completion")
    _stub(bin_dir, "llama-cli")
    monkeypatch.setenv("PATH", str(bin_dir))
    name, abs_path = resolve_binary(_policy())
    assert name == "llama-completion"  # order matters [L4-F1]
    assert abs_path == str(bin_dir / "llama-completion")
    # argv[0] must survive the SafeExecutor bare-command gate.
    assert is_bare_command(name)


def test_resolve_binary_falls_back_to_llama_cli(bin_dir, monkeypatch):
    _stub(bin_dir, "llama-cli")
    monkeypatch.setenv("PATH", str(bin_dir))
    name, abs_path = resolve_binary(_policy())
    assert name == "llama-cli"
    assert abs_path == str(bin_dir / "llama-cli")


def test_resolve_binary_missing_raises_runner_missing(bin_dir, monkeypatch):
    # [D9]: no candidate on the child PATH -> fail loud, actionable message.
    monkeypatch.setenv("PATH", str(bin_dir))  # empty dir
    with pytest.raises(RunnerError) as ei:
        resolve_binary(_policy())
    assert ei.value.reason_code == "runner_missing"
    msg = str(ei.value)
    assert "llama-completion" in msg
    assert "PATH" in msg
    assert "env_allowlist" in msg


def test_resolve_binary_uses_scrubbed_child_path_not_host_path(bin_dir, monkeypatch):
    # [D9]/[F11]: resolution runs on the SCRUBBED child env's PATH. When the
    # policy does NOT allow-list PATH, the child would spawn without it -- so
    # preflight must fail closed the same way, even though the binary exists
    # on the host os.environ PATH.
    _stub(bin_dir, "llama-completion")
    monkeypatch.setenv("PATH", str(bin_dir))
    no_path_policy = _policy(env_allowlist=["HOME"])  # PATH deliberately absent
    with pytest.raises(RunnerError) as ei:
        resolve_binary(no_path_policy)
    assert ei.value.reason_code == "runner_missing"
    # Negative control: identical host state WITH PATH allow-listed resolves.
    name, _ = resolve_binary(_policy())
    assert name == "llama-completion"


@POSIX_ONLY
def test_resolve_binary_ignores_non_executable_files(bin_dir, monkeypatch):
    # Negative control: a non-executable file named like the binary must not
    # resolve (shutil.which requires X_OK) -- fail closed, not a broken spawn.
    _stub(bin_dir, "llama-completion", mode=0o644)
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(RunnerError) as ei:
        resolve_binary(_policy())
    assert ei.value.reason_code == "runner_missing"


# --------------------------------------------------------------------------
# canonical argv [D3]/[D7] + self-labeling recording [D5]/[F9]
# --------------------------------------------------------------------------

def test_build_argv_exact_canonical_shape():
    # [D3]: exact-shape assertion of the code-owned canonical argv:
    # <binary> -m <abs> -f /dev/stdin -no-cnv --no-display-prompt
    # --simple-io -n <max_tokens>   (A4: no log flag)
    argv = build_argv("llama-completion", "/cache/models/tiny.gguf", max_tokens=64)
    assert argv == [
        "llama-completion",
        "-m", "/cache/models/tiny.gguf",
        "-f", "/dev/stdin",
        "-no-cnv",
        "--no-display-prompt",
        "--simple-io",
        "-n", "64",
    ]


def test_build_argv_appends_temp_then_seed():
    # [D3]/[D7]: optional params append in fixed order; -n always present
    # (llama's default -1 = infinite is never allowed).
    argv = build_argv(
        "llama-cli", "/cache/m.gguf", max_tokens=8, temperature=0.5, seed=7
    )
    assert argv == [
        "llama-cli",
        "-m", "/cache/m.gguf",
        "-f", "/dev/stdin",
        "-no-cnv",
        "--no-display-prompt",
        "--simple-io",
        "-n", "8",
        "--temp", "0.5",
        "--seed", "7",
    ]


def test_build_argv_omits_absent_optionals_individually():
    with_temp = build_argv("b", "/m", max_tokens=1, temperature=1.0)
    assert with_temp[-2:] == ["--temp", "1.0"]
    assert "--seed" not in with_temp
    with_seed = build_argv("b", "/m", max_tokens=1, seed=42)
    assert with_seed[-2:] == ["--seed", "42"]
    assert "--temp" not in with_seed


def test_recorded_argv_self_labels_model_slot():
    # [D5]/[F9]: the model-path slot is replaced by the literal
    # ${<env>}/<relative> form; everything else is untouched; input not mutated.
    argv = build_argv("llama-completion", "/abs/cache/models/tiny.gguf", max_tokens=4)
    rec = recorded_argv(
        argv,
        cache_dir_env="SANDBOX_MODEL_DIR",
        relative_path="models/tiny.gguf",
        model_abs_path="/abs/cache/models/tiny.gguf",
    )
    assert rec[2] == "${SANDBOX_MODEL_DIR}/models/tiny.gguf"
    assert "/abs/cache/models/tiny.gguf" not in rec  # no absolute local path [D5]
    assert rec[0] == "llama-completion" and rec[1] == "-m"
    assert rec[3:] == argv[3:]
    assert argv[2] == "/abs/cache/models/tiny.gguf"  # original is a copy, unmutated


def test_recorded_argv_honors_custom_env_name():
    # [D5]: the label uses the request's model_dir_env name verbatim.
    rec = recorded_argv(
        ["b", "-m", "/root/w.gguf"],
        cache_dir_env="MY_MODELS",
        relative_path="w.gguf",
        model_abs_path="/root/w.gguf",
    )
    assert rec == ["b", "-m", "${MY_MODELS}/w.gguf"]


# --------------------------------------------------------------------------
# run_llama end-to-end with a STUB binary [D3]/[F2]/[L4-F2]/[F9]/[F11]
# --------------------------------------------------------------------------

@POSIX_ONLY
def test_run_llama_prompt_travels_stdin_only(bin_dir, capture, tmp_path, monkeypatch):
    # THE stub-binary test [F2]/[L4-F2]: proves the -f /dev/stdin channel
    # works through SafeExecutor's input_text, and that the prompt appears
    # NOWHERE in the child argv or the child env.
    _capture_stub(bin_dir, capture)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    policy = _policy()
    resolved = _resolved(tmp_path)
    prompt = "TOP-SECRET-PROMPT-c4f7: summarize the doc"

    result, rec, binary = run_llama(policy, resolved, prompt, max_tokens=32)

    assert binary == "llama-completion"
    assert result.status == "ok" and result.ok
    # (a) prompt arrived via stdin: the stub echoes a transformed copy.
    assert result.stdout == f"STUB[{prompt}]"
    # (b) prompt absent from child argv and child env [D3: never -p, never env].
    argv_seen = (capture / "argv.txt").read_text()
    env_seen = (capture / "env.txt").read_text()
    assert "TOP-SECRET-PROMPT-c4f7" not in argv_seen
    assert "TOP-SECRET-PROMPT-c4f7" not in env_seen
    assert "TOP-SECRET-PROMPT-c4f7" not in " ".join(result.argv)
    # The child received the exact canonical argv with the REAL abs path [D3].
    expected_child_args = build_argv(binary, resolved.abs_path, max_tokens=32)[1:]
    assert argv_seen.splitlines() == expected_child_args
    # The child ran under the scrubbed env (offline marker present) [F11].
    assert "SANDBOX_NETWORK=offline" in env_seen
    assert result.network == "offline"
    # (c) recorded argv carries the ${ENV}/relative self-label form [D5]/[F9].
    assert rec[2] == "${SANDBOX_MODEL_DIR}/models/tiny.gguf"
    assert resolved.abs_path not in rec
    assert rec[0] == binary
    # JSON boundary: ExecResult must round-trip losslessly.
    round_tripped = json.loads(json.dumps(result.to_dict()))
    assert round_tripped["status"] == "ok"
    assert round_tripped["ok"] is True
    assert round_tripped["exit_code"] == 0


@POSIX_ONLY
def test_run_llama_child_failure_surfaces_exit_nonzero(bin_dir, capture, tmp_path, monkeypatch):
    # [D5]: infer.py maps exit_nonzero -> generation_error at ONE documented
    # point. The runner surface itself must report the raw sandbox status.
    _capture_stub(bin_dir, capture, tail="exit 3\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    result, rec, binary = run_llama(
        _policy(), _resolved(tmp_path), "irrelevant", max_tokens=4
    )
    assert result.status == "exit_nonzero"
    assert result.ok is False
    assert result.exit_code == 3


@POSIX_ONLY
def test_run_llama_inherits_sandbox_deny_gate(bin_dir, capture, tmp_path, monkeypatch):
    # [D3]: "all gates inherited" -- a policy that does not allow the resolved
    # binary name yields DENIED from SafeExecutor, and nothing spawns.
    _capture_stub(bin_dir, capture)
    monkeypatch.setenv("PATH", str(bin_dir))
    policy = _policy(allowed_commands=["something-else"])
    result, rec, binary = run_llama(
        policy, _resolved(tmp_path), "prompt-text", max_tokens=4
    )
    assert result.status == "denied"
    assert result.exit_code is None
    assert not (capture / "argv.txt").exists()  # negative control: no spawn
    # Recording still self-labels even on denial [F9].
    assert rec[2] == "${SANDBOX_MODEL_DIR}/models/tiny.gguf"


def test_run_llama_missing_binary_fails_before_any_spawn(bin_dir, capture, tmp_path, monkeypatch):
    # [D9]: resolution failure is pre-spawn and loud (RunnerError -> the infer
    # layer's model_error/runner_missing) -- never a broken half-run.
    monkeypatch.setenv("PATH", str(bin_dir))  # no stubs on the child PATH
    with pytest.raises(RunnerError) as ei:
        run_llama(_policy(), _resolved(tmp_path), "prompt", max_tokens=4)
    assert ei.value.reason_code == "runner_missing"
    assert not (capture / "argv.txt").exists()


@POSIX_ONLY
def test_run_llama_passes_temperature_and_seed_to_child(bin_dir, capture, tmp_path, monkeypatch):
    # [D3]/[D7]: optional params reach the child argv in canonical positions.
    _capture_stub(bin_dir, capture, tail=": > /dev/null\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    result, rec, binary = run_llama(
        _policy(), _resolved(tmp_path), "p", max_tokens=2, temperature=0.25, seed=99
    )
    assert result.status == "ok"
    lines = (capture / "argv.txt").read_text().splitlines()
    assert lines[-4:] == ["--temp", "0.25", "--seed", "99"]
    assert rec[-4:] == ["--temp", "0.25", "--seed", "99"]


# --------------------------------------------------------------------------
# FakeRunner protocol conformance [D1]/[F4]
# (the 2 pre-existing tests in test_fake_runner.py stay green unmodified)
# --------------------------------------------------------------------------

def test_fake_runner_accepts_full_protocol_kwargs():
    # [D1]: generate(prompt, *, max_tokens, temperature=None, seed=None).
    r = FakeRunner(profile="unit")
    out = r.generate("abc", max_tokens=8, temperature=0.7, seed=42)
    assert set(out) == {"profile", "text", "usage", "is_fake"}  # keys preserved
    assert out["is_fake"] is True  # [F4] every fake result self-labels
    assert out["profile"] == "unit"
    assert out["usage"] == {"prompt_chars": 3, "completion_chars": len(out["text"])}


def test_fake_runner_deterministic_across_sampling_params():
    # [D1]: params are accepted for protocol conformance but the fake stays
    # canned -- identical output for any temperature/seed/max_tokens.
    r = FakeRunner(profile="unit")
    base = r.generate("hello world")
    assert r.generate("hello world", max_tokens=1) == base
    assert r.generate("hello world", temperature=1.9, seed=1) == base
    assert r.generate("hello world", temperature=0.0, seed=999) == base


def test_fake_runner_satisfies_runner_protocol_surface():
    # [D1]: profile / requires_weights / generate -- the full protocol surface.
    r = FakeRunner(profile="p")
    assert r.profile == "p"
    assert r.requires_weights is False
    out = r.generate("x")
    assert isinstance(out, dict)
    # JSON boundary: the generate dict must serialize losslessly.
    assert json.loads(json.dumps(out)) == out
    assert "fake" in RUNNER_FAMILIES


# --------------------------------------------------------------------------
# interlock with the parallel modelcache lane
# --------------------------------------------------------------------------

def test_resolved_model_interlock_matches_pinned_shape():
    # The pinned ResolvedModel dataclass (modelcache.py, parallel agent) must
    # carry at least the fields run_llama/recorded_argv consume. Skips only
    # while modelcache.py has not landed; the synthesizer re-runs the full
    # suite after integration, at which point this executes against the real
    # class.
    if not HAVE_MODELCACHE:
        pytest.skip(
            "modelcache.py not landed yet (parallel lane); tests above used "
            "the pinned-shape local double"
        )
    assert dataclasses.is_dataclass(ResolvedModel)
    field_names = {f.name for f in dataclasses.fields(ResolvedModel)}
    assert {
        "profile",
        "runner",
        "abs_path",
        "relative_path",
        "sha256",
        "sha256_verified",
        "size_bytes",
    } <= field_names
    # Pinned: frozen dataclass (house style).
    params = getattr(ResolvedModel, "__dataclass_params__")
    assert params.frozen is True


# --------------------------------------------------------------------------
# probe_version [A2] + run_llama cache_dir_env label override [A3]
# --------------------------------------------------------------------------

def _write_version_stub(bin_dir, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@POSIX_ONLY
def test_probe_version_records_first_line_on_success(bin_dir, monkeypatch):
    # [A2]: best-effort --version probe under the same gate chain; records the
    # first non-empty output line (stdout-then-stderr), exit-0 only.
    _write_version_stub(
        bin_dir, "llama-completion", "#!/bin/sh\nprintf 'stub-llama 1.2.3\\n'\n"
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    assert probe_version(_policy(), "llama-completion") == "stub-llama 1.2.3"


@POSIX_ONLY
def test_probe_version_returns_none_on_failure_and_never_raises(bin_dir, monkeypatch):
    # [A2]: nonzero exit (no --version support) or a denying policy must yield
    # None -- the probe can never fail or crash the inference call.
    _write_version_stub(bin_dir, "llama-completion", "#!/bin/sh\nexit 2\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    assert probe_version(_policy(), "llama-completion") is None
    deny = dataclasses.replace(_policy(), allow_exec=False)
    assert probe_version(deny, "llama-completion") is None


@POSIX_ONLY
def test_run_llama_cache_dir_env_overrides_label(bin_dir, capture, tmp_path, monkeypatch):
    # [A3]: infer passes the catalog's cache_dir_env so preflight and execution
    # recorded-argv labels always agree; without it the label defaults to
    # policy.model_dir_env (covered by the e2e test above).
    _capture_stub(bin_dir, capture)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    _, rec, _ = run_llama(
        _policy(), _resolved(tmp_path), "p", max_tokens=4, cache_dir_env="CUSTOM_CACHE"
    )
    assert rec[2] == "${CUSTOM_CACHE}/models/tiny.gguf"
    assert "${SANDBOX_MODEL_DIR}" not in " ".join(rec)
