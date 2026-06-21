# Security Policy

## Status

`broker-lane-sandbox` is **experimental, personal-use software**. It is a **default-deny
execution guardrail and bounded executor**, **not** a kernel/container sandbox. Its security
properties are best-effort and intentionally limited — read
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) for what is and is not defended (no filesystem
jail; network "offline" is env-level only; command identity is not pinned). Do not rely on it
as a hard isolation boundary for untrusted code.

## Supported versions

Only the latest commit on `main` is supported. There is no backport or LTS policy.

| Version | Supported |
|---------|-----------|
| `main` (latest `0.1.x`) | ✅ (experimental, best-effort) |
| older commits / tags | ❌ |

## Reporting a vulnerability

Please report security issues **privately** rather than opening a public issue:

1. Preferred: use GitHub's **private vulnerability reporting** —
   **Security → Advisories → "Report a vulnerability"** on this repository.
2. If that is unavailable, open a minimal GitHub issue that says only *"security report —
   please open a private channel"* and **withhold the details** until a private channel exists.

When reporting, include: affected file/function, a minimal reproduction, the impact, and the
commit SHA. Expect a best-effort response; this is a personal project with no SLA.

## Do NOT include secrets in reports

**Never paste secrets** into an issue, advisory, PR, log, or reproduction:
- no API keys, tokens, passwords, private keys, cookies, or session material;
- no `.env` files or real credential files;
- no model weight blobs or other large runtime artifacts (see INVARIANT-1 /
  [`docs/model-cache-policy.md`](docs/model-cache-policy.md)).

Use placeholder values (e.g. `dummy-not-a-real-key`) in reproductions. If you believe a secret
was exposed, **rotate it immediately** and note that fact (not the value) in your report.

## Scope notes

Reports about the **documented out-of-scope limitations** (kernel isolation, filesystem
jailing, network containment beyond env-level neutralization, binary-identity pinning) are
acknowledged but are **by-design** and tracked in the threat model rather than treated as
vulnerabilities. Reports about a **broken in-scope guarantee** (a default-deny bypass, a secret
leaking into a sandboxed child, the model-artifact guard failing open, a timeout that fails to
bound execution) are in scope and welcome.
