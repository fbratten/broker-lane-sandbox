from __future__ import annotations

import json
from pathlib import Path

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

# --- contract-gap batch (finalization audit F11-F14, F19) --------------------

def test_broker_request_stdin_reaches_child() -> None:
    # Seam doc: `stdin` is optional text passed to the child's standard input.
    wrapper = run_broker_request(
        {
            "schema_version": 1,
            "request_id": "job-stdin",
            "policy": POLICY,
            "argv": ["python3", "-c", "import sys; print(sys.stdin.read().strip())"],
            "stdin": "ping\n",
        }
    )
    assert wrapper["result"]["status"] == "ok"
    assert wrapper["result"]["stdout"] == "ping\n"


def test_broker_request_rejects_non_string_stdin() -> None:
    with pytest.raises(BrokerRunError, match="stdin must be a string or null"):
        run_broker_request(
            {"schema_version": 1, "policy": POLICY,
             "argv": ["python3", "-c", "print(1)"], "stdin": 123}
        )


def test_broker_request_top_level_timeout_overrides_policy() -> None:
    # Seam doc: request-level timeout_seconds overrides the inline policy's value.
    wrapper = run_broker_request(
        {
            "schema_version": 1,
            "request_id": "job-timeout-override",
            "policy": {**POLICY, "timeout_seconds": 30},
            "timeout_seconds": 1,
            "argv": ["python3", "-c", "import time; time.sleep(30)"],
        }
    )
    assert wrapper["result"]["status"] == "timeout"
    assert wrapper["result"]["ok"] is False
    assert wrapper["result"]["limits"]["timeout_seconds"] == 1


def test_broker_request_top_level_working_dir_overrides_policy(tmp_path) -> None:
    # Seam doc: request-level working_dir is a per-request cwd override.
    wrapper = run_broker_request(
        {
            "schema_version": 1,
            "policy": POLICY,
            "working_dir": str(tmp_path),
            "argv": ["python3", "-c", "import os; print(os.getcwd())"],
        }
    )
    assert wrapper["result"]["status"] == "ok"
    assert Path(wrapper["result"]["stdout"].strip()).resolve() == tmp_path.resolve()


def test_broker_request_rejects_wrong_or_missing_schema_version() -> None:
    # Seam doc: request schema_version is required and must equal the supported one.
    with pytest.raises(BrokerRunError, match="schema_version"):
        run_broker_request(
            {"schema_version": 2, "policy": POLICY, "argv": ["python3", "-c", "print(1)"]}
        )
    with pytest.raises(BrokerRunError, match="schema_version"):
        run_broker_request(
            {"policy": POLICY, "argv": ["python3", "-c", "print(1)"]}
        )


def test_broker_run_cli_exit_one_for_child_nonzero(tmp_path, capsys) -> None:
    # Exit-code table at the seam boundary: 1 = ran but exited non-zero.
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {"schema_version": 1, "request_id": "cli-exit7", "policy": POLICY,
             "argv": ["python3", "-c", "import sys; sys.exit(7)"]}
        ),
        encoding="utf-8",
    )
    exit_code = main(["broker-run", "--request", str(request_path)])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["result"]["status"] == "exit_nonzero"
    assert payload["result"]["exit_code"] == 7


def test_broker_run_cli_exit_124_for_timeout(tmp_path, capsys) -> None:
    # Exit-code table at the seam boundary: 124 = wall-clock timeout.
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {"schema_version": 1, "request_id": "cli-timeout",
             "policy": {**POLICY, "timeout_seconds": 1},
             "argv": ["python3", "-c", "import time; time.sleep(30)"]}
        ),
        encoding="utf-8",
    )
    exit_code = main(["broker-run", "--request", str(request_path)])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 124
    assert payload["result"]["status"] == "timeout"
    assert payload["result"]["exit_code"] not in (0, None)


def test_broker_run_cli_unparseable_request_file_is_request_error(tmp_path, capsys) -> None:
    # Seam doc: a body that cannot be parsed as JSON returns a request_error
    # wrapper with request_id null and exit 2 -- never a traceback.
    request_path = tmp_path / "request.json"
    request_path.write_text("not json {", encoding="utf-8")
    exit_code = main(["broker-run", "--request", str(request_path)])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "request_error"
    assert payload["ok"] is False
    assert payload["request_id"] is None


def test_broker_run_cli_missing_request_file_is_request_error(capsys) -> None:
    exit_code = main(["broker-run", "--request", "/no/such/dir/request.json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "request_error"
    assert payload["ok"] is False
    assert payload["request_id"] is None
