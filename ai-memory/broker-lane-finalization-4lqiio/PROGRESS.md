# Broker-Lane Finalization — Progress Log

Lane: `broker-lane-finalization-4lqiio` · Branch: `claude/broker-lane-finalization-4lqiio`
Newest iteration at the bottom. Every iteration records: what ran, what was found,
what changed, verification state.

---

## Iteration 1 — 2026-07-08 — Audit + verification baseline

**Ran:**
- Read state: README, docs/PUBLIC_READINESS_AUDIT.md, pyproject.toml, git log,
  sibling context (project-broker-loom NEXT.md `bls_consumer` proof slice,
  project-backlog entries BACKLOG-122/137/139).
- `python3 -m pytest tests/ -q` → **70 passed**.
- `python3 scripts/check_model_artifacts.py --tracked` → **exit 0** (INVARIANT-1 green).
- `PYTHONPATH=src python3 -m broker_lane_sandbox.cli version` →
  `{"name": "broker-lane-sandbox", "version": "0.1.0", "schema_version": 1}`.

**Found:**
- Delivered scope confirmed: P0 + P1 + P2 done; P3/P4 planned (out of scope here).
- **Doc drift:** README (2 places) and docs/MANUAL.md claim "42 tests"; actual
  suite is 70 (PR #3 seam took it to 49, PR #5 resource-limit hardening to 70).
- Public-readiness audit (2026-06-21): no blockers; repo intentionally PRIVATE.
- BACKLOG-137 notes a stray operator-local branch (`cc/bls-public-docs-deepening-s01`,
  no upstream) — operator-machine state, not reachable from this environment;
  explicitly deferred by operator directive 2026-07-07. Not actioned here.

**Changed:** created this lane folder (SCOPE.md, PROGRESS.md). No code/doc fixes yet.

**Verification state:** baseline green (tests 70/70, guard 0, CLI smoke OK).

**Next:** multi-agent audit workflow (doc-drift sweep, correctness review,
test-contract gaps, packaging/CI readiness), adversarial verify, then fix loop.

---

## Iteration 2 — 2026-07-08 — Audit workflow + first fixes

**Ran:**
- Fixed the iteration-1 drift immediately: README/MANUAL test count 42 -> 70.
- Full CLI smoke: `preflight` (0), `run` ok (0), `run` denied path-argv0 (2),
  `run` denied not-allowlisted (2), `models` (0).
- Full P2 seam smoke: `broker-run` ok (0), timeout (124, child exit -9,
  request_id echoed), malformed request (request_error, 2, request_id preserved).
- Audit workflow `bls-finalization-audit` (run wf_f68d113b-643): 32 agents,
  4 audit dimensions, per-finding skeptic verification.
  28 raw findings -> **27 confirmed, 1 refuted**. Full record: FINDINGS.md.

**Found (headlines; see FINDINGS.md F01-F27):**
- Doc drift (F01-F07): README body still calls P2 "not yet built" in two places;
  `broker-run` missing from README CLI section and MANUAL exit-code table; stale
  P2 result fixture (missing `max_file_size_bytes`); wrong request_id-null claim
  in seam doc; starter-policy description and layout list one field/module behind.
- Correctness (F08-F10): non-UTF-8 child output raises UnicodeDecodeError out of
  the executor (contract violation: every outcome must be an ExecResult);
  empty-string `env_passthrough_prefixes` entry passes the ENTIRE environment;
  malformed catalog profile crashes `bls models` with AttributeError.
- Test gaps (F11-F21): stdin, request-level timeout/working_dir overrides,
  exit codes 1/124 at the CLI boundary, schema_version mismatch, network=online,
  env_passthrough_prefixes, empty-argv gate, missing-exe spawn_error,
  unparseable request file, run --timeout/--cwd, preflight exit-1 pinning.
- Packaging/CI (F22-F27): pre-commit guard hook tracked WITHOUT the executable
  bit (documented enablement silently no-ops); CI pins 3.12 only vs
  requires-python >=3.10; `bls models` default catalog path broken on installed
  copies; CI never pip-installs the package; no [build-system] table; artifact
  guard only matches cache dirs at repo root while .gitignore matches any depth.

**Changed:** commit `265304c` (test-count sync), FINDINGS.md added.

**Verification state:** tests 70/70 after doc sync; guard green.

**Next:** fix loop — iteration 3: doc-drift batch (F01-F07); iteration 4:
correctness defects (F08-F10) with regression tests; iteration 5: test-gap
batch (F11-F21); iteration 6: packaging/CI (F22-F27); iteration 7: final
verification + handover + push.
