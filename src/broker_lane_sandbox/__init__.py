"""broker-lane-sandbox: safe execution + local model runtime boundary.

Separate project from project-broker-loom. broker-loom integrates only via the
CLI/API contract, never as a library.
"""
__version__ = "0.2.0"
# CLI/API ENVELOPE version negotiated with broker-loom. The result vocabulary is
# per-command (discriminator: the invoked subcommand) -- contract D12.
SCHEMA_VERSION = 1
