# P2 broker-loom JSON Seam

P2 adds the broker-facing command that `project-broker-loom` should call when it needs
bounded execution:

```bash
bls broker-run --request request.json
```

The boundary remains process/CLI based. `broker-loom` must not import
`broker_lane_sandbox` modules directly. The broker owns routing, state, ledger, verifier
choice, and handoff interpretation; the sandbox owns execution policy, env scrubbing,
process limits, timeout handling, and JSON results.

## Request Shape

A broker request is one JSON object:

```json
{
  "schema_version": 1,
  "request_id": "broker-job-001",
  "policy": {
    "schema_version": 1,
    "allow_exec": true,
    "allowed_commands": ["python3"],
    "network": "offline",
    "timeout_seconds": 10
  },
  "argv": ["python3", "-c", "print('hello')"],
  "stdin": null,
  "timeout_seconds": 5,
  "working_dir": null
}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `schema_version` | yes | Must equal the sandbox `SCHEMA_VERSION` (`1`). |
| `request_id` | no | Correlation id returned unchanged. Use broker ledger/job id. |
| `policy` | yes | Inline `SandboxPolicy` object. Paths are deliberately not resolved across the seam. |
| `argv` | yes | Non-empty command vector. `argv[0]` must still be a bare allow-listed command. |
| `stdin` | no | Optional text passed to the child process. Must be string or `null`. |
| `timeout_seconds` | no | Per-request override applied to the inline policy. |
| `working_dir` | no | Per-request cwd override applied to the inline policy. |

Unknown request keys fail loud with `request_error`.

## Success / Execution Result Shape

For valid requests, stdout is one wrapper object:

```json
{
  "schema_version": 1,
  "request_id": "broker-job-001",
  "result": {
    "status": "ok",
    "ok": true,
    "argv": ["python3", "-c", "print('hello')"],
    "reason": "completed",
    "exit_code": 0,
    "stdout": "hello\n",
    "stderr": "",
    "duration_ms": 25,
    "truncated": false,
    "network": "offline",
    "env_keys": ["HOME", "LANG", "NO_PROXY", "PATH", "SANDBOX_NETWORK", "TZ", "no_proxy"],
    "limits": {
      "resource_module": true,
      "timeout_seconds": 5,
      "cpu_seconds": null,
      "address_space_bytes": null,
      "max_processes": null,
      "max_file_size_bytes": null,
      "max_output_bytes": 1000000,
      "enforced_rlimits": []
    }
  }
}
```

`result` is the normal `ExecResult` schema. Denials, non-zero exits, timeouts, and spawn
errors are still machine-readable results. `env_keys` lists the names the child actually
received (sorted, names only) — it is environment-dependent, but in `offline` mode always
includes `SANDBOX_NETWORK`, `NO_PROXY`, and `no_proxy`. `limits` is always populated for a
result that ran; it is `{}` only for `denied` / `spawn_error`, which never start a child.

## Request Errors

Malformed broker requests never spawn a child process. They emit JSON and exit `2`:

```json
{
  "schema_version": 1,
  "request_id": null,
  "status": "request_error",
  "ok": false,
  "reason": "argv must be a non-empty list of strings"
}
```

`request_error` is a wrapper status, not an `ExecResult.status`. `request_id` is echoed
unchanged whenever the request provided one (a string) and the body parsed as JSON —
including on this error path. It is `null` when the request omitted `request_id`
(as in the example above), supplied a non-string `request_id` (only string ids are
echoed; the request is rejected with `request_error`), or when the request file
could not be read or parsed as JSON at all.

## Exit Codes

| Condition | Exit |
|-----------|------|
| `result.status == "ok"` | `0` |
| `result.status == "exit_nonzero"` | `1` |
| `result.status == "denied"` or `"spawn_error"` | `2` |
| request-shape error | `2` |
| `result.status == "timeout"` | `124` |

Callers should parse stdout JSON first and use the process exit code only as a coarse
control-flow signal.

## Broker-loom Integration Rules

- Call `bls broker-run --request ...` as a subprocess.
- Treat stdout JSON as the source of truth.
- Record `request_id`, exit code, and `result.status` in the broker ledger.
- Do not pass secrets through the request. The sandbox drops secret-looking env names,
  but broker-loom should still keep secrets out of argv, stdin, and logs.
- Do not pass model weights, runtime caches, or local private paths into tracked fixtures.
- Keep OpenRouter verifier/spec lanes separate: OpenRouter may verify or summarize, but
  it must not be treated as an execution lane.

## Examples

- [`examples/broker_run_request.example.json`](../examples/broker_run_request.example.json)
- [`examples/broker_run_result.ok.example.json`](../examples/broker_run_result.ok.example.json)
- [`examples/broker_run_result.denied.example.json`](../examples/broker_run_result.denied.example.json)
