"""JSON-boundary tests for `bls infer --stream` -- the additive P4 JSONL transport.

Everything goes through cli.main(["infer", "--request", PATH, "--stream"]) -- the
exact boundary a real consumer uses -- and the stdout is parsed as JSONL (one JSON
object per line). The suite is negative-control heavy and grammar-first: every event
carries {stream_version==1, event, seq}; seq is 0,1,2,... gapless with a single
writer; `start` is legal only as seq 0; `final` is the unique terminal (nothing may
follow). Clause tags cite the P4 streaming contract S-clauses.

No network, no real weights (INVARIANT-1). The real-runner path is exercised with
stub binaries that PRINT deterministic output and do NOT read stdin -- this proves
the streaming relay + wiring (start/chunk/final, cap, status parity) independent of
prompt delivery, matching the S10 "delayed-emission stub-binary" CI intent (real
prompt-fed model streaming is operator-gated per S10).
"""
from __future__ import annotations

import copy
import hashlib
import json
import stat
from pathlib import Path

import pytest

from broker_lane_sandbox.cli import main
from broker_lane_sandbox.runners.fake_runner import FakeRunner
from broker_lane_sandbox.streaming import MAX_EVENT_TEXT_CHARS, STREAM_VERSION

# Fixture "weights": small text bytes, sha256 computed -- never a real model.
WEIGHTS = b"tiny fixture weights: not a real model\n"
WEIGHTS_SHA = hashlib.sha256(WEIGHTS).hexdigest()

FULL_POLICY = {
    "schema_version": 1,
    "allow_exec": True,
    "allowed_commands": ["llama-completion", "llama-cli"],
    "network": "offline",
    "timeout_seconds": 10,
}
MIN_POLICY = {"schema_version": 1}

PROMPT = "seam-proof prompt payload"


# --- fixtures ----------------------------------------------------------------

@pytest.fixture()
def catalog_path(tmp_path: Path) -> Path:
    cat = {
        "schema_version": 1,
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
    root = tmp_path / "model-cache"
    (root / "example").mkdir(parents=True)
    (root / "example" / "unit.gguf").write_bytes(WEIGHTS)
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))
    return root


def _write_stub(tmp_path: Path, body: str) -> Path:
    """Install an executable stub `llama-completion` on its own bin dir. The stub
    PRINTS a fixed completion and does NOT read stdin (prompt delivery on the stream
    path is out of scope here -- S10 real-model streaming is operator-gated)."""
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "llama-completion"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


# Prints a deterministic completion to stdout; ignores argv + stdin.
STUB_STREAM_OK = """#!/bin/sh
printf 'HELLO-STREAM-OUTPUT\\n'
"""

# Emits a diagnostic to stderr and exits nonzero; no stdout (no chunks).
STUB_STREAM_FAIL = """#!/bin/sh
printf 'diag: model load failed\\n' >&2
exit 3
"""

# Emits 36 chars of stdout then exits 0 -- more than a small output cap, to exercise
# the S3 no-kill cap: relay stops emitting, emits ONE warning, child still exits 0.
STUB_STREAM_CHATTY = """#!/bin/sh
printf 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\\n'
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


def _run(tmp_path: Path, capsys, request: dict, *extra: str) -> tuple[int, str]:
    rp = tmp_path / "request.json"
    rp.write_text(json.dumps(request), encoding="utf-8")
    rc = main(["infer", "--request", str(rp), *extra])
    return rc, capsys.readouterr().out


def _events(out: str) -> list[dict]:
    """Parse the JSONL stream: one JSON object per non-blank line (S2)."""
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # Every emitted line must be a compact single-line JSON object.
    return [json.loads(ln) for ln in lines]


def _assert_grammar(events: list[dict], *, expect_start: bool) -> None:
    """The producer-side S2/S4 grammar, enforced at the JSON boundary."""
    assert events, "a stream is never empty"
    for i, ev in enumerate(events):
        assert ev["stream_version"] == STREAM_VERSION == 1   # S2 envelope
        assert ev["seq"] == i                                 # S4 gapless, single writer
        assert ev["event"] in {"start", "chunk", "warning", "final"}  # S3 closed set
    finals = [i for i, ev in enumerate(events) if ev["event"] == "final"]
    assert finals == [len(events) - 1]                        # S4 unique terminal, last
    starts = [i for i, ev in enumerate(events) if ev["event"] == "start"]
    if expect_start:
        assert starts == [0]                                  # S3 start only at seq 0
    else:
        assert starts == []                                   # S6 pre-start: no start


def _strip_volatile(wrapper: dict) -> dict:
    """A deep copy with the run-to-run volatile field (duration_ms) dropped, so a
    stream `final.wrapper` can be compared for byte-parity with the non-stream body."""
    w = copy.deepcopy(wrapper)
    if isinstance(w.get("result"), dict):
        w["result"].pop("duration_ms", None)
    return w


# --- S1: capability probe -----------------------------------------------------

def test_version_advertises_infer_stream(capsys):
    # S1: the streaming capability is probe-advertised as an additive entry.
    rc = main(["version"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "infer-stream" in payload["capabilities"]
    assert payload["capabilities"][-1] == "infer-stream"  # additive, appended
    assert payload["schema_version"] == 1                 # envelope unchanged


# --- S3/S5: fake path -- start, gapless chunks, one terminal final -------------

def test_stream_fake_ok_start_chunks_final(tmp_path, capsys, catalog_path):
    # S3/S5: allow_fake --stream -> start(seq 0) + chunk(s) + one terminal final;
    # concat(chunk text) == final.wrapper.result.generation.text (the self-check).
    rc, out = _run(tmp_path, capsys, _base_request(catalog_path, allow_fake=True), "--stream")
    assert rc == 0
    events = _events(out)
    _assert_grammar(events, expect_start=True)

    start = events[0]
    assert start["event"] == "start"
    assert start["request_id"] == "infer-test"
    assert start["model"]["is_fake"] is True
    assert start["model"]["runner"] == "fake"

    chunks = [ev for ev in events if ev["event"] == "chunk"]
    assert chunks, "fake completion is non-empty -> at least one chunk"
    for ev in chunks:
        assert len(ev["text"]) <= MAX_EVENT_TEXT_CHARS   # S3 per-event bound

    final = events[-1]
    assert final["event"] == "final"
    result = final["wrapper"]["result"]
    assert result["status"] == "ok"
    assert result["generation"]["finish_reason"] == "stop"
    # S5 self-check (ok, not truncated): concat(chunks) == generation.text.
    assert "".join(ev["text"] for ev in chunks) == result["generation"]["text"]


def test_stream_fake_final_wrapper_equals_nonstream_body(tmp_path, capsys, catalog_path):
    # S3/S13: final.wrapper is the EXACT non-streaming response body (modulo the
    # run-to-run duration_ms). Same request, both transports, structurally identical.
    req = _base_request(catalog_path, allow_fake=True)
    rc_ns, out_ns = _run(tmp_path, capsys, req)
    assert rc_ns == 0
    nonstream_wrapper = json.loads(out_ns)

    rc_s, out_s = _run(tmp_path, capsys, req, "--stream")
    assert rc_s == 0
    final = _events(out_s)[-1]
    assert _strip_volatile(final["wrapper"]) == _strip_volatile(nonstream_wrapper)


# --- S6: pre-start failures -- a SINGLE seq-0 final, no start ------------------

def test_stream_preflight_and_stream_mutually_exclusive(tmp_path, capsys, catalog_path):
    # S6: --preflight + --stream -> a single seq-0 request_error final, exit 2.
    rc, out = _run(
        tmp_path, capsys, _base_request(catalog_path, allow_fake=True),
        "--stream", "--preflight",
    )
    assert rc == 2
    events = _events(out)
    assert len(events) == 1
    _assert_grammar(events, expect_start=False)
    wrapper = events[0]["wrapper"]
    assert wrapper["status"] == "request_error"
    assert wrapper["ok"] is False
    assert wrapper["request_id"] == "infer-test"   # correlation id still echoed
    assert "mutually exclusive" in wrapper["reason"]


def test_stream_request_shape_error_is_single_final(tmp_path, capsys, catalog_path):
    # S6: a request-shape violation fails before any emission -> single seq-0 final
    # carrying the FLAT request_error wrapper (the same shape broker-run uses), exit 2.
    rc, out = _run(tmp_path, capsys, _base_request(catalog_path, surprise=True), "--stream")
    assert rc == 2
    events = _events(out)
    assert len(events) == 1
    _assert_grammar(events, expect_start=False)
    wrapper = events[0]["wrapper"]
    assert wrapper["status"] == "request_error"
    assert wrapper["request_id"] == "infer-test"
    assert "unknown request keys" in wrapper["reason"]


def test_stream_unreadable_request_is_single_final(tmp_path, capsys):
    # S6: an unreadable/unparseable request file -> single seq-0 request_error final.
    missing = tmp_path / "nope.json"
    rc = main(["infer", "--request", str(missing), "--stream"])
    events = _events(capsys.readouterr().out)
    assert rc == 2
    assert len(events) == 1
    _assert_grammar(events, expect_start=False)
    assert events[0]["wrapper"]["status"] == "request_error"


def test_stream_fake_without_allow_fake_is_model_error_single_final(
    tmp_path, capsys, catalog_path
):
    # S6: fake refused (no allow_fake) is a PRE-start model_error -> a single seq-0
    # final carrying the run-result wrapper (result.status == model_error), exit 2.
    rc, out = _run(tmp_path, capsys, _base_request(catalog_path), "--stream")
    assert rc == 2
    events = _events(out)
    assert len(events) == 1
    _assert_grammar(events, expect_start=False)
    result = events[0]["wrapper"]["result"]
    assert result["status"] == "model_error"
    assert result["reason_code"] == "unsupported_runner"
    assert result["argv"] == []


def test_stream_model_dir_unset_is_model_error_single_final(
    tmp_path, capsys, catalog_path, monkeypatch
):
    # S6: a pre-spawn model-layer failure (env unset) -> single seq-0 model_error
    # final, NO start (nothing spawned), exit 2.
    monkeypatch.delenv("SANDBOX_MODEL_DIR", raising=False)
    rc, out = _run(
        tmp_path, capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
        "--stream",
    )
    assert rc == 2
    events = _events(out)
    assert len(events) == 1
    _assert_grammar(events, expect_start=False)
    result = events[0]["wrapper"]["result"]
    assert result["status"] == "model_error"
    assert result["reason_code"] == "model_dir_unset"


# --- S3/S5/S10: real-runner (stub) streaming ----------------------------------

def test_stream_stub_ok_relays_output_then_final(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # S3/S5/S10: the child spawns -> start(seq 0, is_fake False) + chunk(s) + one
    # terminal final; concat(chunks) == generation.text (ok, not truncated).
    bindir = _write_stub(tmp_path, STUB_STREAM_OK)
    monkeypatch.setenv("PATH", str(bindir))
    rc, out = _run(
        tmp_path, capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
        "--stream",
    )
    assert rc == 0
    events = _events(out)
    _assert_grammar(events, expect_start=True)

    assert events[0]["model"]["is_fake"] is False
    assert events[0]["model"]["runner_binary"] == "llama-completion"

    chunks = [ev for ev in events if ev["event"] == "chunk"]
    result = events[-1]["wrapper"]["result"]
    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["generation"]["finish_reason"] == "unknown"  # llama.cpp reports none
    assert "".join(ev["text"] for ev in chunks) == result["generation"]["text"]
    assert result["generation"]["text"] == "HELLO-STREAM-OUTPUT\n"
    # F1 path hygiene still holds on the stream path: no absolute model path leaks.
    assert str(model_root) not in out


def test_stream_stub_final_wrapper_equals_nonstream_body(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # S13: real-runner final.wrapper is byte-identical (modulo duration_ms) to the
    # non-stream response body for the same request. Both runs pass --verify-full so
    # model.sha256_verified is "full" for each: without it the on-disk sidecar makes
    # the field call-order-dependent ("full" then "cached"), a P3 caching artifact
    # unrelated to the transport that would mask true parity.
    bindir = _write_stub(tmp_path, STUB_STREAM_OK)
    monkeypatch.setenv("PATH", str(bindir))
    req = _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY))

    rc_ns, out_ns = _run(tmp_path, capsys, req, "--verify-full")
    assert rc_ns == 0
    nonstream_wrapper = json.loads(out_ns)

    rc_s, out_s = _run(tmp_path, capsys, req, "--stream", "--verify-full")
    assert rc_s == 0
    final = _events(out_s)[-1]
    assert _strip_volatile(final["wrapper"]) == _strip_volatile(nonstream_wrapper)


def test_stream_stub_nonzero_exit_is_generation_error(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # S6/S8: the child spawned (start emitted) then exited nonzero -> final status
    # generation_error, exit 1; no stdout means no chunks; non-ok generation is {}
    # and reconciles against stdout (S5), diagnostic stderr preserved.
    bindir = _write_stub(tmp_path, STUB_STREAM_FAIL)
    monkeypatch.setenv("PATH", str(bindir))
    rc, out = _run(
        tmp_path, capsys,
        _base_request(catalog_path, profile="llama-unit", policy=dict(FULL_POLICY)),
        "--stream",
    )
    assert rc == 1
    events = _events(out)
    _assert_grammar(events, expect_start=True)
    assert [ev for ev in events if ev["event"] == "chunk"] == []  # no stdout

    result = events[-1]["wrapper"]["result"]
    assert result["status"] == "generation_error"
    assert result["exit_code"] == 3
    assert result["generation"] == {}
    assert "model load failed" in result["stderr"]


# --- S3/S5: no-kill output cap -- warning + truncated reconciliation ----------

def test_stream_output_cap_emits_warning_and_truncates(
    tmp_path, capsys, catalog_path, model_root, monkeypatch
):
    # S3/S5: a chatty child that overruns the policy's max_output_bytes is NOT
    # killed -- the relay stops emitting, emits ONE warning, and the final is a
    # `truncated: true` OK result (P3 status parity). concat(chunks) == the capped
    # prefix and is a strict prefix of generation.text (== prefix + P3 marker):
    # consumers reconcile prefix-of, never byte-equality (S5).
    bindir = _write_stub(tmp_path, STUB_STREAM_CHATTY)
    monkeypatch.setenv("PATH", str(bindir))
    cap_policy = dict(FULL_POLICY, max_output_bytes=10)
    rc, out = _run(
        tmp_path, capsys,
        _base_request(catalog_path, profile="llama-unit", policy=cap_policy),
        "--stream",
    )
    assert rc == 0
    events = _events(out)
    _assert_grammar(events, expect_start=True)

    warnings = [ev for ev in events if ev["event"] == "warning"]
    assert len(warnings) == 1                              # S3 one warning at the cap
    for ev in warnings:
        assert len(ev["message"]) <= MAX_EVENT_TEXT_CHARS  # S3 message bound

    chunks = [ev for ev in events if ev["event"] == "chunk"]
    prefix = "".join(ev["text"] for ev in chunks)
    assert prefix == "ABCDEFGHIJ"                          # exactly the 10-char cap

    result = events[-1]["wrapper"]["result"]
    assert result["status"] == "ok"                        # not killed -> ok parity
    assert result["truncated"] is True
    gen_text = result["generation"]["text"]
    assert gen_text.startswith(prefix)                     # S5 prefix-of
    assert gen_text != prefix and "truncated" in gen_text  # prefix + P3 marker


# --- S2: --pretty is ignored in stream mode -----------------------------------

def test_stream_pretty_is_ignored(tmp_path, capsys, catalog_path):
    # S2: in stream mode every event is compact single-line JSON regardless of
    # --pretty (documented deliberate skew). --pretty is a GLOBAL option (it
    # precedes the subcommand); the stream path never forwards it to the emitter.
    rp = tmp_path / "request.json"
    rp.write_text(json.dumps(_base_request(catalog_path, allow_fake=True)), encoding="utf-8")
    rc = main(["--pretty", "infer", "--request", str(rp), "--stream"])
    out = capsys.readouterr().out
    assert rc == 0
    lines = [ln for ln in out.splitlines() if ln.strip()]
    events = _events(out)
    assert len(lines) == len(events)                    # one object per line
    assert all(not ln.startswith(" ") for ln in lines)  # never pretty-indented
    _assert_grammar(events, expect_start=True)


# --- S10: FakeRunner.generate_stream deterministic chunked emission -----------

def test_generate_stream_matches_generate_in_fixed_slices():
    # S10: generate_stream yields the SAME text as generate() in deterministic
    # fixed-size slices -- proves incremental chunked emission with no real model.
    r = FakeRunner(profile="unit")
    slices = list(r.generate_stream("hello world"))
    assert "".join(slices) == r.generate("hello world")["text"]
    assert len(slices) > 1                              # genuinely multiple slices
    assert all(len(s) <= 5 for s in slices)             # fixed-size (<= step)
    assert all(len(s) == 5 for s in slices[:-1])        # only the last may be short
    # Deterministic: same input -> same slicing.
    assert list(r.generate_stream("hello world")) == slices
