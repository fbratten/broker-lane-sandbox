"""Tests for the strict execution-path model cache (contract §1 D11, test plan §5).

Fixtures are tiny text files with hashlib-computed sha256 pins -- NEVER real
weights (INVARIANT-1). JSON-boundary + negative-control heavy: every rejection
asserts the machine-readable reason_code, not just "it raised".
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

from broker_lane_sandbox.modelcache import (
    CATALOG_SCHEMA_VERSION,
    ModelCacheError,
    ResolvedModel,
    load_catalog_mapping,
    resolve_and_verify,
    validate_profile,
)
from broker_lane_sandbox.policy import SandboxPolicy

REPO_ROOT = Path(__file__).resolve().parent.parent

# Mirrors runners/base.py RUNNER_FAMILIES; passed explicitly so this suite has
# no dependency on the runner modules (D1 closed registry).
REGISTERED = ("fake", "llama.cpp")

WEIGHT_BYTES = b"tiny text bytes standing in for GGUF weights (INVARIANT-1)\n"
WEIGHT_SHA = hashlib.sha256(WEIGHT_BYTES).hexdigest()
REL = "example/tiny-instruct.gguf"


def make_catalog(**overrides) -> dict:
    """A minimal valid catalog mapping; overrides patch the profile."""
    prof = {
        "runner": "llama.cpp",
        "relative_path": REL,
        "sha256": WEIGHT_SHA,
        "size_bytes": len(WEIGHT_BYTES),
    }
    prof.update(overrides)
    return {"schema_version": 1, "profiles": {"tiny": prof}}


def make_store(tmp_path: Path, data: bytes = WEIGHT_BYTES, rel: str = REL) -> Path:
    """Create a model root with one weight fixture file; returns the root."""
    root = tmp_path / "modelroot"
    weight = root / rel
    weight.parent.mkdir(parents=True, exist_ok=True)
    weight.write_bytes(data)
    return root


def validated(catalog: dict | None = None) -> dict:
    return validate_profile(
        catalog if catalog is not None else make_catalog(),
        "tiny",
        registered_runners=REGISTERED,
    )


def resolve_ready(tmp_path: Path, monkeypatch) -> tuple[dict, Path]:
    """Standard happy-path setup: store on disk + env root + validated profile."""
    root = make_store(tmp_path)
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))
    return validated(), root


# ---------------------------------------------------------------------------
# load_catalog_mapping (D11: unreadable/non-mapping -> catalog_invalid)
# ---------------------------------------------------------------------------

def test_load_catalog_mapping_reads_json(tmp_path) -> None:
    p = tmp_path / "models.json"
    p.write_text(json.dumps(make_catalog()), encoding="utf-8")
    data = load_catalog_mapping(str(p))
    assert data["schema_version"] == 1
    assert "tiny" in data["profiles"]


def test_load_catalog_mapping_reads_yaml_when_pyyaml_present(tmp_path) -> None:
    pytest.importorskip("yaml")  # YAML is opportunistic, core is stdlib-only
    p = tmp_path / "models.yaml"
    p.write_text(
        "schema_version: 1\n"
        "profiles:\n"
        "  tiny:\n"
        "    runner: llama.cpp\n"
        f"    relative_path: {REL}\n"
        f"    sha256: {WEIGHT_SHA}\n"
        f"    size_bytes: {len(WEIGHT_BYTES)}\n",
        encoding="utf-8",
    )
    data = load_catalog_mapping(str(p))
    assert data["profiles"]["tiny"]["sha256"] == WEIGHT_SHA


def test_load_catalog_mapping_missing_file_is_catalog_invalid(tmp_path) -> None:
    with pytest.raises(ModelCacheError) as exc:
        load_catalog_mapping(str(tmp_path / "nope.json"))
    assert exc.value.reason_code == "catalog_invalid"


def test_load_catalog_mapping_bad_json_is_catalog_invalid(tmp_path) -> None:
    p = tmp_path / "models.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ModelCacheError) as exc:
        load_catalog_mapping(str(p))
    assert exc.value.reason_code == "catalog_invalid"


def test_load_catalog_mapping_non_mapping_is_catalog_invalid(tmp_path) -> None:
    p = tmp_path / "models.json"
    p.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
    with pytest.raises(ModelCacheError) as exc:
        load_catalog_mapping(str(p))
    assert exc.value.reason_code == "catalog_invalid"


# ---------------------------------------------------------------------------
# validate_profile -- structural + schema_version (D11, L2-F6)
# ---------------------------------------------------------------------------

def test_validate_profile_happy_path_injects_name_and_cache_dir_env() -> None:
    prof = validated()
    assert prof["name"] == "tiny"
    assert prof["cache_dir_env"] == "SANDBOX_MODEL_DIR"  # default injected
    assert prof["runner"] == "llama.cpp"
    assert prof["relative_path"] == REL
    assert prof["sha256"] == WEIGHT_SHA
    assert prof["size_bytes"] == len(WEIGHT_BYTES)


def test_validate_profile_honors_catalog_cache_dir_env() -> None:
    cat = make_catalog()
    cat["cache_dir_env"] = "MY_MODEL_ROOT"
    assert validated(cat)["cache_dir_env"] == "MY_MODEL_ROOT"


def test_validate_profile_does_not_mutate_input_catalog() -> None:
    cat = make_catalog()
    before = json.dumps(cat, sort_keys=True)
    validated(cat)
    assert json.dumps(cat, sort_keys=True) == before  # returns a NEW dict


@pytest.mark.parametrize("bad_sv", [2, 0, None, "1", True])  # True == 1 in Python!
def test_validate_profile_wrong_catalog_schema_version(bad_sv) -> None:
    # CATALOG_SCHEMA_VERSION is separate from the wire SCHEMA_VERSION (L2-F6);
    # bool True must not sneak past an == 1 comparison.
    cat = make_catalog()
    cat["schema_version"] = bad_sv
    with pytest.raises(ModelCacheError) as exc:
        validated(cat)
    assert exc.value.reason_code == "catalog_invalid"


def test_catalog_schema_version_constant_is_one() -> None:
    assert CATALOG_SCHEMA_VERSION == 1


def test_validate_profile_unknown_profile_is_catalog_invalid() -> None:
    with pytest.raises(ModelCacheError) as exc:
        validate_profile(make_catalog(), "nope", registered_runners=REGISTERED)
    assert exc.value.reason_code == "catalog_invalid"


def test_validate_profile_non_mapping_profile_is_catalog_invalid() -> None:
    cat = {"schema_version": 1, "profiles": {"tiny": "not-a-mapping"}}
    with pytest.raises(ModelCacheError) as exc:
        validated(cat)
    assert exc.value.reason_code == "catalog_invalid"


def test_validate_profile_missing_profiles_key_is_catalog_invalid() -> None:
    with pytest.raises(ModelCacheError) as exc:
        validate_profile({"schema_version": 1}, "tiny", registered_runners=REGISTERED)
    assert exc.value.reason_code == "catalog_invalid"


def test_validate_profile_non_string_cache_dir_env_is_catalog_invalid() -> None:
    cat = make_catalog()
    cat["cache_dir_env"] = 7
    with pytest.raises(ModelCacheError) as exc:
        validated(cat)
    assert exc.value.reason_code == "catalog_invalid"


# ---------------------------------------------------------------------------
# validate_profile -- runner gating (D1 closed registry)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_runner", ["ollama", "transformers", "", None, 3])
def test_validate_profile_unregistered_runner_is_unsupported_runner(bad_runner) -> None:
    with pytest.raises(ModelCacheError) as exc:
        validated(make_catalog(runner=bad_runner))
    assert exc.value.reason_code == "unsupported_runner"


def test_validate_profile_accepts_registered_fake_runner() -> None:
    assert validated(make_catalog(runner="fake"))["runner"] == "fake"


# ---------------------------------------------------------------------------
# validate_profile -- relative_path traversal set (D11 segment-wise check)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_rel",
    [
        None,                       # missing
        7,                          # non-str
        "",                         # empty path
        "/etc/passwd",              # absolute
        "../outside.gguf",          # leading .. segment
        "a/../outside.gguf",        # embedded .. segment
        "a/..",                     # trailing .. segment
        "a\\b.gguf",                # backslash separator
        "C:/models/x.gguf",         # drive-letter
        "c:evil.gguf",              # drive-letter, relative form
    ],
)
def test_validate_profile_traversal_shapes_are_catalog_invalid(bad_rel) -> None:
    with pytest.raises(ModelCacheError) as exc:
        validated(make_catalog(relative_path=bad_rel))
    assert exc.value.reason_code == "catalog_invalid"


def test_validate_profile_dots_inside_a_segment_are_allowed() -> None:
    # Positive control: the check is SEGMENT-wise, not substring ('..' in name).
    prof = validated(make_catalog(relative_path="a..b/tiny..v2.gguf"))
    assert prof["relative_path"] == "a..b/tiny..v2.gguf"


# ---------------------------------------------------------------------------
# validate_profile -- sha256 / size_bytes / typed optionals (D11)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_sha",
    [None, 7, "", "abc123", "z" * 64, WEIGHT_SHA[:-1], WEIGHT_SHA + "0"],
)
def test_validate_profile_bad_sha256_is_catalog_invalid(bad_sha) -> None:
    with pytest.raises(ModelCacheError) as exc:
        validated(make_catalog(sha256=bad_sha))
    assert exc.value.reason_code == "catalog_invalid"


def test_validate_profile_uppercase_sha256_is_normalized_lowercase() -> None:
    prof = validated(make_catalog(sha256=WEIGHT_SHA.upper()))
    assert prof["sha256"] == WEIGHT_SHA  # hashlib emits lowercase


@pytest.mark.parametrize("bad_size", [None, 0, -1, True, "10", 10.5])
def test_validate_profile_bad_size_bytes_is_catalog_invalid(bad_size) -> None:
    with pytest.raises(ModelCacheError) as exc:
        validated(make_catalog(size_bytes=bad_size))
    assert exc.value.reason_code == "catalog_invalid"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("quantization", 4),
        ("quantization", ""),
        ("context_length", 0),
        ("context_length", -8),
        ("context_length", True),
        ("context_length", "8192"),
        ("license", 1),
        ("source", ["u"]),
        ("notes", {"n": 1}),
    ],
)
def test_validate_profile_mistyped_optionals_are_catalog_invalid(field, bad) -> None:
    with pytest.raises(ModelCacheError) as exc:
        validated(make_catalog(**{field: bad}))
    assert exc.value.reason_code == "catalog_invalid"


def test_validate_profile_valid_optionals_pass_through() -> None:
    prof = validated(
        make_catalog(
            quantization="Q4_K_M",
            context_length=8192,
            license="MIT",
            source="https://example.invalid/x.gguf",
            notes="fixture",
        )
    )
    assert prof["quantization"] == "Q4_K_M"
    assert prof["context_length"] == 8192
    assert prof["license"] == "MIT"


# ---------------------------------------------------------------------------
# resolve_and_verify -- env root + containment (D11 chain, F3)
# ---------------------------------------------------------------------------

def test_resolve_model_dir_unset_when_env_missing(tmp_path, monkeypatch) -> None:
    make_store(tmp_path)
    monkeypatch.delenv("SANDBOX_MODEL_DIR", raising=False)
    with pytest.raises(ModelCacheError) as exc:
        resolve_and_verify(validated())
    assert exc.value.reason_code == "model_dir_unset"


def test_resolve_model_dir_unset_when_env_empty(tmp_path, monkeypatch) -> None:
    make_store(tmp_path)
    monkeypatch.setenv("SANDBOX_MODEL_DIR", "")
    with pytest.raises(ModelCacheError) as exc:
        resolve_and_verify(validated())
    assert exc.value.reason_code == "model_dir_unset"


def test_resolve_symlink_out_of_root_is_catalog_invalid(tmp_path, monkeypatch) -> None:
    # Contract F3: realpath containment catches symlink escape. The OUTSIDE
    # file's bytes match the catalog pin exactly -- a hash-only gate would pass
    # it; containment must still refuse.
    outside = tmp_path / "outside.bin"
    outside.write_bytes(WEIGHT_BYTES)
    root = tmp_path / "modelroot"
    link = root / "example" / "link.gguf"
    link.parent.mkdir(parents=True)
    os.symlink(outside, link)
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))

    prof = validated(make_catalog(relative_path="example/link.gguf"))
    with pytest.raises(ModelCacheError) as exc:
        resolve_and_verify(prof)
    assert exc.value.reason_code == "catalog_invalid"


def test_resolve_missing_file_is_model_missing(tmp_path, monkeypatch) -> None:
    root = tmp_path / "modelroot"
    root.mkdir()
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))
    with pytest.raises(ModelCacheError) as exc:
        resolve_and_verify(validated())
    assert exc.value.reason_code == "model_missing"


def test_resolve_size_mismatch_before_any_hashing(tmp_path, monkeypatch) -> None:
    root = make_store(tmp_path)
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))
    prof = validated(make_catalog(size_bytes=len(WEIGHT_BYTES) + 1))
    with pytest.raises(ModelCacheError) as exc:
        resolve_and_verify(prof)
    assert exc.value.reason_code == "size_mismatch"
    # size gate precedes checksum work: no sidecar may appear (D11 chain order)
    assert not (root / (REL + ".blsverify.json")).exists()


# ---------------------------------------------------------------------------
# resolve_and_verify -- sidecar lifecycle (D11 verification sidecar cache)
# ---------------------------------------------------------------------------

def test_first_use_full_hash_writes_sidecar(tmp_path, monkeypatch) -> None:
    prof, root = resolve_ready(tmp_path, monkeypatch)
    resolved = resolve_and_verify(prof)

    assert isinstance(resolved, ResolvedModel)
    assert resolved.sha256_verified == "full"
    assert resolved.sha256 == WEIGHT_SHA
    assert resolved.size_bytes == len(WEIGHT_BYTES)
    assert resolved.relative_path == REL
    assert resolved.abs_path == os.path.realpath(str(root / REL))

    sidecar = json.loads((root / (REL + ".blsverify.json")).read_text("utf-8"))
    assert set(sidecar) == {"size", "mtime_ns", "sha256", "verified_at"}
    assert sidecar["sha256"] == WEIGHT_SHA
    assert sidecar["size"] == len(WEIGHT_BYTES)
    assert sidecar["mtime_ns"] == os.stat(root / REL).st_mtime_ns


def test_second_call_uses_cached_fast_path(tmp_path, monkeypatch) -> None:
    prof, _root = resolve_ready(tmp_path, monkeypatch)
    assert resolve_and_verify(prof).sha256_verified == "full"
    assert resolve_and_verify(prof).sha256_verified == "cached"


def test_cached_fast_path_really_skips_hashing(tmp_path, monkeypatch) -> None:
    # Documented trust model: the sidecar is a CACHE, not a security boundary.
    # Same-size corruption with a restored mtime_ns passes the fast path --
    # proving no re-hash happened -- and --verify-full is the paranoia escape.
    prof, root = resolve_ready(tmp_path, monkeypatch)
    resolve_and_verify(prof)  # writes sidecar

    weight = root / REL
    st = os.stat(weight)
    corrupt = b"X" * len(WEIGHT_BYTES)  # same size, different bytes
    weight.write_bytes(corrupt)
    os.utime(weight, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime_ns

    assert resolve_and_verify(prof).sha256_verified == "cached"  # no hash: cached
    with pytest.raises(ModelCacheError) as exc:
        resolve_and_verify(prof, verify_full=True)  # verify_full forces the hash
    assert exc.value.reason_code == "checksum_mismatch"


def test_mtime_change_forces_full_rehash(tmp_path, monkeypatch) -> None:
    prof, root = resolve_ready(tmp_path, monkeypatch)
    resolve_and_verify(prof)
    weight = root / REL
    st = os.stat(weight)
    os.utime(weight, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # touch

    resolved = resolve_and_verify(prof)
    assert resolved.sha256_verified == "full"  # content still matches the pin
    sidecar = json.loads((root / (REL + ".blsverify.json")).read_text("utf-8"))
    assert sidecar["mtime_ns"] == st.st_mtime_ns + 1_000_000  # sidecar refreshed


def test_corrupt_sidecar_forces_full_rehash(tmp_path, monkeypatch) -> None:
    prof, root = resolve_ready(tmp_path, monkeypatch)
    resolve_and_verify(prof)
    (root / (REL + ".blsverify.json")).write_text("{not json", encoding="utf-8")
    assert resolve_and_verify(prof).sha256_verified == "full"


def test_wrong_keys_sidecar_forces_full_rehash(tmp_path, monkeypatch) -> None:
    prof, root = resolve_ready(tmp_path, monkeypatch)
    resolve_and_verify(prof)
    (root / (REL + ".blsverify.json")).write_text(
        json.dumps({"size": len(WEIGHT_BYTES)}), encoding="utf-8"
    )
    assert resolve_and_verify(prof).sha256_verified == "full"


def test_sidecar_sha_disagreeing_with_catalog_forces_full_rehash(
    tmp_path, monkeypatch
) -> None:
    # A tampered sidecar sha must not shortcut verification: cached path
    # requires sidecar sha == catalog sha, else full re-hash (D11).
    prof, root = resolve_ready(tmp_path, monkeypatch)
    resolve_and_verify(prof)
    sidecar_path = root / (REL + ".blsverify.json")
    sidecar = json.loads(sidecar_path.read_text("utf-8"))
    sidecar["sha256"] = "0" * 64
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    resolved = resolve_and_verify(prof)
    assert resolved.sha256_verified == "full"  # file matches catalog -> ok


def test_verify_full_forces_hash_even_with_valid_sidecar(
    tmp_path, monkeypatch
) -> None:
    prof, _root = resolve_ready(tmp_path, monkeypatch)
    resolve_and_verify(prof)
    assert resolve_and_verify(prof, verify_full=True).sha256_verified == "full"


def test_checksum_mismatch_errors_and_never_writes_sidecar(
    tmp_path, monkeypatch
) -> None:
    # D11: never write a sidecar on mismatch -- a poisoned entry must not
    # launder a bad weight into the fast path.
    other = b"different bytes, same length as nothing in particular"
    root = make_store(tmp_path, data=other)
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))
    prof = validated(make_catalog(size_bytes=len(other)))  # size ok, sha wrong

    with pytest.raises(ModelCacheError) as exc:
        resolve_and_verify(prof)
    assert exc.value.reason_code == "checksum_mismatch"
    assert not (root / (REL + ".blsverify.json")).exists()


def test_sidecar_write_failure_is_nonfatal(tmp_path, monkeypatch) -> None:
    # Contract D11: failure to WRITE the sidecar is non-fatal -- verification
    # itself succeeded; the next call just pays the full hash again.
    if os.geteuid() == 0:  # pragma: no cover - root ignores dir permissions
        pytest.skip("permission-based test is meaningless as root")
    prof, root = resolve_ready(tmp_path, monkeypatch)
    weight_dir = (root / REL).parent
    os.chmod(weight_dir, 0o555)  # read+exec only: sidecar create must fail
    try:
        resolved = resolve_and_verify(prof)
        assert resolved.sha256_verified == "full"
        assert not (root / (REL + ".blsverify.json")).exists()
    finally:
        os.chmod(weight_dir, 0o755)


# ---------------------------------------------------------------------------
# ResolvedModel shape
# ---------------------------------------------------------------------------

def test_resolved_model_is_frozen(tmp_path, monkeypatch) -> None:
    prof, _root = resolve_ready(tmp_path, monkeypatch)
    resolved = resolve_and_verify(prof)
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.sha256 = "0" * 64  # type: ignore[misc]


def test_resolved_model_carries_typed_optionals(tmp_path, monkeypatch) -> None:
    root = make_store(tmp_path)
    monkeypatch.setenv("SANDBOX_MODEL_DIR", str(root))
    prof = validated(
        make_catalog(quantization="Q4_K_M", context_length=8192, license="MIT")
    )
    resolved = resolve_and_verify(prof)
    assert resolved.quantization == "Q4_K_M"
    assert resolved.context_length == 8192
    assert resolved.license == "MIT"


def test_resolved_model_optionals_default_none(tmp_path, monkeypatch) -> None:
    prof, _root = resolve_ready(tmp_path, monkeypatch)
    resolved = resolve_and_verify(prof)
    assert resolved.quantization is None
    assert resolved.context_length is None
    assert resolved.license is None


# ---------------------------------------------------------------------------
# ModelCacheError contract
# ---------------------------------------------------------------------------

def test_model_cache_error_is_a_value_error_with_reason_code() -> None:
    err = ModelCacheError("boom", reason_code="model_missing")
    assert isinstance(err, ValueError)
    assert err.reason_code == "model_missing"


def test_model_cache_error_rejects_unknown_reason_code() -> None:
    # runner_missing belongs to RunnerError (base.py), not ModelCacheError (D5).
    with pytest.raises(ValueError):
        ModelCacheError("boom", reason_code="runner_missing")


# ---------------------------------------------------------------------------
# policy.model.example.json (L4-F3 binding docs deliverable)
# ---------------------------------------------------------------------------

def test_policy_model_example_is_policy_legal_and_omits_address_space() -> None:
    # Proves the '_comment' convention (policy ignores '_'-prefixed keys) and
    # the recommended field choices parse via SandboxPolicy.from_mapping.
    raw = json.loads(
        (REPO_ROOT / "policy.model.example.json").read_text(encoding="utf-8")
    )
    assert "address_space_bytes" not in raw  # RLIMIT_AS gates mmap -> ENOMEM
    assert any(k.startswith("_comment") for k in raw)  # rationale ships in-file

    policy = SandboxPolicy.from_mapping(raw)
    assert policy.address_space_bytes is None
    assert policy.allow_exec is True
    assert policy.allowed_commands == ["llama-completion", "llama-cli"]
    assert "PATH" in policy.env_allowlist  # D9: binary resolves on child PATH
    assert "HOME" in policy.env_allowlist
    assert "TMPDIR" in policy.env_allowlist
    assert policy.network == "offline"
    assert policy.timeout_seconds == 300
    assert policy.cpu_seconds is not None  # sized, not the lethal 10 s default
    assert policy.max_processes is not None
    assert policy.max_file_size_bytes is not None
