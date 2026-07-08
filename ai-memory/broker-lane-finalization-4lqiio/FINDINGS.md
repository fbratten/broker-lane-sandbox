# Broker-Lane Finalization - Audit Findings (Iteration 2)

Produced 2026-07-08 by a 32-agent audit workflow: 4 audit dimensions
(doc-drift, correctness, test-gaps, packaging-ci), every raw finding
adversarially verified by an independent skeptic agent against the working tree.
Raw findings: 28. Confirmed: 27. Refuted: 1.

## Confirmed findings

### F01 [doc-drift / medium] README body still says broker-loom integration is "P2 (not yet built)" while the same README declares P2 complete

- **Where:** `README.md` line 48
- **Detail:** README.md:48-49 says "broker-loom integration is **P2** (not yet built)" and README.md:70 says "it does **not** yet integrate with broker-loom (P2)". Both directly contradict README.md:8 ("Status: P2 (broker-loom <-> sandbox CLI/JSON seam) complete -- merged in #3 (2a80000)") and the phase table at README.md:187 ("P2 ... done (merged in #3)"). The P2 seam is in fact implemented (src/broker_lane_sandbox/broker_run.py, `bls broker-run` in cli.py:131, tests/test_broker_run.py), so the two body passages are stale pre-P2 text.
- **Proposed fix:** Update README.md:48-49 to say the sandbox-side P2 seam is delivered (`bls broker-run`) and that broker-loom-side consumption is the next slice; update the README.md:69-70 "does NOT" bullet to drop "does not yet integrate with broker-loom (P2)" (or rephrase as "broker-loom-side consumption not yet built").
- **Skeptic verdict:** Confirmed in the working tree: README.md:48-49 says "broker-loom integration is **P2** (not yet built)" and README.md:69-70 says it "does **not** yet integrate with broker-loom (P2)", directly contradicting README.md:8 and the phase table at README.md:186 which declare P2 done (merged in #3); the seam genuinely exists (src/broker_lane_sandbox/broker_run.py, "broker-run" subparser at cli.py:131, tests/test_broker_run.py), so both body passages are stale pre-P2 text, distinct from the already-tracked "42 tests" finding.

### F02 [doc-drift / medium] README CLI usage section omits the delivered `bls broker-run` subcommand

- **Where:** `README.md` line 163
- **Detail:** The "CLI usage (bls)" block (README.md:163-168) lists only `version`, `preflight`, `run`, and `models`, but cli.py:131 registers a fifth subcommand `broker-run` (and cli.py's module docstring lines 6-10 lists all five). Since the README's own status line touts the P2 broker-run seam as the headline deliverable, the usage section is stale: the flagship P2 command is invisible to a reader of the CLI section. The exit-code summary on README.md:160-161 (0/1/2/124) is also silent that request-shape errors from broker-run exit 2 (cli.py:96-99).
- **Proposed fix:** Add `bls broker-run --request request.json   # broker-loom JSON request seam` to the usage block and link docs/P2_BROKER_LOOM_SEAM.md; optionally note request_error also exits 2.
- **Skeptic verdict:** Confirmed: README.md:163-168 usage block omits broker-run (no mention anywhere in README), while src/broker_lane_sandbox/cli.py:131 registers it and the docstring (lines 6-10) lists all five subcommands; P2 broker-run is delivered scope per README's own status table (line 186).

### F03 [doc-drift / medium] MANUAL CLI command/exit-code table omits `bls broker-run` (and the manual never documents the P2 seam)

- **Where:** `docs/MANUAL.md` line 101
- **Detail:** The section-4 table (docs/MANUAL.md:101-106) documents only `version`, `preflight`, `run`, `models` -- `broker-run` (cli.py:131-132) is missing entirely, along with its `request_error` -> exit 2 path (cli.py:96-99). README.md:177-178 explicitly points readers here for "the full policy schema, result schema, exit-code table", yet the exit-code table is incomplete for a shipped subcommand. Meanwhile the same manual's roadmap (docs/MANUAL.md:250) marks P2 "merged (#3)", and the intro (docs/MANUAL.md:3) still frames the doc as "the P1 safe-exec core" only.
- **Proposed fix:** Add a `bls broker-run --request R` row (exits: 0 ok, 1 exit_nonzero, 2 denied/spawn_error/request_error, 124 timeout) to the section-4 table with a cross-link to docs/P2_BROKER_LOOM_SEAM.md, and widen the intro from "P1 safe-exec core" to include the P2 seam.
- **Skeptic verdict:** Confirmed: src/broker_lane_sandbox/cli.py:131-132 ships `broker-run` (with request_error -> exit 2 at cli.py:96-99), but the MANUAL's CLI/exit-code table (docs/MANUAL.md:101-106) omits it and the only mention of the P2 seam in the manual is the roadmap row at line 250, while the intro (line 3) still scopes the doc to "the P1 safe-exec core" and README.md:177-178 points readers to that table.

### F04 [doc-drift / medium] broker_run_result.ok.example.json is stale: `limits` object is missing `max_file_size_bytes`

- **Where:** `examples/broker_run_result.ok.example.json` line 15
- **Detail:** The fixture's `limits` object (lines 15-27) lacks the `max_file_size_bytes` key, but `limits_summary()` (src/broker_lane_sandbox/limits.py:54) unconditionally emits it since commit 2285cbc. Verified live: `PYTHONPATH=src python3 -m broker_lane_sandbox.cli broker-run --request examples/broker_run_request.example.json` returns `"limits": {..., "max_processes": 32, "max_file_size_bytes": null, "max_output_bytes": 1000000, ...}`. docs/P2_BROKER_LOOM_SEAM.md:75 was updated to include the field, but the fixture it links at line 135 was not, so the shipped contract example shows a result shape the sandbox never produces.
- **Proposed fix:** Add `"max_file_size_bytes": null` to the `limits` object in examples/broker_run_result.ok.example.json (between `max_processes` and `max_output_bytes`, matching limits_summary key order).
- **Skeptic verdict:** Confirmed: examples/broker_run_result.ok.example.json:15-27 lacks max_file_size_bytes, but limits_summary() (src/broker_lane_sandbox/limits.py:54) unconditionally emits it — verified by running the CLI live, which produced the key (null) between max_processes and max_output_bytes; docs/P2_BROKER_LOOM_SEAM.md:75 documents the field and line 135 links the stale fixture as the P2 contract example.

### F05 [doc-drift / low] P2 seam doc's "request_id is null only when..." claim is wrong for a non-string request_id

- **Where:** `docs/P2_BROKER_LOOM_SEAM.md` line 105
- **Detail:** docs/P2_BROKER_LOOM_SEAM.md:104-106 claims request_id "is null only when the request omitted request_id, or when the body could not be parsed as JSON at all." Verified counterexample: a request with `"request_id": 123` parses as JSON and provides a request_id, yet returns `{"request_id": null, "status": "request_error", "reason": "request_id must be a string when set"}` because cli.py:93 only captures string ids. An unreadable request file (OSError, cli.py:96) is a second uncovered null case.
- **Proposed fix:** Amend the sentence to: null when the request omitted request_id, supplied a non-string request_id, or the request file could not be read / parsed as JSON.
- **Skeptic verdict:** Reproduced live: a request with "request_id": 123 returns {"request_id": null, ..., "reason": "request_id must be a string when set"} (cli.py:93 captures only string ids; broker_run.py:81 rejects non-strings), contradicting docs/P2_BROKER_LOOM_SEAM.md:105's "null only when omitted or unparseable JSON"; an unreadable request file (OSError caught at cli.py:96) is a second uncovered null case.

### F06 [doc-drift / low] README description of policy.example.json omits its file-size (RLIMIT_FSIZE) cap

- **Where:** `README.md` line 176
- **Detail:** README.md:176-177 describes policy.example.json as "default-deny; allows echo/python3, offline, with CPU/AS/process caps", but since commit 2285cbc the example policy also sets `"max_file_size_bytes": 104857600` (policy.example.json:20), i.e. a per-file write cap via RLIMIT_FSIZE. The README's own feature list (line 59-60) advertises the per-file write-size limit, so the starter-policy description is one field behind the file it describes.
- **Proposed fix:** Change the parenthetical to "with CPU/AS/process/file-size caps" (or "CPU/AS/NPROC/FSIZE caps").
- **Skeptic verdict:** Confirmed: README.md:176-177 says the starter policy has "CPU/AS/process caps" but policy.example.json:20 also sets max_file_size_bytes (104857600), a per-file RLIMIT_FSIZE cap that README.md:59-60 lists as a delivered feature; the description is one field behind the file.

### F07 [doc-drift / low] README source layout list omits the broker_run module

- **Where:** `README.md` line 203
- **Detail:** README.md:203-204 lists the package contents as "src/broker_lane_sandbox/ (policy, envscrub, limits, executor, preflight, catalog, result, cli, runners)" but src/broker_lane_sandbox/broker_run.py -- the P2 seam module -- exists and is not listed. Stale since PR #3 added the module.
- **Proposed fix:** Add `broker_run` to the module list in the Layout sentence.
- **Skeptic verdict:** README.md:203-204 lists modules "policy, envscrub, limits, executor, preflight, catalog, result, cli, runners" but src/broker_lane_sandbox/broker_run.py exists in the working tree and is omitted; broker_run is the delivered P2 seam module, so the omission is in-scope doc drift.

### F08 [correctness / high] Non-UTF-8 child output crashes the executor (text=True with strict decoding) instead of returning an ExecResult

- **Where:** `src/broker_lane_sandbox/executor.py` line 77
- **Detail:** SafeExecutor.run opens the child with subprocess.Popen(..., text=True) (line 77) and no errors= argument, so communicate() (line 89) decodes stdout/stderr as strict UTF-8. Any policy-permitted command that writes non-UTF-8 bytes to a pipe raises UnicodeDecodeError, which escapes run() entirely. Confirmed reproduction: policy with allow_exec=True, allowed_commands=["printf"], run(["printf", r"\xff\xfe\x80"]) -> `CRASHED: UnicodeDecodeError 'utf-8' codec can't decode byte 0xff in position 0`. This is a normal, allow-listed run (e.g. `cat` of a binary file, `gzip`, image/PDF tooling, or any program emitting a stray Latin-1 byte), yet it produces an unhandled traceback rather than an ExecResult. It also breaks the broker-run seam: run_broker_request -> SafeExecutor.run propagates the UnicodeDecodeError, and cmd_broker_run only catches (OSError, json.JSONDecodeError, BrokerRunError, PolicyError, TypeError) at cli.py:96 -- UnicodeDecodeError (a ValueError subclass) is not in that tuple, so `bls broker-run` dies with a stack trace and no JSON body/request_id echo, violating the documented 'every attempt returns JSON' contract. The 70-test suite only exercises ASCII/text output so it never triggers this.
- **Proposed fix:** Decode leniently: pass errors="replace" (or "backslashreplace") to Popen, or capture bytes (text=False) and decode with errors="replace" before truncation. That keeps stdout/stderr JSON-serializable strings for any byte stream while preserving the offline/limit semantics.
- **Skeptic verdict:** Confirmed by live reproduction: executor.py:77 uses text=True with strict UTF-8, so an allow-listed `printf '\xff'` raises UnicodeDecodeError out of SafeExecutor.run, and `bls broker-run` exits 1 with a traceback and zero JSON output because cli.py:96's except tuple omits UnicodeDecodeError; no doc declares UTF-8-only output as an intentional limitation.

### F09 [correctness / medium] Empty-string env_passthrough_prefix silently passes the entire environment, defeating the default-empty env guarantee

- **Where:** `src/broker_lane_sandbox/envscrub.py` line 27
- **Detail:** _name_allowed returns True when any prefix satisfies name.startswith(pfx). An empty-string prefix makes name.startswith("") True for every variable, so the whole of os.environ passes into the child (only names matching SECRET_NAME_RE are still dropped). SandboxPolicy.__post_init__ (policy.py) type-hardens numeric fields and validates network/allowed_commands, but never validates env_passthrough_prefixes, so "" (or a stray blank line in YAML) is accepted silently. Confirmed: policy with env_allowlist=[], env_passthrough_prefixes=[""] yields a child env of 108 vars including SOME_RANDOM_VAR and ANOTHER_ONE. This contradicts the module's stated contract ('the child starts with nothing') and is exactly the passthrough-prefix abuse the sandbox is meant to prevent; because secret-regex is name-only, credential-bearing names that don't match the regex (e.g. DATABASE_URL) leak too.
- **Proposed fix:** Reject empty/whitespace-only prefixes in SandboxPolicy.__post_init__ with a PolicyError (fail loud), and/or make _name_allowed treat an empty prefix as a non-match. Same validation should confirm every prefix is a non-empty str.
- **Skeptic verdict:** Reproduced: SandboxPolicy(env_allowlist=[], env_passthrough_prefixes=[""]) is accepted without error (policy.py __post_init__ lines 115-149 never validates env_passthrough_prefixes), and envscrub.py:27's name.startswith("") matches every var, so build_child_env passed DATABASE_URL and SOME_RANDOM_VAR into the child (only regex-matching API_KEY was dropped) — contradicting the module's default-empty contract and the project's fail-loud rule, with no validation or documented limitation anywhere in the repo.

### F10 [correctness / low] Malformed catalog profile (non-dict value) crashes `bls models` with an opaque AttributeError instead of a clear PolicyError

- **Where:** `src/broker_lane_sandbox/catalog.py` line 34
- **Detail:** load_catalog validates the top-level object (`if not isinstance(data, dict): raise PolicyError`), but list_profiles then assumes every value under `profiles` is a dict and calls prof.get(...) at lines 34-38. A profile whose value is a string/list raises AttributeError. Confirmed: catalog {"profiles": {"foo": "notadict"}} -> `CATALOG CRASH: AttributeError 'str' object has no attribute 'get'`. cmd_models (cli.py:105) does not wrap this, so the CLI exits with a raw traceback. This is inconsistent with the module's own 'fail loud with guidance' posture and the careful top-level type check just above.
- **Proposed fix:** In list_profiles, validate each profile is a mapping (`if not isinstance(prof, dict): raise PolicyError(f"catalog profile {name!r} must be a mapping")`), or coerce/skip non-dict entries with a clear PolicyError message.
- **Skeptic verdict:** Confirmed by live repro: catalog {"profiles": {"foo": "notadict"}} makes `bls models` crash with a raw AttributeError traceback at catalog.py:34 (no per-profile isinstance check; cmd_models at cli.py:105 and main() do not wrap it). `bls models` is a delivered, README-documented command (P0/P1 manifest listing, not P3), and the module docstring promises fail-loud-with-guidance, so the opaque crash is a real in-scope defect.

### F11 [test-gaps / high] broker-run `stdin` field is completely untested (delivery and type validation)

- **Where:** `src/broker_lane_sandbox/broker_run.py` line 102
- **Detail:** docs/P2_BROKER_LOOM_SEAM.md documents `stdin` as a request field ("Optional text passed to the child process. Must be string or null.") and broker_run.py:87-89 validates it and passes it via `SafeExecutor(policy).run(argv, input_text=stdin)` at line 102. No test in tests/test_broker_run.py (or anywhere) exercises stdin: neither the happy path (stdin text actually reaching the child's standard input) nor the validation rejection (non-string stdin -> BrokerRunError "stdin must be a string or null when set"). If `input_text=stdin` were dropped from the run() call, or the executor stopped wiring `stdin=subprocess.PIPE` (executor.py:74), every test would still pass while broker-loom requests using stdin silently sent nothing to the child.
- **Proposed fix:** Add to tests/test_broker_run.py: (1) a request with `"stdin": "ping\n"` and argv `["python3", "-c", "import sys; print(sys.stdin.read().strip())"]` asserting `wrapper["result"]["stdout"] == "ping\n"`; (2) `pytest.raises(BrokerRunError, match="stdin must be a string")` for `"stdin": 42` or `["x"]`.
- **Skeptic verdict:** Confirmed: stdin is documented (docs/P2_BROKER_LOOM_SEAM.md:43), validated (src/broker_lane_sandbox/broker_run.py:87-89), and forwarded at line 102 via input_text, but a repo-wide grep for stdin/input_text/PIPE finds zero matches in tests/ — neither delivery nor type-rejection is tested anywhere.

### F12 [test-gaps / high] Per-request `timeout_seconds` / `working_dir` overrides in broker-run have no tests

- **Where:** `src/broker_lane_sandbox/broker_run.py` line 96
- **Detail:** docs/P2_BROKER_LOOM_SEAM.md documents request-level `timeout_seconds` ("Per-request override applied to the inline policy") and `working_dir` ("Per-request cwd override"). The implementation is broker_run.py:96-99, which copies the two fields into the policy mapping before construction. No test sends either field at the request level (tests only set timeout_seconds inside the inline policy). Deleting lines 96-99 leaves the whole suite green while a broker request's timeout/cwd override is silently ignored — the child would run under the policy's (possibly 30s default) timeout instead of the requested one.
- **Proposed fix:** Add tests: a request with policy `timeout_seconds: 30` plus request-level `"timeout_seconds": 1` and a sleeping child, asserting `result["status"] == "timeout"` and `result["limits"]["timeout_seconds"] == 1`; and a request with `"working_dir": str(tmp_path)` plus argv printing `os.getcwd()`, asserting the child ran in tmp_path (and a bad request-level working_dir yields `spawn_error`).
- **Skeptic verdict:** Confirmed empirically: deleting broker_run.py:96-99 (the request-level timeout_seconds/working_dir override block) leaves the entire 70-test suite green, while docs/P2_BROKER_LOOM_SEAM.md:44-45 documents both overrides as delivered P2 behavior; the only timeout_seconds in tests/test_broker_run.py is inside the inline policy (line 16), never at the request level. File was restored byte-identical after the mutation check.

### F13 [test-gaps / high] Exit codes 1 (exit_nonzero) and 124 (timeout) are documented but never tested at any CLI boundary

- **Where:** `src/broker_lane_sandbox/cli.py` line 35
- **Detail:** README.md:160-161, MANUAL.md section 4, and P2_BROKER_LOOM_SEAM.md's exit-code table all promise the mapping 0 ok / 1 ran-but-nonzero / 2 denied-or-error / 124 timeout, and the seam doc tells broker-loom to branch on it. Tests cover only exit 0 (test_cli_run_executes, test_broker_run_cli_emits_json_and_exit_zero) and exit 2 (test_cli_run_denied_returns_nonzero, request-error tests). `_exit_for_result_status` (cli.py:35-42) returns 1 as an unguarded fall-through and 124 for timeout — a regression that mapped timeout to 1, or exit_nonzero to 2 (which broker-loom would misread as 'denied, do not retry'), passes the entire suite.
- **Proposed fix:** Add CLI-level tests pinning the remaining codes: `bls run` (or `broker-run`) with argv `python3 -c "import sys; sys.exit(7)"` asserting `main(...) == 1`, and with `--timeout 1` (or request-level timeout_seconds 1) plus a sleeping child asserting `main(...) == 124` and `status == "timeout"`. A cheap unit test over `_exit_for_result_status` for all five statuses also works, but at least one end-to-end 124 case is worth having.
- **Skeptic verdict:** Confirmed: docs promise the 0/1/2/124 mapping (docs/MANUAL.md:105, docs/P2_BROKER_LOOM_SEAM.md:116, README.md:161) and _exit_for_result_status (cli.py:35-42) implements it, but no test asserts main() returning 1 or 124 — CLI/broker-run tests only assert exit codes 0 and 2 (test_safe_exec.py:407-449, test_broker_run.py:87/103/122/180); EXIT_NONZERO and TIMEOUT are covered only at the executor result-status level, so a regression in the CLI exit-code mapping would pass the suite.

### F14 [test-gaps / medium] broker-run request `schema_version` mismatch rejection is untested

- **Where:** `src/broker_lane_sandbox/broker_run.py` line 74
- **Detail:** P2_BROKER_LOOM_SEAM.md marks request `schema_version` as required: "Must equal the sandbox SCHEMA_VERSION (1)". broker_run.py:73-77 enforces it, but no test sends a wrong or missing schema_version. If this check were loosened or dropped (e.g. during a schema_version=2 migration), a version-skewed broker-loom request would be silently accepted under the wrong contract instead of failing loud with request_error — exactly the fail-loud versioning promise the seam makes.
- **Proposed fix:** Add a test: `pytest.raises(BrokerRunError, match="schema_version")` for requests with `"schema_version": 2` and with the key omitted; optionally one CLI-level assertion that the wrapper is `status: request_error`, exit 2.
- **Skeptic verdict:** broker_run.py:73-77 raises BrokerRunError on schema_version mismatch (missing key -> None also fails), but every test in tests/test_broker_run.py sends "schema_version": 1 and none exercises a wrong/missing value; the only version-mismatch test (tests/test_safe_exec.py:64) covers SandboxPolicy, not the P2 broker-run request boundary.

### F15 [test-gaps / medium] `network: "online"` contract (SANDBOX_NETWORK=online, proxies left intact) has zero coverage

- **Where:** `src/broker_lane_sandbox/envscrub.py` line 59
- **Detail:** README.md's Network policy section documents the opt-out: "`network: \"online\"` opts out (sets SANDBOX_NETWORK=online, leaves proxies intact)". envscrub.py:59-60 implements only the `SANDBOX_NETWORK=online` half of the branch. Every env test uses offline mode. A regression in the online branch — stripping allow-listed proxy vars anyway, setting NO_PROXY=*, or failing to set SANDBOX_NETWORK=online — breaks the documented opt-out (e.g. a future P3 model-weight fetch step relying on it) with no failing test.
- **Proposed fix:** Add a build_child_env test with `network="online"` and `env_allowlist=["HTTPS_PROXY"]` (monkeypatched): assert `child["SANDBOX_NETWORK"] == "online"`, `child["HTTPS_PROXY"]` survives, and `"NO_PROXY"` is not injected.
- **Skeptic verdict:** Confirmed: envscrub.py:59-60 implements the documented online opt-out (README.md:144-146), but every network-related test in tests/test_safe_exec.py uses network="offline" (or the invalid "wifi" rejection case); grep finds no test asserting SANDBOX_NETWORK=="online" or proxy-var survival, so the delivered P1 online branch has zero coverage.

### F16 [test-gaps / medium] `env_passthrough_prefixes` policy field is documented but never tested

- **Where:** `src/broker_lane_sandbox/envscrub.py` line 27
- **Detail:** MANUAL.md's policy-schema table documents `env_passthrough_prefixes` ("env-name prefixes passed to the child") and README.md's Environment scrubbing section promises names "matching an env_passthrough_prefixes entry are passed through". The only implementation is the one-line prefix match in `_name_allowed` (envscrub.py:27). No test sets the field. If prefix matching broke (or, worse, started bypassing the secret-name drop for prefixed names like `MYAPP_API_KEY`), nothing fails.
- **Proposed fix:** Add a build_child_env test with `env_passthrough_prefixes=["MYAPP_"]` and monkeypatched `MYAPP_MODE=x`, `OTHER=y`, `MYAPP_API_KEY=s`: assert MYAPP_MODE passes, OTHER does not, and MYAPP_API_KEY is dropped and listed in dropped_secret (secret guard still applies to prefix-matched names).
- **Skeptic verdict:** envscrub.py:27 is the sole implementation of the documented env_passthrough_prefixes field (MANUAL.md:70, README.md:125), and a grep of tests/ shows no test mentions "prefix" or sets the field — test_safe_exec.py only covers env_allowlist, secret drops, and proxy stripping (lines 169-195).

### F17 [test-gaps / medium] Executor gate 1 (empty argv -> denied result) is untested; a reorder would turn it into an IndexError crash

- **Where:** `src/broker_lane_sandbox/executor.py` line 41
- **Detail:** README.md:86 documents gate 1: "non-empty argv -> else denied", and the contract promises "policy denials are results, not crashes". `SafeExecutor.run([])` is handled at executor.py:41-42, but no test calls the executor (or `bls run --policy p --` with nothing after `--`, cli.py:68-71) with an empty argv — the only empty-argv tests hit the broker_run validator, a different layer. If gate 1 were removed or reordered below the `is_bare_command(argv[0])` check, `run([])` would raise IndexError instead of returning a denied ExecResult, silently breaking the results-not-crashes contract for direct `bls run` callers.
- **Proposed fix:** Add: `r = SafeExecutor(_exec_policy()).run([])` asserting `r.status == Status.DENIED` and `"empty argv" in r.reason`; plus a CLI test that `cli.main(["run", "--policy", str(pf), "--"])` returns 2 with a denied JSON body ("no command given after --").
- **Skeptic verdict:** Confirmed: executor.py:41-42 implements gate 1 ("empty argv" denial) and README.md:86,95-96 document it, but no test calls SafeExecutor.run([]) or the CLI run path with empty argv (grep for run([]) hits only source/build copies; test_broker_run.py:96,114 test the separate broker_run validator layer). A reorder below the argv[0] access at executor.py:45 would indeed raise IndexError.

### F18 [test-gaps / medium] spawn_error for an allow-listed command missing from PATH is documented but untested

- **Where:** `src/broker_lane_sandbox/executor.py` line 81
- **Detail:** MANUAL.md section 6 promises: "Pre-spawn failures (missing exe, bad cwd, rlimit above the host ceiling) become spawn_error results, not crashes." Bad cwd and rlimit-above-hard each have a regression test, but the first-listed case — an allow-listed command that does not resolve on PATH (FileNotFoundError from Popen, caught at executor.py:81) — has none. If FileNotFoundError were dropped from the except tuple during a refactor, `run()` would raise across the CLI seam (traceback on stderr, no JSON) for any stale allow-list entry, and no test would notice.
- **Proposed fix:** Add: policy `_exec_policy(allowed_commands=["definitely-not-a-real-binary-xyz"])`, run `["definitely-not-a-real-binary-xyz"]`, assert `r.status == Status.SPAWN_ERROR` and `"could not start process" in r.reason` (and optionally exit 2 via the CLI).
- **Skeptic verdict:** Confirmed: MANUAL.md:148-149 promises missing-exe -> spawn_error, executor.py:81 handles it, and no test anywhere exercises run() with a missing binary (test_safe_exec.py:384 only tests preflight warnings; bad-cwd and rlimit cases have tests at :298 and :365). Caveat: the stated failure scenario is overstated — FileNotFoundError is a subclass of OSError (also in the except tuple), so dropping only FileNotFoundError would not break behavior; the gap is real but the regression requires narrowing past OSError.

### F19 [test-gaps / medium] broker-run CLI contract for an unparseable/missing request file (JSON request_error, exit 2, request_id null) is untested

- **Where:** `src/broker_lane_sandbox/cli.py` line 96
- **Detail:** P2_BROKER_LOOM_SEAM.md documents that malformed requests "emit JSON and exit 2" and that `request_id` "is null ... when the body could not be parsed as JSON at all (as in the example above)" — the doc's own request_error example is this exact case. cli.py:96 implements it by catching OSError and json.JSONDecodeError. No test feeds broker-run a non-JSON body or a nonexistent --request path. If either exception were dropped from the catch tuple, broker-loom would receive a raw traceback and a non-contract exit code instead of the documented machine-readable request_error.
- **Proposed fix:** Add two CLI tests: (1) request file containing `not json {` -> exit 2, stdout parses as JSON with `status == "request_error"`, `request_id is None`; (2) `--request /no/such/file.json` -> exit 2 with a request_error JSON body.
- **Skeptic verdict:** cli.py:96 catches OSError/json.JSONDecodeError per the P2 contract (P2_BROKER_LOOM_SEAM.md:96-106), but all broker-run CLI tests in tests/test_broker_run.py (lines 70/93/109/160) feed valid-JSON files failing validation; no test exercises a non-JSON body or nonexistent --request path, so those branches are unverified.

### F20 [test-gaps / medium] `bls run` --timeout / --cwd per-invocation overrides are documented but untested

- **Where:** `src/broker_lane_sandbox/cli.py` line 74
- **Detail:** MANUAL.md section 4 documents: "`--timeout` / `--cwd` on `run` override the policy's `timeout_seconds` / `working_dir` for that invocation", and the README's worked example relies on `--timeout 1`. The implementation is cli.py:74-77 (two unguarded assignments onto the loaded policy). No test passes either flag, so silently dropping the override (or applying it to the wrong field) keeps the suite green while the documented worked example (`bls run ... --timeout 1 -- python3 -c "time.sleep(30)"` -> exit 124) stops working. Note these assignments also bypass SandboxPolicy validation (e.g. `--timeout 0` or a negative value is accepted), so a test would also pin whatever behavior is intended there.
- **Proposed fix:** Add CLI tests: `cli.main(["run", "--policy", pf, "--timeout", "1", "--", PYBIN, "-c", "import time; time.sleep(30)"])` asserting exit 124 / status timeout; and `--cwd str(tmp_path)` with a child printing os.getcwd(), asserting the override took effect.
- **Skeptic verdict:** Confirmed: cli.py:74-77 applies --timeout/--cwd as unguarded assignments onto the loaded policy (bypassing SandboxPolicy validation, which elsewhere rejects timeout_seconds=0), MANUAL.md lines 105/108/206 document the overrides including a worked --timeout 1 -> exit 124 example, and grep of tests/ shows no test ever passes --timeout or --cwd through cli.main (CLI run tests at tests/test_safe_exec.py:416,424 use neither flag). Minor correction: the worked example is in docs/MANUAL.md, not README.md, but the untested-documented-feature claim holds and is within delivered P2 scope.

### F21 [test-gaps / low] preflight exit-code mapping (1 on warnings) is not pinned — the existing test accepts either code

- **Where:** `tests/test_safe_exec.py` line 437
- **Detail:** MANUAL.md's CLI table documents `bls preflight` exit codes as "0 ok, 1 warnings". The only CLI-level preflight test asserts `rc in (0, 1)` (tests/test_safe_exec.py:437), which passes regardless of whether the mapping in cmd_preflight (cli.py:61, `return 0 if report["ok"] else 1`) works at all. A regression returning 0 unconditionally — hiding a not-on-PATH command or secret-looking allow-list warning from a scripted caller — is invisible to the suite. The warning content itself is only tested at the library (preflight()) level.
- **Proposed fix:** Add one CLI test with a policy that must warn (e.g. `allowed_commands: ["definitely-not-a-real-binary-xyz"]`) asserting `cli.main(["preflight", ...]) == 1`, and tighten the existing test to assert the specific expected code for its clean-ish policy (or assert `rc == (0 if out["ok"] else 1)`).
- **Skeptic verdict:** Confirmed: tests/test_safe_exec.py:437 asserts `rc in (0, 1)` (the only CLI-level preflight test), so the exit-code mapping at cli.py:61 (`return 0 if report["ok"] else 1`) documented in docs/MANUAL.md:104 ("0 ok, 1 warnings") is unpinned; warning cases are only tested at the library preflight() level (lines 378-387), so an unconditional-0 regression would pass the suite.

### F22 [packaging-ci / high] Pre-commit guard hook is tracked without the executable bit, so the documented enablement silently does nothing

- **Where:** `/home/user/broker-lane-sandbox/.githooks/pre-commit` line 1
- **Detail:** The hook is stored in git as mode 100644 (`git ls-files -s .githooks/pre-commit` -> `100644 c0c4cc9...`) and checks out as `-rw-r--r--`. Git refuses to run non-executable hooks: after the README-documented setup (`git config core.hooksPath .githooks`, README.md:198) a fresh clone on Linux/WSL gets only a hint message ('hook was ignored because it's not set as executable') and commits proceed with NO INVARIANT-1 pre-commit check. README.md:113-116 claims the guard is 'enforced ... as a pre-commit hook' and that 'even git add -f weights.gguf is refused' — on a fresh clone it is not; only CI catches it after push. The hook script's own header comment (.githooks/pre-commit:3) documents the same enablement path that fails.
- **Proposed fix:** Set the executable bit in the index and commit it: `git update-index --chmod=+x .githooks/pre-commit` (works from Windows too). Optionally add a tracked-mode assertion to tests/test_model_artifact_invariant.py so a regression is caught in CI.
- **Skeptic verdict:** Confirmed: `git ls-files -s .githooks/pre-commit` shows mode 100644 and the checked-out file is -rw-r--r--, so after the README:198-documented `core.hooksPath` setup git skips the non-executable hook on Linux/WSL, contradicting README:113-114's claim that INVARIANT-1 is enforced pre-commit; this is delivered P0 scope and not a documented limitation.

### F23 [packaging-ci / medium] CI tests only Python 3.12 while the package claims requires-python >=3.10

- **Where:** `/home/user/broker-lane-sandbox/.github/workflows/ci.yml` line 18
- **Detail:** pyproject.toml:8 declares `requires-python = ">=3.10"` and README.md:205 / docs/MANUAL.md:23 repeat 'Python >= 3.10', but the single CI job pins `python-version: "3.12"` with no matrix. 3.10 and 3.11 are never exercised, so the >=3.10 claim is unverified by CI (I found no 3.11+-only syntax by grep, but nothing enforces this staying true).
- **Proposed fix:** Use a matrix, e.g. `strategy: {matrix: {python-version: ["3.10", "3.11", "3.12", "3.13"]}}` and `python-version: ${{ matrix.python-version }}`; or narrow requires-python to what is actually tested.
- **Skeptic verdict:** Confirmed: ci.yml:18 pins python-version "3.12" with no matrix while pyproject.toml:8 declares requires-python ">=3.10" and README.md:205 / docs/MANUAL.md:23 claim Python >= 3.10; nothing in the repo documents 3.12-only CI as intentional, so the >=3.10 support claim is untested.

### F24 [packaging-ci / medium] `bls models` default catalog path is broken on any installed copy (works only from a source checkout)

- **Where:** `/home/user/broker-lane-sandbox/src/broker_lane_sandbox/cli.py` line 32
- **Detail:** `_default_catalog()` computes `Path(__file__).resolve().parents[2] / "models.example.yaml"`, which is the repo root only in a src-layout checkout. After a normal `pip install .` (the purpose of the `[project.scripts] bls` entry point, pyproject.toml:19), `parents[2]` is `<venv>/lib/pythonX.Y/`. Verified in a clean venv: `bls models` (exactly as documented in README.md:167 and docs/MANUAL.md:106, no --catalog) crashes with `FileNotFoundError: .../venv-inst/lib/python3.11/models.example.yaml`. models.example.yaml is not packaged as package data, so no installed layout can find it.
- **Proposed fix:** Ship the example catalog as package data (e.g. src/broker_lane_sandbox/data/models.example.yaml + importlib.resources) and point _default_catalog() at it; or, simpler for the MVP, make --catalog effectively required by failing with a clean 'no catalog found; pass --catalog' PolicyError-style message when the source-checkout default does not exist.
- **Skeptic verdict:** Confirmed: cli.py:32 computes parents[2] (repo root only in a src-layout checkout), models.example.yaml is absent from the packaged tree (build/lib contains no data files; pyproject.toml has no package-data), and a live repro of the installed layout crashes `bls models` with FileNotFoundError on `<prefix>/lib/python3.11/models.example.yaml` (exit 1, raw traceback). Minor caveat: the docs only show `pip install -e .` (MANUAL.md:35), under which the default happens to work because __file__ points back into the checkout — but plain `pip install .` of the shipped `bls` entry point (pyproject.toml:19) is broken exactly as described.

### F25 [packaging-ci / low] CI never installs the package, so the entry point / packaging metadata are untested

- **Where:** `/home/user/broker-lane-sandbox/.github/workflows/ci.yml` line 19
- **Detail:** The workflow installs only pytest and runs the suite from the checkout; tests/conftest.py:6 inserts src/ onto sys.path, so `pip install .`, the src-layout auto-discovery, and the `bls` console script (pyproject.toml:19) are never exercised anywhere. This is exactly why the installed-mode `bls models` breakage (previous finding) went unnoticed: everything that runs in CI uses the checkout layout.
- **Proposed fix:** Add a smoke step to ci.yml: `python -m pip install .` then `bls version && bls preflight --policy policy.example.json && bls models` (the last will fail until the catalog finding is fixed).
- **Skeptic verdict:** Confirmed: ci.yml installs only pytest (lines 19-24) and tests/conftest.py:6 puts src/ on sys.path, so the bls console script (pyproject.toml:19) and installed layout are never exercised; docs/MANUAL.md:35 advertises pip install as supported, and cli.py:32's __file__-relative catalog default demonstrably breaks only when installed, which CI cannot detect.

### F26 [packaging-ci / low] pyproject.toml has no [build-system] table; install relies on pip's legacy setuptools fallback

- **Where:** `/home/user/broker-lane-sandbox/pyproject.toml` line 1
- **Detail:** The file declares PEP 621 `[project]` metadata and a src layout but no `[build-system]` table. pip then falls back to `setuptools>=40.8.0` + the legacy backend; PEP 621 metadata and src-layout auto-discovery only work because pip's isolated build env happens to pull a modern setuptools (>=61). A build with an older pinned setuptools (or `--no-build-isolation` in an env with setuptools <61) silently ignores the [project] table. Install did succeed in my clean-venv test, so this is a robustness gap, not a current breakage.
- **Proposed fix:** Add:
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"
- **Skeptic verdict:** Confirmed: /home/user/broker-lane-sandbox/pyproject.toml has PEP 621 [project] metadata, a [project.scripts] entry point, and src layout but no [build-system] table (grep across the repo finds none), so builds rely on pip's legacy setuptools>=40.8.0 fallback where setuptools <61 would silently ignore the [project] table; not documented as intentional anywhere.

### F27 [packaging-ci / low] Model-artifact guard anchors cache-directory checks at the repo root while .gitignore matches those directories at any depth

- **Where:** `/home/user/broker-lane-sandbox/scripts/check_model_artifacts.py` line 85
- **Detail:** .gitignore lines 6-13 use unanchored patterns (`models/`, `model-cache/`, `.cache/`, `hf-cache/`, ...) which git applies at ANY depth, but the guard uses `low.startswith(p)` against repo-relative paths, so it only flags those directories at the repo root. A `git add -f tests/models/weights.dat` or `src/.cache/blob` (no forbidden extension, under the 5 MB cap) passes both `--staged` and `--tracked` even though .gitignore's INVARIANT-1 intent is to keep any such cache directory out of git. Weight-extension files are still caught by the extension check, so exposure is limited to non-standard-extension cache contents.
- **Proposed fix:** Match path segments instead of prefixes, e.g. derive dir names from FORBIDDEN_DIR_PREFIXES and flag when any segment of `rel.lower().split('/')[:-1]` is in {"models", "model-cache", "runtime", ".cache", ".huggingface", "hf-cache", "ollama", "llama.cpp"}.
- **Skeptic verdict:** Empirically reproduced: with the repo's .gitignore, git check-ignore confirms `models/` and `.cache/` match at any depth (tests/models/weights.dat, src/.cache/blob), yet after `git add -f` the guard exits 0 because scripts/check_model_artifacts.py:85 uses `low.startswith(p)` on repo-relative paths (root-anchored only); tests only cover root-level cache dirs and no doc declares root-only matching intentional.

## Refuted findings

- (correctness) max_output_bytes is enforced as a character count, not bytes, so multibyte output can exceed the configured cap several-fold - refuted: The char-vs-byte behavior exists (executor.py:147 slices by len(text)), but it is an intentional documented limitation: docs/MANUAL.md:158 and docs/THREAT_MODEL.md:113,146 explicitly state "max_output_bytes counts characters, not bytes," and THREAT_MODEL.md:149 marks these limitations as intentional for the delivered scope.
