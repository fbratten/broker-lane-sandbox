"""Broker-facing JSON request seam.

This module backs ``bls broker-run --request request.json``. It intentionally stays
small: broker-loom sends one JSON object, the sandbox validates it, executes through
the existing SafeExecutor, and returns one JSON object with the original request_id.
"""
from __future__ import annotations

from typing import Any

from . import SCHEMA_VERSION
from .executor import SafeExecutor
from .policy import SandboxPolicy


class BrokerRunError(ValueError):
    """Raised when a broker-run request is malformed or out of contract."""


_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "policy",
        "argv",
        "stdin",
        "timeout_seconds",
        "working_dir",
    }
)


def request_error(reason: str, request_id: str | None = None) -> dict[str, Any]:
    """Return a broker-run wrapper for request-shape errors.

    Request errors happen before the safe executor receives an argv, so they are not
    represented as ExecResult objects. They still return JSON and a stable status.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "status": "request_error",
        "ok": False,
        "reason": reason,
    }


def run_broker_request(data: Any) -> dict[str, Any]:
    """Validate and run a broker request.

    Expected shape::

        {
          "schema_version": 1,
          "request_id": "optional-correlation-id",
          "policy": { ... SandboxPolicy ... },
          "argv": ["python3", "script.py"],
          "stdin": "optional text",
          "timeout_seconds": 10,
          "working_dir": "/optional/cwd"
        }

    The policy is inline by design. broker-loom should not make the sandbox resolve
    broker-local policy paths across the trust boundary.
    """
    if not isinstance(data, dict):
        raise BrokerRunError("request must be a JSON object")

    unknown = set(data) - _REQUEST_KEYS
    if unknown:
        raise BrokerRunError(f"unknown request keys: {sorted(unknown)}")

    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise BrokerRunError(
            f"request schema_version {schema_version!r} != supported {SCHEMA_VERSION}"
        )

    request_id = data.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise BrokerRunError("request_id must be a string when set")

    argv = data.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise BrokerRunError("argv must be a non-empty list of strings")

    stdin = data.get("stdin")
    if stdin is not None and not isinstance(stdin, str):
        raise BrokerRunError("stdin must be a string or null when set")

    policy_data = data.get("policy")
    if not isinstance(policy_data, dict):
        raise BrokerRunError("policy must be an inline JSON object")

    policy_mapping = dict(policy_data)
    if "timeout_seconds" in data:
        policy_mapping["timeout_seconds"] = data["timeout_seconds"]
    if "working_dir" in data:
        policy_mapping["working_dir"] = data["working_dir"]

    policy = SandboxPolicy.from_mapping(policy_mapping)
    result = SafeExecutor(policy).run(argv, input_text=stdin)

    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "result": result.to_dict(),
    }
