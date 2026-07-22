"""Runner-family registry -- the CLOSED set of model-runner families (P3 D1).

Trust model: runner families are CODE-OWNED. Operator/config input can only
*select* among the families listed here; it can never introduce a new runner or
binary into the policy (contract section 3.1 ownership). The registry is
deliberately data-light: `infer.py` branches on the resolved family name --
there is no dynamic loading, no entry points, no plugin path. Unknown or
non-string families fail loud with RunnerError(reason_code="unsupported_runner")
which the infer layer maps to a `model_error` result (contract D5).

Reason codes carried by RunnerError:
  * unsupported_runner -- family is not in the closed RUNNER_FAMILIES set
  * runner_missing     -- family is supported but no binary resolves on the
                          scrubbed child PATH (contract D9/F11)
"""
from __future__ import annotations

from typing import Protocol


class RunnerError(ValueError):
    """Fail-loud runner-layer error with a machine-readable reason_code.

    Mirrors the house PolicyError style: always a ValueError subclass, never a
    silent fallback. `reason_code` is one of the D5 closed set entries
    ("unsupported_runner", "runner_missing") and is surfaced verbatim in the
    infer result's `reason_code` field when status == "model_error".
    """

    def __init__(self, message: str, *, reason_code: str):
        super().__init__(message)
        self.reason_code = reason_code


# The closed set (contract D1/D2): fake (weight-free, cross-platform) and the
# llama.cpp family. ollama/transformers are DEFERRED, not silently accepted.
RUNNER_FAMILIES = ("fake", "llama.cpp")


class Runner(Protocol):
    """Structural protocol every runner family implements (contract D1).

    FakeRunner satisfies it in-process (seam-only, never the gates);
    the llama.cpp family satisfies it via SafeExecutor subprocess execution.
    """

    profile: str

    @property
    def requires_weights(self) -> bool: ...

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 256,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> dict: ...


def resolve_runner_family(family: str) -> str:
    """Return `family` iff it is in the closed registry; else fail loud.

    Non-string input is rejected with the same reason code -- the caller
    (infer.py) maps this to model_error/unsupported_runner (contract D1/D5).
    """
    if not isinstance(family, str) or family not in RUNNER_FAMILIES:
        raise RunnerError(
            f"unsupported runner family {family!r}; "
            f"supported families: {list(RUNNER_FAMILIES)}",
            reason_code="unsupported_runner",
        )
    return family
