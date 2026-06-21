"""The fake runner lets model-execution tests run with NO real weights (INVARIANT-1)."""
from broker_lane_sandbox.runners.fake_runner import FakeRunner


def test_fake_runner_requires_no_weights():
    r = FakeRunner(profile="unit")
    assert r.requires_weights is False


def test_fake_runner_is_deterministic_and_fileless():
    r = FakeRunner(profile="unit")
    out = r.generate("hello world")          # 11 chars
    assert out["is_fake"] is True
    assert out["profile"] == "unit"
    assert "received 11 chars" in out["text"]
    # Deterministic: same input -> same output.
    assert r.generate("hello world") == out
