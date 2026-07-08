# Broker-Lane Finalization — Scope (lane: broker-lane-finalization-4lqiio)

**Date opened:** 2026-07-08
**Branch:** `claude/broker-lane-finalization-4lqiio`
**Repo:** `fbratten/broker-lane-sandbox`
**Operator request:** finalize the broker-lane-sandbox project; document progress in
this slug-derived lane folder; loop iterations until finalized; use workflow
orchestration where possible.

## Clarification (5PP-1)

"Finalization" is interpreted as: bring broker-lane-sandbox to a **verified,
drift-free, internally consistent finalized state** for its delivered scope
(P0 repo invariants, P1 safe-exec core, P2 broker-loom seam). It does NOT mean
implementing new phases.

## In scope (5PP-2)

- Full verification baseline: pytest suite, model-artifact guard (`--tracked`),
  CLI smoke (`bls version` / `preflight` / `run` / `models` / `broker-run`).
- Documentation drift repair: stale counts/status/roadmap claims across
  README.md, docs/MANUAL.md, docs/THREAT_MODEL.md, docs/P2_BROKER_LOOM_SEAM.md,
  docs/model-cache-policy.md, policy.example.json.
- Multi-agent audit (workflow): correctness review of `src/`, doc/code
  consistency, test-contract gaps, packaging/CI readiness; adversarial
  verification of findings before any fix.
- Conservative fixes for confirmed defects found by the audit.
- Lane progress documentation in this folder (per-iteration log).
- Commits per meaningful step; final push to the designated branch.

## Out of scope (5PP-2)

- P3 (local/quantized model runners) and P4 (streaming) — planned phases,
  not authorized by a finalization request.
- Repo visibility change (stays PRIVATE per operator instruction in
  docs/PUBLIC_READINESS_AUDIT.md).
- Rewriting the dated 2026-06-21 audit document (historical record; a dated
  addendum is permitted, silent rewriting is not).
- Any change to project-broker-loom, project-backlog, or control-center-ops
  beyond what the operator separately authorizes.
- Deleting anything (archive-only conventions apply estate-wide).

## Constraints preserved

- Default-deny posture and fail-loud behavior of the sandbox are untouched
  unless a confirmed defect requires a fix (then: minimal, tested, documented).
- INVARIANT-1 (no model weights in git) — guard must stay green after every step.
- Zero runtime dependencies for the core (stdlib-only).
