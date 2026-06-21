# broker-lane-sandbox — Threat Model

This document states what `broker-lane-sandbox` defends, against whom, and where its
guarantees stop. It is deliberately honest about limitations: this is a **personal-use,
single-operator MVP** that provides a *default-deny guardrail and bounded executor*, **not**
a kernel/container sandbox. Read it alongside the [README](../README.md) and
[MANUAL](MANUAL.md).

## 1. Assets protected

1. **The operator's environment & secrets.** API keys, tokens, and other secrets present
   in the operator's shell environment must not be handed to a sandboxed child that could
   read or exfiltrate them.
2. **The host's resources.** CPU time, address space, and the process table must not be
   exhausted by a runaway or forking child.
3. **Execution intent.** Only the commands the operator explicitly allow-listed should run;
   an allow-listed *name* must not be able to front for an arbitrary on-disk binary.
4. **The git repository.** Model weight blobs (and other large/sensitive runtime artifacts)
   must never enter version control (INVARIANT-1).
5. **Honest reporting.** Every outcome — including refusals and failures — is a structured,
   serializable result, so the caller can never mistake a silent failure for success.

## 2. Trust boundaries

```
   TRUSTED                         |  CONFINED (this sandbox's job)        |  UNTRUSTED
   --------------------------------+---------------------------------------+----------------
   operator / broker-loom caller   |  the spawned child process / "lane":  |  model output,
   the policy file (operator-authored) |  a subprocess, a local model       |  processed data,
   the host OS / kernel            |  runner, future agent execution       |  remote services
                                   |                                       |  (when online)
            ^------------ CLI / JSON seam (bls) ------------^
```

- **Trusted (above the boundary):** the **caller** (broker-loom or the operator) that writes
  the policy and constructs `argv`; the policy file itself; the host OS and kernel. A trusted
  caller can already run anything — the sandbox does not defend against its own operator.
- **Confined (the boundary):** the **child** the sandbox spawns. The sandbox's entire job is
  to bound what *this* can see (env), reach (network), run (allow-list), and consume (limits).
- **Untrusted (inside the child):** the content/data the child processes and any remote
  service it talks to when `network: online`.
- The **CLI/JSON seam** (`bls`, JSON in/out) is the interface between caller and sandbox;
  the sandbox is never imported as a library across this boundary.

## 3. Attacker model

**In scope — what the sandbox is built to resist:** a **misbehaving or compromised child /
lane** that, once running, tries to:
- read secrets from the inherited environment,
- reach the network to phone home or exfiltrate,
- consume unbounded CPU/memory or fork-bomb the host,
- run past its time budget,
- or get an *unintended* command to execute by naming it after an allow-listed one.

**Explicitly OUT of scope (not defended):**
- A **malicious caller/operator.** The caller authors the policy and `argv`; someone who
  controls those can already run anything directly. Crafting a path-bearing `argv[0]` or a
  permissive policy is *above* the boundary — the sandbox is a guardrail against *accidents*,
  not a defense against its trusted operator.
- A **filesystem-write-capable adversary** doing PATH-shadowing / TOCTOU binary substitution.
  The allow-list gates the invocation **name** (resolved on `PATH`); it does **not** pin
  binary identity (`realpath`/hash).
- **Kernel/hypervisor escape, side channels, or covert channels** — there is no kernel
  isolation to escape; the child runs as the same user with normal filesystem access.

## 4. Risk areas, mitigations, and limitations

### 4.1 Model-artifact risk (INVARIANT-1)
- **Risk:** weight blobs (`.gguf`/`.safetensors`/…), HF/Ollama caches, or `llama.cpp` builds
  committed to git — repo bloat, license/IP exposure, accidental redistribution.
- **Mitigations:** `.gitignore` covers all cache dirs + weight extensions;
  `scripts/check_model_artifacts.py` runs as a **pre-commit hook** (`--staged`) and in **CI**
  (`--tracked`); even `git add -f` is refused; the guard **fails closed** (exit non-zero) if
  git errors or is absent. Local weights resolve from an env-driven cache (`SANDBOX_MODEL_DIR`),
  never committed; tests use **fake runners** so CI needs zero real weights. See
  [model-cache-policy.md](model-cache-policy.md).
- **Limitation:** the guard protects *git*; it does not encrypt or access-control the local
  cache on disk.

### 4.2 Secret / environment leakage
- **Risk:** a secret in the operator env (e.g. an API key) is inherited by a child that leaks it.
- **Mitigations:** the child env is built **from empty**; only allow-listed names pass;
  **secret-looking names** (`KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|SESSION|
  COOKIE|AUTH`) are **dropped even if allow-listed** unless `allow_secret_env: true`;
  `ExecResult.env_keys` lists names **only, never values**; dropped secret names are reported.
- **Limitation:** the secret-name filter is a **best-effort heuristic** blocklist, not an
  exhaustive secret detector. The real protection is the default-empty allow-list — a secret
  reaches the child only if the operator deliberately allow-lists its exact name (or sets
  `allow_secret_env`).

### 4.3 Network
- **Risk:** a child phones home or exfiltrates over the network.
- **Mitigations:** `network: offline` (the default) strips proxy variables and sets
  `NO_PROXY=*` + `SANDBOX_NETWORK=offline` as a clear signal to cooperating runners.
- **Limitation (important):** this is **env-level, best-effort** neutralization — a
  **cooperation contract plus proxy removal, NOT a network namespace or firewall.** A
  determined child that opens raw sockets to hardcoded IPs is **not** blocked. True network
  isolation would require OS-level sandboxing (namespaces / seccomp), which is out of scope.

### 4.4 Subprocess / process-tree
- **Risk:** runaway CPU/memory, fork bombs, children that outlive the timeout, or a descendant
  that escapes the process group and holds the output pipe open.
- **Mitigations:** the child starts a **new session** (`setsid`); **RLIMIT_CPU / RLIMIT_AS /
  RLIMIT_NPROC** are applied (POSIX); a **wall-clock timeout** kills the whole **process group**
  (`killpg` + `SIGKILL`); the post-kill drain is **time-boxed** so an escaped descendant cannot
  pin `run()` open past the budget; output is truncated to a cap. Pre-spawn failures (missing
  exe, bad cwd, an rlimit above the host ceiling) return a `spawn_error` **result**, not a crash.
- **Limitations:** a child that **double-forks and `setsid`s** to escape the group survives the
  group kill (best-effort, documented); `RLIMIT_NPROC` is **per-UID** (a POSIX property), not
  per-job; `max_output_bytes` counts **characters**, not bytes; on non-POSIX hosts rlimits are
  unavailable and only the wall-clock timeout applies.

### 4.5 Command execution / default-deny
- **Risk:** an unintended command runs, or an allow-listed *name* executes an arbitrary file.
- **Mitigations:** **default-deny** (`allow_exec` false → nothing spawns; empty allow-list →
  nothing permitted); commands are allow-listed by **bare name** and a **path-bearing `argv[0]`
  is refused**, so `/tmp/evil/python3` cannot pass an allow-list of `python3`. All gates run
  **before** any spawn; denials are results.
- **Limitation:** no binary-identity pinning — `PATH` resolution decides which `python3` runs.
  A writable earlier-`PATH` entry is a host-integrity concern outside the boundary.

## 5. Mitigation summary

| Risk | Primary control | Residual limitation |
|------|-----------------|---------------------|
| Model weights in git | guard (pre-commit + CI, fail-closed) + `.gitignore` | protects git, not local-disk access |
| Secret env leakage | empty-baseline env + allow-list + secret-name drop | heuristic blocklist; operator can opt in |
| Network exfiltration | offline proxy-strip + signal | env-level only; raw sockets not blocked |
| Resource exhaustion | rlimits + group-kill timeout | per-UID NPROC; escaped double-fork survives |
| Unintended execution | default-deny + bare-name allow-list | no binary-identity pinning |

## 6. Known limitations (consolidated)

- **Not a kernel/container sandbox.** No namespaces, seccomp, cgroups, or chroot. The child runs
  as the same user with normal filesystem access; the filesystem is **not** jailed (an
  allow-listed `cat` can read any readable path).
- **Network "offline" is best-effort** env neutralization, not containment.
- **No binary-identity pinning** — invocation names are gated and resolved on `PATH`.
- **Heuristic secret filter** — exact-name allow-listing of a secret (or `allow_secret_env`)
  still passes it.
- **`max_output_bytes` is a character cap; `RLIMIT_NPROC` is per-UID.**
- **Single-operator MVP** — no multi-tenancy, no audit log signing, no formal verification.

These limitations are intentional for the current scope (P1). Hardening toward OS-level
isolation, binary pinning, or true network containment would be a separate, larger effort and
is not claimed today.
