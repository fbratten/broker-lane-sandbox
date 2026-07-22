"""Deterministic fake model runner -- requires NO model weights.

Lets sandbox/inference tests run in CI without any real model file
(INVARIANT-1). The fake family exercises the infer SEAM, never the sandbox
gates: it runs in-process and bypasses SafeExecutor entirely -- documented
honestly per contract D1 fake gating (F4): `bls infer` refuses it unless the
request carries `"allow_fake": true`, and every fake result's model block
carries `"is_fake": true`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeRunner:
    """A canned, deterministic 'model'. No file I/O, no weights, no network."""
    profile: str = "fake"

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 256,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> dict:
        # Deterministic echo-style completion; enough to exercise plumbing.
        # Sampling params (max_tokens/temperature/seed) are accepted for
        # Runner-protocol conformance (contract D1) but deliberately ignored:
        # the fake stays canned and deterministic regardless of params.
        text = f"[fake:{self.profile}] received {len(prompt)} chars"
        return {
            "profile": self.profile,
            "text": text,
            "usage": {"prompt_chars": len(prompt), "completion_chars": len(text)},
            "is_fake": True,
        }

    @property
    def requires_weights(self) -> bool:
        return False
