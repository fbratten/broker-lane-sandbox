"""Producer-boundary flush / liveness tests for `bls infer --stream` (P4 S7).

The unit-level flush wiring lives in test_streaming.py
(test_emitter_flushes_at_the_producer_boundary_after_every_event). THIS module
proves the END-TO-END liveness property over a REAL pipe: `start` becomes
observable before `final` while the child is still running, WITHOUT any
consumer-side unbuffer override.

Why a subprocess (not capsys): in-process capture cannot exhibit stdout pipe
buffering. We spawn the real CLI (`sys.executable -c ...main`) with stdout as a
pipe and NO PYTHONUNBUFFERED in its env, drive a print-then-sleep stub runner,
and time when each event arrives. Without the per-event flush, sys.stdout is
block-buffered on a pipe and the whole stream arrives as one burst only at
process exit -- this test would then see `start` arrive no earlier than `final`.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Fixture "weights": tiny text bytes, never a real model (INVARIANT-1).
WEIGHTS = b"tiny fixture weights for the flush liveness test\n"
WEIGHTS_SHA = hashlib.sha256(WEIGHTS).hexdigest()

_LAUNCH = (
    "import sys; from broker_lane_sandbox.cli import main; "
    "sys.exit(main(sys.argv[1:]))"
)


def _catalog(tmp_path: Path) -> Path:
    cat = {
        "schema_version": 1,
        "cache_dir_env": "SANDBOX_MODEL_DIR",
        "profiles": {
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


def _model_root(tmp_path: Path) -> Path:
    root = tmp_path / "model-cache"
    (root / "example").mkdir(parents=True)
    (root / "example" / "unit.gguf").write_bytes(WEIGHTS)
    return root


def _write_stub(tmp_path: Path, body: str) -> Path:
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "llama-completion"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _request(tmp_path: Path, catalog: Path) -> Path:
    req = {
        "schema_version": 1,
        "request_id": "flush-liveness",
        "profile": "llama-unit",
        "catalog": str(catalog),
        "prompt": "liveness prompt",
        "params": {"max_tokens": 8},
        "policy": {
            "schema_version": 1,
            "allow_exec": True,
            "allowed_commands": ["llama-completion", "llama-cli"],
            "network": "offline",
            "timeout_seconds": 20,
        },
    }
    rp = tmp_path / "request.json"
    rp.write_text(json.dumps(req), encoding="utf-8")
    return rp


def _child_path(bindir: Path) -> str:
    # The stub bindir FIRST (so the sandbox resolves llama-completion to the
    # stub), then the system bins (so the stub shell can run `sleep`, an
    # external command). The sandbox exposes this PATH to its child via the
    # env allowlist.
    system = os.environ.get("PATH", "/usr/bin:/bin")
    return str(bindir) + os.pathsep + system


def _child_env(bindir: Path, model_root: Path) -> dict:
    # INHERIT the parent environment (so the spawned interpreter locates the
    # installed package exactly as the test-running interpreter does -- a
    # minimal replacement env drops what CI needs and yields ModuleNotFound),
    # then override PATH (stub first) + the model cache. Crucially STRIP
    # PYTHONUNBUFFERED so correctness must come from the sandbox flush at the
    # producer boundary, never an inherited consumer-side override.
    env = dict(os.environ)
    env["PATH"] = _child_path(bindir)
    env["SANDBOX_MODEL_DIR"] = str(model_root)
    env.pop("PYTHONUNBUFFERED", None)
    return env


def _spawn_stream(rp: Path, bindir: Path, model_root: Path) -> subprocess.Popen:
    env = _child_env(bindir, model_root)
    return subprocess.Popen(
        [sys.executable, "-c", _LAUNCH, "infer", "--request", str(rp), "--stream"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )


def _read_timed(proc: subprocess.Popen, overall_deadline_s: float):
    """readline() (not `for line in ...`, which read-ahead-buffers) until final
    or EOF or the overall deadline; return (events, arrival_times_by_index)."""
    events: list[dict] = []
    arrivals: list[float] = []
    t0 = time.monotonic()
    while True:
        if time.monotonic() - t0 > overall_deadline_s:
            proc.kill()
            raise AssertionError("stream did not complete within the deadline")
        line = proc.stdout.readline()
        if line == "":  # EOF
            break
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
        arrivals.append(time.monotonic() - t0)
        if events[-1]["event"] == "final":
            break
    return events, arrivals


# Answers the runner's `--version` probe INSTANTLY (as a real llama-completion
# does), then on the real generation run prints one line to stdout IMMEDIATELY
# and holds the process open by sleeping -- so `start` (emitted right after the
# real spawn) must reach the pipe DURING the sleep if (and only if) the sandbox
# flushed it at the producer boundary. (Chunk delivery of the small output is
# separately batched to EOF -- the documented runner-dependent granularity --
# which is why the liveness property under test is start-before-final, not
# chunk-by-chunk.) Integer sleep for portability across sh implementations.
_STUB_PRINT_THEN_SLEEP = """#!/bin/sh
case "$*" in
  *--version*) echo 'llama-completion version flush-liveness-stub'; exit 0 ;;
esac
printf 'LIVE-OUTPUT\\n'
sleep {sleep}
"""


def test_start_is_observable_over_a_pipe_before_final_no_consumer_unbuffer(tmp_path):
    # 3s child sleep leaves generous headroom over the fixed cost (subprocess
    # spawn + import + full sha256 of the tiny fixture) so a loaded CI runner
    # still delivers `start` inside half the sleep window.
    sleep_s = 3
    catalog = _catalog(tmp_path)
    model_root = _model_root(tmp_path)
    bindir = _write_stub(tmp_path, _STUB_PRINT_THEN_SLEEP.format(sleep=sleep_s))
    rp = _request(tmp_path, catalog)

    proc = _spawn_stream(rp, bindir, model_root)
    try:
        events, arrivals = _read_timed(proc, overall_deadline_s=sleep_s + 15.0)
        rc = proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()

    # Well-formed ok stream: start(seq 0) + chunk(s) + one terminal final.
    assert rc == 0, proc.stderr.read()
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert events[0]["event"] == "start"
    assert events[-1]["event"] == "final"
    assert events[-1]["wrapper"]["result"]["status"] == "ok"

    idx_start = next(i for i, e in enumerate(events) if e["event"] == "start")
    idx_final = len(events) - 1
    t_start = arrivals[idx_start]
    t_final = arrivals[idx_final]

    # THE liveness property: start arrives WHILE the child is still sleeping --
    # far earlier than the child's exit -- and final only after the sleep. A
    # block-buffered producer would deliver both together at exit (t_start ~=
    # t_final ~>= sleep). Generous margins absorb spawn + sha256(tiny) overhead.
    assert t_start < sleep_s * 0.5, (
        f"start arrived at {t_start:.2f}s (>= half the {sleep_s}s child sleep) -- "
        "events are being buffered, not flushed at the producer boundary"
    )
    assert t_final >= sleep_s * 0.9, (
        f"final arrived at {t_final:.2f}s -- expected only after the child's "
        f"{sleep_s}s sleep"
    )
    assert (t_final - t_start) >= sleep_s * 0.5, (
        f"start->final gap {t_final - t_start:.2f}s too small: start was not "
        "delivered early"
    )


def test_pre_start_failure_final_is_flushed_and_immediate(tmp_path):
    # A pre-emission failure (mutually-exclusive flags) is a single seq-0 final;
    # it too must be flushed (exit before any read-ahead) and arrive promptly.
    catalog = _catalog(tmp_path)
    model_root = _model_root(tmp_path)
    bindir = _write_stub(tmp_path, "#!/bin/sh\nprintf 'x\\n'\n")
    rp = _request(tmp_path, catalog)
    env = _child_env(bindir, model_root)
    proc = subprocess.Popen(
        [sys.executable, "-c", _LAUNCH,
         "infer", "--request", str(rp), "--stream", "--preflight"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True,
    )
    out, _err = proc.communicate(timeout=15)
    rc = proc.returncode
    events = [json.loads(x) for x in out.splitlines() if x.strip()]
    assert rc == 2
    assert len(events) == 1
    assert events[0]["seq"] == 0
    assert events[0]["event"] == "final"
    assert events[0]["wrapper"]["status"] == "request_error"
