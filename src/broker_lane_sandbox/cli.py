"""`bls` -- the broker-lane-sandbox CLI seam.

JSON in / JSON out. This is the *stable contract* broker-loom (or any caller) uses to
reach the sandbox; it never imports the sandbox as a library. Subcommands:

  bls version
  bls preflight --policy POLICY              # inspect posture, no execution
  bls run       --policy POLICY -- ARGV...   # default-deny sandboxed execution
  bls broker-run --request REQUEST.json      # broker-loom JSON request seam
  bls models    [--catalog CATALOG]          # list model manifests (no weights)
  bls infer     --request REQUEST.json [--preflight] [--verify-full]
                                             # local-model inference seam (P3)

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
         "schema_version": SCHEMA_VERSION,
         # Contract D12/P4 S1: consumers MUST probe this list before the first
         # `infer` call; an absent capabilities key means the P2 baseline (no
         # infer), present-without "infer-stream" means the P3 baseline (no
         # `--stream`) -- both clean BLOCKED outcomes, never attempt-and-parse.
         "capabilities": ["run", "broker-run", "infer", "models", "preflight",
                          "infer-stream"]},
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError,
            BrokerRunError, PolicyError, TypeError) as exc:
        # UnicodeDecodeError: a non-UTF-8 request FILE must be a structured
        # request_error (exit 2), same as any other unreadable/unparseable body.
        wrapper = request_error(str(exc), request_id)
        _emit(wrapper, args.pretty)
        return 2

    _emit(wrapper, args.pretty)
    return _exit_for_result_status(wrapper["result"]["status"])


def _read_request_id(request_path: str) -> str | None:
    """Best-effort read of a request's correlation id for an early flag-level
    error (the P4 S6 --preflight/--stream conflict), so the id is still echoed.
    Any read/parse problem yields None; the real error surfaces via the flag check."""
    try:
        data = json.loads(Path(request_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("request_id"), str):
        return data["request_id"]
    return None


def cmd_infer(args) -> int:
    # Same boundary discipline as cmd_broker_run: request-shape problems become
    # broker-run's request_error wrapper (exit 2). ModelCacheError / RunnerError
    # never escape run_infer_request -- they arrive here as model_error results.
    from .broker_run import request_error
    from .infer import InferRequestError, run_infer_request
    from .policy import PolicyError

    if args.stream:
        return _cmd_infer_stream(args)

    request_id = None
    try:
        data = json.loads(Path(args.request).read_text(encoding="utf-8"))
        # Echo the correlation id back unchanged even when the request later fails
        # validation (the contract guarantees this). Only a string request_id is valid.
        if isinstance(data, dict) and isinstance(data.get("request_id"), str):
            request_id = data["request_id"]
        wrapper, exit_code = run_infer_request(
            data, preflight=args.preflight, verify_full=args.verify_full
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError,
            InferRequestError, PolicyError, TypeError) as exc:
        wrapper = request_error(str(exc), request_id)
        _emit(wrapper, args.pretty)
        return 2

    _emit(wrapper, args.pretty)
    return exit_code


def _cmd_infer_stream(args) -> int:
    """`bls infer --stream`: the additive P4 JSONL transport (S2/S6). Every event
    is a compact single-line JSON object via StreamEmitter -- ``--pretty`` is
    IGNORED (S2). The terminal ``final`` is emitted exactly once: by
    run_infer_request (through its stream_emitter) on the normal path, or here as a
    single seq-0 ``final`` for a pre-emission failure (flag conflict, unreadable
    request, or a validation error raised BEFORE any event is on the wire). A
    failure AFTER ``start``/chunks are on the wire fails loud with NO ``final`` --
    an absent final is the consumer's INTERRUPTED signal (S4/S6), never a duplicate.
    """
    from .broker_run import request_error
    from .infer import InferRequestError, run_infer_request
    from .policy import PolicyError
    from .streaming import StreamEmitter

    # S6: --preflight and --stream are mutually exclusive -> a single seq-0 final
    # carrying the request_error wrapper, exit 2 (fail-loud, uniform-JSONL).
    if args.preflight:
        request_id = _read_request_id(args.request)
        StreamEmitter(sys.stdout.write, sys.stdout.flush).final(
            request_error("--preflight and --stream are mutually exclusive", request_id)
        )
        return 2

    # Phase 1: read + parse the request. A pre-emission failure is a single final.
    request_id = None
    try:
        data = json.loads(Path(args.request).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        StreamEmitter(sys.stdout.write, sys.stdout.flush).final(
            request_error(str(exc), request_id)
        )
        return 2
    if isinstance(data, dict) and isinstance(data.get("request_id"), str):
        request_id = data["request_id"]

    # Phase 2: validate + run. InferRequestError/PolicyError/TypeError are raised
    # during validation -- BEFORE run_infer_request emits any event -- so the
    # emitter is pristine and a single seq-0 final is correct. run_infer_request
    # itself emits start/chunk/... and the unique terminal final via the emitter,
    # so on success we must NOT emit anything else -- just return its exit code.
    # sys.stdout.flush is wired so every event reaches the pipe when emitted
    # (S7 liveness): on a pipe sys.stdout is block-buffered, so without this
    # the whole stream would arrive as one burst at process exit.
    emitter = StreamEmitter(sys.stdout.write, sys.stdout.flush)
    try:
        _wrapper, exit_code = run_infer_request(
            data, verify_full=args.verify_full, stream_emitter=emitter
        )
    except (InferRequestError, PolicyError, TypeError) as exc:
        emitter.final(request_error(str(exc), request_id))
        return 2
    return exit_code


def cmd_models(args) -> int:
    from .catalog import list_profiles

    catalog = Path(args.catalog) if args.catalog else _default_catalog()
    try:
        summary = list_profiles(catalog)
    except OSError as exc:
        # The default resolves relative to a SOURCE CHECKOUT; an installed copy
        # has no repo root above it. Fail with clean JSON, not a traceback.
        # (Catching the read error, not pre-checking is_file(), keeps
        # non-regular-file paths like process substitution working.)
        _emit(
            {"ok": False,
             "error": f"catalog not found or not readable: {catalog} ({exc}). "
                      "Pass --catalog PATH (the default models.example.yaml "
                      "only resolves from a source checkout)."},
            args.pretty,
        )
        return 2
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

    inf = sub.add_parser("infer", help="run a local-model inference JSON request")
    inf.add_argument("--request", required=True, help="path to an infer request .json")
    inf.add_argument("--preflight", action="store_true",
                     help="verify model + resolve runner, execute nothing")
    inf.add_argument("--verify-full", action="store_true",
                     help="force a full sha256 re-verification of the weights")
    inf.add_argument("--stream", action="store_true",
                     help="emit the additive P4 JSONL event stream on stdout "
                          "(start/chunk/warning/final); --pretty is ignored, and "
                          "--stream is mutually exclusive with --preflight")

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
        "infer": cmd_infer,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())