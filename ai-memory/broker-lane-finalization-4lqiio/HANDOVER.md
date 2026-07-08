# Broker-Lane Finalization — Handover

**Date:** 2026-07-08
**Lane:** `broker-lane-finalization-4lqiio` · **Branch:** `claude/broker-lane-finalization-4lqiio`
**Repo:** `fbratten/broker-lane-sandbox` (only repo changed; the sibling repos were read for context only)

---

## 1. What was done

Finalization of broker-lane-sandbox's delivered scope (P0 repo invariants, P1
safe-exec core, P2 broker-loom seam): a full audit-verify-fix loop, no new phases.

- **Baseline verification** (it. 1-2): 70/70 tests, INVARIANT-1 guard clean, full
  CLI + P2 seam smoke (ok / denied / timeout / request_error paths, exit codes
  0/2/124, request_id echo) — all green before any change.
- **Audit workflow** (32 agents, 4 dimensions, per-finding skeptic verification):
  28 raw findings -> 27 confirmed (FINDINGS.md F01-F27), 1 correctly refuted.
- **Fix loop** (it. 3-6): doc-drift batch, 3 code defects, 11 contract-test gaps,
  6 packaging/CI/guard issues — each batch committed with tests re-run.
- **Verification workflow** (3 agents): confirmed all 27 findings resolved in the
  working tree; surfaced 5 residual issues (1 genuine missed bug, 1 regression
  introduced by a fix, 3 doc/hygiene) — all fixed in iteration 8.

## 2. Current state

- **Suite: 96 tests, all pass** (was 70). Guard `--tracked` exit 0.
- Notable code fixes on this branch:
  - executor decodes child output UTF-8 + `errors="replace"` (non-UTF-8 output
    and non-UTF-8 *request files* both return results, never tracebacks);
  - empty `env_passthrough_prefixes` entry can no longer pass the entire
    environment through (PolicyError + envscrub defense in depth);
  - `bls models`: malformed catalog profiles fail loud with PolicyError; missing
    catalog returns clean JSON + exit 2 on installed copies; process-substitution
    paths still work;
  - artifact guard matches forbidden cache dirs at any depth (closes the
    `git add -f tests/models/w.dat` bypass);
  - `.githooks/pre-commit` now tracked executable (100755) — it was silently
    ignored by git before, so the documented enablement did nothing.
- Packaging/CI: `[build-system]` table added; CI matrix 3.10-3.13 + an
  install/entry-point smoke step; `build/`/`dist/` gitignored.
- Docs (README/MANUAL/seam doc/fixtures) are drift-free against the code as of
  this branch, including the previously-undocumented `bls broker-run` command.
- Commits (oldest first): `b4024eb` lane open · `265304c` count sync ·
  `4e9f0fa` findings record · `3f5a3c3` doc drift · `514c0b9` code defects ·
  `a23e2bd` contract tests · `53937d9` packaging/CI · `f8101f3` count sync +
  lane log · `3665a89` residual fixes · closure commit (this one).

## 3. Pending / next (operator decisions, NOT started here)

- **P3** (local/quantized model runners) and **P4** (streaming) — planned phases,
  out of finalization scope.
- **Broker-loom-side seam consumption** — `broker/bls_consumer.py` exists in
  project-broker-loom as an opt-in proof slice, not wired into any lane.
- **Repo visibility** — still PRIVATE by operator instruction; the 2026-06-21
  public-readiness audit found no blockers.
- **BACKLOG-137** — stray `cc/bls-public-docs-deepening-s01` branch exists only
  on the operator's machine (no upstream); unreachable from this environment;
  operator-deferred 2026-07-07.
- **PR for this branch** — not created (not requested).

## 4. Decisions made

| Decision | Rationale |
|---|---|
| "Finalization" = verify + de-drift + harden delivered P0-P2; no new phases | Finalization request is not implementation authorization for P3/P4 (broker-loom process rule applied estate-wide) |
| Lane docs live in `ai-memory/broker-lane-finalization-4lqiio/` in THIS repo | Operator clarified the target project is broker-lane-sandbox; folder name derived from the branch slug |
| 2026-06-21 audit doc left untouched | Dated historical record; living docs (README/MANUAL) were fixed instead |
| `errors="replace"` + `encoding="utf-8"` rather than bytes-capture | Keeps ExecResult JSON-string contract and stays deterministic cross-platform; raw-bytes preservation documented as a non-goal |
| `bls models` catches read errors instead of pre-checking `is_file()` | Preserves fail-loud for malformed content while fixing installed-copy traceback WITHOUT breaking non-regular-file paths |
| Guard matches cache-dir names as segments at any depth | Mirrors .gitignore's unanchored patterns; root-only prefix match was a real bypass |
| Repo stays PRIVATE; no PR opened | Operator instruction (visibility) / not requested (PR) |
