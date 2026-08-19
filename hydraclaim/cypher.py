"""Cypher literal serialization.

Statements are built with inline literals instead of `$parameters` because
parameter support in HydraDB's OpenCypher subset is unverified (the
`schema --verify` battery probes the features we do rely on). Everything
that goes into a statement MUST pass through `to_cypher_literal`.
"""

from __future__ import annotations

from typing import Any


def to_cypher_literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f"'{escaped}'"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(to_cypher_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {to_cypher_literal(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"cannot serialize {type(value).__name__} to a Cypher literal")
