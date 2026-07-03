from __future__ import annotations

import json

import pytest

from broker_lane_sandbox.broker_run import BrokerRunError, run_broker_request
from broker_lane_sandbox.cli import main


POLICY = {
    "schema_version": 1,
    "allow_exec": True,
    "allowed_commands": ["python3"],
    "network": "offline",
    "timeout_seconds": 5,
}


def test_broker_request_wraps_exec_result_with_request_id() -> None:
    wrapper = run_broker_request(
        {
            "schema_version": 1,
            "request_id": "job-001",
            "policy": POLICY,
            "argv": ["python3", "-c", "print('hello from broker seam')"],
        }
    )

    assert wrapper["schema_version"] == 1
    assert wrapper["request_id"] == "job-001"
    assert wrapper["result"]["status"] == "ok"
    assert wrapper["result"]["ok"] is True
    assert wrapper["result"]["stdout"] == "hello from broker seam\n"


def test_broker_request_preserves_default_deny_denial_as_result() -> None:
    wrapper = run_broker_request(
        {
            "schema_version": 1,
            "request_id": "job-denied",
            "policy": POLICY,
            "argv": ["/usr/bin/python3", "-c", "print('must not run')"],
        }
    )

    assert wrapper["request_id"] == "job-denied"
    assert wrapper["result"]["status"] == "denied"
    assert wrapper["result"]["ok"] is False
    assert "bare command name" in wrapper["result"]["reason"]


def test_broker_request_rejects_unknown_request_keys() -> None:
    with pytest.raises(BrokerRunError, match="unknown request keys"):
        run_broker_request(
            {
                "schema_version": 1,
                "policy": POLICY,
                "argv": ["python3", "-c", "print('hi')"],
                "surprise": True,
            }
        )


def test_broker_request_rejects_bad_argv_shape() -> None:
    with pytest.raises(BrokerRunError, match="argv must be a non-empty list of strings"):
        run_broker_request({"schema_version": 1, "policy": POLICY, "argv": "python3"})


def test_broker_run_cli_emits_json_and_exit_zero(tmp_path, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "cli-ok",
                "policy": POLICY,
                "argv": ["python3", "-c", "print('cli seam')"],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["broker-run", "--request", str(request_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["request_id"] == "cli-ok"
    assert payload["result"]["status"] == "ok"
    assert payload["result"]["stdout"] == "cli seam\n"


def test_broker_run_cli_request_error_is_json(tmp_path, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({"schema_version": 1, "policy": POLICY, "argv": []}),
        encoding="utf-8",
    )

    exit_code = main(["broker-run", "--request", str(request_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "request_error"
    assert payload["ok"] is False
    assert "argv must be a non-empty list of strings" in payload["reason"]


def test_broker_run_cli_error_preserves_request_id(tmp_path, capsys) -> None:
    """A request_id present in a request that fails validation is echoed unchanged."""
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {"schema_version": 1, "request_id": "job-err", "policy": POLICY, "argv": []}
        ),
        encoding="utf-8",
    )

    exit_code = main(["broker-run", "--request", str(request_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "request_error"
    assert payload["ok"] is False
    assert payload["request_id"] == "job-err"


def test_broker_request_limits_flow_through_to_result() -> None:
    # Opt-in resource limits requested in the broker-run policy must be applied
    # and reported back in the result's limits summary.
    wrapper = run_broker_request(
        {
            "schema_version": 1,
            "request_id": "job-limits",
            "policy": {**POLICY, "cpu_seconds": 5, "max_file_size_bytes": 1048576},
            "argv": ["python3", "-c", "print('limited ok')"],
        }
    )
    assert wrapper["result"]["status"] == "ok"
    limits = wrapper["result"]["limits"]
    assert limits["cpu_seconds"] == 5
    assert limits["max_file_size_bytes"] == 1048576


def test_broker_request_malformed_limit_is_policy_error_not_typeerror() -> None:
    # JSON `true` for a limit is malformed: it must surface as PolicyError
    # (clean request_error at the CLI), never a raw TypeError or a silent limit=1.
    from broker_lane_sandbox.policy import PolicyError

    with pytest.raises(PolicyError, match="boolean"):
        run_broker_request(
            {
                "schema_version": 1,
                "policy": {**POLICY, "cpu_seconds": True},
                "argv": ["python3", "-c", "print('never runs')"],
            }
        )


def test_broker_run_cli_malformed_limit_is_clean_request_error(tmp_path, capsys) -> None:
    # The actual CLI boundary broker-loom talks to: a malformed limit (JSON true)
    # must come back as a structured request_error JSON with exit 2 -- never a
    # traceback and never a silently-applied limit.
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "job-bad-limit",
                "policy": {**POLICY, "max_file_size_bytes": True},
                "argv": ["python3", "-c", "print('never runs')"],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["broker-run", "--request", str(request_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "request_error"
    assert payload["ok"] is False
    assert payload["request_id"] == "job-bad-limit"
    assert "boolean" in payload["reason"]
    assert "never runs" not in payload["reason"]
