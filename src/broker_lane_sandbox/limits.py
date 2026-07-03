"""Process resource limits + isolation (POSIX best-effort).

`rlimit_spec(policy)` is a pure function returning the (resource, soft, hard) tuples
the policy asks for -- unit-testable without spawning anything. `build_preexec(policy)`
returns a child-side callable that starts a new session (so the whole process group can
be killed on timeout) and applies those rlimits. On platforms without `resource`
(e.g. Windows) it degrades to setsid-if-available / None, and the executor falls back
to wall-clock timeout only. Limits are reported, never silently skipped.
"""
from __future__ import annotations

import os
from typing import Callable

try:
    import resource  # POSIX only
    _HAVE_RESOURCE = True
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore
    _HAVE_RESOURCE = False


def have_resource() -> bool:
    return _HAVE_RESOURCE


def rlimit_spec(policy) -> list[tuple[int, int]]:
    """Pure: the rlimits this policy requests, as (RLIMIT_*, value) pairs.

    Empty when `resource` is unavailable or no limits are configured.
    """
    if not _HAVE_RESOURCE:
        return []
    spec: list[tuple[int, int]] = []
    if policy.cpu_seconds is not None:
        spec.append((resource.RLIMIT_CPU, int(policy.cpu_seconds)))
    if policy.address_space_bytes is not None:
        spec.append((resource.RLIMIT_AS, int(policy.address_space_bytes)))
    if policy.max_processes is not None:
        spec.append((resource.RLIMIT_NPROC, int(policy.max_processes)))
    if policy.max_file_size_bytes is not None:
        spec.append((resource.RLIMIT_FSIZE, int(policy.max_file_size_bytes)))
    return spec


def limits_summary(policy) -> dict:
    """JSON-friendly description of the effective limits (for results / preflight)."""
    return {
        "resource_module": _HAVE_RESOURCE,
        "timeout_seconds": policy.timeout_seconds,
        "cpu_seconds": policy.cpu_seconds,
        "address_space_bytes": policy.address_space_bytes,
        "max_processes": policy.max_processes,
        "max_file_size_bytes": policy.max_file_size_bytes,
        "max_output_bytes": policy.max_output_bytes,
        "enforced_rlimits": [
            {"resource": r, "value": v} for r, v in rlimit_spec(policy)
        ],
    }


def build_preexec(policy) -> Callable[[], None] | None:
    """Child-side setup: new session + rlimits. None if nothing to do / no setsid."""
    spec = rlimit_spec(policy)
    has_setsid = hasattr(os, "setsid")
    if not has_setsid and not spec:
        return None

    def _preexec() -> None:  # pragma: no cover - runs in the forked child
        if has_setsid:
            os.setsid()
        for res, val in spec:
            # Soft == hard so the child cannot raise its own ceiling.
            resource.setrlimit(res, (val, val))

    return _preexec
