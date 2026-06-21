# Public-Readiness Audit — broker-lane-sandbox

**Date:** 2026-06-21 · **Repo:** `fbratten/broker-lane-sandbox` (currently **PRIVATE**) ·
**Audited HEAD:** P1 + publication docs (pre-publish working tree).

**Method:** 3-lens adversarial audit (value/docs-quality, secrets/runtime/artifacts,
local-path/leak/license) over the full tracked tree plus the publication docs, cross-checked
with a deterministic local scan (grep + `git ls-files` + `check_model_artifacts.py --tracked`
+ git-history blob scan).

---

## Final verdict

| Target | Verdict |
|--------|---------|
| **Private publication (now)** | ✅ **READY** — zero blockers; safe to push to private `main`. |
| **Public visibility (later)** | ⚠️ **READY_AFTER_FIXES** — one required fix: **add a LICENSE**. Everything else is clean. |

> This repo is being published **private**. The single "blocker" below is a blocker to
> *going public*, which is **not** being done now. Per instruction, no license was added.

---

## 1. Public value proposition

A small (~600 LOC), **zero-dependency, stdlib-only** Python tool that answers one focused
question well: *"how do I run an arbitrary subprocess as safely as a personal tool reasonably
can, and get a machine-readable JSON verdict for every outcome — including refusals?"*

It offers a reusable **default-deny execution pattern** with working code:
- a **bare-name command allow-list** that defeats the path-bypass class,
- an **empty-baseline environment** build with a secret-name drop heuristic,
- **offline-by-default** proxy stripping,
- **POSIX rlimits + a process-group-killing wall-clock timeout** with a time-boxed post-kill drain,
- all behind a stable **`bls` CLI / JSON seam**.

The honest framing ("not a kernel sandbox") is part of the value: it is an auditable *pattern*
for factoring the execution-safety concern out of an orchestrator behind a contract — useful
to anyone building agent/broker systems. The **model-artifact invariant** (weights never in
git, enforced by a fail-closed pre-commit + CI guard) is a transferable hygiene pattern on its own.

## 2. Remaining private / local assumptions

None rise to a publication blocker; the tree is well-sanitized:
- **POSIX assumption** — rlimits / `setsid` / `killpg` are POSIX; the code degrades gracefully
  on non-POSIX and the docs say so. Mild "operator runs Linux/WSL" assumption, **documented**.
- **Use-case hint** — `OPENROUTER_API_KEY` appears as an illustrative env name (in a comment,
  a doc example with the dummy value `dummy-not-a-real-key`, and a test fixture). It quietly
  reveals the original OpenRouter-using broker context; this is acceptable and arguably useful.
- **Sibling reference** — `project-broker-loom` is referenced as a (separate, private) sibling.
  The README relationship table is self-contained, so the sandbox reads as genuinely standalone.
- **No operator PII / private paths** — the operator's private memory/vault machinery does
  **not** bleed into this repo (see §4).

## 3. Secrets / runtime / model-artifact scan — **CLEAN**

- **Secret values:** ZERO. Regex sweeps (`sk-`, `gh[posru]_`, `AKIA`, `AIza`, `xox[baprs]-`,
  JWT `eyJ…`, `-----BEGIN PRIVATE KEY-----`) and a high-entropy sweep found no matches. Every
  credential-shaped hit is a **type name** (the `SECRET_NAME_RE` blocklist and its docs) or the
  explicit dummy `OPENROUTER_API_KEY=dummy-not-a-real-key`.
- **Runtime creds / files:** no `.env`, `.pem`, key, or credential files tracked.
- **Model artifacts:** `check_model_artifacts.py --tracked` → **exit 0**. No forbidden weight
  extension or cache-dir file tracked; **git history** shows no model blob ever committed and no
  >1 MB blob. Example configs use placeholder values (`example.invalid`, `0000…` sha, size 0).

## 4. Local-path / internal-operator-reference scan — **CLEAN**

- **Absolute local paths** (Windows/WSL home and temp roots): ZERO hits in the tracked tree + docs.
- **Internal operator systems** (the operator's private memory stores, vaults, and sibling
  projects): ZERO real references. (An earlier `docs/model-cache-policy.md` mention of an
  internal memory location was **genericized** during this pass; the remaining word "handover"
  is ordinary prose, not an operator-system reference.)
- **`broker-loom` references** are present and **expected** (documented sibling) — not leakage.
- **Commit metadata** uses a **pseudonymous handle/email**, not real-name PII.

## 5. Docs quality assessment

Strong and unusually honest for a personal MVP; coverage of every required topic verified
**accurate against the code**:

| Topic | Where | Status |
|------|-------|--------|
| what / why | README | ✔ |
| relationship to broker-loom | README table + rule | ✔ |
| status / phase | README + MANUAL roadmap (consistent) | ✔ |
| safe-exec model + default-deny (5 ordered gates) | README + MANUAL + executor docstring | ✔ (docstring gate list corrected this pass) |
| env scrubbing (secret regex matches code) | README + MANUAL | ✔ |
| network policy (env-level, best-effort) | README + MANUAL | ✔ |
| timeout / process controls (setsid, killpg, rlimits, drain) | README + MANUAL + THREAT_MODEL | ✔ |
| model-artifact invariant (lists match code constants) | README + model-cache-policy | ✔ (exit-code note added this pass) |
| CLI examples (match cli.py exit codes 0/1/2/124) | README + MANUAL | ✔ |
| out-of-scope | README + MANUAL §6 + THREAT_MODEL §3/§6 | ✔ |
| install/dev, policy validation, running a job, reading result JSON, preflight, tests, troubleshooting | MANUAL §1–9 | ✔ |
| threat model (assets, boundaries, attacker model, mitigations, limitations) | THREAT_MODEL.md | ✔ |

Spot-checks passed: "42 tests" is exact, CI uses `--tracked`, `schema_version=1`, the default
env allow-list tuple matches `policy.py`. Tone is appropriately humble.

## 6. License recommendation (recommendation only — not applied)

The repo currently has **no LICENSE file** (README has a prose "personal-use, no guarantees"
note only; default is therefore all-rights-reserved).
- **If made public for reuse:** **MIT** is the simplest permissive fit for a small, stdlib-only,
  personal-use utility, and matches the existing "as-is, no support" framing.
- **If reuse should be forbidden:** keep it closed and add an explicit *all-rights-reserved*
  `LICENSE` notice so the intent is deliberate rather than implicit.

**No license was added or changed** in this audit (operator approval required).

## 7. Exact blockers before switching to PUBLIC

1. **Add a `LICENSE` file** (MIT recommended) and, optionally, set `pyproject [project].license`.
   This is the **only** blocker — nothing else in the tree would leak or embarrass if public.

**Optional (non-blocking) polish for a public repo:**
- Add `readme`, `authors`, and `[project.urls]` to `pyproject.toml` (cosmetic metadata).

---

*This audit is advisory. The repo remains PRIVATE; no license or visibility change was made.*
