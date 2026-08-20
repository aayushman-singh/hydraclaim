"""HydraDB feature-verification battery — the D1 spike as a runnable tool.

HydraDB supports a *subset* of OpenCypher. Everything HydraClaim relies on
is probed here against a live node, so we learn on day 1 (not day 5) if a
feature is missing. Probe nodes are labelled HydraClaimProbe and carry a
per-run `run` id.

CLI: python -m hydraclaim.schema --verify
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Sequence

from hydraclaim.db import HydraDB, HydraDBError


_SCHEMA_REFERENCE = """// HydraClaim graph model on HydraDB (documentation + canonical queries).
// Feature support is verified against a live node by:
//     python -m hydraclaim.schema --verify
//
// ---------------------------------------------------------------------------
// Node shapes
// ---------------------------------------------------------------------------
// (:Entity   {id, name, type, aliases})                      // people, projects, systems
// (:Claim    {id, subject, predicate, value,
//             valid_from, valid_to,                          // event time (bitemporal)
//             recorded_at,                                   // ingestion time
//             status,                                        // active | superseded | disputed
//             confidence})
// (:Evidence {id, quote, ts, session_id, msg_id,
//             extraction_confidence, explicitness})
// (:Source   {id, kind, author, channel})                    // kind: slack | linear | meeting
// (:SourceEvent {id, key, source_kind, author, occurred_at, captured_at,
//                content, content_hash, status, ingestion_kind})
// (:Extraction {id, key, provider, model, prompt_version,
//               started_at, finished_at, status})
// (:FailureRecord {id, key, step, error_type, message, traceback, failed_at})
//
// Edges
// (Claim)-[:ABOUT]->(Entity)
// (Claim)-[:SUPPORTED_BY]->(Evidence)
// (Evidence)-[:FROM]->(Source)
// (SourceEvent)-[:FROM]->(Source)
// (Extraction)-[:READ_FROM]->(SourceEvent)
// (Claim)-[:PRODUCED_BY]->(Extraction)
// (Evidence)-[:QUOTED_FROM]->(SourceEvent)
// (Extraction)-[:FAILED_WITH]->(FailureRecord)
// (Claim)-[:SUPERSEDES {at}]->(Claim)                        // explicit overwrite, new -> old
// (Claim)-[:CONTRADICTS {resolved, detected_at}]->(Claim)    // unresolved conflict
//
// Invariant: claims are never overwritten. A correction creates a new Claim
// plus a SUPERSEDES edge; the old claim keeps its validity window closed by
// valid_to. Contradictions without a supersession edge stay CONTRADICTS
// {resolved: false} until reconciled.

// ---------------------------------------------------------------------------
// Canonical queries (router probe + retrieval paths build on these)
// ---------------------------------------------------------------------------

// 1. Current truth: active claims for one (subject, predicate)
MATCH (c:Claim)-[:ABOUT]->(e:Entity {name: 'payments integration'})
WHERE c.predicate = 'owned_by' AND c.status = 'active'
RETURN c.value, c.valid_from
ORDER BY c.valid_from DESC;

// 2. Time travel: what was believed as of T.
//    An empty valid_to value marks an open validity window.
MATCH (c:Claim)-[:ABOUT]->(e:Entity {name: 'product launch'})
WHERE c.predicate = 'deadline'
  AND c.recorded_at <= '2026-05-12T00:00:00+00:00'
  AND (c.valid_to = '' OR c.valid_to > '2026-05-12')
RETURN c.value, c.valid_from, c.valid_to;

// 3. One supersession step for a selected claim.
//    The caller repeats this bounded one-hop query up to five times. It checks
//    the subject for each older claim with the one-hop ABOUT query below.
MATCH (newer:Claim {id: 123})-[:SUPERSEDES]->(older:Claim)
WHERE newer.predicate = 'deadline' AND older.predicate = 'deadline'
RETURN newer.id AS newer_id, older.id AS older_id,
       newer.value AS newer_value, older.value AS older_value;

MATCH (c:Claim {id: 123})-[:ABOUT]->(e:Entity {name: 'product launch'})
WHERE c.predicate = 'deadline'
RETURN c.id AS id, e.name AS subject, c.predicate AS predicate;

// 4. Unresolved conflicts for one selected claim endpoint.
//    CONTRADICTS is directed from the first claim to the second claim.
MATCH (a:Claim {id: 123})-[r:CONTRADICTS]->(b:Claim)
WHERE a.predicate = 'deadline' AND b.predicate = 'deadline' AND r.resolved = false
RETURN a.predicate, a.value, b.value, a.valid_from, b.valid_from;

// 5. Coverage probe (abstention trigger): zero rows -> abstain
MATCH (c:Claim)-[:ABOUT]->(e:Entity {name: 'product launch'})
WHERE c.predicate = 'budget'
RETURN count(c) AS coverage;

// 6. Evidence with provenance for citations
MATCH (c:Claim {key: 'deadline_drift:dl-3'})-[:SUPPORTED_BY]->(ev:Evidence)-[:FROM]->(s:Source)
RETURN ev.quote, ev.ts, s.kind, s.author;
"""


def render_schema_reference() -> str:
    """Return the canonical schema document for the verified dialect."""
    return _SCHEMA_REFERENCE


def _probes(run: str) -> list[tuple[str, list[str]]]:
    """(name, statements) pairs; statements run in order, last one is checked.

    Every probe uses only the verified HydraDB v0.1 dialect that the codebase
    depends on (see hydraclaim/model.py): integer node ids, scalar properties,
    one-hop CREATE patterns whose endpoints upsert by `id`, label/property-
    scoped MATCH, and no IS NULL / length() / undirected matches.
    """
    base = (int(run, 16) % 10**9) * 100  # unique integer id space per run
    a, b, c, d, e, f, g, h, i = (base + i for i in range(1, 10))
    run_int = base
    return [
        (
            "one-hop CREATE + read back",
            [
                f"CREATE (x:HydraClaimProbe {{run: {run_int}, id: {a}, name: '{run}-a', v: 1}})"
                f"-[:LINK]->(y:HydraClaimProbe {{run: {run_int}, id: {b}, v: 2}})",
                f"MATCH (x:HydraClaimProbe {{id: {a}}})-[:LINK]->(y) RETURN y.v AS v",
            ],
        ),
        (
            "property-scoped selected relation reads",
            [
                f"CREATE (a:HydraClaimProbe {{run: {run_int}, id: {h}, predicate: 'deadline'}})"
                f"-[:ABOUT]->(e:HydraClaimProbe {{run: {run_int}, id: {g}, name: '{run}-subject'}})",
                f"CREATE (a:HydraClaimProbe {{run: {run_int}, id: {h}}})"
                f"-[:SUPERSEDES]->(b:HydraClaimProbe {{run: {run_int}, id: {i}, predicate: 'deadline'}})",
                f"CREATE (a:HydraClaimProbe {{run: {run_int}, id: {h}}})"
                f"-[:CONTRADICTS {{resolved: false}}]->(b:HydraClaimProbe {{id: {i}}})",
                f"MATCH (a:HydraClaimProbe {{id: {h}}})"
                "-[:SUPERSEDES]->(b:HydraClaimProbe) "
                "WHERE a.predicate = 'deadline' AND b.predicate = 'deadline' "
                "RETURN a.id AS new_id, b.id AS old_id",
                f"MATCH (a:HydraClaimProbe {{id: {h}}})"
                "-[r:CONTRADICTS]->(b:HydraClaimProbe) "
                "WHERE a.predicate = 'deadline' AND b.predicate = 'deadline' "
                "RETURN a.id AS a_id, b.id AS b_id, r.resolved AS resolved",
                f"MATCH (a:HydraClaimProbe {{id: {h}}})"
                f"-[:ABOUT]->(e:HydraClaimProbe {{name: '{run}-subject'}}) "
                "WHERE a.predicate = 'deadline' "
                "RETURN a.id AS id, e.name AS subject, a.predicate AS predicate",
            ],
        ),
        (
            "upsert by integer id (re-CREATE is idempotent)",
            [
                f"CREATE (x:HydraClaimProbe {{id: {a}}})-[:LINK]->(y:HydraClaimProbe {{id: {b}}})",
                f"MATCH (n:HydraClaimProbe {{run: {run_int}}}) RETURN count(n.id) AS c",
            ],
        ),
        (
            "string equality in WHERE over an edge pattern",
            [
                f"MATCH (x:HydraClaimProbe)-[:LINK]->(y:HydraClaimProbe) WHERE x.name = '{run}-a' "
                "RETURN x.id AS id",
            ],
        ),
        (
            "bounded variable-length path (SUPERSEDES*1..5 shape)",
            [
                f"CREATE (x:HydraClaimProbe {{id: {b}}})-[:NEXT]->(y:HydraClaimProbe "
                f"{{run: {run_int}, id: {c}, v: 3}})",
                f"CREATE (x:HydraClaimProbe {{id: {c}}})-[:NEXT]->(y:HydraClaimProbe "
                f"{{run: {run_int}, id: {d}, v: 4}})",
                f"MATCH p=(x:HydraClaimProbe {{id: {b}}})-[:NEXT*1..5]->(y) RETURN y.id AS id",
            ],
        ),
        (
            "OPTIONAL MATCH",
            [
                f"MATCH (n:HydraClaimProbe {{id: {a}}}) "
                "OPTIONAL MATCH (n)-[:MISSING]->(m) RETURN m.id AS mid",
            ],
        ),
        (
            "aggregation count(*) over an edge pattern",
            [
                f"MATCH (x:HydraClaimProbe {{id: {a}}})-[:LINK]->(y:HydraClaimProbe {{id: {b}}}) "
                "RETURN count(*) AS c",
            ],
        ),
        (
            "SET update",
            [
                f"MATCH (n:HydraClaimProbe {{id: {a}}}) SET n.flag = true",
                f"MATCH (n:HydraClaimProbe {{id: {a}}}) RETURN n.flag AS flag",
            ],
        ),
        (
            "label/property-scoped DETACH DELETE (reset pattern)",
            [
                f"CREATE (x:HydraClaimProbe {{run: {run_int}, id: {e}}})"
                f"-[:TMP]->(y:HydraClaimProbe {{run: {run_int}, id: {f}}})",
                f"MATCH (n:HydraClaimProbe {{id: {e}}}) DETACH DELETE n",
            ],
        ),
    ]


def verify(db: HydraDB) -> bool:
    run = uuid.uuid4().hex[:8]
    print(f"probe run id: {run}")
    all_ok = True
    for name, statements in _probes(run):
        try:
            result = None
            for statement in statements:
                result = db.query(statement)
            print(f"PASS  {name}  ({len(result)} row(s) back)")
        except HydraDBError as exc:
            all_ok = False
            detail = str(exc).splitlines()[0][:160]
            print(f"FAIL  {name}: {detail}")
    try:
        run_int = (int(run, 16) % 10**9) * 100
        db.query(f"MATCH (n:HydraClaimProbe {{run: {run_int}}}) DETACH DELETE n")
        print("cleanup: probe nodes deleted")
    except HydraDBError as exc:
        all_ok = False
        print(f"cleanup: FAILED (probe nodes with run '{run}' left behind): {exc}")
    return all_ok


def main(argv: Sequence[str] | None = None) -> int | None:
    from hydraclaim.config import command_epilog

    parser = argparse.ArgumentParser(
        prog="hydraclaim schema",
        epilog=command_epilog(hydradb=True),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="probe a live HydraDB node for the features HydraClaim needs",
    )
    args = parser.parse_args(argv)
    if not args.verify:
        parser.print_help()
        return

    from hydraclaim import config

    config.require_settings(hydradb=True)

    from hydraclaim.config import connect

    with connect() as db:
        ok = verify(db)
    return 0 if ok else 1


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("schema", main))
