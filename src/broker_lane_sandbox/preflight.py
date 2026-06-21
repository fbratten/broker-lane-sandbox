"""Preflight -- inspect a policy + environment WITHOUT executing anything.

Returns a JSON-friendly report so broker-loom (or an operator) can see the posture
before committing to a run: default-deny status, which allow-listed commands actually
resolve on PATH, the env-scrub plan (names only), the network posture, limit support,
and whether the model cache root is configured. Pure inspection -- never spawns.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .envscrub import build_child_env
from .limits import have_resource, limits_summary, rlimit_spec
from .policy import SECRET_NAME_RE, SandboxPolicy


def preflight(policy: SandboxPolicy) -> dict:
    warnings: list[str] = []

    # --- command resolution -------------------------------------------------
    commands = {}
    for cmd in policy.allowed_commands:
        resolved = shutil.which(cmd)
        commands[cmd] = resolved
        if resolved is None:
            warnings.append(f"allowed command not found on PATH: {cmd}")

    if policy.allow_exec and not policy.allowed_commands:
        warnings.append("allow_exec is true but allowed_commands is empty (nothing can run)")

    # --- env scrub plan (names only) ----------------------------------------
    child_env, dropped_secret = build_child_env(policy)
    for name in policy.env_allowlist:
        if not policy.allow_secret_env and SECRET_NAME_RE.search(name):
            warnings.append(f"allow-listed env name looks secret and will be dropped: {name}")

    # --- limits -------------------------------------------------------------
    if not have_resource():
        warnings.append("resource module unavailable: rlimits not enforced (timeout only)")
    elif not rlimit_spec(policy):
        warnings.append("no rlimits configured (cpu/address-space/processes are unbounded)")

    # --- model cache --------------------------------------------------------
    model_root = os.environ.get(policy.model_dir_env)
    model_cache = {
        "env": policy.model_dir_env,
        "set": model_root is not None,
        "exists": bool(model_root) and Path(model_root).is_dir(),
        "path": model_root,
    }

    return {
        "ok": len(warnings) == 0,
        "schema_version": policy.schema_version,
        "execution": {
            "allow_exec": policy.allow_exec,
            "default_deny": not policy.allow_exec,
            "allowed_commands": commands,
        },
        "network": policy.network,
        "env_plan": {
            "passthrough_names": sorted(child_env.keys()),
            "dropped_secret_names": sorted(dropped_secret),
            "allow_secret_env": policy.allow_secret_env,
        },
        "limits": limits_summary(policy),
        "model_cache": model_cache,
        "warnings": warnings,
    }
