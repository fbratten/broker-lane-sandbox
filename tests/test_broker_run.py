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
