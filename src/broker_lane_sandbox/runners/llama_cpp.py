"""llama.cpp runner family -- subprocess under SafeExecutor (P3 D2/D3/D9).

Trust model:
  * The prompt is DATA. It travels exclusively through SafeExecutor's
    `input_text` (child stdin), read by the child via `-f /dev/stdin`
    (contract D3/F2/L4-F2). The prompt is NEVER placed in argv (`-p`) or in
    the child environment under any code path -- a failed delivery fails
    loud, it never degrades.
  * argv is CODE-OWNED and canonical (contract D3): `-no-cnv` is mandatory
    (conversation mode auto-enables when the GGUF carries a chat template),
    `--log-disable` keeps the load banner (and the absolute model path it
    would print) out of stderr, `--no-display-prompt` prevents prompt echo
    into stdout, and `-n` is always emitted (llama's default -1 = infinite
    is never allowed, contract D7).
  * Binary identity (contract D9/F11/L4-F1): the family maps to the ordered
    code-owned candidate set CANDIDATE_BINARIES, resolved by BARE NAME with
    `shutil.which` against the SCRUBBED child env's PATH -- the exact PATH
    the spawned child will search -- so preflight and spawn can never
    disagree. No realpath/hash pinning (threat-model consistent).
  * Recorded argv self-labels the model-path slot as
    `${<model_dir_env>}/<relative_path>` (contract D5/F9) so no absolute
    local path leaves the sandbox in argv or the model block. The child of
    course receives the real absolute path.
  * All sandbox gates are inherited unchanged: execution goes through
    `SafeExecutor.run()` (contract D3) -- allow_exec, allowed_commands,
    bare-name gate, env scrub, rlimits, wall-clock timeout, group kill.

Platform: this real-runner path is POSIX-only (contract D10); `/dev/stdin`
and the process-group semantics are not offered on Windows.
"""
from __future__ import annotations

import dataclasses
import shutil
from typing import TYPE_CHECKING

from ..envscrub import build_child_env
from ..executor import SafeExecutor
from .base import RunnerError

if TYPE_CHECKING:  # modelcache.py lands in the same P3 change-set (parallel lane)
    from ..modelcache import ResolvedModel
    from ..policy import SandboxPolicy
    from ..result import ExecResult

# Ordered candidate set (contract L4-F1): upstream moved completion mode to
# `llama-completion` (discussion #17618); the rewritten server-based llama-cli
# is NOT a supported flag surface, but older completion-mode llama-cli builds
# are -- hence the fallback, in this exact order.
CANDIDATE_BINARIES = ("llama-completion", "llama-cli")


def resolve_binary(policy: SandboxPolicy) -> tuple[str, str]:
    """Resolve the llama.cpp binary on the SCRUBBED child env's PATH.

    Returns (bare_name, absolute_path_found). The bare name is what goes into
    argv[0] (the SafeExecutor bare-command gate requires it); the absolute
    path is informational (preflight reporting). Resolution uses
    `shutil.which(name, path=child_env PATH)` over CANDIDATE_BINARIES in
    order (contract D9/F11): if PATH is not allow-listed into the child env,
    resolution fails closed -- exactly as the spawn would.
    """
    child_env, _dropped = build_child_env(policy)
    child_path = child_env.get("PATH", "")
    for name in CANDIDATE_BINARIES:
        found = shutil.which(name, path=child_path) if child_path else None
        if found:
            return name, found
    raise RunnerError(
        "no llama.cpp binary found on the sandboxed PATH; place "
        "`llama-completion` (or a completion-mode `llama-cli`) on PATH -- "
        "e.g. a symlink from llama.cpp's build/bin -- and keep PATH in the "
        "policy env_allowlist",
        reason_code="runner_missing",
    )


def probe_version(policy: SandboxPolicy, binary: str) -> str | None:
    """Best-effort `--version` probe (contract D3/A2): recorded when it succeeds,
    else None. Runs under the same gate chain with a short budget (<=10s) and
    NEVER fails or delays the inference call materially. The probe spawns the
    BINARY only -- no model is loaded -- and is skipped entirely on preflight
    (preflight promises that nothing executes).
    """
    try:
        probe_policy = dataclasses.replace(
            policy, timeout_seconds=min(10.0, policy.timeout_seconds)
        )
        res = SafeExecutor(probe_policy).run([binary, "--version"])
        if res.status != "ok":
            return None
        combined = (res.stdout + "\n" + res.stderr).splitlines()
        line = next((ln.strip() for ln in combined if ln.strip()), None)
        return line[:200] if line else None
    except Exception:
        return None


def build_argv(
    binary: str,
    model_abs_path: str,
    *,
    max_tokens: int,
    temperature: float | None = None,
    seed: int | None = None,
) -> list[str]:
    """The canonical code-owned argv (contract D3). The prompt is NOT here."""
    argv = [
        binary,
        "-m", model_abs_path,
        "-f", "/dev/stdin",
        "-no-cnv",
        "--no-display-prompt",
        "--simple-io",
        "--log-disable",
        "-n", str(max_tokens),
    ]
    if temperature is not None:
        argv += ["--temp", str(temperature)]
    if seed is not None:
        argv += ["--seed", str(seed)]
    return argv


def recorded_argv(
    argv: list[str],
    *,
    cache_dir_env: str,
    relative_path: str,
    model_abs_path: str,
) -> list[str]:
    """Self-labeling redaction of the model-path slot (contract D5/F9).

    Returns a COPY of argv with every element equal to `model_abs_path`
    replaced by the literal `${<cache_dir_env>}/<relative_path>` form. The
    result is what gets recorded/reported; the child received the real path.
    """
    label = "${" + cache_dir_env + "}/" + relative_path
    return [label if a == model_abs_path else a for a in argv]


def run_llama(
    policy: SandboxPolicy,
    resolved: ResolvedModel,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float | None = None,
    seed: int | None = None,
    cache_dir_env: str | None = None,
) -> tuple[ExecResult, list[str], str]:
    """Execute one llama.cpp generation under the full sandbox gate chain.

    Returns (exec_result, recorded_argv, binary_name). The PROMPT GOES ONLY
    TO input_text -- never argv, never env (contract D3/F2). The recorded
    argv self-labels the model slot as `${<env>}/<relative_path>` (contract
    D5/A3): the env name is `cache_dir_env` when given (infer passes the
    catalog's cache_dir_env so preflight and execution labels always agree),
    else `policy.model_dir_env`. Status mapping to the InferResult vocabulary
    (exit_nonzero -> generation_error) happens at ONE documented point in
    infer.py, not here.
    """
    binary_name, _binary_abs = resolve_binary(policy)
    argv = build_argv(
        binary_name,
        resolved.abs_path,
        max_tokens=max_tokens,
        temperature=temperature,
        seed=seed,
    )
    recorded = recorded_argv(
        argv,
        cache_dir_env=cache_dir_env or policy.model_dir_env,
        relative_path=resolved.relative_path,
        model_abs_path=resolved.abs_path,
    )
    exec_result = SafeExecutor(policy).run(argv, input_text=prompt)
    return exec_result, recorded, binary_name
