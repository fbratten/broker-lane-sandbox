# Model Manifest (EXAMPLE / template) — TRACKED, no weights

A manifest records *where a model comes from and how to verify it* — never the
weights themselves. One entry per local/quantized model profile.

| Field | Value (example) |
|---|---|
| profile | `example-small-instruct-gguf` |
| runner | `llama.cpp` |
| source URL | `https://example.invalid/models/...Q4_K_M.gguf` |
| relative_path | `example/example-small-instruct-Q4_K_M.gguf` (under `$SANDBOX_MODEL_DIR`) |
| sha256 | `0000...0000` (verify after fetch) |
| size_bytes | `0` |
| quantization | `Q4_K_M` |
| context_length | `8192` |
| license | record the **real** license + URL |

**Rules**
- The weight file lives only in the runtime cache (`$SANDBOX_MODEL_DIR`), never git.
- Fetch (online) and verify checksum as a separate explicit step; execution is offline.
- Do not paste weights, base64 blobs, or local absolute paths into this file.
