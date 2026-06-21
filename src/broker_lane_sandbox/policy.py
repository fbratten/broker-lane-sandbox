"""SandboxPolicy -- the default-deny execution contract.

Everything is forbidden until the policy explicitly allows it:
  * `allow_exec` is False        -> no process may be spawned at all,
  * `allowed_commands` is empty  -> no executable is permitted,
  * `network` is "offline"       -> proxies stripped, runners must not open sockets,
  * env is empty                 -> only allow-listed names pass through,
  * secret-looking env names are dropped even if allow-listed (unless explicitly opted in).

Canonical on-disk format is JSON (stdlib, zero deps). YAML is read opportunistically
*iff* PyYAML is importable -- the core never depends on it. Unknown keys fail loud.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import SCHEMA_VERSION


class PolicyError(ValueError):
    """Raised for any malformed / out-of-contract policy. Fail loud, never silent."""


# A minimal, safe baseline of env names a child usually needs to start at all.
DEFAULT_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")

# Names that smell like a secret -- dropped from the child env unless the policy
# sets allow_secret_env=True. Defense-in-depth so an accidental allow-list of e.g.
# OPENROUTER_API_KEY never leaks into a sandboxed child.
SECRET_NAME_RE = re.compile(
    r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|SESSION|COOKIE|AUTH)",
    re.IGNORECASE,
)

_VALID_NETWORK = ("offline", "online")


@dataclass
class SandboxPolicy:
    """A default-deny policy. Construct via `from_mapping` / `from_file`."""

    schema_version: int = SCHEMA_VERSION

    # --- execution gate (default-deny) --------------------------------------
    allow_exec: bool = False
    allowed_commands: list[str] = field(default_factory=list)   # basenames, allow-list

    # --- environment scrubbing ----------------------------------------------
    env_allowlist: list[str] = field(default_factory=lambda: list(DEFAULT_ENV_ALLOWLIST))
    env_passthrough_prefixes: list[str] = field(default_factory=list)
    allow_secret_env: bool = False

    # --- network -------------------------------------------------------------
    network: str = "offline"

    # --- process / resource limits ------------------------------------------
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1_000_000          # cap on captured stdout / stderr each
    cpu_seconds: int | None = None             # RLIMIT_CPU (POSIX)
    address_space_bytes: int | None = None     # RLIMIT_AS (POSIX)
    max_processes: int | None = None           # RLIMIT_NPROC (POSIX)

    # --- working directory / model cache ------------------------------------
    working_dir: str | None = None
    model_dir_env: str = "SANDBOX_MODEL_DIR"

    # -------------------------------------------------------------------------

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise PolicyError(
                f"policy schema_version {self.schema_version} != supported {SCHEMA_VERSION}"
            )
        if self.network not in _VALID_NETWORK:
            raise PolicyError(
                f"network must be one of {_VALID_NETWORK}, got {self.network!r}"
            )
        if self.timeout_seconds <= 0:
            raise PolicyError("timeout_seconds must be > 0")
        if self.max_output_bytes <= 0:
            raise PolicyError("max_output_bytes must be > 0")
        for name, val in (
            ("cpu_seconds", self.cpu_seconds),
            ("address_space_bytes", self.address_space_bytes),
            ("max_processes", self.max_processes),
        ):
            if val is not None and val <= 0:
                raise PolicyError(f"{name} must be > 0 when set, got {val}")
        if not isinstance(self.allowed_commands, list):
            raise PolicyError("allowed_commands must be a list")

    # --- construction --------------------------------------------------------

    @classmethod
    def from_mapping(cls, data: dict) -> "SandboxPolicy":
        if not isinstance(data, dict):
            raise PolicyError("policy must be a mapping/object")
        # Keys starting with "_" are comments (JSON has none); ignore them.
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise PolicyError(f"unknown policy keys: {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def from_file(cls, path: str | os.PathLike) -> "SandboxPolicy":
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        suffix = p.suffix.lower()
        if suffix in (".yaml", ".yml"):
            data = _load_yaml(text, p)
        else:
            data = json.loads(text)          # canonical, stdlib
        return cls.from_mapping(data)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    # --- helpers consumed by env scrub / executor ---------------------------

    def is_command_allowed(self, command: str) -> bool:
        """A command (path or bare name) is allowed iff its basename is allow-listed."""
        if not self.allow_exec:
            return False
        base = os.path.basename(command)
        return base in self.allowed_commands


def _load_yaml(text: str, path: Path) -> dict:
    try:
        import yaml  # optional; core never hard-depends on it
    except ImportError as exc:  # pragma: no cover - exercised only without PyYAML
        raise PolicyError(
            f"{path} is YAML but PyYAML is not installed. Use a .json policy "
            "or `pip install pyyaml`."
        ) from exc
    data = yaml.safe_load(text)
    return data or {}
