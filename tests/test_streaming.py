"""P4 acceptance suite for `bls infer --stream` -- the JSONL streaming transport.

Binding contract: ai-memory/bls-p4-streaming-contract-s01/P4-CONTRACT.md (S1-S14).

Trust model of THIS test module
-------------------------------
Two independent drive surfaces, both JSON-boundary:

  * PURE  -- ``streaming.run_llama_stream`` driven directly with a real
    :class:`StreamEmitter` writing into a list collector, over STUB "binaries"
    (executable interpreter scripts on an allow-listed PATH). These tests are
    independent of the infer.py/cli.py wiring (Agent A) and are the authoritative
    checks for the execution layer (S3/S5/S7/S8/S10) and the emitter grammar
    (S2/S4/S12). ``run_llama_stream`` emits ``start``+``chunk``/``warning`` but
    NEVER ``final`` (the caller owns the result shape, S10) -- so a pure run's
    event list has no terminal.

  * CLI   -- the whole stack through ``cli.main(["infer","--request",P,"--stream"])``,
    stdout parsed as JSONL. The consumer's S1 obligation is a MANDATORY
    capability probe before the first streaming call; we honour it literally:
    the CLI tests SKIP unless ``bls version`` advertises ``"infer-stream"``
    (i.e. until the wiring lands). Once advertised they assert the full framing
    incl. the unique terminal ``final``.

Negative-control heavy, fail-loud: every stub is deterministic; the stubs use
ONLY shell-free interpreter builtins and never read the network or real weights.
The stub shebang is an ABSOLUTE interpreter path so the scrubbed child PATH
(bindir only) still launches it; the bare stub NAME is what the sandbox gate
resolves (S10 spawns through the same extracted gate chain as SafeExecutor.run).
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

# conftest.py puts src/ on sys.path.
from broker_lane_sandbox import SCHEMA_VERSION
from broker_lane_sandbox.executor import _KILL_GRACE
from broker_lane_sandbox.policy import SandboxPolicy
from broker_lane_sandbox.streaming import (
    MAX_EVENT_TEXT_CHARS,
    MAX_WARNINGS,
    STREAM_VERSION,
    StreamEmitter,
    StreamProtocolError,
    run_llama_stream,
)

# The pure execution path spawns a process group (setsid) + kills it; POSIX-only
# (mirrors the P3 real-runner platform scope, contract D10).
requires_posix = pytest.mark.skipif(
    not (hasattr(os, "fork") and hasattr(os, "setsid")),
    reason="POSIX fork+setsid required for the streaming process-group path",
)

PYBIN = os.path.realpath(sys.executable)  # absolute interpreter for stub shebangs
STUB_NAME = "streamstub"                   # bare command name for the pure stubs


# ============================================================================
# Section A -- StreamEmitter grammar (S2 framing, S4 ordering/terminality, S12)
# These are pure and do NOT depend on any spawn.
# ============================================================================


def _emitter_and_lines():
    lines: list[str] = []
    return StreamEmitter(lines.append), lines


def _parse(lines: list[str]) -> list[dict]:
    return [json.loads(x) for x in lines]


def test_emitter_constants_match_contract():
    # S12/S3: the stream-envelope version and the two bounds are frozen in the
    # streaming module (per-enforcing-layer ownership, mirroring CATALOG_SCHEMA_VERSION).
    assert STREAM_VERSION == 1
    assert MAX_EVENT_TEXT_CHARS == 8192
    assert MAX_WARNINGS == 8


def test_emitter_seq_gapless_and_versioned():
    # S2/S4: single writer, gapless seq starting at 0; every event carries
    # {stream_version==1, event, seq}.
    em, lines = _emitter_and_lines()
    em.start("rid", {"is_fake": False})
    em.chunk("alpha")
    em.chunk("beta")
    em.warning("heads up")
    em.final({"schema_version": SCHEMA_VERSION, "request_id": "rid", "result": {}})
    objs = _parse(lines)
    assert [o["seq"] for o in objs] == [0, 1, 2, 3, 4]
    assert [o["event"] for o in objs] == ["start", "chunk", "chunk", "warning", "final"]
    assert {o["stream_version"] for o in objs} == {1}


def test_emitter_start_must_be_seq0():
    # S3/S4: `start` is legal only as seq 0. A chunk-first stream cannot then start.
    em, _ = _emitter_and_lines()
    em.chunk("x")
    with pytest.raises(StreamProtocolError):
        em.start("rid", {})


def test_emitter_start_only_once():
    em, _ = _emitter_and_lines()
    em.start("rid", {})
    with pytest.raises(StreamProtocolError):
        em.start("rid", {})


def test_emitter_final_is_unique_terminal():
    # S4 + acceptance (6): `final` is the unique terminal -- NOTHING may follow it,
    # and a second final is itself a protocol error. final-then-emit MUST raise.
    em, lines = _emitter_and_lines()
    em.start("rid", {})
    em.final({"ok": True})
    for fn in (
        lambda: em.chunk("x"),
        lambda: em.warning("w"),
        lambda: em.final({}),
        lambda: em.start("rid", {}),
    ):
        with pytest.raises(StreamProtocolError):
            fn()
    # the terminal really was terminal: exactly one final, and it is last.
    objs = _parse(lines)
    assert [o["event"] for o in objs] == ["start", "final"]


def test_emitter_warning_bound_is_bilateral():
    # S3/S4: at most MAX_WARNINGS per stream; the emitter refuses further warnings
    # (returns False) rather than emitting a 9th (a 9th is a consumer protocol error).
    em, lines = _emitter_and_lines()
    em.start("rid", {})
    rets = [em.warning(f"w{i}") for i in range(MAX_WARNINGS + 3)]
    assert rets == [True] * MAX_WARNINGS + [False] * 3
    assert sum(1 for o in _parse(lines) if o["event"] == "warning") == MAX_WARNINGS


def test_emitter_warning_message_is_bounded():
    # S3: a warning message is bounded by MAX_EVENT_TEXT_CHARS.
    em, lines = _emitter_and_lines()
    em.start("rid", {})
    em.warning("Z" * (MAX_EVENT_TEXT_CHARS + 500))
    msg = _parse(lines)[-1]["message"]
    assert len(msg) == MAX_EVENT_TEXT_CHARS


def test_emitter_lines_are_compact_ascii_single_line():
    # S2: one JSON object per line, "\n"-terminated, compact (no --pretty in stream
    # mode). ensure_ascii keeps every line 7-bit + newline-free regardless of the
    # chunk's unicode content, and it round-trips back to the original text.
    em, lines = _emitter_and_lines()
    em.start("rid", {})
    em.chunk("héllo — €dge\ncase")  # embedded newline + multibyte must not break framing
    line = lines[-1]
    assert line.endswith("\n")
    assert line.count("\n") == 1  # the embedded "\n" was JSON-escaped, not literal
    assert all(ord(c) < 128 for c in line)  # ASCII-safe wire
    assert json.loads(line)["text"] == "héllo — €dge\ncase"


# ============================================================================
# Section B -- run_llama_stream execution layer (PURE; S3/S5/S7/S8/S10)
# Driven directly with a StreamEmitter over a list collector + stub binaries.
# ============================================================================


def _make_executable(p: Path) -> None:
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _pure_stub(behavior: str) -> str:
    """A stub whose body is exactly `behavior` (column-0 python)."""
    return "#!" + PYBIN + "\n" + behavior


def _stream_policy(**over) -> SandboxPolicy:
    base = dict(
        schema_version=SCHEMA_VERSION,
        allow_exec=True,
        allowed_commands=[STUB_NAME],
        network="offline",
        timeout_seconds=10.0,
        max_output_bytes=1_000_000,
    )
    base.update(over)
    return SandboxPolicy.from_mapping(base)


def _run_stream(tmp_path, monkeypatch, behavior, *, prompt="PROMPT-DATA", **polover):
    """Install `behavior` as the `streamstub` binary, prepend its bin dir to PATH,
    and drive run_llama_stream with a real StreamEmitter over a list collector.
    Returns (events, exec_result, wall_seconds)."""
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / STUB_NAME
    stub.write_text(_pure_stub(behavior), encoding="utf-8")
    _make_executable(stub)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))

    lines: list[str] = []
    em = StreamEmitter(lines.append)
    t0 = time.monotonic()
    res = run_llama_stream(
        _stream_policy(**polover),
        [STUB_NAME],
        prompt,
        emitter=em,
        request_id="rid-pure",
        model_block={"profile": "unit", "is_fake": False},
    )
    dur = time.monotonic() - t0
    return _parse(lines), res, dur


def _events(events, name):
    return [e for e in events if e["event"] == name]


def _assert_pure_prelude(events):
    """Common structural checks for a pure run: gapless from 0, exactly one start
    at seq 0, and NO final (run_llama_stream never emits it -- S10)."""
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert {e["stream_version"] for e in events} == {1}
    starts = _events(events, "start")
    assert len(starts) == 1 and events[0]["event"] == "start" and events[0]["seq"] == 0
    assert _events(events, "final") == []  # the caller owns `final`, not the relay


@requires_posix
def test_pure_success_start_then_chunk(tmp_path, monkeypatch):
    # Acceptance (1): deterministic success -> start(seq0) + chunk; concat(chunks)
    # == the raw ExecResult stdout (S5 real-runner equality: concat==stdout when ok
    # and not truncated); exactly one start; the start carries request_id + model.
    events, res, _ = _run_stream(
        tmp_path, monkeypatch, "import sys\nsys.stdout.write('HELLO-STREAM\\n')\n"
    )
    _assert_pure_prelude(events)
    assert events[0]["request_id"] == "rid-pure"
    assert events[0]["model"] == {"profile": "unit", "is_fake": False}
    chunks = _events(events, "chunk")
    assert chunks and "".join(c["text"] for c in chunks) == res.stdout == "HELLO-STREAM\n"
    assert res.status == "ok" and res.exit_code == 0 and res.truncated is False


@requires_posix
def test_pure_chunks_split_only_at_char_bound(tmp_path, monkeypatch):
    # S3: per-event framing bound MAX_EVENT_TEXT_CHARS; a large completion is split
    # into whole-char chunks whose concatenation reconstructs stdout exactly.
    events, res, _ = _run_stream(
        tmp_path, monkeypatch, "import sys\nsys.stdout.write('x'*20000)\n"
    )
    _assert_pure_prelude(events)
    chunks = _events(events, "chunk")
    assert [len(c["text"]) for c in chunks] == [8192, 8192, 3616]
    assert all(len(c["text"]) <= MAX_EVENT_TEXT_CHARS for c in chunks)
    assert "".join(c["text"] for c in chunks) == res.stdout == "x" * 20000
    assert res.status == "ok" and res.truncated is False


@requires_posix
def test_pure_empty_completion_no_chunk(tmp_path, monkeypatch):
    # Acceptance (2): empty completion -> start, no chunk, ok.
    events, res, _ = _run_stream(tmp_path, monkeypatch, "pass\n")
    _assert_pure_prelude(events)
    assert _events(events, "chunk") == []
    assert res.status == "ok" and res.exit_code == 0 and res.stdout == ""


@requires_posix
def test_pure_nonzero_exit_earlier_chunks_stand(tmp_path, monkeypatch):
    # Acceptance (3): nonzero-exit child -> ExecResult exit_nonzero (the caller maps
    # to generation_error, S6); the chunk emitted before the failure STANDS.
    events, res, _ = _run_stream(
        tmp_path,
        monkeypatch,
        "import sys\nsys.stdout.write('PARTIAL-OUT\\n'); sys.exit(3)\n",
    )
    _assert_pure_prelude(events)
    chunks = _events(events, "chunk")
    assert [c["text"] for c in chunks] == ["PARTIAL-OUT\n"]
    assert res.status == "exit_nonzero" and res.exit_code == 3


@requires_posix
def test_pure_timeout_after_partial_watchdog_is_independent(tmp_path, monkeypatch):
    # Acceptance (4): the child emits+flushes text then sleeps >> the timeout; the
    # INDEPENDENT watchdog (S7) group-kills it. ExecResult status is timeout, the
    # partial chunk stands, AND the call RETURNS within timeout+grace+slack -- it
    # does NOT wait out the 30s child sleep (watchdog independence, not relay-driven).
    behavior = (
        "import sys, time\n"
        "sys.stdout.write('PARTIAL\\n'); sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    events, res, dur = _run_stream(
        tmp_path, monkeypatch, behavior, timeout_seconds=1.0
    )
    _assert_pure_prelude(events)
    assert [c["text"] for c in _events(events, "chunk")] == ["PARTIAL\n"]
    assert res.status == "timeout"
    # returns at ~timeout, decisively before the 30s sleep would have elapsed.
    assert dur < 1.0 + _KILL_GRACE + 6.0
    assert dur < 15.0


@requires_posix
def test_pure_output_cap_no_kill_one_warning(tmp_path, monkeypatch):
    # Acceptance (5) + S3 no-kill-at-cap: child emits > max_output_bytes chars then
    # EXITS 0. The relay stops emitting/accumulating at the cap, emits EXACTLY ONE
    # warning, and drains to natural exit -> status stays `ok` (NOT generation_error),
    # truncated True. concat(chunks)==the capped prefix (marker in NO chunk); final
    # stdout == prefix + P3's literal truncation marker.
    events, res, _ = _run_stream(
        tmp_path, monkeypatch, "import sys\nsys.stdout.write('a'*200)\n",
        max_output_bytes=50,
    )
    _assert_pure_prelude(events)
    warns = _events(events, "warning")
    assert len(warns) == 1
    chunks = _events(events, "chunk")
    prefix = "".join(c["text"] for c in chunks)
    assert prefix == "a" * 50
    assert all("[truncated" not in c["text"] for c in chunks)  # marker never in a chunk
    assert res.status == "ok"           # a chatty child that exits 0 is ok, not error
    assert res.truncated is True
    assert res.stdout == "a" * 50 + "\n...[truncated 150 chars]"


@requires_posix
def test_pure_stderr_separation_and_no_deadlock(tmp_path, monkeypatch):
    # Acceptance (7) + S8/R3: the child floods >64 KiB to stderr WHILE producing
    # stdout. The mandatory stderr-drain thread prevents the 64 KiB pipe deadlock;
    # stderr NEVER enters the stream (no chunk carries it) and is reported only in
    # the final ExecResult. The call must complete (no hang).
    behavior = (
        "import sys\n"
        "sys.stderr.write('ERRMARK')\n"
        "sys.stderr.write('E'*100000)\n"
        "sys.stderr.flush()\n"
        "sys.stdout.write('STDOUT-OK\\n')\n"
    )
    events, res, dur = _run_stream(tmp_path, monkeypatch, behavior)
    _assert_pure_prelude(events)
    chunks = _events(events, "chunk")
    assert "".join(c["text"] for c in chunks) == res.stdout == "STDOUT-OK\n"
    assert "ERRMARK" in res.stderr
    assert all("ERRMARK" not in c["text"] and "EEEE" not in c["text"] for c in chunks)
    assert res.status == "ok"
    assert dur < 10.0  # drained concurrently: no deadlock, no wall-clock stall


@requires_posix
def test_pure_utf8_split_across_reads_reassembles(tmp_path, monkeypatch):
    # Acceptance (8) + R6/S10: a 3-byte char (U+20AC, EURO SIGN) is split across two
    # child writes with a hold between them, forcing the FIRST parent read to see an
    # INCOMPLETE sequence (b'A\xe2\x82'). The ONE shared incremental decoder buffers
    # the partial bytes across reads and reassembles the char -- so NO U+FFFD
    # replacement char appears at the split. (A non-buffering, per-read decode with
    # errors='replace' would instead have produced replacement characters here, so
    # the clean 'A€B' is a positive proof of cross-read buffering.)
    behavior = (
        "import sys, time\n"
        "w = sys.stdout.buffer\n"
        r"w.write(b'A'); w.write(b'\xe2\x82'); w.flush()" + "\n"
        "time.sleep(0.4)\n"
        r"w.write(b'\xac'); w.write(b'B'); w.flush()" + "\n"
    )
    events, res, _ = _run_stream(tmp_path, monkeypatch, behavior)
    _assert_pure_prelude(events)
    concat = "".join(c["text"] for c in _events(events, "chunk"))
    assert concat == res.stdout == "A€B"
    assert "�" not in concat and "�" not in res.stdout
    assert res.status == "ok"


@requires_posix
def test_pure_prompt_reaches_child_via_stdin(tmp_path, monkeypatch):
    # F2/D3 positive control for the streaming path: the prompt reaches the child
    # ONLY via stdin (the _StdinWriter thread writes it and closes the pipe so the
    # child sees EOF). The stub reads stdin (bounded by a 1s select so it cannot
    # hang the watchdog) and echoes it; the prompt payload MUST appear in output.
    # (Was an XFAIL flagging the prompt-delivery gap; fixed in streaming.py.)
    behavior = (
        "import sys, select\n"
        "data = b''\n"
        "r,_,_ = select.select([sys.stdin.buffer], [], [], 1.0)\n"
        "if r:\n"
        "    data = sys.stdin.buffer.read()\n"
        "sys.stdout.write('GOT:' + data.decode('utf-8','replace'))\n"
    )
    _events_, res, _ = _run_stream(
        tmp_path, monkeypatch, behavior, prompt="PROMPT-STDIN-PAYLOAD"
    )
    assert "PROMPT-STDIN-PAYLOAD" in res.stdout


# ============================================================================
# Section C -- full-stack CLI JSONL acceptance (S1/S2/S3/S4/S6/S8/S13)
# Gated on the S1 mandatory capability probe: SKIP until `bls version` advertises
# "infer-stream" (i.e. until the infer.py/cli.py wiring, Agent A, lands).
# ============================================================================

from broker_lane_sandbox.cli import main  # noqa: E402  (after the pure section)


def _capabilities() -> list:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["version"])
    return json.loads(buf.getvalue())["capabilities"]


STREAM_WIRED = "infer-stream" in _capabilities()
requires_stream = pytest.mark.skipif(
    not STREAM_WIRED,
    reason="P4 streaming CLI not wired yet (S1 probe: capability 'infer-stream' absent)",
)

import hashlib  # noqa: E402

# Small text "weights" with a computed sha256 -- never a real model (INVARIANT-1).
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


def _cli_llama_stub(tmp_path: Path, monkeypatch, behavior: str) -> Path:
    """Install an executable `llama-completion` stub and point the scrubbed child
    PATH at it (PATH == bindir, mirroring the P3 stub tests). The stub answers the
    infer-layer `--version` probe fast (never sleeps) and only then runs `behavior`."""
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "llama-completion"
    body = (
        "#!" + PYBIN + "\n"
        "import sys, time, os\n"
        'if "--version" in sys.argv:\n'
        '    sys.stdout.write("stubllama 0.0-test\\n"); sys.exit(0)\n'
    ) + behavior
    stub.write_text(body, encoding="utf-8")
    _make_executable(stub)
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


def _fake_request(catalog: Path, **over) -> dict:
    req = {
        "schema_version": 1,
        "request_id": "strm-test",
        "profile": "fake-unit",
        "catalog": str(catalog),
        "prompt": PROMPT,
        "params": {"max_tokens": 8},
        "allow_fake": True,
        "policy": dict(MIN_POLICY),
    }
    req.update(over)
    return req


def _llama_request(catalog: Path, *, policy: dict | None = None, **over) -> dict:
    req = {
        "schema_version": 1,
        "request_id": "strm-test",
        "profile": "llama-unit",
        "catalog": str(catalog),
        "prompt": PROMPT,
        "params": {"max_tokens": 8},
        "policy": dict(policy or FULL_POLICY),
    }
    req.update(over)
    return req


def _infer_stream(tmp_path: Path, capsys, request: dict, *extra: str):
    """Drive cli.main([infer,--request,P,--stream, *extra]); parse stdout as JSONL."""
    rp = tmp_path / "request.json"
    rp.write_text(json.dumps(request), encoding="utf-8")
    rc = main(["infer", "--request", str(rp), "--stream", *extra])
    out = capsys.readouterr().out
    objs = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
    return rc, objs


def _final_status(final_event: dict) -> str:
    """Effective status of a `final` wrapper: run-result -> result.status;
    the flat request_error wrapper -> "request_error" (the two shapes S3 defines)."""
    w = final_event["wrapper"]
    if "result" in w:
        return w["result"]["status"]
    return w.get("status")


def _assert_cli_grammar(objs, *, expect_start: bool):
    # S2/S4: gapless seq from 0, single writer, versioned; `final` is the UNIQUE
    # terminal (exactly one, and last); `start` appears iff expected and only at seq 0.
    assert objs, "stream must not be empty"
    assert [o["seq"] for o in objs] == list(range(len(objs)))
    assert {o["stream_version"] for o in objs} == {1}
    finals = [o for o in objs if o["event"] == "final"]
    assert len(finals) == 1 and objs[-1]["event"] == "final"  # unique + terminal
    starts = [o for o in objs if o["event"] == "start"]
    if expect_start:
        assert len(starts) == 1 and objs[0]["event"] == "start" and objs[0]["seq"] == 0
    else:
        assert starts == []


# --- acceptance (12): the S1 capability the whole feature is gated on -----------


@requires_stream
def test_cli_capabilities_advertise_infer_stream():
    # S1 + the ONE permitted P3-regression change: `bls version` capabilities gains
    # "infer-stream" appended to the existing closed list (probe-before-first-call).
    assert _capabilities() == [
        "run", "broker-run", "infer", "models", "preflight", "infer-stream",
    ]


# --- acceptance (1): deterministic success, full framing incl. final ------------


@requires_stream
def test_cli_stream_fake_success_full_framing(tmp_path, capsys, catalog_path):
    # start(seq0)/chunk+/final(last); gapless; exactly one start + one final;
    # concat(chunk.text)==final generation.text (S5); ok final, exit 0 (S8).
    rc, objs = _infer_stream(tmp_path, capsys, _fake_request(catalog_path))
    _assert_cli_grammar(objs, expect_start=True)
    chunks = [o for o in objs if o["event"] == "chunk"]
    final = objs[-1]
    gen_text = final["wrapper"]["result"]["generation"]["text"]
    assert chunks and "".join(c["text"] for c in chunks) == gen_text
    assert _final_status(final) == "ok"
    assert final["wrapper"]["result"]["model"]["is_fake"] is True
    assert objs[0]["request_id"] == "strm-test"
    assert rc == 0


# --- acceptance (2): empty completion (real-runner stub emits nothing) -----------


@requires_stream
@requires_posix
def test_cli_stream_empty_completion(tmp_path, capsys, catalog_path, model_root, monkeypatch):
    _cli_llama_stub(tmp_path, monkeypatch, "pass\n")
    rc, objs = _infer_stream(tmp_path, capsys, _llama_request(catalog_path))
    _assert_cli_grammar(objs, expect_start=True)
    assert [o for o in objs if o["event"] == "chunk"] == []
    assert _final_status(objs[-1]) == "ok"
    assert objs[-1]["wrapper"]["result"]["generation"]["text"] == ""
    assert rc == 0


# --- acceptance (3): nonzero exit -> generation_error, earlier chunks stand ------


@requires_stream
@requires_posix
def test_cli_stream_nonzero_is_generation_error(tmp_path, capsys, catalog_path, model_root, monkeypatch):
    _cli_llama_stub(
        tmp_path, monkeypatch,
        "import sys\nsys.stdout.write('PARTIAL-OUT\\n')\n"
        "sys.stderr.write('diag: load failed\\n'); sys.exit(1)\n",
    )
    rc, objs = _infer_stream(tmp_path, capsys, _llama_request(catalog_path))
    _assert_cli_grammar(objs, expect_start=True)
    chunks = [o for o in objs if o["event"] == "chunk"]
    assert "".join(c["text"] for c in chunks) == "PARTIAL-OUT\n"  # earlier chunk stands
    assert _final_status(objs[-1]) == "generation_error"
    assert objs[-1]["wrapper"]["result"]["generation"] == {}  # non-ok final: {} (S5)
    assert rc == 1


# --- acceptance (4): timeout after partial -> final timeout, bounded return ------


@requires_stream
@requires_posix
def test_cli_stream_timeout_after_partial(tmp_path, capsys, catalog_path, model_root, monkeypatch):
    _cli_llama_stub(
        tmp_path, monkeypatch,
        "import sys, time\nsys.stdout.write('LIVE\\n'); sys.stdout.flush()\n"
        "time.sleep(30)\n",
    )
    req = _llama_request(catalog_path, policy=dict(FULL_POLICY, timeout_seconds=1))
    t0 = time.monotonic()
    rc, objs = _infer_stream(tmp_path, capsys, req)
    dur = time.monotonic() - t0
    _assert_cli_grammar(objs, expect_start=True)
    chunks = [o for o in objs if o["event"] == "chunk"]
    assert "".join(c["text"] for c in chunks) == "LIVE\n"  # partial chunk stands
    assert _final_status(objs[-1]) == "timeout"
    assert rc == 124
    assert dur < 20.0  # watchdog independence: nowhere near the 30s child sleep


# --- acceptance (5): output cap, no kill, one warning, ok final ------------------


@requires_stream
@requires_posix
def test_cli_stream_output_cap_no_kill(tmp_path, capsys, catalog_path, model_root, monkeypatch):
    _cli_llama_stub(tmp_path, monkeypatch, "import sys\nsys.stdout.write('a'*200)\n")
    req = _llama_request(catalog_path, policy=dict(FULL_POLICY, max_output_bytes=50))
    rc, objs = _infer_stream(tmp_path, capsys, req)
    _assert_cli_grammar(objs, expect_start=True)
    warns = [o for o in objs if o["event"] == "warning"]
    assert len(warns) == 1
    chunks = [o for o in objs if o["event"] == "chunk"]
    assert "".join(c["text"] for c in chunks) == "a" * 50  # prefix only
    assert all("[truncated" not in c["text"] for c in chunks)  # marker never in a chunk
    result = objs[-1]["wrapper"]["result"]
    assert _final_status(objs[-1]) == "ok"          # no-kill parity: ok, not error
    assert result["truncated"] is True
    assert result["generation"]["text"] == "a" * 50 + "\n...[truncated 150 chars]"
    assert rc == 0


# --- acceptance (7): stderr never enters the stream; reported in final only ------


@requires_stream
@requires_posix
def test_cli_stream_stderr_separation(tmp_path, capsys, catalog_path, model_root, monkeypatch):
    _cli_llama_stub(
        tmp_path, monkeypatch,
        "import sys\nsys.stderr.write('ERRMARK'+'E'*100000); sys.stderr.flush()\n"
        "sys.stdout.write('CLEAN-OUT\\n')\n",
    )
    rc, objs = _infer_stream(tmp_path, capsys, _llama_request(catalog_path))
    _assert_cli_grammar(objs, expect_start=True)
    chunks = [o for o in objs if o["event"] == "chunk"]
    assert "".join(c["text"] for c in chunks) == "CLEAN-OUT\n"
    assert all("ERRMARK" not in c["text"] for c in chunks)  # stderr never in a chunk
    assert "ERRMARK" in objs[-1]["wrapper"]["result"]["stderr"]  # reported in final
    assert _final_status(objs[-1]) == "ok" and rc == 0


# --- acceptance (9): pre-start failure -> single seq-0 final, NO start -----------


@requires_stream
def test_cli_stream_prestart_model_error_no_start(tmp_path, capsys, catalog_path):
    # Fake profile without allow_fake -> model_error BEFORE any spawn: a single
    # seq-0 final carrying the run-result wrapper (model_error), NO start, exit 2 (S6).
    req = _fake_request(catalog_path, allow_fake=False)
    rc, objs = _infer_stream(tmp_path, capsys, req)
    _assert_cli_grammar(objs, expect_start=False)
    assert len(objs) == 1 and objs[0]["seq"] == 0 and objs[0]["event"] == "final"
    assert _final_status(objs[0]) == "model_error"
    assert objs[0]["wrapper"]["result"]["reason_code"] == "unsupported_runner"
    assert rc == 2


@requires_stream
@requires_posix
def test_cli_stream_prestart_model_missing_no_start(
    tmp_path, capsys, catalog_path, monkeypatch
):
    # llama profile with the cache dir set but the weight absent -> model_error/
    # model_missing pre-spawn: single seq-0 final, NO start (S6).
    empty = tmp_path / "empty-cache"
    empty.mkdir()
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(empty))
    rc, objs = _infer_stream(tmp_path, capsys, _llama_request(catalog_path))
    _assert_cli_grammar(objs, expect_start=False)
    assert len(objs) == 1 and objs[0]["event"] == "final" and objs[0]["seq"] == 0
    assert _final_status(objs[0]) == "model_error"
    assert objs[0]["wrapper"]["result"]["reason_code"] == "model_missing"
    assert rc == 2


# --- acceptance (10): --preflight + --stream are mutually exclusive --------------


@requires_stream
def test_cli_stream_preflight_mutually_exclusive(tmp_path, capsys, catalog_path):
    # S6: the combination emits a SINGLE seq-0 request_error final (flat wrapper),
    # exit 2 -- fail-loud, uniform-JSONL, no start, no chunks.
    rc, objs = _infer_stream(
        tmp_path, capsys, _fake_request(catalog_path), "--preflight"
    )
    _assert_cli_grammar(objs, expect_start=False)
    assert len(objs) == 1 and objs[0]["seq"] == 0 and objs[0]["event"] == "final"
    assert _final_status(objs[0]) == "request_error"
    assert objs[0]["wrapper"]["ok"] is False
    assert rc == 2


# --- acceptance (11): --pretty is IGNORED in stream mode -> compact JSONL --------


@requires_stream
def test_cli_stream_pretty_is_ignored(tmp_path, capsys, catalog_path):
    # S2: in stream mode every event is one compact single-line JSON object even
    # with --pretty. Each raw stdout line must parse standalone, and none may carry
    # the multi-line indentation a pretty dump would. (--pretty is a top-level global
    # flag, so it precedes the subcommand: `bls --pretty infer ... --stream`.)
    rp = tmp_path / "request.json"
    rp.write_text(json.dumps(_fake_request(catalog_path)), encoding="utf-8")
    rc = main(["--pretty", "infer", "--request", str(rp), "--stream"])
    raw = capsys.readouterr().out
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    for ln in lines:
        obj = json.loads(ln)               # each line parses on its own
        assert obj == json.loads(json.dumps(obj))
        assert "\n" not in ln
        assert not ln.startswith(" ")      # no pretty indentation
    # a compact fake stream is start + chunk(s) + final -> at least 3 lines.
    assert len(lines) >= 3
    assert rc == 0


# --- acceptance (13): exit 0 IFF a final with status ok was emitted (S8) ---------


@requires_stream
@requires_posix
@pytest.mark.parametrize("scenario", ["ok", "generation_error", "timeout", "model_error"])
def test_cli_stream_exit_zero_iff_ok_final(
    tmp_path, capsys, catalog_path, monkeypatch, scenario
):
    # S8 sandbox invariant: bls exits 0 <=> a final with status ok was emitted.
    def _populate_cache() -> None:
        cache = tmp_path / "mc" / "example"
        cache.mkdir(parents=True)
        (cache / "unit.gguf").write_bytes(WEIGHTS)
        monkeypatch.setenv("SANDBOX_MODEL_DIR", str(tmp_path / "mc"))

    if scenario == "ok":
        _cli_llama_stub(tmp_path, monkeypatch, "import sys\nsys.stdout.write('done')\n")
        _populate_cache()
        req = _llama_request(catalog_path)
    elif scenario == "generation_error":
        _cli_llama_stub(tmp_path, monkeypatch, "import sys\nsys.exit(2)\n")
        _populate_cache()
        req = _llama_request(catalog_path)
    elif scenario == "timeout":
        _cli_llama_stub(
            tmp_path, monkeypatch,
            "import sys, time\nsys.stdout.write('x'); sys.stdout.flush()\n"
            "time.sleep(30)\n",
        )
        _populate_cache()
        req = _llama_request(catalog_path, policy=dict(FULL_POLICY, timeout_seconds=1))
    else:  # model_error (fake without allow_fake) -- pre-spawn
        req = _fake_request(catalog_path, allow_fake=False)

    rc, objs = _infer_stream(tmp_path, capsys, req)
    final = objs[-1]
    ok_final_emitted = final["event"] == "final" and _final_status(final) == "ok"
    assert (rc == 0) == ok_final_emitted
    assert (scenario == "ok") == ok_final_emitted


# ============================================================================
# Regression: watchdog / deadline boundedness against a group-ESCAPING child
# (found by the pre-commit adversarial review; the blocking-read loop hung
# forever when a setsid descendant held stdout open past the group kill).
# ============================================================================

@requires_posix
def test_pure_escaped_descendant_holding_stdout_cannot_hang(tmp_path, monkeypatch):
    # S7/F2 + P3 bounded-return parity: the direct child forks a NEW-SESSION
    # (setsid) grandchild that inherits stdout and sleeps far past the budget,
    # then the direct child exits. killpg on the child's group does NOT reap the
    # escaped grandchild, so its stdout write-end stays open (no EOF). The
    # deadline-bounded selector loop must still return within
    # timeout + drain grace + slack -- NEVER block on the never-closing pipe.
    import os as _os
    behavior = (
        "import os, sys, time\n"
        "sys.stdout.write('before-fork ')\n"
        "sys.stdout.flush()\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setsid()\n"                      # escape the process group
        "    time.sleep(3600)\n"                 # hold the inherited stdout open
        "    os._exit(0)\n"
        "sys.exit(0)\n"                          # direct child exits immediately
    )
    _events_, res, dur = _run_stream(
        tmp_path, monkeypatch, behavior, timeout_seconds=1.0
    )
    # Bounded: 1s budget + 5s kill grace + generous slack, NOT 3600s.
    assert dur < 20.0, f"run_llama_stream hung ({dur:.1f}s) on an escaped descendant"
    # The status is a legitimate terminal (ok if the direct child's exit was
    # observed before the deadline, else timeout); NEVER a None/garbage exit.
    assert res.status in ("ok", "timeout", "exit_nonzero")
    assert res.exit_code is None or isinstance(res.exit_code, int)


@requires_posix
def test_pure_child_closes_stdout_but_lingers_is_reaped(tmp_path, monkeypatch):
    # MED regression: a child that closes stdout (EOF) early but keeps running
    # must still be reaped and get a deterministic status -- not left as a
    # None-exit_code 'exited with code None' mislabel.
    behavior = (
        "import sys, time, os\n"
        "sys.stdout.write('done')\n"
        "sys.stdout.flush()\n"
        "sys.stdout.close()\n"
        "os.close(1)\n"                          # EOF on stdout now
        "time.sleep(1.5)\n"                      # linger past the read-loop EOF
        "sys.exit(0)\n"
    )
    _events_, res, dur = _run_stream(
        tmp_path, monkeypatch, behavior, timeout_seconds=10.0
    )
    assert dur < 20.0
    assert res.status == "ok" and res.exit_code == 0
    assert "".join(e["text"] for e in _events(_events_, "chunk")) == "done"
