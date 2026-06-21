"""Deterministic fake model runner — requires NO model weights.

Lets sandbox/inference tests run in CI without any real model file (INVARIANT-1).
Real runners in P3 implement the same minimal protocol (`generate`) but load
weights from the env-driven runtime cache.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeRunner:
    """A canned, deterministic 'model'. No file I/O, no weights, no network."""
    profile: str = "fake"

    def generate(self, prompt: str, *, max_tokens: int = 256) -> dict:
        # Deterministic echo-style completion; enough to exercise plumbing.
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
