# Public-Readiness Audit - broker-lane-sandbox

> **HISTORICAL SNAPSHOT (point-in-time).** This audit was performed on **2026-06-21**,
> during the **P1 era** (before P2/P3/P4). Its figures - e.g. the 42-test spot-check -
> were **correct as of that date** and are preserved below unchanged as a historical
> record; they are **not** the current contract. For the current, authoritative contract
> see the [README](../README.md), [MANUAL](MANUAL.md), [THREAT_MODEL](THREAT_MODEL.md),
> and the [P2 broker seam](P2_BROKER_LOOM_SEAM.md). P2/P3/P4 are now delivered and the
> suite has grown well past the 42-test figure below (286 tests as of 2026-07-23, head
> `19091b1`).

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
| **Private publication** | ✅ **READY** - zero blockers; published to private `main`. |
| **Public visibility (later)** | ✅ **READY** - the prior blocker (no LICENSE) is resolved (MIT `LICENSE` added; `pyproject` metadata polished). No remaining blockers. |

> **Update (2026-06-21):** the MIT `LICENSE` and `pyproject` metadata (`readme`/`license`/
> `authors`/`urls`) have been added with operator approval. The repo is **kept PRIVATE** by
> instruction, but is now technically clean to flip to public whenever the operator chooses.

---

## 1. Public value proposition

A small (~600 LOC), **zero-dependency, stdlib-only** Python tool that answers one focused
question well: *"how do I run an arbitrary subprocess as safely as a personal tool reasonably
can, and get a machine-readable JSON verdict for every outcome - including refusals?"*

It offers a reusable **default-deny execution pattern** with working code:
- a **bare-name command allow-list** that defeats the path-bypass class,
- an **empty-baseline environment** build with a secret-name drop heuristic,
- **offline-by-default** proxy stripping,
- **POSIX rlimits + a process-group-killing wall-clock timeout** with a time-boxed post-kill drain,
- all behind a stable **`bls` CLI / JSON seam**.

The honest framing ("not a kernel sandbox") is part of the value: it is an auditable *pattern*
for factoring the execution-safety concern out of an orchestrator behind a contract - useful
to anyone building agent/broker systems. The **model-artifact invariant** (weights never in
git, enforced by a fail-closed pre-commit + CI guard) is a transferable hygiene pattern on its own.

## 2. Remaining private / local assumptions

None rise to a publication blocker; the tree is well-sanitized:
- **POSIX assumption** - rlimits / `setsid` / `killpg` are POSIX; the code degrades gracefully
  on non-POSIX and the docs say so. Mild "operator runs Linux/WSL" assumption, **documented**.
- **Use-case hint** - `OPENROUTER_API_KEY` appears as an illustrative env name (in a comment,
  a doc example with the dummy value `dummy-not-a-real-key`, and a test fixture). It quietly
  reveals the original OpenRouter-using broker context; this is acceptable and arguably useful.
- **Sibling reference** - `project-broker-loom` is referenced as a (separate, private) sibling.
  The README relationship table is self-contained, so the sandbox reads as genuinely standalone.
- **No operator PII / private paths** - the operator's private memory/vault machinery does
  **not** bleed into this repo (see §4).

## 3. Secrets / runtime / model-artifact scan - **CLEAN**

- **Secret values:** ZERO. Regex sweeps (`sk-`, `gh[posru]_`, `AKIA`, `AIza`, `xox[baprs]-`,
  JWT `eyJ…`, `-----BEGIN PRIVATE KEY-----`) and a high-entropy sweep found no matches. Every
  credential-shaped hit is a **type name** (the `SECRET_NAME_RE` blocklist and its docs) or the
  explicit dummy `OPENROUTER_API_KEY=dummy-not-a-real-key`.
- **Runtime creds / files:** no `.env`, `.pem`, key, or credential files tracked.
- **Model artifacts:** `check_model_artifacts.py --tracked` → **exit 0**. No forbidden weight
  extension or cache-dir file tracked; **git history** shows no model blob ever committed and no
  >1 MB blob. Example configs use placeholder values (`example.invalid`, `0000…` sha, size 0).

## 4. Local-path / internal-operator-reference scan - **CLEAN**

- **Absolute local paths** (Windows/WSL home and temp roots): ZERO hits in the tracked tree + docs.
- **Internal operator systems** (the operator's private memory stores, vaults, and sibling
  projects): ZERO real references. (An earlier `docs/model-cache-policy.md` mention of an
  internal memory location was **genericized** during this pass; the remaining word "handover"
  is ordinary prose, not an operator-system reference.)
- **`broker-loom` references** are present and **expected** (documented sibling) - not leakage.
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
| install/dev, policy validation, running a job, reading result JSON, preflight, tests, troubleshooting | MANUAL §1-9 | ✔ |
| threat model (assets, boundaries, attacker model, mitigations, limitations) | THREAT_MODEL.md | ✔ |

Spot-checks passed: "42 tests" is exact, CI uses `--tracked`, `schema_version=1`, the default
env allow-list tuple matches `policy.py`. Tone is appropriately humble.

## 6. License - RESOLVED

The repo is now licensed under the **MIT License** ([`LICENSE`](../LICENSE), copyright
`fbratten`), and `pyproject.toml` declares `license = { text = "MIT" }` plus `readme`,
`authors`, and `[project.urls]`. MIT was chosen as the simplest permissive fit for a small,
stdlib-only, personal-use utility, consistent with the "as-is, no support" framing. Added with
operator approval (2026-06-21).

## 7. Exact blockers before switching to PUBLIC - NONE

The previously-identified single blocker (missing LICENSE) is **resolved**. No remaining
blockers: secrets/runtime/model-artifact and local-path/internal-system scans are clean, docs
are accurate and complete, and the license + metadata are in place. The repo is **kept
PRIVATE** by operator instruction but is technically ready to be flipped public at any time.

---

*This audit is advisory. The repo remains PRIVATE; no license or visibility change was made.*
