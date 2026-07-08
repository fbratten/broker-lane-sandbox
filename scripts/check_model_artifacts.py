#!/usr/bin/env python3
"""Model-artifact guard for broker-lane-sandbox (INVARIANT-1).

Refuses to let model weight blobs (or oversize files, or files under a known
model-cache directory) enter version control. Used two ways:

  * pre-commit hook:  check_model_artifacts.py --staged
  * CI / audit:       check_model_artifacts.py --tracked

Exit code 0 = clean, 5 = at least one violation (matches the sandbox's
"model_artifact_violation" result class). Stdlib only.

Configuration:
  --max-mb N      / env BLS_GUARD_MAX_MB   size cap in MB (default 5.0)
  --repo PATH                              repo root (default: git toplevel)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

# Weight / serialized-model blob extensions that must NEVER be tracked.
FORBIDDEN_EXT = {
    ".gguf", ".safetensors", ".bin", ".pt", ".pth",
    ".onnx", ".mlmodel", ".ckpt", ".tflite",
}

# Directory names that are runtime cache only -- nothing under them is tracked.
# Matched as a path SEGMENT at any depth (e.g. tests/models/x.dat), mirroring the
# unanchored .gitignore patterns; a root-only prefix match would let a nested
# cache dir slip past the guard.
FORBIDDEN_DIR_NAMES = (
    "models", "model-cache", "runtime", ".cache",
    ".huggingface", "hf-cache", "ollama", "llama.cpp",
)

VIOLATION_EXIT = 5
GUARD_ERROR_EXIT = 2   # git/setup failure -- fail loud (closed), never report clean


class GuardError(RuntimeError):
    """A git/setup failure. The guard must fail closed, not silently report clean."""


def _git(repo: str, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        # Do NOT swallow a git failure into empty output -- that would make the
        # guard report a clean tree precisely when git is broken or run from the
        # wrong directory (a fail-open bypass of INVARIANT-1).
        raise GuardError(
            f"git {' '.join(args)} failed (exit {out.returncode}) in {repo!r}: "
            f"{out.stderr.strip()}"
        )
    return out.stdout


def _toplevel() -> str:
    top = _git(".", "rev-parse", "--show-toplevel").strip()
    if not top:
        raise GuardError("could not resolve git toplevel (not a git repository?)")
    return top


def _staged_files(repo: str) -> list[str]:
    raw = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACM")
    return [p for p in raw.splitlines() if p.strip()]


def _tracked_files(repo: str) -> list[str]:
    raw = _git(repo, "ls-files")
    return [p for p in raw.splitlines() if p.strip()]


def _violations(repo: str, paths: list[str], max_bytes: int) -> list[str]:
    found: list[str] = []
    for rel in paths:
        low = rel.lower()
        _, ext = os.path.splitext(low)
        if ext in FORBIDDEN_EXT:
            found.append(f"{rel}  [forbidden model-weight extension '{ext}']")
            continue
        if any(seg in FORBIDDEN_DIR_NAMES for seg in low.split("/")[:-1]):
            found.append(f"{rel}  [under a runtime model-cache directory]")
            continue
        abs = os.path.join(repo, rel)
        try:
            size = os.path.getsize(abs)
        except OSError:
            size = 0
        if size > max_bytes:
            mb = size / (1024 * 1024)
            found.append(f"{rel}  [oversize {mb:.1f} MB > cap]")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Guard against model artifacts in git.")
    ap.add_argument("--staged", action="store_true", help="check staged files (pre-commit)")
    ap.add_argument("--tracked", action="store_true", help="check all tracked files (audit/CI)")
    ap.add_argument("--max-mb", type=float,
                    default=float(os.environ.get("BLS_GUARD_MAX_MB", "5.0")))
    ap.add_argument("--repo", default=None)
    args = ap.parse_args(argv)

    max_bytes = int(args.max_mb * 1024 * 1024)

    # Default to both scopes when neither flag is given. Any git failure here is a
    # hard error (fail closed) -- never a silent clean pass.
    try:
        repo = args.repo or _toplevel()
        scopes = []
        if args.staged or not (args.staged or args.tracked):
            scopes.append(("staged", _staged_files(repo)))
        if args.tracked or not (args.staged or args.tracked):
            scopes.append(("tracked", _tracked_files(repo)))
    except (GuardError, FileNotFoundError, OSError) as exc:
        # FileNotFoundError covers git missing from PATH entirely; OSError covers
        # other exec failures. All fail CLOSED with a clear message (never exit 0).
        sys.stderr.write(f"GUARD ERROR (failing closed): {exc}\n")
        return GUARD_ERROR_EXIT

    all_violations: list[str] = []
    for label, paths in scopes:
        for v in _violations(repo, paths, max_bytes):
            all_violations.append(f"({label}) {v}")

    if all_violations:
        sys.stderr.write(
            "REFUSED: model artifacts must never enter git (INVARIANT-1).\n"
            f"  cap = {args.max_mb} MB; override with BLS_GUARD_MAX_MB.\n"
        )
        for v in all_violations:
            sys.stderr.write("  - " + v + "\n")
        sys.stderr.write(
            "Fix: keep weights in the runtime cache (config/env-driven path),\n"
            "track only manifests/checksums/URLs/license notes. See docs/model-cache-policy.md.\n"
        )
        return VIOLATION_EXIT

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
