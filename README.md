# broker-lane-sandbox

Safe execution boundaries for broker lanes, and the local/quantized-model runtime
boundary. **Separate project** from `project-broker-loom`.

- **broker-loom** owns orchestration, task state, ledger, routing, verifier-lane
  choice, handoff parsing, repair loops. It does **not** execute inside itself.
- **broker-lane-sandbox** (this repo) wraps execution: worktrees, subprocesses,
  local model calls, future agent execution — under a **default-deny** policy with
  env scrubbing, network controls, and process limits. broker-loom integrates only
  via a **CLI/API contract** (JSON in/out), never as a library.

> Architectural boundary: OpenRouter is a *verifier/spec* lane in broker-loom and
> never executes. Execution lanes are wrapped by this sandbox.

## Status

- **P0 — repo invariants** ✅ Model-artifact exclusion (`.gitignore` + guard + tests),
  manifests, policy docs.
- **P1 — safe-exec core** ✅ Default-deny `SandboxPolicy`, env scrubbing (secret guard +
  offline proxy strip), POSIX rlimits + wall-clock timeout, machine-readable `ExecResult`,
  preflight, and the `bls` CLI seam.
- Broker-loom seam lands in P2; local model runners in P3; streaming in P4.

## Safe-exec (`bls`) — default-deny

Everything is forbidden until a policy explicitly allows it (`allow_exec=false`,
empty command allow-list, `network=offline`, empty env). JSON in / JSON out:

```bash
bls version
bls preflight --policy policy.example.json          # inspect posture, no execution
bls run       --policy policy.example.json -- echo hi   # sandboxed run
bls models                                           # list model manifests (no weights)
```

The child runs with a scrubbed env (only allow-listed, non-secret names; proxies
stripped when offline), an isolated session, configured CPU/address-space/process
rlimits, and a timeout that kills the whole process group. Policy denials are
*results*, not crashes. Secret-looking env names (`*KEY*`, `*TOKEN*`, …) are dropped
even if allow-listed, unless `allow_secret_env` is set.

## INVARIANT-1 — model artifacts are runtime cache only

No `.gguf`/`.safetensors`/`.bin`/`.pt`/`.pth`/`.onnx`/`.mlmodel`/`.ckpt`/`.tflite`,
HF cache, Ollama blobs, or `llama.cpp` builds in git. Track only manifests /
checksums / URLs / license notes. Local model paths are env-driven
(`SANDBOX_MODEL_DIR`). See `docs/model-cache-policy.md`. Enforced by
`scripts/check_model_artifacts.py` (pre-commit + CI); even `git add -f` is refused.

## Develop

```bash
git config core.hooksPath .githooks          # enable the model-artifact guard
python3 -m pytest tests/ -q                    # invariant + fake-runner tests
python3 scripts/check_model_artifacts.py --tracked   # audit the tracked tree
```

Tests use **fake fixtures / mocked runners** — never a real model file.
