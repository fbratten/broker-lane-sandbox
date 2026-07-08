"""Environment scrubbing -- build a child env from a default-empty baseline.

The child starts with **nothing** and receives only the names the policy explicitly
allow-lists (exact names + prefixes). Secret-looking names are dropped even when
allow-listed, unless `policy.allow_secret_env` is True. Offline network strips proxy
variables so a misbehaving runner can't reach a configured proxy.

Returns the child env plus the list of names dropped *because* they looked secret, so
preflight / results can surface that without ever exposing values.
"""
from __future__ import annotations

import os

from .policy import SECRET_NAME_RE, SandboxPolicy

# Proxy-ish vars removed when network == "offline" (best-effort env neutralization).
_PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy",
)


def _name_allowed(name: str, policy: SandboxPolicy) -> bool:
    if name in policy.env_allowlist:
        return True
    # Empty prefixes are rejected at policy construction; the `if pfx` guard is
    # belt-and-braces so a mutated policy still cannot match every name.
    return any(name.startswith(pfx) for pfx in policy.env_passthrough_prefixes if pfx)


def build_child_env(
    policy: SandboxPolicy,
    *,
    source_env: dict | None = None,
) -> tuple[dict, list[str]]:
    """Return (child_env, dropped_secret_names).

    child_env is built from empty: only allow-listed, non-secret names from
    `source_env` (defaults to os.environ) survive.
    """
    src = dict(os.environ if source_env is None else source_env)
    child: dict[str, str] = {}
    dropped_secret: list[str] = []

    for name, value in src.items():
        if not _name_allowed(name, policy):
            continue
        if not policy.allow_secret_env and SECRET_NAME_RE.search(name):
            dropped_secret.append(name)
            continue
        child[str(name)] = str(value)

    if policy.network == "offline":
        for pv in _PROXY_VARS:
            child.pop(pv, None)
        # A clear signal to cooperating runners that they must stay offline.
        child["SANDBOX_NETWORK"] = "offline"
        child["NO_PROXY"] = "*"
        child["no_proxy"] = "*"
    else:
        child["SANDBOX_NETWORK"] = "online"

    return child, dropped_secret
