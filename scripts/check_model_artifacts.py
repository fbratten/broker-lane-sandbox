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

# Directory prefixes that are runtime cache only -- nothing under them is tracked.
FORBIDDEN_DIR_PREFIXES = (
    "models/", "model-cache/", "runtime/", ".cache/",
    ".huggingface/", "hf-cache/", "ollama/", "llama.cpp/",
)

VIOLATION_EXIT = 5


def _git(repo: str, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True,
    )
    return out.stdout


def _toplevel() -> str:
    return _git(".", "rev-parse", "--show-toplevel").strip() or "."


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
        if any(low.startswith(p) for p in FORBIDDEN_DIR_PREFIXES):
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

    repo = args.repo or _toplevel()
    max_bytes = int(args.max_mb * 1024 * 1024)

    # Default to both scopes when neither flag is given.
    scopes = []
    if args.staged or not (args.staged or args.tracked):
        scopes.append(("staged", _staged_files(repo)))
    if args.tracked or not (args.staged or args.tracked):
        scopes.append(("tracked", _tracked_files(repo)))

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
