"""Model runners. Real runners (llama.cpp/ollama/transformers) arrive in P3 and
load weights from the env-driven runtime cache. Tests use the fake runner so no
real model file is ever required (INVARIANT-1)."""
