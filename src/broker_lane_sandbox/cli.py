"""`bls` -- the broker-lane-sandbox CLI seam.

JSON in / JSON out. This is the *stable contract* broker-loom (or any caller) uses to
reach the sandbox; it never imports the sandbox as a library. Subcommands:

  bls version
  bls preflight --policy POLICY              # inspect posture, no execution
  bls run       --policy POLICY -- ARGV...   # default-deny sandboxed execution
  bls broker-run --request REQUEST.json      # broker-loom JSON request seam
  bls models    [--catalog CATALOG]          # list model manifests (no weights)

Exit codes: 0 on a clean/OK outcome, non-zero on denial / timeout / error, so the
caller can branch on the process status as well as parse the JSON body.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import SCHEMA_VERSION, __version__
from .result import NON_RUN_STATUSES, Status


def _emit(obj: dict, pretty: bool) -> None:
    sys.stdout.write(json.dumps(obj, indent=2 if pretty else None, sort_keys=pretty) + "\n")


def _default_catalog() -> Path:
    # repo root: src/broker_lane_sandbox/cli.py -> up 3
    return Path(__file__).resolve().parents[2] / "models.example.yaml"


def _exit_for_result_status(status: str) -> int:
    if status == Status.OK:
        return 0
    if status in NON_RUN_STATUSES:
        return 2          # refused / could not start
    if status == Status.TIMEOUT:
        return 124        # conventional timeout code
    return 1              # ran but exited non-zero


def cmd_version(args) -> int:
    _emit(
        {"name": "broker-lane-sandbox", "version": __version__,
         "schema_version": SCHEMA_VERSION},
        args.pretty,
    )
    return 0


def cmd_preflight(args) -> int:
    from .policy import SandboxPolicy
    from .preflight import preflight

    policy = SandboxPolicy.from_file(args.policy)
    report = preflight(policy)
    _emit(report, args.pretty)
    return 0 if report["ok"] else 1


def cmd_run(args) -> int:
    from .executor import SafeExecutor
    from .policy import SandboxPolicy

    if not args.argv:
        _emit({"status": Status.DENIED, "reason": "no command given after --", "ok": False},
              args.pretty)
        return 2

    policy = SandboxPolicy.from_file(args.policy)
    if args.timeout is not None:
        policy.timeout_seconds = args.timeout
    if args.cwd is not None:
        policy.working_dir = args.cwd

    result = SafeExecutor(policy).run(args.argv)
    _emit(result.to_dict(), args.pretty)
    return _exit_for_result_status(result.status)


def cmd_broker_run(args) -> int:
    from .broker_run import BrokerRunError, request_error, run_broker_request
    from .policy import PolicyError

    request_id = None
    try:
        data = json.loads(Path(args.request).read_text(encoding="utf-8"))
        # Echo the correlation id back unchanged even when the request later fails
        # validation (the contract guarantees this). Only a string request_id is valid.
        if isinstance(data, dict) and isinstance(data.get("request_id"), str):
            request_id = data["request_id"]
        wrapper = run_broker_request(data)
    except (OSError, json.JSONDecodeError, BrokerRunError, PolicyError, TypeError) as exc:
        wrapper = request_error(str(exc), request_id)
        _emit(wrapper, args.pretty)
        return 2

    _emit(wrapper, args.pretty)
    return _exit_for_result_status(wrapper["result"]["status"])


def cmd_models(args) -> int:
    from .catalog import list_profiles

    catalog = Path(args.catalog) if args.catalog else _default_catalog()
    if not catalog.is_file():
        # The default resolves relative to a SOURCE CHECKOUT; an installed copy
        # has no repo root above it. Fail with clean JSON, not a traceback.
        _emit(
            {"ok": False,
             "error": f"catalog not found: {catalog}. Pass --catalog PATH "
                      "(the default models.example.yaml only resolves from a "
                      "source checkout)."},
            args.pretty,
        )
        return 2
    summary = list_profiles(catalog)
    _emit(summary, args.pretty)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bls", description="broker-lane-sandbox safe-exec CLI")
    p.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="print version + schema_version")

    pf = sub.add_parser("preflight", help="inspect policy/env posture (no execution)")
    pf.add_argument("--policy", required=True, help="path to a .json (or .yaml) policy")

    pr = sub.add_parser("run", help="run ARGV under the default-deny sandbox")
    pr.add_argument("--policy", required=True, help="path to a .json (or .yaml) policy")
    pr.add_argument("--timeout", type=float, default=None, help="override timeout seconds")
    pr.add_argument("--cwd", default=None, help="override working directory")
    pr.add_argument("argv", nargs=argparse.REMAINDER,
                    help="command to run (after `--`)")

    br = sub.add_parser("broker-run", help="run a broker-loom JSON request")
    br.add_argument("--request", required=True, help="path to a broker-run request .json")

    md = sub.add_parser("models", help="list model manifests (no weights)")
    md.add_argument("--catalog", default=None,
                    help="catalog path (default: models.example.yaml)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # argparse.REMAINDER keeps a leading "--"; drop it for a clean argv.
    if getattr(args, "argv", None) and args.argv and args.argv[0] == "--":
        args.argv = args.argv[1:]

    handlers = {
        "version": cmd_version,
        "preflight": cmd_preflight,
        "run": cmd_run,
        "broker-run": cmd_broker_run,
        "models": cmd_models,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())