# Model-Cache Policy (INVARIANT-1)

Downloaded / local / quantized model weights are **runtime cache only**. They are
never committed to git, and never copied into documentation, handover notes, or
PR artifacts.

## What is forbidden in git

- Weight / serialized-model blobs: `.gguf`, `.safetensors`, `.bin`, `.pt`, `.pth`,
  `.onnx`, `.mlmodel`, `.ckpt`, `.tflite`, tokenizer/weight blobs.
- Vendor caches / builds: HuggingFace cache, Ollama blobs, `llama.cpp` builds.
- Anything under the runtime cache directories: `models/`, `model-cache/`,
  `runtime/`, `.cache/`, `.huggingface/`, `hf-cache/`, `ollama/`, `llama.cpp/`.

## What IS tracked

Only **manifests / profiles / checksums / URLs / license notes**:

- `models.example.yaml` — model profile catalog (source, sha256, license, runner).
- `MODEL-MANIFEST.example.md` — human-readable manifest template.
- This policy doc.

## Path resolution

Local model paths are **config/env-driven**, never hardcoded, never committed:

```
SANDBOX_MODEL_DIR   # root of the runtime model cache (outside git)
```

Resolution: profile (manifest) -> `${SANDBOX_MODEL_DIR}/<relative path>` -> verify
`sha256` -> load. Missing/mismatch fails loud. **Fetch is a separate, explicit,
online step; execution runs offline** (see network policy). Execution never downloads.

## Enforcement

- `.gitignore` ignores all cache dirs + weight extensions.
- `scripts/check_model_artifacts.py` is a guard run two ways:
  - pre-commit hook (`git config core.hooksPath .githooks`) — refuses **staged** blobs.
  - CI / audit (`--tracked`) — refuses any **tracked** blob.
  It also refuses oversize files (default 5 MB cap, `BLS_GUARD_MAX_MB`) and anything
  under a runtime cache directory. Even `git add -f weights.gguf` is refused.
- **Exit codes:** `0` = clean · `5` = a forbidden artifact was found · `2` = git/setup
  failure (the guard **fails closed** rather than reporting a false-clean tree).

## Tests

Invariant tests use **fake fixtures / mocked runners** (`runners/fake_runner.py`),
never a real model file. CI must pass with **zero** model weights present.
