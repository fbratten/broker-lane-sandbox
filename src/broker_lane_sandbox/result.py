"""Machine-readable result classes for sandbox execution.

Every sandbox operation returns a JSON-serializable result. broker-loom (or any
caller) consumes these over the CLI/API seam -- never as Python objects. Keep the
shape stable. SCHEMA_VERSION (in __init__) is the CLI/API ENVELOPE version; result
vocabularies are per-command (`run`/`broker-run` transport ExecResult; `bls infer`
has its own InferResult vocabulary in infer.py) -- bump SCHEMA_VERSION only when
the envelope itself changes incompatibly (contract D12).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


class Status:
    """Stable result-status strings (the only values `ExecResult.status` takes).

    This is the `run`/`broker-run` vocabulary. `bls infer` results carry their
    own closed status enum, defined and mapped in infer.py (contract D12).
    """

    OK = "ok"                       # process ran and exited 0
    EXIT_NONZERO = "exit_nonzero"   # process ran and exited non-zero
    DENIED = "denied"               # blocked by policy BEFORE any spawn (default-deny)
    TIMEOUT = "timeout"             # killed after exceeding the wall-clock budget
    SPAWN_ERROR = "spawn_error"     # could not start the process (missing exe, etc.)


# Statuses that mean "the sandbox refused / failed to run", as opposed to a clean run.
NON_RUN_STATUSES = frozenset({Status.DENIED, Status.SPAWN_ERROR})


@dataclass
class ExecResult:
    """Outcome of a single sandboxed execution attempt."""

    status: str
    argv: list[str]
    reason: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    truncated: bool = False
    network: str = "offline"
    env_keys: list[str] = field(default_factory=list)   # NAMES only, never values
    limits: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == Status.OK

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d

    # Convenience constructors keep call sites terse and consistent. ----------

    @classmethod
    def denied(cls, argv: list[str], reason: str) -> "ExecResult":
        return cls(status=Status.DENIED, argv=list(argv), reason=reason)

    @classmethod
    def spawn_error(cls, argv: list[str], reason: str) -> "ExecResult":
        return cls(status=Status.SPAWN_ERROR, argv=list(argv), reason=reason)
