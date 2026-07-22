"""Model runners (P3): the CLOSED family registry (D1) -- fake + llama.cpp (D2).

FakeRunner is cross-platform and weight-free (INVARIANT-1); it exercises the
infer seam only, never the sandbox gates, and is refused unless the request
carries `allow_fake: true` (F4). The llama.cpp family executes through
SafeExecutor with the canonical D3 argv and stdin-only prompt delivery;
ollama/transformers remain DEFERRED (never silently accepted). The registry
is data-light: infer.py branches on the family name -- no dynamic loading.
"""
from .base import RUNNER_FAMILIES, Runner, RunnerError, resolve_runner_family
from .fake_runner import FakeRunner

__all__ = [
    "FakeRunner",
    "RUNNER_FAMILIES",
    "Runner",
    "RunnerError",
    "resolve_runner_family",
]
