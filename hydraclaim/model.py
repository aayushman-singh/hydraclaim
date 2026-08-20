"""Graph model mappings for the verified HydraDB v0.1 dialect.

The D1 live battery (see PLAN.md "D1 findings") established hard constraints
that this module centralizes:

- Node `id` properties must be INTEGERS. `graph_id` mints a deterministic
  62-bit integer from our namespaced string keys (md5-based, so re-ingesting
  the same key yields the same id — idempotency for free). The original
  string key is kept in a `key` property for readability.
- Property values must be SCALARS (int/float/bool/string). Alias lists are
  stored pipe-delimited ("a|b|c") and split client-side.
- `IS NULL` is unsupported, so an open validity window is the empty string:
  `valid_to = ''` means "still valid".
"""

from __future__ import annotations

import hashlib
import re

OPEN = ""  # open-ended validity window (IS NULL is unsupported)


def graph_id(key: str) -> int:
    """Deterministic 62-bit integer id for a string key."""
    digest = hashlib.md5(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & (2**62 - 1)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def join_aliases(aliases: list[str]) -> str:
    return "|".join(a for a in aliases if a)


def split_aliases(stored: str | None) -> list[str]:
    if not stored:
        return []
    return [a for a in stored.split("|") if a]


def entity_key(scenario: str, name: str) -> str:
    return f"{scenario}:{slug(name)}"


def source_key(source_kind: str, author: str) -> str:
    return f"{source_kind}:{author}"


def claim_props(claim: dict, cid: str, recorded_at: str) -> dict:
    return {
        "id": graph_id(cid),
        "key": cid,
        "predicate": claim["predicate"],
        "value": claim["value"],
        "valid_from": claim["valid_from"],
        "valid_to": claim.get("valid_to") or OPEN,
        "recorded_at": recorded_at,
        "status": claim.get("status", "active"),
        "confidence": claim.get("confidence", 0.5),
    }


def evidence_props(claim: dict, cid: str) -> dict:
    return {
        "id": graph_id(f"{cid}:ev0"),
        "key": f"{cid}:ev0",
        "quote": claim["quote"],
        "ts": claim["valid_from"],
        "session_id": claim.get("session_id", ""),
        "msg_id": claim.get("msg_id", ""),
        "extraction_confidence": claim.get("confidence", 0.5),
        "explicitness": claim.get("explicitness", 1.0),
    }


def entity_props(
    scenario: str, name: str, etype: str = "unknown", aliases: list[str] | None = None
) -> dict:
    key = entity_key(scenario, name)
    return {
        "id": graph_id(key),
        "key": key,
        "name": name,
        "type": etype,
        "aliases": join_aliases(aliases or []),
    }


def source_props(source_kind: str, author: str) -> dict:
    key = source_key(source_kind, author)
    return {"id": graph_id(key), "key": key, "kind": source_kind, "author": author}
