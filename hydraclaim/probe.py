"""Compatibility entry points for bounded claim probes."""

from __future__ import annotations

from hydraclaim.claim_read import ClaimReader, ClaimScope, ProbeResult, _chain_depth
from hydraclaim.claims import QualifierSelection
from hydraclaim.db import HydraDB


__all__ = ["ProbeResult", "probe", "_chain_depth"]


def probe(
    db: HydraDB,
    subject: str,
    predicate: str | None,
    *,
    qualifier: str | QualifierSelection | None = None,
) -> ProbeResult:
    return ClaimReader(db).probe(
        ClaimScope(subject=subject, predicate=predicate, qualifier=qualifier)
    )
