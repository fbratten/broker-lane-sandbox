# broker-lane-sandbox

**Safe execution boundary for broker lanes, plus the local/quantized-model runtime
boundary.** A small, dependency-free, default-deny sandbox that wraps the act of
*running something* — a subprocess today, a local model or agent lane tomorrow — and
returns a machine-readable JSON result. A **separate project** from `project-broker-loom`.

> Status: **P2 (broker-loom ↔ sandbox CLI/JSON seam) complete — merged in #3 (`2a80000`).**
> The sandbox-side seam is delivered; broker-loom-side consumption is the next slice. P1
> safe-exec core remains adversarially reviewed. Personal-use MVP — fail-loud, no
> enterprise/kernel hardening, no backward-compatibility promises.

---

## What it is

`broker-lane-sandbox` is the component that owns **how a command is executed safely**.
It takes a policy (what is allowed) and an `argv`, and runs it under a strict
**default-deny** posture: nothing runs, no environment leaks, no network is assumed,
and resource use is bounded — unless the policy explicitly grants it. Every outcome,
including a refusal, comes back as a JSON `ExecResult` rather than an exception or a
process crash.

It is reached through a stable **CLI/API seam** (`bls`, JSON in / JSON out), never
imported as a library by its caller. That keeps the trust boundary explicit and lets
the executor evolve (local model runners, streaming) without changing the contract.

## Why it exists

The broker (`broker-loom`) decides *what* should happen — it routes tasks between a
spec/verify lane and an execution lane and tracks state. But the broker must **not**
execute untrusted-ish work inside its own process. Execution needs its own hardened
boundary with one job: run the thing safely, scrub what it can see, bound what it can
consume, and report honestly. Folding that into the broker would entangle
orchestration with process/credential/model-weight handling. So it lives here, behind
a contract, where the execution-safety concern can be reviewed and hardened on its own.

## Relationship to broker-loom

| | **broker-loom** (`project-broker-loom`) | **broker-lane-sandbox** (this repo) |
|---|---|---|
| Owns | orchestration, task state, ledger, routing, verifier-lane choice, handoff parsing, repair loops | safe execution, env scrubbing, network policy, process/resource limits, model-cache boundary |
| Executes work in-process? | **No** | **Yes** — but only under a default-deny policy |
| Integration | calls the sandbox over the **CLI/API contract** (JSON in/out) | exposes that contract; never imports broker-loom |

Architectural rule: in broker-loom, the **OpenRouter lane is a verifier/spec lane and
never executes**. The **execution** lanes are what this sandbox wraps. The two repos
are developed and versioned independently; broker-loom integration is **P2** (not yet
built).

## What it does / does NOT do

**Does:**
- Refuses everything by default; runs only allow-listed **bare** command names.
- Builds the child environment from empty, passing only allow-listed names; drops
  secret-looking variables even if allow-listed.
- Treats the network as **offline** by default (strips proxy variables; signals
  cooperating runners to stay offline).
- Bounds the child with opt-in POSIX rlimits (CPU / address-space / processes /
  per-file write size) and a wall-clock timeout that kills the whole process group.
- Returns a JSON `ExecResult` for every outcome — including denials and spawn errors.
- Keeps model **weights out of git** and resolves them from an env-driven runtime cache.

**Does NOT (by design — these are out of scope, not bugs):**
- It is **not** a kernel/container sandbox. Network "offline" is best-effort
  env-level neutralization, not a network namespace; filesystem access is not jailed.
- It does **not** pin command identity (no `realpath`/hash of the binary) — it gates
  the **invocation name** and resolves it on `PATH`.
- It does **not** download or run real models (P3); it does **not** stream (P4); it
  does **not** yet integrate with broker-loom (P2).
- It is **not** enterprise-grade or multi-tenant; it is a single-operator personal tool.

## Safe-exec model (default-deny)

Everything is forbidden until a policy explicitly allows it:

- `allow_exec` is `false` → **no process spawns at all**.
- `allowed_commands` is empty → **no executable is permitted**.
- Commands are allow-listed by **bare name** (no path component) and resolved on
  `PATH`. A path-bearing `argv[0]` like `/tmp/evil/python3` is **refused**, so an
  allow-listed *name* can never front for an arbitrary file.
- `network` defaults to `offline`; the child env starts **empty**.

Execution gates run in order, all **before** any spawn:

1. non-empty `argv` → else `denied`
2. `allow_exec` is true → else `denied`
3. `argv[0]` is a bare name → else `denied`
4. `argv[0]` is in `allowed_commands` → else `denied`
5. `working_dir` (if set) exists → else `spawn_error`

Then the child runs with a scrubbed env, an isolated session (`setsid`), configured
rlimits, and a wall-clock timeout. On timeout the **whole process group** is killed,
and the recovery read is time-boxed so a descendant that escaped the group can't pin
the call open. `stdout`/`stderr` are captured and truncated to the policy cap. **Policy
denials are results, not crashes** — only genuinely unexpected internal failures raise.

`ExecResult` (JSON) carries: `status` (`ok` / `exit_nonzero` / `denied` / `timeout` /
`spawn_error`), `ok`, `argv`, `reason`, `exit_code`, `stdout`, `stderr`, `duration_ms`,
`truncated`, `network`, `env_keys` (names only — never values), and `limits`. See
[`docs/MANUAL.md`](docs/MANUAL.md) for the full field table.

## Model-artifact invariant (INVARIANT-1)

Downloaded / local / quantized model weights are **runtime cache only** — never in git:

- Forbidden in git: `.gguf`, `.safetensors`, `.bin`, `.pt`, `.pth`, `.onnx`,
  `.mlmodel`, `.ckpt`, `.tflite`, HuggingFace cache, Ollama blobs, `llama.cpp` builds,
  and anything under a runtime cache dir (`models/`, `model-cache/`, `runtime/`, …).
- Tracked instead: **manifests / checksums / URLs / license notes** only
  (`models.example.yaml`, `MODEL-MANIFEST.example.md`).
- Local model paths are **env-driven** (`SANDBOX_MODEL_DIR`), never committed.
- Enforced by `scripts/check_model_artifacts.py` as a **pre-commit hook** and in **CI**
  (`--tracked`, see `.github/workflows/ci.yml`); even `git add -f weights.gguf` is
  refused. The guard **fails closed** — if git itself errors or is absent, it exits
  non-zero rather than reporting a (false) clean tree.

See `docs/model-cache-policy.md`. Tests use **fake fixtures / mocked runners** — never
a real model file.

## Environment scrubbing

The child environment is built **from empty**:

- Only names in `env_allowlist` (exact) or matching an `env_passthrough_prefixes`
  entry are passed through. A minimal safe baseline (`PATH`, `HOME`, `LANG`, …) is the
  default allow-list.
- **Secret-looking** names (matching `KEY` / `TOKEN` / `SECRET` / `PASSWORD` /
  `PASSWD` / `CREDENTIAL` / `PRIVATE` / `SESSION` / `COOKIE` / `AUTH`) are **dropped
  even if allow-listed**, unless the policy sets `allow_secret_env: true`. Dropped
  names are reported (names only) in the result.
- `ExecResult.env_keys` lists the names the child received — **never the values**.

## Network policy

Default `network: "offline"`:

- Proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `FTP_PROXY`, and lowercase
  forms) are stripped from the child env.
- `NO_PROXY=*` and `SANDBOX_NETWORK=offline` are set as a clear signal to cooperating
  runners that they must not reach the network.

This is **env-level, best-effort** neutralization — a cooperation contract plus proxy
removal, **not** a kernel network namespace. `network: "online"` opts out (sets
`SANDBOX_NETWORK=online`, leaves proxies intact). Fetching model weights is a separate,
explicit, online step; **execution runs offline**.

## Security & threat model

The sandbox confines the **child** it spawns (a subprocess / future model or agent lane);
the **caller** that writes the policy and `argv` is trusted. It is a **default-deny
guardrail + bounded executor**, **not** a kernel/container sandbox: there is no filesystem
jail, network "offline" is best-effort env-level neutralization, and command identity is
not pinned (the invocation name is gated and resolved on `PATH`). Full asset list, trust
boundaries, attacker model, per-risk mitigations, and known limitations:
**[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md)**.

## CLI usage (`bls`)

JSON in / JSON out; exit code mirrors the outcome (0 ok, 1 ran-but-nonzero, 2
denied/error, 124 timeout):

```bash
bls version                                          # name, version, schema_version
bls preflight --policy policy.example.json           # inspect posture — no execution
bls run       --policy policy.example.json -- echo hi # default-deny sandboxed run
bls models                                            # list model manifests (no weights)
```

Run from a source checkout without installing:

```bash
PYTHONPATH=src python3 -m broker_lane_sandbox.cli version
```

A starter policy is in `policy.example.json` (default-deny; allows `echo`/`python3`,
offline, with CPU/AS/process caps). See **`docs/MANUAL.md`** for the full policy schema,
result schema, exit-code table, and worked examples.

## Current phase / status

| Phase | Scope | State |
|------|-------|-------|
| **P0** | repo invariants — model-artifact guard, `.gitignore`, manifests, policy docs | ✅ done |
| **P1** | safe-exec core — default-deny policy, env scrub, network policy, rlimits, `ExecResult`, preflight, `bls` CLI | ✅ done + adversarially reviewed |
| **P2** | broker-loom ↔ sandbox CLI/JSON seam | ✅ done (merged in #3) |
| **P3** | local/quantized model runners (env-driven cache) | ⏳ planned |
| **P4** | streaming | ⏳ planned |

P1 shipped with an adversarial review (4 lenses + per-finding skeptic verification);
4 confirmed defects were fixed and re-verified (default-deny path bypass, timeout
defeat, rlimit crash, guard fail-open). **42 tests pass**, stdlib-only.

## Develop

```bash
git config core.hooksPath .githooks                  # enable the model-artifact guard
python3 -m pytest tests/ -q                           # full suite (42 tests)
python3 scripts/check_model_artifacts.py --tracked    # audit the tracked tree
```

Layout: `src/broker_lane_sandbox/` (policy, envscrub, limits, executor, preflight,
catalog, result, cli, runners), `scripts/check_model_artifacts.py` (INVARIANT-1 guard),
`tests/`, `docs/`. Python ≥ 3.10, zero runtime dependencies (PyYAML optional, for YAML
policies/catalogs; JSON works with the stdlib).

## License / status note

Licensed under the **MIT License** — see [`LICENSE`](LICENSE). Personal-use project,
provided as-is with no support or compatibility guarantees. Future P2/P3/P4 work lands
via feature branches + PRs against `main`.
