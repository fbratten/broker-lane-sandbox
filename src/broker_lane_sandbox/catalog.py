"""Model-profile catalog loader (manifests only -- never weights).

Reads a tracked catalog (`models.yaml`/`models.example.yaml`, or a `.json` equivalent)
and returns profile metadata: runner, source URL, sha256, license, env-driven path.
INVARIANT-1: this loads *descriptions* of models, never model files. JSON works with
the stdlib; YAML requires PyYAML (optional) -- absence fails loud with guidance.
"""
from __future__ import annotations

import json
from pathlib import Path

from .policy import PolicyError, _load_yaml


def load_catalog(path: str | Path) -> dict:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        data = _load_yaml(text, p)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise PolicyError(f"catalog {p} must be a mapping/object")
    return data


def list_profiles(path: str | Path) -> dict:
    """Return a JSON-friendly summary of the catalog's model profiles."""
    data = load_catalog(path)
    profiles = data.get("profiles", {}) or {}
    summary = {
        name: {
            "runner": prof.get("runner"),
            "source": prof.get("source"),
            "sha256": prof.get("sha256"),
            "license": prof.get("license"),
            "relative_path": prof.get("relative_path"),
        }
        for name, prof in profiles.items()
    }
    return {
        "schema_version": data.get("schema_version"),
        "cache_dir_env": data.get("cache_dir_env"),
        "count": len(summary),
        "profiles": summary,
    }
