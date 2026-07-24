"""Temporary negative-control test for governance-lane validation (NEVER merged).

Deliberately fails so the fail-closed aggregate `test` check reports red, proving
that a pull request which does not satisfy the required check cannot be merged
through the normal path. Removed with the branch after evidence capture.
"""


def test_o4_negative_control_deliberate_failure():
    assert False, "intentional failure: governance negative control"
