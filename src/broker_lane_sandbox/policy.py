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
import math
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


def _positive_int(name: str, val) -> int | None:
    """Validate an integer policy field: int (or integral float) > 0, or None.

    A malformed policy must surface as PolicyError -- never a raw TypeError and
    never a silently-coerced limit. In particular JSON ``true`` must not become
    limit=1 (bool is an int subclass), and ``"10"`` must not crash with a raw
    TypeError. Integral floats are accepted and normalized to int because JSON
    numbers like ``1e9`` arrive as float; fractional floats are rejected rather
    than silently truncated.
    """
    if val is None:
        return None
    if isinstance(val, bool):
        raise PolicyError(f"{name} must be an integer, got boolean {val!r}")
    if isinstance(val, int):
        out = val
    elif isinstance(val, float) and val.is_integer():
        out = int(val)
    else:
        raise PolicyError(f"{name} must be a positive integer, got {val!r}")
    if out <= 0:
        raise PolicyError(f"{name} must be > 0 when set, got {out}")
    return out


def is_bare_command(command: str) -> bool:
    """True iff `command` is a bare name with no directory component.

    Only a bare name can be soundly gated by a basename allow-list, because then
    basename == the file PATH will resolve and execute. Anything with a path
    separator (absolute or relative) is rejected to close the path-bypass where a
    crafted ``/dir/python3`` slips an allow-listed *name* past the gate.
    """
    if not command:
        return False
    if command != os.path.basename(command):
        return False
    return "/" not in command and "\\" not in command


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
    max_file_size_bytes: int | None = None     # RLIMIT_FSIZE (POSIX), per-file write cap

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
        # Numeric fields are type-hardened: malformed values (bool/str/fractional
        # float) raise PolicyError -- never a raw TypeError, never silent coercion.
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise PolicyError(
                f"timeout_seconds must be a number, got {self.timeout_seconds!r}"
            )
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise PolicyError(
                f"timeout_seconds must be a finite number > 0, got {self.timeout_seconds!r}"
            )
        out = _positive_int("max_output_bytes", self.max_output_bytes)
        if out is None:
            raise PolicyError("max_output_bytes must be a positive integer, got None")
        self.max_output_bytes = out
        self.cpu_seconds = _positive_int("cpu_seconds", self.cpu_seconds)
        self.address_space_bytes = _positive_int(
            "address_space_bytes", self.address_space_bytes
        )
        self.max_processes = _positive_int("max_processes", self.max_processes)
        self.max_file_size_bytes = _positive_int(
            "max_file_size_bytes", self.max_file_size_bytes
        )
        if not isinstance(self.allowed_commands, list):
            raise PolicyError("allowed_commands must be a list")
        if not isinstance(self.env_allowlist, list) or not all(
            isinstance(n, str) for n in self.env_allowlist
        ):
            raise PolicyError("env_allowlist must be a list of strings")
        # An empty (or whitespace) prefix would match EVERY env name and pass the
        # entire environment through, defeating the default-empty guarantee.
        if not isinstance(self.env_passthrough_prefixes, list) or not all(
            isinstance(p, str) for p in self.env_passthrough_prefixes
        ):
            raise PolicyError("env_passthrough_prefixes must be a list of strings")
        for p in self.env_passthrough_prefixes:
            if not p.strip():
                raise PolicyError(
                    "env_passthrough_prefixes entries must be non-empty "
                    f"(an empty prefix matches every env name), got {p!r}"
                )

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
        """Allowed iff exec is enabled AND `command` is a bare name in the allow-list.

        The allow-list gates by *name*, so argv[0] must BE its own basename (no path
        component). A path-bearing argv[0] like ``/tmp/evil/python3`` would let the
        basename ``python3`` pass while Popen executes an arbitrary file -- so it is
        refused here. Bare names resolve through PATH, matching how preflight checks
        availability. Identity/realpath pinning is intentionally out of scope.
        """
        if not self.allow_exec:
            return False
        if not is_bare_command(command):
            return False
        return command in self.allowed_commands


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
