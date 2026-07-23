"""`bls infer --stream` -- the additive JSONL streaming transport (P4 contract).

One JSON object per line on stdout (UTF-8, "\\n"-terminated). Event envelope on
every line: ``{stream_version, event, seq, ...}``. Closed event set (S3):

    start   (seq 0, once)  {..., request_id, model}
    chunk   {..., text}                 -- incremental generation deltas
    warning {..., message}              -- <= MAX_WARNINGS, bounded message
    final   (once, last)   {..., wrapper}  -- wrapper = the exact non-streaming
                                             response body (run-result OR the
                                             flat request_error), built by the
                                             caller (infer.py) as the single
                                             source of truth for the result shape.

Correctness mechanisms (P4 S7/S10):
  * Deadline enforcement lives on an INDEPENDENT WATCHDOG (a threading.Timer),
    not on relay read/write progress: the child is group-killed at the budget
    even if the relay is blocked writing to a stalled consumer. Only the relay
    itself may then remain blocked in its final write (same exposure as P3's
    buffered _emit).
  * A dedicated STDERR-DRAIN THREAD reads child stderr continuously so a full
    64 KiB stderr pipe can never deadlock the stdout relay (R3).
  * ONE incremental UTF-8 decoder (errors="replace") feeds both chunk emission
    and the final accumulation, so an invalid byte is replaced where observed
    and a split multibyte sequence is buffered across reads (R6). The final
    generation text is exactly the concatenation of the emitted chunk text
    (plus, on truncation, the P3 truncation marker -- S5).
  * NO kill at the output cap (S3/F3): on reaching max_output_bytes the relay
    stops emitting/accumulating and emits ONE warning, but keeps DRAINING child
    stdout so the child exits on its own -- preserving P3's status parity (a
    chatty child that exits 0 is ``ok`` + ``truncated: true``, never
    generation_error). The final stdout is ``prefix + P3 marker``.

The gate chain is NOT forked: this module spawns through the SAME extracted
helpers as SafeExecutor.run (check_gates / prepare_child / spawn_child /
assemble_exec_result / _kill_tree / _truncate), differing only in pipe mode
(binary, unbuffered) and the incremental relay in place of buffered communicate.
"""
from __future__ import annotations

import codecs
import json
import os
import selectors
import subprocess
import threading
import time
from typing import Callable, Optional

from . import SCHEMA_VERSION
from .executor import (
    _KILL_GRACE,
    _kill_tree,
    _truncate,
    assemble_exec_result,
    check_gates,
    prepare_child,
    spawn_child,
)
from .policy import SandboxPolicy
from .result import ExecResult, Status

# The stream-envelope version (P4 S12): sandbox-owned, in THIS module, distinct
# from the wire SCHEMA_VERSION and the CATALOG_SCHEMA_VERSION. New event TYPES
# bump this (consumers reject unknown types); new FIELDS may be added only when
# semantically inert.
STREAM_VERSION = 1

MAX_EVENT_TEXT_CHARS = 8192   # per-chunk framing bound (decoded chars, S3)
MAX_WARNINGS = 8             # bilateral bound (S3/S4)
_READ_CHUNK = 65536          # 64 KiB binary reads (S7)


class StreamProtocolError(RuntimeError):
    """A producer-side violation of the event grammar (should never happen)."""


class StreamEmitter:
    """Producer-side enforcement of the S2/S4 event grammar.

    Assigns a gapless seq starting at 0; ``start`` is legal only as seq 0 and
    once; ``final`` is unique and terminal (nothing may follow); warnings are
    bounded. Every event is written as ONE compact JSON line (``--pretty`` is
    ignored in stream mode, S2).
    """

    def __init__(
        self,
        write: Callable[[str], None],
        flush: Optional[Callable[[], None]] = None,
    ) -> None:
        self._write = write
        # Producer-boundary flush (P4 S7 liveness): on a pipe, sys.stdout is
        # block-buffered, so without a per-event flush every event would sit in
        # the sandbox's own buffer until process exit -- collapsing streaming
        # to a single terminal burst and defeating the start-before-final
        # liveness the transport exists to provide. The producer OWNS this
        # flush; a consumer must NOT have to unbuffer the sandbox to see events
        # arrive incrementally. Defaults to a no-op so in-process callers that
        # capture into a list (tests) need not supply one.
        self._flush = flush if flush is not None else (lambda: None)
        self._seq = 0
        self._started = False
        self._final = False
        self._warnings = 0

    def _line(self, payload: dict) -> None:
        if self._final:
            raise StreamProtocolError("no event may follow 'final'")
        obj = {"stream_version": STREAM_VERSION, "seq": self._seq, **payload}
        # ensure_ascii keeps the line 7-bit and newline-free regardless of
        # chunk content; one object per line, "\n"-terminated (S2).
        self._write(json.dumps(obj, ensure_ascii=True) + "\n")
        # Flush at the producer boundary so EVERY event (start included)
        # reaches the pipe the instant it is emitted (S7 liveness).
        self._flush()
        self._seq += 1

    def start(self, request_id: Optional[str], model: dict) -> None:
        if self._started:
            raise StreamProtocolError("'start' already emitted")
        if self._seq != 0:
            raise StreamProtocolError("'start' must be seq 0")
        self._started = True
        self._line({"event": "start", "request_id": request_id, "model": model})

    def chunk(self, text: str) -> None:
        self._line({"event": "chunk", "text": text})

    def warning(self, message: str) -> bool:
        """Emit a warning if under the bound. Returns False once the bound is
        reached (the caller stops emitting further warnings)."""
        if self._warnings >= MAX_WARNINGS:
            return False
        self._warnings += 1
        self._line({"event": "warning", "message": message[:MAX_EVENT_TEXT_CHARS]})
        return True

    def final(self, wrapper: dict) -> None:
        if self._final:
            raise StreamProtocolError("'final' already emitted")
        self._line({"event": "final", "wrapper": wrapper})
        self._final = True


class _StdinWriter(threading.Thread):
    """Feed the prompt to the child's stdin on a dedicated thread, then close it.

    The main thread reads stdout; a bounded child (llama.cpp reading
    ``-f /dev/stdin``) may not drain stdin until it starts producing output, so
    writing stdin inline could deadlock against a child blocked writing stdout.
    A separate writer thread (the same shape ``communicate`` uses internally)
    avoids that: it writes the prompt bytes and closes stdin so the child sees
    EOF. A BrokenPipe (child exited before reading the whole prompt) is benign.
    """

    def __init__(self, stream, data: bytes) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._data = data

    def run(self) -> None:
        try:
            if self._data:
                self._stream.write(self._data)
                self._stream.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass
        finally:
            try:
                self._stream.close()
            except (BrokenPipeError, ValueError, OSError):
                pass


def run_llama_stream(
    policy: SandboxPolicy,
    argv: list[str],
    prompt: str,
    *,
    emitter: StreamEmitter,
    request_id: Optional[str],
    model_block: dict,
) -> ExecResult:
    """Execute one generation with incremental streaming; return an ExecResult.

    Emits ``start`` (only after a successful spawn), then ``chunk``/``warning``
    during execution. Does NOT emit ``final`` -- the caller assembles the
    InferResult (the single source of truth, incl. the A4 path scrub) and emits
    the final event. A pre-spawn denial or spawn error returns the corresponding
    ExecResult WITHOUT emitting ``start`` (S3/S6: the caller then emits a single
    seq-0 ``final``). The returned ExecResult's stdout is the raw (unscrubbed)
    generation with, on truncation, the P3 marker appended -- byte-parity with
    ``SafeExecutor.run`` so the caller's non-stream result assembly is reused
    verbatim (S5/S13).
    """
    argv = list(argv)

    denial = check_gates(policy, argv)
    if denial is not None:
        return denial

    prepared = prepare_child(policy)
    start = time.monotonic()
    try:
        proc = spawn_child(argv, prepared, input_text=prompt, text_mode=False)
    except (FileNotFoundError, PermissionError, OSError, subprocess.SubprocessError) as exc:
        return ExecResult.spawn_error(argv, f"could not start process: {exc}")

    # The child is alive: emit start (S3) with the model block.
    emitter.start(request_id, model_block)

    # Deliver the prompt to child stdin on a dedicated thread, then close it
    # (llama.cpp reads it via -f /dev/stdin). Doing this inline could deadlock
    # against a child blocked writing stdout before it drains stdin.
    stdin_writer = None
    if proc.stdin is not None:
        stdin_writer = _StdinWriter(proc.stdin, (prompt or "").encode("utf-8"))
        stdin_writer.start()

    cap = policy.max_output_bytes  # enforced in decoded CHARS (P3 parity, F4)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    kept = []            # emitted/retained prefix chars (concat == chunk stream)
    kept_len = 0
    overage = 0          # chars past the cap (discarded; sizes the P3 marker)
    pending = ""         # buffer to split chunks at MAX_EVENT_TEXT_CHARS
    cap_warned = False

    stderr_buf = bytearray()          # bounded stderr retention (drained in-loop)
    stderr_cap = cap * 4 + _READ_CHUNK
    stderr_total = 0                  # total stderr bytes observed (for the marker)

    # Deadline = spawn time + the child budget. Enforced TWO ways so a stalled
    # consumer OR a pipe-holding escaped descendant can never hang the call:
    #  (a) an INDEPENDENT watchdog Timer group-kills the child at the deadline
    #      even while the relay is blocked writing a chunk (S7/F2); and
    #  (b) every selector wait is bounded by the remaining budget, so a
    #      descendant that escaped the process-group kill and holds a pipe open
    #      can never block a read past the deadline + a fixed drain grace
    #      (parity with P3 run()'s time-boxed _drain_after_kill).
    deadline = start + max(0.0, policy.timeout_seconds)
    deadline_hit = threading.Event()

    def _on_deadline() -> None:
        _kill_tree(proc)
        deadline_hit.set()

    watchdog = threading.Timer(max(0.0, policy.timeout_seconds), _on_deadline)
    watchdog.daemon = True
    watchdog.start()

    def _flush_pending(force: bool) -> None:
        # Emit whole MAX_EVENT_TEXT_CHARS chunks from `pending`; if force, emit
        # the remainder too. Only runs while under the cap.
        nonlocal pending
        while len(pending) >= MAX_EVENT_TEXT_CHARS:
            emitter.chunk(pending[:MAX_EVENT_TEXT_CHARS])
            pending = pending[MAX_EVENT_TEXT_CHARS:]
        if force and pending:
            emitter.chunk(pending)
            pending = ""

    def _consume_stdout(b: bytes) -> None:
        nonlocal kept_len, overage, cap_warned
        text = decoder.decode(b)
        if not text:
            return
        if kept_len < cap:
            room = cap - kept_len
            take = text[:room]
            kept.append(take)
            kept_len += len(take)
            pending_append(take)
            _flush_pending(force=False)
            rest = text[room:]
            if rest:
                overage += len(rest)
                if not cap_warned:
                    _flush_pending(force=True)   # flush the exact prefix
                    emitter.warning(
                        f"output cap of {cap} chars reached; further generation "
                        "is discarded and the child is drained to its natural "
                        "exit (not killed)"
                    )
                    cap_warned = True
        else:
            overage += len(text)

    def pending_append(s: str) -> None:
        nonlocal pending
        pending += s

    def _consume_stderr(b: bytes) -> None:
        nonlocal stderr_total
        stderr_total += len(b)
        if len(stderr_buf) < stderr_cap:
            stderr_buf.extend(b[: stderr_cap - len(stderr_buf)])
        # bytes past the retention cap are read and discarded (pipe stays drained)

    # Non-blocking selector over BOTH pipes: draining stderr in the same loop
    # removes the 64 KiB stderr-pipe deadlock (R3) WITHOUT a second thread (so
    # there is no _buf race), and every wait is deadline-bounded.
    os.set_blocking(proc.stdout.fileno(), False)
    os.set_blocking(proc.stderr.fileno(), False)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, "out")
    sel.register(proc.stderr, selectors.EVENT_READ, "err")
    open_streams = 2

    def _pump(until: float) -> None:
        # Read whatever is ready on both pipes until they EOF or `until` passes.
        nonlocal open_streams
        while open_streams > 0:
            remaining = until - time.monotonic()
            if remaining <= 0:
                return
            for key, _mask in sel.select(timeout=min(remaining, 0.5)):
                try:
                    b = key.fileobj.read(_READ_CHUNK)
                except (BlockingIOError, InterruptedError):
                    continue
                except (ValueError, OSError):
                    b = b""   # pipe closed under us (post-kill) -> treat as EOF
                if b:
                    if key.data == "out":
                        _consume_stdout(b)   # emit may block; watchdog still kills the child
                    else:
                        _consume_stderr(b)
                else:
                    sel.unregister(key.fileobj)
                    open_streams -= 1

    try:
        _pump(until=deadline)
        if open_streams == 0:
            # Both pipes closed before the deadline: wait for the REAL exit
            # (parity with P3 communicate) within the remaining budget.
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                _on_deadline()
        else:
            # Deadline reached with a pipe still open: kill, then a bounded
            # post-kill drain to collect any last buffered output (never hangs
            # -- a leaked descendant is abandoned after the grace).
            _on_deadline()
            _pump(until=time.monotonic() + _KILL_GRACE)
        # flush any trailing incomplete multibyte sequence (R6) + tail chunk
        tail = decoder.decode(b"", final=True)
        if tail and kept_len < cap:
            room = cap - kept_len
            take = tail[:room]
            kept.append(take)
            kept_len += len(take)
            pending_append(take)
            overage += len(tail) - len(take)
        elif tail:
            overage += len(tail)
        _flush_pending(force=True)
    finally:
        watchdog.cancel()
        try:
            sel.close()
        except OSError:
            pass
        if stdin_writer is not None:
            stdin_writer.join(timeout=_KILL_GRACE)

    # Reap the child so it is never left a zombie and returncode is populated.
    try:
        proc.wait(timeout=_KILL_GRACE)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.wait(timeout=_KILL_GRACE)
        except subprocess.TimeoutExpired:
            pass   # a leaked descendant kept a handle; the direct child is dead

    duration_ms = int((time.monotonic() - start) * 1000)
    stderr_text = stderr_buf.decode("utf-8", errors="replace")
    if stderr_total > len(stderr_buf):
        # stderr exceeded the in-loop retention cap: some bytes were drained and
        # discarded (the pipe was kept clear). Mark truncated honestly -- streaming
        # stderr is bounded, not byte-identical to P3 (S8).
        stderr_text = stderr_text[:cap] + f"\n...[truncated: stderr exceeded {cap} chars]"
        se_trunc = True
    else:
        stderr_text, se_trunc = _truncate(stderr_text, cap)

    prefix = "".join(kept)
    if overage > 0:
        stdout_text = prefix + f"\n...[truncated {overage} chars]"
        so_trunc = True
    else:
        stdout_text = prefix
        so_trunc = False

    timed_out = deadline_hit.is_set()
    if timed_out:
        status = Status.TIMEOUT
        reason = f"killed after {policy.timeout_seconds}s timeout"
        exit_code = proc.returncode
    elif proc.returncode == 0:
        status = Status.OK
        reason = "completed"
        exit_code = 0
    else:
        status = Status.EXIT_NONZERO
        reason = f"exited with code {proc.returncode}"
        exit_code = proc.returncode

    return assemble_exec_result(
        status=status,
        argv=argv,
        reason=reason,
        exit_code=exit_code,
        stdout=stdout_text,
        stderr=stderr_text,
        duration_ms=duration_ms,
        truncated=so_trunc or se_trunc,
        policy=policy,
        child_env=prepared.child_env,
        limits=prepared.limits,
    )
