"""SafeExecutor -- the default-deny core that actually runs a command.

Order of gates (all BEFORE any process is spawned):
  1. argv is non-empty                     -> else DENIED
  2. allow_exec must be True               -> else DENIED
  3. argv[0] is a bare command name        -> else DENIED (no path-bypass)
  4. argv[0] is in allowed_commands        -> else DENIED
  5. working_dir (if set) must exist       -> else SPAWN_ERROR

Then the child is launched with a scrubbed env, an isolated session, configured
rlimits, and a wall-clock timeout. On timeout the whole process group is killed.
stdout/stderr are captured and truncated to the policy cap. Everything is returned
as an `ExecResult` (JSON-serializable) -- policy denials are *results*, not crashes.
Genuinely unexpected internal failures are allowed to raise (fail loud).
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .envscrub import build_child_env
from .limits import build_preexec, limits_summary
from .policy import SandboxPolicy, is_bare_command
from .result import ExecResult, Status

_KILL_GRACE = 5.0  # seconds to wait for pipes to close after a timeout-kill


class SafeExecutor:
    def __init__(self, policy: SandboxPolicy):
        self.policy = policy

    def run(self, argv: list[str], *, input_text: str | None = None) -> ExecResult:
        policy = self.policy
        argv = list(argv)

        # --- gate 1+2: default-deny + command allow-list --------------------
        if not argv:
            return ExecResult.denied(argv, "empty argv")
        if not policy.allow_exec:
            return ExecResult.denied(argv, "execution disabled (allow_exec is false)")
        if not is_bare_command(argv[0]):
            # A path-bearing argv[0] would let an allow-listed *basename* run an
            # arbitrary file; only bare names (resolved on PATH) may pass the gate.
            return ExecResult.denied(
                argv,
                f"argv[0] must be a bare command name with no path component: {argv[0]!r}",
            )
        if not policy.is_command_allowed(argv[0]):
            return ExecResult.denied(
                argv, f"command {argv[0]!r} not in allowed_commands",
            )

        # --- gate 3: working dir --------------------------------------------
        cwd = policy.working_dir
        if cwd is not None and not Path(cwd).is_dir():
            return ExecResult.spawn_error(argv, f"working_dir does not exist: {cwd}")

        child_env, dropped_secret = build_child_env(policy)
        preexec = build_preexec(policy)
        limits = limits_summary(policy)
        if dropped_secret:
            limits["dropped_secret_env"] = sorted(dropped_secret)

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=child_env,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",              # deterministic on every platform/locale
                errors="replace",              # non-UTF-8 output must yield a result, not a crash
                preexec_fn=preexec,            # POSIX: setsid + rlimits; None elsewhere
                close_fds=True,
            )
        except (FileNotFoundError, PermissionError, OSError, subprocess.SubprocessError) as exc:
            # SubprocessError covers a preexec/rlimit-setup failure (e.g. a requested
            # rlimit above the host's inherited hard ceiling); it is NOT an OSError.
            # A pre-spawn setup failure is a recoverable result, not a crash.
            return ExecResult.spawn_error(argv, f"could not start process: {exc}")

        timed_out = False
        try:
            stdout, stderr = proc.communicate(input=input_text, timeout=policy.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            stdout, stderr = _drain_after_kill(proc)

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout, t1 = _truncate(stdout, policy.max_output_bytes)
        stderr, t2 = _truncate(stderr, policy.max_output_bytes)

        if timed_out:
            status = Status.TIMEOUT
            reason = f"killed after {policy.timeout_seconds}s timeout"
        elif proc.returncode == 0:
            status = Status.OK
            reason = "completed"
        else:
            status = Status.EXIT_NONZERO
            reason = f"exited with code {proc.returncode}"

        return ExecResult(
            status=status,
            argv=argv,
            reason=reason,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=t1 or t2,
            network=policy.network,
            env_keys=sorted(k for k in child_env),
            limits=limits,
        )


def _drain_after_kill(proc: subprocess.Popen) -> tuple[str, str]:
    """Bounded read after a timeout-kill.

    A descendant that escaped the process group (own setsid / double-fork) and
    inherited the stdout/stderr pipe can hold it open indefinitely. communicate()
    with no timeout would block on that pipe forever, defeating the wall-clock
    budget -- so the recovery read is time-boxed. If it still can't drain, we
    abandon the output (the direct child is already SIGKILL'd) and reap it so run()
    always returns within a bounded time.
    """
    try:
        return proc.communicate(timeout=_KILL_GRACE)
    except subprocess.TimeoutExpired:
        try:
            proc.wait(timeout=_KILL_GRACE)
        except subprocess.TimeoutExpired:
            pass
        return "", "[output abandoned: a descendant leaked past the process-group kill]"


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    if text is None:
        return "", False
    if len(text) <= cap:
        return text, False
    return text[:cap] + f"\n...[truncated {len(text) - cap} chars]", True


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child's whole process group when possible; fall back to the child."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - Windows
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
