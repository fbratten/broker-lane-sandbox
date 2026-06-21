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

**P0 — repo invariants.** Model-artifact exclusion (`.gitignore` + guard + tests),
manifests, policy docs. Safe-exec CLI core (policy schema, env scrub, network,
limits) lands in P1; broker-loom seam in P2; local model runners in P3; streaming in P4.

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
