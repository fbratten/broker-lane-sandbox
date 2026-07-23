# broker-lane-sandbox - Manual

A practical guide to the P1 safe-exec core and the P2 broker seam: the policy schema,
the result schema, the `bls` CLI, the security model, and worked examples. For the
project overview see the [README](../README.md); for the model-weight rules see
[model-cache-policy.md](model-cache-policy.md); for the full P2 request/wrapper
contract see [P2_BROKER_LOOM_SEAM.md](P2_BROKER_LOOM_SEAM.md).

- [1. Install / run](#1-install--run)
- [2. Concepts](#2-concepts)
- [3. Policy schema](#3-policy-schema)
- [4. The `bls` CLI](#4-the-bls-cli)
- [5. `ExecResult` schema](#5-execresult-schema)
- [6. Security model & threat boundary](#6-security-model--threat-boundary)
- [7. Worked examples](#7-worked-examples)
- [8. Model catalog](#8-model-catalog)
- [9. Troubleshooting](#9-troubleshooting)
- [10. Roadmap](#10-roadmap)

---

## 1. Install / run

Python ≥ 3.10. Zero runtime dependencies (PyYAML is optional, only for YAML policies /
catalogs - JSON works with the stdlib).

Run from a source checkout without installing:

```bash
PYTHONPATH=src python3 -m broker_lane_sandbox.cli version
```

Or install the `bls` entry point:

```bash
pip install -e .          # or: pip install -e '.[yaml]' for YAML support
bls version
```

Enable the model-artifact guard and run the tests:

```bash
git config core.hooksPath .githooks
python3 -m pytest tests/ -q          # 286 tests (2026-07-23, head 19091b1)
```

CI (`.github/workflows/ci.yml`) runs the same suite plus the model-artifact guard
(INVARIANT-1, `--tracked`) and an install smoke across the Python 3.10-3.13 matrix
(check contexts `test (3.10)` ... `test (3.13)`), followed by a **fail-closed aggregate
job named `test`** that is green **only** when the whole matrix succeeds. Branch
protection can therefore require the single stable `test` check instead of the four
volatile per-version contexts; any leg failure, cancellation, or an incomplete matrix
makes the aggregate exit non-zero.

## 2. Concepts

- **Policy** - a `SandboxPolicy`: the *only* thing that grants capability. Everything is
  denied until the policy allows it. Canonical format is **JSON**; YAML is read only if
  PyYAML is installed. Unknown keys **fail loud** (keys starting with `_` are treated as
  comments and ignored).
- **Executor** - `SafeExecutor(policy).run(argv)`: applies the gates, scrubs the env,
  bounds the child, and returns an `ExecResult`. Never imported by the caller across the
  trust boundary - reached via the CLI.
- **Result** - a JSON-serializable `ExecResult`. A policy **denial is a result**, not an
  exception. Only genuinely unexpected internal failures raise.
- **Runner** - pluggable model execution (P3). The shipped `FakeRunner` requires no
  weights so tests never touch a real model.

## 3. Policy schema

A policy is a JSON object. Defaults are **default-deny**; you must opt in.

| Field | Type | Default | Meaning |
|------|------|---------|---------|
| `schema_version` | int | `1` | must equal the supported version (fails loud otherwise) |
| `allow_exec` | bool | `false` | master switch - `false` means **nothing spawns** |
| `allowed_commands` | string[] | `[]` | allow-list of **bare** command names (no path) |
| `env_allowlist` | string[] | `["PATH","HOME","LANG","LC_ALL","TZ","TMPDIR"]` | exact env names passed to the child |
| `env_passthrough_prefixes` | string[] | `[]` | env-name prefixes passed to the child |
| `allow_secret_env` | bool | `false` | if `false`, secret-looking names are dropped even when allow-listed |
| `network` | string | `"offline"` | `"offline"` or `"online"` |
| `timeout_seconds` | number | `30` | wall-clock budget (> 0) |
| `max_output_bytes` | int | `1000000` | cap on captured stdout/stderr each (> 0) |
| `cpu_seconds` | int? | `null` | RLIMIT_CPU (POSIX), if set (> 0) |
| `address_space_bytes` | int? | `null` | RLIMIT_AS (POSIX), if set (> 0) |
| `max_processes` | int? | `null` | RLIMIT_NPROC (POSIX), if set (> 0) |
| `max_file_size_bytes` | int? | `null` | RLIMIT_FSIZE (POSIX): per-**file** write cap, if set (> 0) |
| `working_dir` | string? | `null` | child cwd; must exist if set |
| `model_dir_env` | string | `"SANDBOX_MODEL_DIR"` | env var naming the runtime model-cache root |

**Validation (fail-loud):** bad `schema_version`, an invalid `network`, non-positive
`timeout_seconds` / `max_output_bytes` / limits, a non-list `allowed_commands`, or any
unknown key raises a `PolicyError`. Numeric limit fields are **strictly typed**: booleans
(JSON `true` is not "limit = 1"), strings, and fractional floats raise `PolicyError`;
integral floats (e.g. JSON `1e9`) are accepted and normalized to int (`timeout_seconds` is a plain number and MAY be fractional, e.g. `0.5`).
`max_file_size_bytes` is new in this revision: an **older** sandbox rejects a policy that sets it, fail-loud, as `unknown policy keys` (the safe direction) -- upgrade the sandbox before emitting the field.
See [`policy.example.json`](../policy.example.json).

Notes:
- **Bare command names only.** `allowed_commands: ["python3"]` permits `python3`
  (resolved on `PATH`) but **not** `/usr/bin/python3` or `./python3` - a path-bearing
  `argv[0]` is denied so an allow-listed name can't front for an arbitrary file.
- **Secret guard.** Names matching `KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|
  SESSION|COOKIE|AUTH` are dropped from the child env unless `allow_secret_env: true`.

## 4. The `bls` CLI

JSON in / JSON out. Global flag: `--pretty`.

| Command | Purpose | Exit codes |
|--------|---------|-----------|
| `bls version` | print name / version / schema_version / capabilities - consumers MUST probe `capabilities` before the first `infer` call (an absent key = P2 baseline, no infer) | `0` |
| `bls preflight --policy P` | inspect posture; **never executes** | `0` ok, `1` warnings |
| `bls run --policy P [--timeout S] [--cwd D] -- ARGV…` | default-deny sandboxed run | `0` ok · `1` ran-but-nonzero · `2` denied/spawn-error · `124` timeout |
| `bls models [--catalog C]` | list model manifests (no weights) | `0` ok · `2` catalog not found (pass `--catalog` on installed copies; the default path resolves only from a source checkout) |
| `bls broker-run --request R` | P2 broker seam: JSON request in, JSON wrapper out | `0` ok · `1` ran-but-nonzero · `2` denied/spawn-error/request-error · `124` timeout |
| `bls infer --request R [--preflight] [--verify-full] [--stream]` | P3 local-model inference (JSON wrapper), or with `--stream` the additive P4 JSONL event stream - see §8 | `0` ok · `1` generation-error · `2` denied/spawn-error/model-error/request-error · `124` timeout |

`--timeout` / `--cwd` on `run` override the policy's `timeout_seconds` / `working_dir`
for that invocation. Put the command after `--`.

`broker-run` reads a full request (inline policy + argv + optional `stdin` /
`timeout_seconds` / `working_dir` overrides) from `--request` and wraps the
`ExecResult` in a correlation envelope; a malformed request returns a structured
`request_error` wrapper (exit `2`), never a crash. Full request/wrapper schema:
[P2_BROKER_LOOM_SEAM.md](P2_BROKER_LOOM_SEAM.md).

`preflight` reports: default-deny posture, whether each allow-listed command resolves on
`PATH`, the env-scrub plan (**names only**), the network posture, rlimit support, the
model-cache root status, and any `warnings` (e.g. an allow-listed name that looks secret,
or `allow_exec: true` with an empty allow-list).

## 5. `ExecResult` schema

Every `run` emits one JSON object:

| Field | Type | Meaning |
|------|------|---------|
| `status` | string | `ok` · `exit_nonzero` · `denied` · `timeout` · `spawn_error` |
| `ok` | bool | `true` only when `status == "ok"` |
| `argv` | string[] | the full original argv (preserved verbatim) |
| `reason` | string | human-readable explanation |
| `exit_code` | int? | child return code (`null` for `denied`) |
| `stdout` / `stderr` | string | captured output, truncated to `max_output_bytes` |
| `duration_ms` | int | wall-clock duration |
| `truncated` | bool | whether output was truncated |
| `network` | string | `offline` / `online` |
| `env_keys` | string[] | env **names** the child received - **never values** |
| `limits` | object | effective limits + any `dropped_secret_env` names |

## 6. Security model & threat boundary

**Threat model:** the *caller* (e.g. broker-loom) is trusted - it writes the policy and
chooses the argv. The sandbox confines the **child** it spawns: which executable runs,
what environment it sees, whether it should reach the network, and how much it can
consume. It is a **guardrail against accidental / unintended commands and leakage**, and
a bounded-resource executor - sized for a **single-operator personal tool**.

**What is enforced:**
- Default-deny execution; bare-name allow-list (no path bypass).
- Empty-baseline env with a secret-name drop; `env_keys` never exposes values.
- Offline-by-default proxy stripping + an offline signal to cooperating runners.
- POSIX rlimits (CPU / AS / NPROC / FSIZE) + a wall-clock timeout that group-kills the
  child; the post-kill drain is time-boxed so an escaped descendant can't pin the call open.
- Pre-spawn failures (missing exe, bad cwd, rlimit above the host ceiling) become
  `spawn_error` **results**, not crashes.

**What is explicitly NOT enforced (out of scope, not bugs):**
- Not a kernel/container sandbox: `network: offline` is env-level neutralization, **not**
  a network namespace; the filesystem is **not** jailed (an allow-listed `cat` can still
  read any absolute path).
- No binary-identity pinning (`realpath`/hash) - the **invocation name** is gated and
  resolved on `PATH`. A writable earlier-`PATH` entry is a host-integrity concern outside
  the boundary.
- `max_output_bytes` counts characters, not bytes; `RLIMIT_NPROC` is per-UID (a POSIX
  property), not per-job; `max_file_size_bytes` caps each **individual file** a child
  writes (SIGXFSZ past the cap) - it is **not** a disk quota, so a child may still write
  many files each below the cap.
- Child output is decoded as UTF-8 with **replacement characters** for invalid bytes
  (`errors="replace"`), so a binary-emitting child still returns a JSON `ExecResult`
  - raw bytes are not preserved.

These boundaries were confirmed by an adversarial review: the path-bypass, timeout-defeat,
rlimit-crash, and guard-fail-open defects were real and fixed; the kernel/identity-pinning
asks were correctly classified as out-of-scope for this MVP.

The full asset list, trust-boundary diagram, attacker model, per-risk mitigation table, and
consolidated known-limitations live in **[THREAT_MODEL.md](THREAT_MODEL.md)**.

## 7. Worked examples

A minimal policy (`policy.json`):

```json
{
  "schema_version": 1,
  "allow_exec": true,
  "allowed_commands": ["python3", "echo"],
  "network": "offline",
  "timeout_seconds": 30,
  "cpu_seconds": 10,
  "address_space_bytes": 1073741824,
  "max_processes": 64,
  "max_file_size_bytes": 104857600
}
```

```bash
# Inspect before running - no execution happens:
bls preflight --policy policy.json --pretty

# Run an allow-listed command (offline, scrubbed env, bounded):
bls run --policy policy.json -- echo "hello"
# -> {"status":"ok","ok":true,"exit_code":0,"stdout":"hello\n", ...}

# Path-bearing argv[0] is refused (bypass protection):
bls run --policy policy.json -- /usr/bin/echo hi
# -> {"status":"denied","reason":"argv[0] must be a bare command name ...","ok":false}  (exit 2)

# Default-deny: with allow_exec=false nothing runs:
echo '{"schema_version":1}' > deny.json
bls run --policy deny.json -- echo hi
# -> {"status":"denied","reason":"execution disabled (allow_exec is false)"}  (exit 2)

# A timeout group-kills the child and returns bounded:
bls run --policy policy.json --timeout 1 -- python3 -c "import time; time.sleep(30)"
# -> {"status":"timeout", ...}  (exit 124)
```

A secret in the environment is dropped from the child even if allow-listed:

```bash
OPENROUTER_API_KEY=dummy-not-a-real-key \
  bls run --policy <(echo '{"schema_version":1,"allow_exec":true,"allowed_commands":["python3"],"env_allowlist":["PATH","OPENROUTER_API_KEY"]}') \
  -- python3 -c "import os; print('OPENROUTER_API_KEY' in os.environ)"
# -> child prints False; result.limits.dropped_secret_env lists OPENROUTER_API_KEY
```

## 8. Model catalog

`bls models` lists **manifests only** - runner, source URL, sha256, license, and the
env-relative path - never weights. The default catalog is
[`models.example.yaml`](../models.example.yaml) (requires PyYAML); a `.json` catalog
works with the stdlib via `--catalog`. Local weights live under `${SANDBOX_MODEL_DIR}`
(outside git) and are resolved + checksum-verified at load time. See
[model-cache-policy.md](model-cache-policy.md) and INVARIANT-1.

### Local inference - `bls infer` (P3)

`bls infer --request R.json` runs one local-model generation through the full safe-exec
gate chain. The request carries: `schema_version` (1), optional `request_id`, `profile`
(catalog profile name), `catalog` (path - explicit; catalogs are operator-tracked
sha256-pinning data), `prompt` (a string - **data only**: it reaches the child
exclusively via stdin, never argv or env), `params` (`max_tokens` required; optional
`temperature` 0-2 and `seed`; unknown keys fail loud; `max_tokens` must respect the
profile's `context_length`), optional `allow_fake`, and an inline `policy` that must
allow-list the runner binary. See
[`examples/infer_request.example.json`](../examples/infer_request.example.json) and
[`examples/infer_result.ok.example.json`](../examples/infer_result.ok.example.json).

- **Runners.** Closed registry: the `llama.cpp` family (binary candidates
  `llama-completion`, then completion-mode `llama-cli` - the rewritten server-based
  llama-cli chat client is not a supported flag surface) and the weight-free `fake`
  runner. `fake` is refused unless the request sets `"allow_fake": true`, runs
  in-process (it exercises the seam, **not** the sandbox gates), and marks its result
  `"is_fake": true` with `sha256_verified: "none"`. Unknown families fail closed.
- **Verification.** Per call, before spawn: cache-root env lookup → traversal-safe,
  realpath-contained join (symlink escapes refused) → existence → size → sha256 via a
  `<weight>.blsverify.json` sidecar cache (full streaming hash on first use, any
  size/mtime drift, or `--verify-full`; otherwise a size+mtime fast path reported
  truthfully as `sha256_verified: "cached"`). A mismatch never writes a sidecar.
- **Statuses.** `ok` · `denied` · `model_error` (pre-spawn only, with `reason_code` ∈
  catalog_invalid / unsupported_runner / model_dir_unset / model_missing /
  size_mismatch / checksum_mismatch / runner_missing) · `spawn_error` ·
  `generation_error` (child ran and failed - including in-child load failures past
  checksum, with diagnostic stderr) · `timeout`. Stdout JSON is the source of truth;
  process exit codes are coarse (see the table above).
- **Preflight.** `--preflight` runs the entire validation/verification chain and binary
  resolution, then stops - nothing is executed, `runner_version` stays null.
- **Privacy.** The recorded `argv` self-labels the model path as
  `${<cache_dir_env>}/<relative_path>` - no absolute local path appears in argv or the
  model block; captured stdout/stderr are scrubbed of the absolute weight path and
  cache root (replaced by their `${…}` label forms). No log flag is passed to the
  runner - on modern llama.cpp builds `--log-disable` would silence generated token
  text itself (upstream issue #10002).
- **Policy guidance for model profiles.** Start from
  [`policy.model.example.json`](../policy.model.example.json). **Omit
  `address_space_bytes`**: llama.cpp mmaps weights by default and RLIMIT_AS gates mmap
  (ENOMEM below model size + ~2 GiB headroom) - the generic `policy.example.json`
  values (1 GiB AS, 10 s CPU) are lethal to real inference. Size `cpu_seconds` as
  threads × expected wall time. Keep `PATH` in `env_allowlist` and put the runner
  binary on PATH (e.g. a symlink from llama.cpp's `build/bin`).
- **Platform.** The real-runner path is POSIX-only; the fake runner is cross-platform.
- **Version skew.** On an older sandbox, `bls infer` fails as an argparse usage error
  (exit 2, no stdout JSON) - probe `bls version` `capabilities` first, never
  attempt-and-parse. New policy capabilities keep arriving as opt-in fields under the
  same envelope `schema_version`; the safe skew direction is unchanged: upgrade the
  sandbox before emitting a new field.

### Streaming - `bls infer --stream` (P4)

`bls infer --request R.json --stream` runs the **same** inference but emits a **JSONL
event stream** on stdout instead of one buffered wrapper. It is **additive**: without
`--stream`, `bls infer` is byte-identical to P3. Probe `bls version` `capabilities` for
`"infer-stream"` before the first streaming call (an older sandbox lacks it).

- **Events (one compact JSON object per line):** `start` (seq 0, once, carries the
  `model` block), `chunk` (incremental generation `text`, split at 8192 chars),
  `warning` (≤ 8, e.g. output-cap reached), `final` (once, last; `wrapper` is the exact
  non-streaming response body - a run-result or a flat `request_error`). Every event
  carries `{stream_version: 1, event, seq}`; `seq` is gapless from 0. `--pretty` is
  ignored in stream mode (events are always compact single lines).
- **Reconciliation.** The concatenation of all `chunk.text` equals the final
  `generation.text` (and, on truncation, equals the capped prefix while the final text
  is that prefix plus the truncation marker). The `final` wrapper is authoritative.
- **No final = interrupted.** A stream that ends without a `final` event was interrupted
  (crash, disconnect, kill); a consumer treats that as a failure, never success - and
  ignores the process exit code in that case. `bls` exits 0 **iff** a `final` with
  status `ok` was emitted.
- **Bounded and honest.** The output cap does **not** kill the child - the relay stops
  emitting, warns once, and drains the child to its natural exit (so an oversize
  completion is `ok` + `truncated`, exactly as non-streaming). The wall-clock budget is
  enforced by an independent watchdog plus deadline-bounded reads, so neither a stalled
  consumer nor a pipe-holding escaped descendant can hang the call. `--preflight` and
  `--stream` are mutually exclusive (a single `request_error` final).
- **Producer-boundary liveness (per-event flush).** The streaming producer flushes
  stdout after **every** JSONL event (`start` included), so each event reaches the pipe
  the instant it is emitted and a consumer observes `start` before `final` at the source.
  A consumer does **not** need to run the sandbox under `PYTHONUNBUFFERED` (or any other
  unbuffering wrapper) to get incremental liveness - the flush is owned by the producer,
  not delegated to the reader.

## 9. Troubleshooting

- **`PolicyError: unknown policy keys: [...]`** - a typo or stray field; policies fail
  loud. Prefix intentional comment keys with `_`.
- **`denied: argv[0] must be a bare command name ...`** - pass the bare name (`python3`),
  not a path; the executor resolves it on `PATH`.
- **`denied: command 'x' not in allowed_commands`** - add the bare name to
  `allowed_commands`.
- **`spawn_error: could not start process: Exception occurred in preexec_fn.`** - a
  requested rlimit (`max_processes` / `cpu_seconds` / `address_space_bytes` / `max_file_size_bytes`) exceeds the
  host's hard ceiling. Lower it (check `ulimit -a`).
- **`bls models` errors about PyYAML** - install `pip install pyyaml`, or pass a `.json`
  catalog via `--catalog`.
- **Guard exits 2 (`GUARD ERROR (failing closed)`)** - git is broken/absent or you ran
  the guard outside a repo; it **fails closed** by design rather than reporting clean.

## 10. Roadmap

| Phase | Scope | State |
|------|-------|-------|
| P0 | repo invariants (model-artifact guard, manifests, policy docs) | ✅ |
| P1 | safe-exec core (policy, env scrub, network, limits, `ExecResult`, `bls`) | ✅ reviewed |
| P2 | broker-loom ↔ sandbox CLI/JSON seam | ✅ merged (#3) |
| P3 | local/quantized model runners (env-driven cache) - `bls infer`, llama.cpp family + fake; ollama/transformers deferred | ✅ delivered 2026-07-22 |
| P4 | streaming - `bls infer --stream`, additive JSONL events | ✅ delivered 2026-07-22 |

Future work lands via feature branches + PRs against `main`.
