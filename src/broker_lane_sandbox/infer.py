"""`bls infer` -- the local-model inference request seam (P3 contract D4/D5/D6/D7).

Trust model
-----------
One JSON request in, one JSON wrapper out -- the same seam doctrine as
``broker-run``: the policy is INLINE (the sandbox never resolves broker-local
policy paths across the trust boundary), while the model catalog is accepted BY
PATH as an explicit, documented seam-doctrine extension (contract F7): the
catalog is operator-tracked sha256-pinning DATA, not caller-supplied policy.

The prompt is DATA ONLY (D3/F2): it reaches a real runner exclusively through
SafeExecutor ``input_text`` (child stdin, read by ``-f /dev/stdin``) and is never
placed in argv or the environment. The recorded argv in every result replaces
the absolute model path with the self-labeling ``${<cache_dir_env>}/<relative_path>``
form (F9), so no absolute local path appears in argv or the model block (D5,
scoped claim -- captured child output is not further scrubbed).

The fake path (``allow_fake``, D1/F4) bypasses SafeExecutor entirely: it
exercises the seam, never the sandbox gates -- documented honestly, and every
fake result's model block carries ``"is_fake": true``.

Failure vocabulary is a NEW closed status enum owned by this layer (D5).
Sandbox ``ExecResult``/``Status`` stay untouched; SafeExecutor output is mapped
at exactly one documented point (``exit_nonzero`` -> ``generation_error``).
Request-shape problems raise :class:`InferRequestError`, which the CLI maps to
the SAME ``request_error`` wrapper broker-run uses (exit 2). ``ModelCacheError``
and ``RunnerError`` never escape :func:`run_infer_request` -- they become
``model_error`` results carrying a ``reason_code``.
"""
from __future__ import annotations

import os
import time
from typing import Any

from . import SCHEMA_VERSION
from .policy import SandboxPolicy


class InferRequestError(ValueError):
    """Raised when an infer request is malformed or out of contract (D4)."""


# Contract D5 exit-code table: 0 ok / 1 generation_error / 2 denied, spawn_error,
# model_error (request_error is also 2, mapped at the CLI) / 124 timeout.
INFER_STATUS_EXIT = {
    "ok": 0,
    "generation_error": 1,
    "denied": 2,
    "spawn_error": 2,
    "model_error": 2,
    "timeout": 124,
}

# The ONE documented SafeExecutor -> infer status mapping point (contract D5).
# A status outside this closed set is contract drift and must fail loud (KeyError).
_EXEC_STATUS_MAP = {
    "ok": "ok",
    "exit_nonzero": "generation_error",
    "denied": "denied",
    "spawn_error": "spawn_error",
    "timeout": "timeout",
}

# Closed reason_code vocabulary for model_error results (contract D5).
_MODEL_ERROR_REASON_CODES = frozenset(
    {
        "catalog_invalid",
        "unsupported_runner",
        "model_dir_unset",
        "model_missing",
        "size_mismatch",
        "checksum_mismatch",
        "runner_missing",
    }
)

_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "profile",
        "catalog",
        "prompt",
        "params",
        "allow_fake",
        "policy",
        "timeout_seconds",
        "working_dir",
    }
)

# Contract D7: exactly these params at MVP; unknown keys fail loud.
_PARAM_KEYS = frozenset({"max_tokens", "temperature", "seed"})


def _require_int(name: str, val: Any) -> int:
    """Strict int: bool is rejected everywhere ints are expected (contract D4/D7)."""
    if isinstance(val, bool):
        raise InferRequestError(f"{name} must be an integer, got boolean {val!r}")
    if not isinstance(val, int):
        raise InferRequestError(f"{name} must be an integer, got {val!r}")
    return val


def _wrap(request_id: str | None, result: dict) -> dict:
    """Wrapper shape (D5): {schema_version: 1, request_id, result}."""
    return {"schema_version": SCHEMA_VERSION, "request_id": request_id, "result": result}


def _result(
    *,
    status: str,
    argv: list[str],
    reason: str,
    network: str,
    reason_code: str | None = None,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    duration_ms: int = 0,
    truncated: bool = False,
    env_keys: list[str] | None = None,
    limits: dict | None = None,
    model: dict | None = None,
    generation: dict | None = None,
) -> dict:
    """Build an InferResult dict. ALL fields always present (contract D5);
    ``reason_code`` is present ONLY when status == "model_error"."""
    if status not in INFER_STATUS_EXIT:
        raise ValueError(f"not an InferResult status: {status!r}")
    out = {
        "status": status,
        "ok": status == "ok",
        "argv": list(argv),
        "reason": reason,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "truncated": truncated,
        "network": network,
        "env_keys": list(env_keys or []),
        "limits": dict(limits or {}),
        "model": dict(model or {}),
        "generation": dict(generation or {}),
    }
    if status == "model_error":
        # Fail loud on contract drift: a model_error without a valid closed-set
        # reason_code would silently break the loom-side distinguish-list (D5).
        if reason_code not in _MODEL_ERROR_REASON_CODES:
            raise ValueError(f"model_error requires a closed-set reason_code, got {reason_code!r}")
        out["reason_code"] = reason_code
    return out


def run_infer_request(
    payload: Any, *, preflight: bool = False, verify_full: bool = False
) -> tuple[dict, int]:
    """Validate and run one infer request; return (wrapper, process_exit).

    Flow (contract §1 D4): validate request -> build policy -> catalog validate
    (ModelCacheError -> model_error) -> runner family -> fake gating -> resolve
    + verify weights -> resolve binary -> preflight short-circuit or execute via
    SafeExecutor. Request-shape problems raise InferRequestError (the CLI maps
    them to broker-run's request_error wrapper, exit 2).
    """
    # --- request validation (D4: closed key set, strict typing) --------------
    if not isinstance(payload, dict):
        raise InferRequestError("request must be a JSON object")

    unknown = set(payload) - _REQUEST_KEYS
    if unknown:
        raise InferRequestError(f"unknown request keys: {sorted(unknown)}")

    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise InferRequestError(
            f"request schema_version {schema_version!r} != supported {SCHEMA_VERSION}"
        )

    request_id = payload.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise InferRequestError("request_id must be a string when set")

    profile_name = payload.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        raise InferRequestError("profile must be a non-empty string")

    catalog_path = payload.get("catalog")
    if not isinstance(catalog_path, str) or not catalog_path:
        raise InferRequestError("catalog must be a non-empty string path")

    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        raise InferRequestError("prompt must be a string")

    params = payload.get("params")
    if not isinstance(params, dict):
        raise InferRequestError("params must be a JSON object")
    unknown_params = set(params) - _PARAM_KEYS
    if unknown_params:
        raise InferRequestError(f"unknown params keys: {sorted(unknown_params)}")
    if "max_tokens" not in params:
        raise InferRequestError("params.max_tokens is required")
    max_tokens = _require_int("params.max_tokens", params["max_tokens"])
    if max_tokens <= 0:
        raise InferRequestError(f"params.max_tokens must be > 0, got {max_tokens}")
    temperature = params.get("temperature")
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise InferRequestError(
                f"params.temperature must be a number, got {temperature!r}"
            )
        if not 0 <= temperature <= 2:  # also rejects NaN (comparison is False)
            raise InferRequestError(
                f"params.temperature must be within [0, 2], got {temperature!r}"
            )
    seed = params.get("seed")
    if seed is not None:
        seed = _require_int("params.seed", seed)

    allow_fake = payload.get("allow_fake", False)
    if not isinstance(allow_fake, bool):
        raise InferRequestError("allow_fake must be a boolean when set")

    policy_data = payload.get("policy")
    if not isinstance(policy_data, dict):
        raise InferRequestError("policy must be an inline JSON object")

    # --- policy build: merge overrides exactly like broker-run ---------------
    policy_mapping = dict(policy_data)
    if "timeout_seconds" in payload:
        policy_mapping["timeout_seconds"] = payload["timeout_seconds"]
    if "working_dir" in payload:
        policy_mapping["working_dir"] = payload["working_dir"]
    policy = SandboxPolicy.from_mapping(policy_mapping)  # PolicyError propagates (CLI maps)

    # --- model layer (pinned P3 interfaces, written in parallel) -------------
    # Imported lazily so the request-validation layer above stands alone; a
    # missing sibling module fails loud (ImportError) exactly when first needed.
    from .modelcache import (
        ModelCacheError,
        load_catalog_mapping,
        resolve_and_verify,
        validate_profile,
    )
    from .runners.base import RUNNER_FAMILIES, RunnerError, resolve_runner_family

    def _model_error(exc: Exception) -> tuple[dict, int]:
        # ModelCacheError / RunnerError both carry .reason_code (pinned interface);
        # they NEVER escape run_infer_request (D5: model_error is pre-spawn only).
        result = _result(
            status="model_error",
            argv=[],  # [] for pre-spawn failures (D5)
            reason=str(exc),
            reason_code=exc.reason_code,  # type: ignore[attr-defined]
            network=policy.network,
        )
        return _wrap(request_id, result), INFER_STATUS_EXIT["model_error"]

    try:
        catalog_data = load_catalog_mapping(catalog_path)
        prof = validate_profile(
            catalog_data, profile_name, registered_runners=RUNNER_FAMILIES
        )
    except ModelCacheError as exc:
        return _model_error(exc)

    # D4/D7: max_tokens must respect the profile's declared context_length.
    context_length = prof.get("context_length")
    if context_length is not None and max_tokens > context_length:
        raise InferRequestError(
            f"params.max_tokens {max_tokens} exceeds profile "
            f"context_length {context_length}"
        )

    try:
        family = resolve_runner_family(prof["runner"])
    except RunnerError as exc:
        return _model_error(exc)

    # --- fake gating (D1/F4): refuse fake unless the request opts in ---------
    if family == "fake":
        if not allow_fake:
            result = _result(
                status="model_error",
                argv=[],
                reason=(
                    f"profile {profile_name!r} uses the fake runner; refused because "
                    'the request does not set "allow_fake": true (contract F4)'
                ),
                reason_code="unsupported_runner",
                network=policy.network,
            )
            return _wrap(request_id, result), INFER_STATUS_EXIT["model_error"]
        return _run_fake(
            prof,
            prompt,
            policy=policy,
            request_id=request_id,
            preflight=preflight,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
        )

    # --- llama.cpp family (D2/D3/D9): the only real runner at MVP ------------
    from .runners.llama_cpp import (
        CANDIDATE_BINARIES,
        build_argv,
        probe_version,
        recorded_argv,
        resolve_binary,
        run_llama,
    )

    try:
        resolved = resolve_and_verify(prof, verify_full=verify_full)
    except ModelCacheError as exc:
        return _model_error(exc)

    try:
        pair = resolve_binary(policy)
    except RunnerError as exc:
        return _model_error(exc)
    # resolve_binary returns tuple[str, str]; pick the bare candidate NAME
    # order-agnostically (the other element is the which()-resolved path).
    binary_name = next((x for x in pair if x in CANDIDATE_BINARIES), pair[0])

    model_block = {
        "profile": resolved.profile,
        "runner": resolved.runner,
        "runner_binary": binary_name,
        # Recorded when the execution-path --version probe succeeds, else None
        # (contract D3/A2). Preflight never spawns anything, so it stays None there.
        "runner_version": None,
        "relative_path": resolved.relative_path,
        "sha256": resolved.sha256,
        "sha256_verified": resolved.sha256_verified,
        "size_verified": True,
        "is_fake": False,
        "quantization": resolved.quantization,
        "context_length": resolved.context_length,
    }
    cache_dir_env = prof["cache_dir_env"]

    if preflight:
        # Ok-shaped result WITHOUT spawning (D5): exec fields zeroed, model block
        # full, argv shows the redacted form of exactly what would run.
        argv = build_argv(
            binary_name,
            resolved.abs_path,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
        )
        rec = recorded_argv(
            argv,
            cache_dir_env=cache_dir_env,
            relative_path=resolved.relative_path,
            model_abs_path=resolved.abs_path,
        )
        result = _result(
            status="ok",
            argv=rec,
            reason="preflight: model verified, runner resolved, nothing executed",
            network=policy.network,
            model=model_block,
        )
        return _wrap(request_id, result), INFER_STATUS_EXIT["ok"]

    model_block["runner_version"] = probe_version(policy, binary_name)
    exec_result, rec_argv, binary_name_run = run_llama(
        policy,
        resolved,
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        seed=seed,
        cache_dir_env=cache_dir_env,  # preflight/execution labels always agree (A3)
    )
    model_block["runner_binary"] = binary_name_run

    # A4 path hygiene: modern llama.cpp routes token text through the same
    # logger --log-disable would pause (upstream #10002), so the argv carries
    # no log flag and the model-path leak is closed HERE instead: every
    # occurrence of the resolved weight path or the cache root in captured
    # output is replaced by its self-labeling ${env}/... form.
    label = "${" + cache_dir_env + "}/" + resolved.relative_path
    real_root = os.path.realpath(os.environ.get(cache_dir_env) or "")

    def _scrub(text: str) -> str:
        text = text.replace(resolved.abs_path, label)
        if len(real_root) > 1:
            text = text.replace(real_root, "${" + cache_dir_env + "}")
        return text

    stdout = _scrub(exec_result.stdout)
    stderr = _scrub(exec_result.stderr)

    status = _EXEC_STATUS_MAP[exec_result.status]
    generation: dict = {}
    if status == "ok":
        generation = {
            "text": stdout,
            "usage": {
                "prompt_chars": len(prompt),
                "completion_chars": len(stdout),
            },
            "finish_reason": "unknown",  # llama.cpp CLI does not report one (D5)
        }
    result = _result(
        status=status,
        argv=rec_argv,  # recorded/redacted form only -- never the raw argv (F9)
        reason=exec_result.reason,
        network=exec_result.network,
        exit_code=exec_result.exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=exec_result.duration_ms,
        truncated=exec_result.truncated,
        env_keys=list(exec_result.env_keys),
        limits=dict(exec_result.limits),
        model=model_block,
        generation=generation,
    )
    return _wrap(request_id, result), INFER_STATUS_EXIT[status]


def _run_fake(
    prof: dict,
    prompt: str,
    *,
    policy: SandboxPolicy,
    request_id: str | None,
    preflight: bool,
    max_tokens: int,
    temperature: float | None,
    seed: int | None,
) -> tuple[dict, int]:
    """Fake-runner path (D1/F4): NO SafeExecutor -- the fake runner runs
    in-process and exercises the seam, never the sandbox gates. Exec fields are
    synthesized; the model block is marked ``"is_fake": true`` so no consumer
    can mistake it for a gated execution. No weights are resolved or hashed
    (``requires_weights`` is False); ``sha256_verified`` is therefore ``"none"``
    (contract amendment A1) -- never a dishonest "full"/"cached".
    """
    model_block = {
        "profile": prof["name"],
        "runner": "fake",
        "runner_binary": None,
        "runner_version": None,
        "relative_path": prof["relative_path"],
        "sha256": prof["sha256"],
        "sha256_verified": "none",  # nothing was hashed; is_fake governs (A1)
        "size_verified": True,
        "is_fake": True,
        "quantization": prof.get("quantization"),
        "context_length": prof.get("context_length"),
    }
    if preflight:
        result = _result(
            status="ok",
            argv=[],
            reason="preflight: fake runner validated, nothing executed",
            network=policy.network,
            model=model_block,
        )
        return _wrap(request_id, result), INFER_STATUS_EXIT["ok"]

    from .runners.fake_runner import FakeRunner

    runner = FakeRunner(profile=prof["name"])
    # Forward only the params that are set: identical semantics either way, and
    # compatible with the pinned generate(..., temperature=None, seed=None) form.
    kwargs: dict = {"max_tokens": max_tokens}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if seed is not None:
        kwargs["seed"] = seed
    start = time.monotonic()
    out = runner.generate(prompt, **kwargs)
    duration_ms = int((time.monotonic() - start) * 1000)

    generation = {
        "text": out["text"],
        "usage": dict(out["usage"]),
        "finish_reason": "stop",  # fake completions always terminate cleanly (D5)
    }
    result = _result(
        status="ok",
        argv=[],  # no process, no argv: the fake path never spawns
        reason="fake runner completed (seam exercised; sandbox gates bypassed)",
        network=policy.network,
        exit_code=0,
        duration_ms=duration_ms,
        model=model_block,
        generation=generation,
    )
    return _wrap(request_id, result), INFER_STATUS_EXIT["ok"]
