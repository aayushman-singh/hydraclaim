"""HydraDB feature-verification battery — the D1 spike as a runnable tool.

HydraDB supports a *subset* of OpenCypher. Everything TrustGraph relies on
is probed here against a live node, so we learn on day 1 (not day 5) if a
feature is missing. Probe nodes are labelled TGProbe and carry a per-run
`run` id; cleanup is best-effort.

CLI: python -m trustgraph.schema --verify
"""

from __future__ import annotations

import argparse
import uuid

from trustgraph.db import HydraDB, HydraDBError


def _probes(run: str) -> list[tuple[str, list[str]]]:
    """(name, statements) pairs; statements run in order, last one is checked."""
    return [
        ("round-trip create + match", [
            f"CREATE (a:TGProbe {{run: '{run}', id: '{run}-rt-a', v: 1}})"
            f"-[:LINK]->(b:TGProbe {{run: '{run}', id: '{run}-rt-b', v: 2}})",
            f"MATCH (a:TGProbe {{id: '{run}-rt-a'}})-[:LINK]->(b) RETURN b.v AS v",
        ]),
        ("batched UNWIND create", [
            f"UNWIND [{{id: '{run}-uw-a', v: 1}}, {{id: '{run}-uw-b', v: 2}}] AS row "
            f"CREATE (n:TGProbe {{run: '{run}', id: row.id, v: row.v}})",
            f"MATCH (n:TGProbe {{run: '{run}', id: '{run}-uw-b'}}) RETURN n.v AS v",
        ]),
        ("property range filter (bitemporal reads)", [
            f"MATCH (n:TGProbe {{run: '{run}'}}) WHERE n.id <= '{run}-rt-b' "
            "RETURN count(n) AS c",
        ]),
        ("bounded variable-length path (SUPERSEDES*1..5)", [
            f"UNWIND [{{a: '{run}-ch-1', b: '{run}-ch-2'}}, "
            f"{{a: '{run}-ch-2', b: '{run}-ch-3'}}] AS row "
            f"CREATE (x:TGProbe {{run: '{run}', id: row.a}})"
            f"-[:NEXT]->(y:TGProbe {{run: '{run}', id: row.b}})",
            f"MATCH p=(a:TGProbe {{id: '{run}-ch-1'}})-[:NEXT*1..5]->(x) RETURN x.id AS id",
        ]),
        ("OPTIONAL MATCH", [
            f"MATCH (n:TGProbe {{id: '{run}-rt-a'}}) "
            "OPTIONAL MATCH (n)-[:MISSING]->(m) RETURN m.id AS mid",
        ]),
        ("aggregation", [
            f"MATCH (n:TGProbe {{run: '{run}'}}) RETURN count(n) AS c",
        ]),
        ("SET update", [
            f"MATCH (n:TGProbe {{id: '{run}-rt-a'}}) SET n.flag = true",
            f"MATCH (n:TGProbe {{id: '{run}-rt-a'}}) RETURN n.flag AS flag",
        ]),
        ("UNWIND + MATCH edge create (ingest edge pattern)", [
            f"UNWIND [{{a: '{run}-rt-a', b: '{run}-rt-b'}}] AS row "
            "MATCH (x:TGProbe {id: row.a}), (y:TGProbe {id: row.b}) "
            "CREATE (x)-[:TAGGED]->(y)",
            f"MATCH (x:TGProbe {{id: '{run}-rt-a'}})-[:TAGGED]->(y) RETURN y.id AS id",
        ]),
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
        db.query(f"MATCH (n:TGProbe {{run: '{run}'}}) DETACH DELETE n")
        print("cleanup: probe nodes deleted")
    except HydraDBError as exc:
        print(f"cleanup: FAILED (probe nodes with run '{run}' left behind): {exc}")
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(prog="trustgraph.schema")
    parser.add_argument("--verify", action="store_true",
                        help="probe a live HydraDB node for the features TrustGraph needs")
    args = parser.parse_args()
    if not args.verify:
        parser.print_help()
        return

    from trustgraph.config import connect

    with connect() as db:
        ok = verify(db)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
