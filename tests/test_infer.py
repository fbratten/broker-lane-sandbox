"""JSON-boundary tests for the `bls infer` seam (P3 contract D4/D5/D7 + test plan §5).

Everything goes through cli.main(["infer", "--request", PATH]) -- the boundary a
real consumer uses. Negative-control heavy: every request-shape violation must
come back as the SAME request_error wrapper broker-run uses (exit 2) with the
request_id echoed; model-layer failures must be model_error results carrying a
closed-set reason_code. No network, no real weights: the weights fixture is small
text bytes with a computed sha256 (INVARIANT-1).
"""
from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from broker_lane_sandbox.cli import main

# Fixture "weights": small text bytes, sha256 computed -- never a real model.
WEIGHTS = b"tiny fixture weights: not a real model\n"
WEIGHTS_SHA = hashlib.sha256(WEIGHTS).hexdigest()

# Policy for the real-runner path: the runner binaries must be allow-listed and
# PATH must remain in env_allowlist (contract D9). DEFAULT_ENV_ALLOWLIST keeps PATH.
FULL_POLICY = {
    "schema_version": 1,
    "allow_exec": True,
    "allowed_commands": ["llama-completion", "llama-cli"],
    "network": "offline",
    "timeout_seconds": 10,
}
# The fake path bypasses the sandbox gates entirely (contract D1/F4), so a
# minimal default-deny policy is sufficient there.
MIN_POLICY = {"schema_version": 1}

PROMPT = "seam-proof prompt payload"


# --- fixtures ----------------------------------------------------------------

@pytest.fixture()
def catalog_path(tmp_path: Path) -> Path:
    """A JSON catalog (stdlib-only: no YAML dependency in tests)."""
    cat = {
        "schema_version": 1,  # CATALOG_SCHEMA_VERSION, not the wire version (L2-F6)
        "cache_dir_env": "SANDBOX_MODEL_DIR",
        "profiles": {
            "fake-unit": {
                "runner": "fake",
                "relative_path": "fake/unit.bin",
                "sha256": "0" * 64,
                "size_bytes": 1,
                "context_length": 64,
            },
            "llama-unit": {
                "runner": "llama.cpp",
                "relative_path": "example/unit.gguf",
                "sha256": WEIGHTS_SHA,
                "size_bytes": len(WEIGHTS),
                "context_length": 4096,
            },
        },
    }
    p = tmp_path / "models.json"
    p.write_text(json.dumps(cat), encoding="utf-8")
    return p


@pytest.fixture()
def model_root(tmp_path: Path, monkeypatch) -> Path:
    """A populated runtime cache: ${SANDBOX_MODEL_DIR}/example/unit.gguf exists."""
    root = tmp_path / "model-cache"
    (root / "example").mkdir(parents=True)
    (root / "example" / "unit.gguf").write_bytes(WEIGHTS)
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))
    return root


def _write_stub(tmp_path: Path, body: str) -> Path:
    """Install an executable stub `llama-completion` in its own bin dir.

    The stub uses ONLY shell builtins (read/printf) because the scrubbed child
    PATH contains just this bin dir.
    """
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "llama-completion"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


# Echoes stdin back with a marker: proves the -f /dev/stdin prompt channel (F2).
STUB_ECHO = """#!/bin/sh
got=""
while IFS= read -r line || [ -n "$line" ]; do got="$got$line"; done
printf 'STUB-GOT:%s\\n' "$got"
"""

# Simulates an in-child load failure past checksum (L4-F6): nonzero + stderr diag.
STUB_FAIL = """#!/bin/sh
got=""
while IFS= read -r line || [ -n "$line" ]; do got="$got$line"; done
printf 'diag: model load failed\\n' >&2
exit 3
"""


def _base_request(catalog: Path, profile: str = "fake-unit", **over) -> dict:
    req = {
        "schema_version": 1,
        "request_id": "infer-test",
        "profile": profile,
        "catalog": str(catalog),
        "prompt": PROMPT,
        "params": {"max_tokens": 8},
        "policy": dict(MIN_POLICY),
    }
    req.update(over)
    return req


def _infer(tmp_path: Path, capsys, request: dict, *extra: str) -> tuple[int, dict]:
    rp = tmp_path / "request.json"
    rp.write_text(json.dumps(request), encoding="utf-8")
    rc = main(["infer", "--request", str(rp), *extra])
    return rc, json.loads(capsys.readouterr().out)


def _assert_request_error(rc: int, payload: dict) -> None:
    # D4: request-shape problems -> the SAME request_error wrapper broker-run
    # uses, exit 2, request_id echoed even on the error path.
    assert rc == 2
    assert payload["status"] == "request_error"
    assert payload["ok"] is False
    assert payload["request_id"] == "infer-test"


# --- request-shape negative controls (D4/D7; no model layer needed) ----------

def test_infer_unknown_request_key_is_request_error(tmp_path, capsys, catalog_path):
    # D4: unknown top-level keys -> request_error.
    rc, payload = _infer(tmp_path, capsys, _base_request(catalog_path, surprise=True))
    _assert_request_error(rc, payload)
    assert "unknown request keys" in payload["reason"]


def test_infer_wrong_schema_version_is_request_error(tmp_path, capsys, catalog_path):
    # D4: schema_version must equal the supported envelope version (1).
    rc, payload = _infer(tmp_path, capsys, _base_request(catalog_path, schema_version=2))
    _assert_request_error(rc, payload)
    assert "schema_version" in payload["reason"]


def test_infer_non_string_prompt_is_request_error(tmp_path, capsys, catalog_path):
    # D4: prompt is a string; data only.
    rc, payload = _infer(tmp_path, capsys, _base_request(catalog_path, prompt=42))
    _assert_request_error(rc, payload)
    assert "prompt" in payload["reason"]


def test_infer_missing_max_tokens_is_request_error(tmp_path, capsys, catalog_path):
    # D7: max_tokens is REQUIRED (-n always emitted; llama's infinite default
    # is never allowed).
    rc, payload = _infer(tmp_path, capsys, _base_request(catalog_path, params={}))
    _assert_request_error(rc, payload)
    assert "max_tokens" in payload["reason"]


def test_infer_bool_max_tokens_is_request_error(tmp_path, capsys, catalog_path):
    # D4/D7: bool rejected everywhere ints are expected (JSON true is not 1).
    rc, payload = _infer(
        tmp_path, capsys, _base_request(catalog_path, params={"max_tokens": True})
    )
    _assert_request_error(rc, payload)
    assert "boolean" in payload["reason"]


def test_infer_out_of_range_temperature_is_request_error(tmp_path, capsys, catalog_path):
    # D7: temperature is a number in [0, 2]; 3.0 must fail loud.
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, params={"max_tokens": 8, "temperature": 3.0}),
    )
    _assert_request_error(rc, payload)
    assert "temperature" in payload["reason"]


def test_infer_unknown_param_is_request_error(tmp_path, capsys, catalog_path):
    # D7: exactly max_tokens/temperature/seed at MVP; unknown keys fail loud.
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, params={"max_tokens": 8, "top_p": 0.9}),
    )
    _assert_request_error(rc, payload)
    assert "top_p" in payload["reason"]


# --- catalog-informed request check (D4: context_length ceiling) -------------

def test_infer_max_tokens_over_context_length_is_request_error(
    tmp_path, capsys, catalog_path
):
    # D4/D7: max_tokens must be <= the profile's declared context_length (64).
    rc, payload = _infer(
        tmp_path, capsys, _base_request(catalog_path, params={"max_tokens": 65})
    )
    _assert_request_error(rc, payload)
    assert "context_length" in payload["reason"]


# --- fake gating (D1/F4) ------------------------------------------------------

def test_infer_fake_without_allow_fake_is_model_error(tmp_path, capsys, catalog_path):
    # F4: `bls infer` refuses runner:fake unless the request carries allow_fake.
    rc, payload = _infer(tmp_path, capsys, _base_request(catalog_path))
    assert rc == 2
    assert payload["request_id"] == "infer-test"
    result = payload["result"]
    assert result["status"] == "model_error"
    assert result["ok"] is False
    assert result["reason_code"] == "unsupported_runner"
    assert "allow_fake" in result["reason"]  # the reason must mention the opt-in
    assert result["argv"] == []              # [] for pre-spawn failures (D5)


def test_infer_fake_with_allow_fake_is_ok(tmp_path, capsys, catalog_path):
    # F4: with allow_fake the seam runs the fake in-process; the model block is
    # marked is_fake so no consumer can mistake it for a gated execution.
    rc, payload = _infer(
        tmp_path, capsys, _base_request(catalog_path, allow_fake=True)
    )
    assert rc == 0
    assert payload["schema_version"] == 1
    assert payload["request_id"] == "infer-test"
    result = payload["result"]
    assert result["status"] == "ok"
    assert result["ok"] is True
    assert result["model"]["is_fake"] is True
    assert result["model"]["runner"] == "fake"
    assert result["model"]["runner_binary"] is None
    gen = result["generation"]
    assert isinstance(gen["text"], str) and gen["text"]
    assert gen["finish_reason"] == "stop"
    assert gen["usage"]["prompt_chars"] == len(PROMPT)
    # reason_code is a model_error-only sibling of reason (D5).
    assert "reason_code" not in result
    # Synthesized exec fields (D1): no process ever spawned.
    assert result["exit_code"] == 0
    assert result["argv"] == []
    assert result["stdout"] == "" and result["stderr"] == ""


def test_infer_fake_preflight_executes_nothing(tmp_path, capsys, catalog_path):
    # D5 + F4: --preflight returns an ok-shaped result with zeroed exec fields
    # and an empty generation block -- nothing runs, not even the fake.
    rc, payload = _infer(
        tmp_path, capsys, _base_request(catalog_path, allow_fake=True), "--preflight"
    )
    assert rc == 0
    result = payload["result"]
    assert result["status"] == "ok"
    assert result["generation"] == {}
    assert result["exit_code"] is None
    assert result["stdout"] == "" and result["stderr"] == ""
    assert result["duration_ms"] == 0
    assert result["env_keys"] == [] and result["limits"] == {}
    assert result["model"]["is_fake"] is True
    assert "preflight" in result["reason"]


# --- model-layer failures (D11 resolution chain, D9 binary identity) ----------

def test_infer_model_dir_unset_is_model_error(tmp_path, capsys, catalog_path, monkeypatch):
    # D11: env unset -> model_error/model_dir_unset (fail loud, never guess a path).
    monkeypatch.delenv("SANDBOX_MODEL_DIR", raising=False)
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
    )
    assert rc == 2
    result = payload["result"]
    assert result["status"] == "model_error"
    assert result["reason_code"] == "model_dir_unset"


def test_infer_missing_model_file_is_model_error(tmp_path, capsys, catalog_path, monkeypatch):
    # D11: cache dir set but the weight file absent -> model_error/model_missing.
    root = tmp_path / "empty-cache"
    root.mkdir()
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
    )
    assert rc == 2
    result = payload["result"]
    assert result["status"] == "model_error"
    assert result["reason_code"] == "model_missing"
    assert result["ok"] is False
    assert result["argv"] == []


def test_infer_runner_not_on_path_is_model_error(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # D9/L4-F1: weights present + verifiable, but no candidate binary on the
    # scrubbed child PATH -> model_error/runner_missing (pre-spawn, fail loud).
    emptybin = tmp_path / "empty-bin"
    emptybin.mkdir()
    monkeypatch.setenv("PATH", str(emptybin))
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
    )
    assert rc == 2
    result = payload["result"]
    assert result["status"] == "model_error"
    assert result["reason_code"] == "runner_missing"
    # Operator guidance: the reason names the expected binary.
    assert "llama-completion" in result["reason"]


# --- end-to-end stub-binary run (D3/F1/F2/F9: canonical argv + prompt channel) -

def test_infer_end_to_end_stub_ok_with_redacted_argv(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # §5 stub-binary test: proves the -f /dev/stdin prompt channel, the canonical
    # argv (-no-cnv/--no-display-prompt/--simple-io), and the self-labeling
    # argv redaction (F9): no absolute local path in argv or the model block (F1).
    bindir = _write_stub(tmp_path, STUB_ECHO)
    monkeypatch.setenv("PATH", str(bindir))
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
    )
    assert rc == 0
    result = payload["result"]
    assert result["status"] == "ok"
    assert result["ok"] is True
    assert result["exit_code"] == 0

    # F2: the prompt reached the child via stdin (the stub echoes it back).
    assert result["generation"]["text"] == f"STUB-GOT:{PROMPT}\n"
    assert result["generation"]["finish_reason"] == "unknown"
    assert result["generation"]["usage"]["prompt_chars"] == len(PROMPT)

    # D3: canonical argv flags are present in the recorded argv.
    argv = result["argv"]
    assert argv[0] == "llama-completion"
    for flag in ("-no-cnv", "--no-display-prompt", "--simple-io"):
        assert flag in argv
    # A4: no log flag -- on modern builds --log-disable pauses the logger that
    # also carries generated token text (upstream issue #10002).
    assert "--log-disable" not in argv
    assert "-n" in argv and "8" in argv

    # F9: the model-path slot is the literal ${SANDBOX_MODEL_DIR}/<relative_path>.
    assert "${SANDBOX_MODEL_DIR}/example/unit.gguf" in argv

    # F1 (scoped path hygiene): the tmp absolute model path appears NOWHERE in
    # the serialized result -- not argv, not the model block, not limits.
    assert str(model_root) not in json.dumps(result)

    # F2 negative control: the prompt is NEVER in argv or env (env_keys = names).
    assert all(PROMPT not in a for a in argv)
    assert all(PROMPT not in k for k in result["env_keys"])

    # D5/D11 model block truthfulness: first use -> full hash, resolved binary name.
    model = result["model"]
    assert model["is_fake"] is False
    assert model["runner"] == "llama.cpp"
    assert model["runner_binary"] == "llama-completion"
    assert model["relative_path"] == "example/unit.gguf"
    assert model["sha256"] == WEIGHTS_SHA
    assert model["sha256_verified"] == "full"


STUB_LEAK = """#!/bin/sh
# Prints the -m value (the ABSOLUTE model path) to stderr like a load banner.
got=""
while IFS= read -r line || [ -n "$line" ]; do got="$got$line"; done
printf 'llama_model_load: loading %s\\n' "$2" >&2
printf 'OUT:%s\\n' "$got"
"""


def test_infer_scrubs_model_path_from_captured_output(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # A4: with no log flag in the canonical argv, a load banner naming the
    # absolute weight path can reach stderr -- the infer layer must scrub it
    # to the self-labeling ${SANDBOX_MODEL_DIR}/... form in ALL captured output.
    bindir = _write_stub(tmp_path, STUB_LEAK)
    monkeypatch.setenv("PATH", str(bindir))
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
    )
    assert rc == 0
    result = payload["result"]
    assert result["status"] == "ok"
    assert "${SANDBOX_MODEL_DIR}/example/unit.gguf" in result["stderr"]
    # Upgraded F1 claim: no absolute local path anywhere in the whole result.
    assert str(model_root) not in json.dumps(result)


def test_infer_llama_preflight_verifies_without_spawning(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # D5 preflight: ok-shaped, exec fields zeroed, model block full, recorded
    # (redacted) argv of exactly what WOULD run -- and nothing spawns.
    bindir = _write_stub(tmp_path, STUB_ECHO)
    monkeypatch.setenv("PATH", str(bindir))
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
        "--preflight",
    )
    assert rc == 0
    result = payload["result"]
    assert result["status"] == "ok"
    assert "preflight" in result["reason"]
    assert result["generation"] == {}
    assert result["exit_code"] is None
    assert result["stdout"] == "" and result["stderr"] == ""
    assert result["env_keys"] == [] and result["limits"] == {}
    assert result["model"]["runner_binary"] == "llama-completion"
    assert "${SANDBOX_MODEL_DIR}/example/unit.gguf" in result["argv"]
    assert str(model_root) not in json.dumps(result)


def test_infer_stub_nonzero_exit_is_generation_error(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # D5/L4-F6: the child ran and exited nonzero (e.g. in-child load failure past
    # checksum) -> generation_error, process exit 1, diagnostic stderr preserved.
    bindir = _write_stub(tmp_path, STUB_FAIL)
    monkeypatch.setenv("PATH", str(bindir))
    rc, payload = _infer(
        tmp_path,
        capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
    )
    assert rc == 1
    result = payload["result"]
    assert result["status"] == "generation_error"
    assert result["ok"] is False
    assert result["exit_code"] == 3
    assert result["generation"] == {}
    assert "model load failed" in result["stderr"]
    assert "reason_code" not in result  # model_error-only field (D5)


# --- capabilities probe surface (D12) -----------------------------------------

def test_version_reports_exact_capabilities_list(capsys):
    # D12: `bls version` gains the additive capabilities list; consumers MUST
    # probe it before the first infer call (absent key == P2 baseline, no infer).
    rc = main(["version"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["capabilities"] == ["run", "broker-run", "infer", "models", "preflight"]
    assert payload["schema_version"] == 1  # envelope version unchanged (D12)
    assert payload["version"] == "0.2.0"
