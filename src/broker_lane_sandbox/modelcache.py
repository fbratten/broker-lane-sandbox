"""Strict execution-path model catalog validation + weight verification (P3 D11).

Trust model
-----------
* The catalog (models.yaml / .json) is operator-tracked sha256-pinning DATA. It
  describes weights; it never contains them (INVARIANT-1). The lenient lister in
  `catalog.py` stays lenient for display; THIS module is the strict gate that the
  execution path (`bls infer`) must pass through. Anything malformed fails loud
  with a typed `ModelCacheError` -- never a silent default.
* Weights live under an env-driven runtime cache root (catalog `cache_dir_env`,
  default ``SANDBOX_MODEL_DIR``) -- outside the repo by definition. The bytes on
  disk are untrusted until their sha256 matches the catalog pin.
* Path containment is enforced twice: segment-wise on `relative_path` (no ``..``
  segment, not absolute, no backslash / drive-letter), then join +
  ``os.path.realpath`` containment under ``realpath(root) + os.sep`` so a
  symlink pointing OUT of the model root is refused even when its target's
  bytes would hash correctly (contract §1 D11 / F3).
* The verification sidecar ``<weight>.blsverify.json`` is an integrity CACHE,
  not a security boundary: trusting it means trusting local disk, which is the
  same trust level as the weights themselves. First use (and any size/mtime_ns
  drift, sidecar corruption, or ``verify_full=True``) forces a full streaming
  sha256 against the catalog pin; the steady-state fast path only proves
  "unchanged since last full verification". A checksum mismatch NEVER writes a
  sidecar. Results report ``sha256_verified: "full" | "cached"`` truthfully
  (contract L2-F6 / L4-F4).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .policy import _load_yaml

# Catalog schema version -- deliberately SEPARATE from the wire SCHEMA_VERSION
# (the CLI/API envelope constant in __init__.py). The catalog is operator data
# with its own evolution rules (contract L2-F6).
CATALOG_SCHEMA_VERSION = 1

# Default env var naming the runtime cache root when the catalog omits
# `cache_dir_env` (matches SandboxPolicy.model_dir_env default).
DEFAULT_CACHE_DIR_ENV = "SANDBOX_MODEL_DIR"

_REASON_CODES = (
    "catalog_invalid",
    "unsupported_runner",
    "model_dir_unset",
    "model_missing",
    "size_mismatch",
    "checksum_mismatch",
)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

_SIDECAR_SUFFIX = ".blsverify.json"
_HASH_CHUNK = 1024 * 1024  # 1 MiB streaming chunks


class ModelCacheError(ValueError):
    """Any catalog/weight problem on the execution path. Fail loud, never silent.

    `reason_code` is the closed machine-readable vocabulary that surfaces as the
    `model_error` result's `reason_code` field (contract D5).
    """

    def __init__(self, message: str, *, reason_code: str) -> None:
        if reason_code not in _REASON_CODES:
            raise ValueError(f"unknown ModelCacheError reason_code {reason_code!r}")
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ResolvedModel:
    """A catalog profile resolved to a verified on-disk weight file."""

    profile: str
    runner: str
    abs_path: str
    relative_path: str
    sha256: str
    sha256_verified: str  # "full" | "cached" -- reported truthfully (D11)
    size_bytes: int
    quantization: str | None = None
    context_length: int | None = None
    license: str | None = None


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

def load_catalog_mapping(catalog_path: str) -> dict:
    """Read a catalog file into a mapping; anything else -> catalog_invalid.

    Same source semantics as catalog.py's lenient lister: JSON via stdlib
    (canonical), YAML opportunistically via policy._load_yaml (PyYAML optional).
    All read/parse failures collapse to ModelCacheError(catalog_invalid) with
    the original exception chained -- the execution path needs one loud typed
    error, not a zoo of OSError/JSONDecodeError/YAMLError shapes.
    """
    p = Path(catalog_path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelCacheError(
            f"catalog {p} unreadable: {exc}", reason_code="catalog_invalid"
        ) from exc
    try:
        if p.suffix.lower() in (".yaml", ".yml"):
            data = _load_yaml(text, p)  # PolicyError when PyYAML absent
        else:
            data = json.loads(text)
    except Exception as exc:  # parse failure of any flavor -> one typed error
        raise ModelCacheError(
            f"catalog {p} is not parseable: {exc}", reason_code="catalog_invalid"
        ) from exc
    if not isinstance(data, dict):
        raise ModelCacheError(
            f"catalog {p} must be a mapping/object, got {type(data).__name__}",
            reason_code="catalog_invalid",
        )
    return data


# ---------------------------------------------------------------------------
# Strict profile validation (execution path only; the lister stays lenient)
# ---------------------------------------------------------------------------

def _require_str(profile_name: str, key: str, val) -> str:
    if not isinstance(val, str) or not val:
        raise ModelCacheError(
            f"profile {profile_name!r}: {key} must be a non-empty string, got {val!r}",
            reason_code="catalog_invalid",
        )
    return val


def _validate_relative_path(profile_name: str, rel) -> str:
    """Segment-wise traversal check (contract D11): the realpath containment in
    resolve_and_verify is the backstop; this rejects obviously hostile shapes
    before any filesystem contact."""
    rel = _require_str(profile_name, "relative_path", rel)
    if "\\" in rel:
        raise ModelCacheError(
            f"profile {profile_name!r}: relative_path must use '/' separators, "
            f"got {rel!r}",
            reason_code="catalog_invalid",
        )
    if rel.startswith("/") or os.path.isabs(rel) or _DRIVE_LETTER_RE.match(rel):
        raise ModelCacheError(
            f"profile {profile_name!r}: relative_path must be relative, got {rel!r}",
            reason_code="catalog_invalid",
        )
    if ".." in rel.split("/"):  # segment-wise: 'a..b/c' is fine, 'a/../b' is not
        raise ModelCacheError(
            f"profile {profile_name!r}: relative_path must not contain a '..' "
            f"segment, got {rel!r}",
            reason_code="catalog_invalid",
        )
    return rel


def _positive_int_field(profile_name: str, key: str, val) -> int:
    # bool is an int subclass: JSON/YAML `true` must not become 1 (house rule,
    # mirrors policy._positive_int).
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise ModelCacheError(
            f"profile {profile_name!r}: {key} must be a positive integer, got {val!r}",
            reason_code="catalog_invalid",
        )
    return val


def validate_profile(
    catalog: Mapping, profile_name: str, *, registered_runners
) -> dict:
    """Strictly validate one catalog profile for execution; returns a NEW dict.

    The returned dict is the validated profile plus injected `name` (the profile
    key) and `cache_dir_env` (catalog top-level, default SANDBOX_MODEL_DIR), with
    sha256 normalized to lowercase. The input catalog is never mutated.
    """
    if not isinstance(catalog, Mapping):
        raise ModelCacheError(
            f"catalog must be a mapping, got {type(catalog).__name__}",
            reason_code="catalog_invalid",
        )

    sv = catalog.get("schema_version")
    # bool guard: True == 1 in Python, but `schema_version: true` is malformed data.
    if isinstance(sv, bool) or sv != CATALOG_SCHEMA_VERSION:
        raise ModelCacheError(
            f"catalog schema_version {sv!r} != supported {CATALOG_SCHEMA_VERSION} "
            "(catalog versioning is separate from the wire SCHEMA_VERSION)",
            reason_code="catalog_invalid",
        )

    cache_dir_env = catalog.get("cache_dir_env", DEFAULT_CACHE_DIR_ENV)
    if not isinstance(cache_dir_env, str) or not cache_dir_env:
        raise ModelCacheError(
            f"catalog cache_dir_env must be a non-empty string, got {cache_dir_env!r}",
            reason_code="catalog_invalid",
        )

    profiles = catalog.get("profiles")
    if not isinstance(profiles, Mapping):
        raise ModelCacheError(
            "catalog 'profiles' must be a mapping/object",
            reason_code="catalog_invalid",
        )
    prof = profiles.get(profile_name)
    if prof is None:
        raise ModelCacheError(
            f"unknown model profile {profile_name!r}", reason_code="catalog_invalid"
        )
    if not isinstance(prof, Mapping):
        raise ModelCacheError(
            f"profile {profile_name!r} must be a mapping/object",
            reason_code="catalog_invalid",
        )

    runner = prof.get("runner")
    if runner not in registered_runners:
        raise ModelCacheError(
            f"profile {profile_name!r}: runner {runner!r} is not a registered "
            f"runner family",
            reason_code="unsupported_runner",
        )

    out = dict(prof)
    out["relative_path"] = _validate_relative_path(
        profile_name, prof.get("relative_path")
    )

    sha = prof.get("sha256")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise ModelCacheError(
            f"profile {profile_name!r}: sha256 must be 64 hex chars, got {sha!r}",
            reason_code="catalog_invalid",
        )
    out["sha256"] = sha.lower()  # hashlib emits lowercase; normalize the pin

    out["size_bytes"] = _positive_int_field(
        profile_name, "size_bytes", prof.get("size_bytes")
    )

    # Typed optionals: absent/None is fine, present-but-mistyped fails loud.
    if prof.get("quantization") is not None:
        out["quantization"] = _require_str(
            profile_name, "quantization", prof.get("quantization")
        )
    if prof.get("context_length") is not None:
        out["context_length"] = _positive_int_field(
            profile_name, "context_length", prof.get("context_length")
        )
    for key in ("license", "source", "notes"):
        if prof.get(key) is not None:
            out[key] = _require_str(profile_name, key, prof.get(key))

    out["name"] = profile_name
    out["cache_dir_env"] = cache_dir_env
    return out


# ---------------------------------------------------------------------------
# Resolution + weight verification (sidecar cache)
# ---------------------------------------------------------------------------

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_sidecar(sidecar_path: str) -> dict | None:
    """Return the sidecar mapping iff readable, parseable, and well-typed.

    Any corruption -> None (caller falls back to a FULL re-hash: sidecar
    problems can only ever cause MORE verification, never less).
    """
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    size = data.get("size")
    mtime_ns = data.get("mtime_ns")
    sha = data.get("sha256")
    verified_at = data.get("verified_at")
    if isinstance(size, bool) or not isinstance(size, int):
        return None
    if isinstance(mtime_ns, bool) or not isinstance(mtime_ns, int):
        return None
    if not isinstance(sha, str) or not isinstance(verified_at, str):
        return None
    return data


def resolve_and_verify(profile: Mapping, *, verify_full: bool = False) -> ResolvedModel:
    """Resolve a validate_profile() output to a verified on-disk weight file.

    Chain (contract D11): env root lookup -> realpath containment (symlink-out
    refused) -> existence -> size vs catalog -> checksum via the sidecar cache
    (full streaming sha256 on first use / drift / verify_full, else cached).
    """
    cache_dir_env = profile["cache_dir_env"]
    root = os.environ.get(cache_dir_env)
    if not root:
        raise ModelCacheError(
            f"model cache root env var {cache_dir_env} is unset or empty; "
            "export it to the local runtime cache directory",
            reason_code="model_dir_unset",
        )

    relative_path = profile["relative_path"]
    real_root = os.path.realpath(root)
    abs_path = os.path.realpath(os.path.join(root, relative_path))
    # Containment incl. symlink-out: a link inside the root resolving outside
    # is refused even if its target's bytes would hash correctly (F3).
    if not abs_path.startswith(real_root + os.sep):
        raise ModelCacheError(
            f"profile {profile['name']!r}: ${{{cache_dir_env}}}/{relative_path} "
            "resolves outside the model root (traversal or symlink escape)",
            reason_code="catalog_invalid",
        )

    if not os.path.isfile(abs_path):
        raise ModelCacheError(
            f"profile {profile['name']!r}: model file "
            f"${{{cache_dir_env}}}/{relative_path} does not exist "
            "(fetch is a separate, explicit operator action -- contract D13)",
            reason_code="model_missing",
        )

    st = os.stat(abs_path)
    expected_size = profile["size_bytes"]
    if st.st_size != expected_size:
        raise ModelCacheError(
            f"profile {profile['name']!r}: size {st.st_size} != catalog "
            f"size_bytes {expected_size}",
            reason_code="size_mismatch",
        )

    catalog_sha = profile["sha256"]
    sidecar_path = abs_path + _SIDECAR_SUFFIX

    need_full = verify_full
    if not need_full:
        sidecar = _read_sidecar(sidecar_path)
        if (
            sidecar is None
            or sidecar["size"] != st.st_size
            or sidecar["mtime_ns"] != st.st_mtime_ns
            or sidecar["sha256"] != catalog_sha
        ):
            # Absent/corrupt sidecar, drift since last verification, or a
            # sidecar that disagrees with the catalog pin -> full re-hash.
            need_full = True

    if not need_full:
        verified = "cached"
    else:
        digest = _sha256_file(abs_path)
        if digest != catalog_sha:
            # NEVER write a sidecar on mismatch: a poisoned cache entry must
            # not be able to launder a bad weight into the fast path.
            raise ModelCacheError(
                f"profile {profile['name']!r}: sha256 mismatch -- file hashed "
                f"{digest}, catalog pins {catalog_sha}",
                reason_code="checksum_mismatch",
            )
        try:
            with open(sidecar_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "size": st.st_size,
                        "mtime_ns": st.st_mtime_ns,
                        "sha256": digest,
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                    },
                    f,
                )
        except OSError:
            # Non-fatal by contract: a read-only cache dir just means the next
            # call pays the full hash again. Verification itself SUCCEEDED.
            pass
        verified = "full"

    return ResolvedModel(
        profile=profile["name"],
        runner=profile["runner"],
        abs_path=abs_path,
        relative_path=relative_path,
        sha256=catalog_sha,
        sha256_verified=verified,
        size_bytes=st.st_size,
        quantization=profile.get("quantization"),
        context_length=profile.get("context_length"),
        license=profile.get("license"),
    )
