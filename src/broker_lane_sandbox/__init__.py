"""broker-lane-sandbox: safe execution + local model runtime boundary.

Separate project from project-broker-loom. broker-loom integrates only via the
CLI/API contract, never as a library.
"""
__version__ = "0.1.0"
SCHEMA_VERSION = 1  # CLI/API contract version negotiated with broker-loom
