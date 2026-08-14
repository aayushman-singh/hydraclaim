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
    """(name, statements) pairs; statements run in order, last one is checked.

    Every probe uses only the verified HydraDB v0.1 dialect that the codebase
    depends on (see trustgraph/model.py): integer node ids, scalar properties,
    one-hop CREATE patterns whose endpoints upsert by `id`, label/property-
    scoped MATCH, and no IS NULL / length() / undirected matches.
    """
    base = (int(run, 16) % 10**9) * 100  # unique integer id space per run
    a, b, c, d, e, f = (base + i for i in range(1, 7))
    run_int = base
    return [
        ("one-hop CREATE + read back", [
            f"CREATE (x:TGProbe {{run: {run_int}, id: {a}, name: '{run}-a', v: 1}})"
            f"-[:LINK]->(y:TGProbe {{run: {run_int}, id: {b}, v: 2}})",
            f"MATCH (x:TGProbe {{id: {a}}})-[:LINK]->(y) RETURN y.v AS v",
        ]),
        ("upsert by integer id (re-CREATE is idempotent)", [
            f"CREATE (x:TGProbe {{id: {a}}})-[:LINK]->(y:TGProbe {{id: {b}}})",
            f"MATCH (n:TGProbe {{run: {run_int}}}) RETURN count(n.id) AS c",
        ]),
        ("string equality in WHERE over an edge pattern", [
            f"MATCH (x:TGProbe)-[:LINK]->(y:TGProbe) WHERE x.name = '{run}-a' "
            "RETURN x.id AS id",
        ]),
        ("bounded variable-length path (SUPERSEDES*1..5 shape)", [
            f"CREATE (x:TGProbe {{id: {b}}})-[:NEXT]->(y:TGProbe "
            f"{{run: {run_int}, id: {c}, v: 3}})",
            f"CREATE (x:TGProbe {{id: {c}}})-[:NEXT]->(y:TGProbe "
            f"{{run: {run_int}, id: {d}, v: 4}})",
            f"MATCH p=(x:TGProbe {{id: {b}}})-[:NEXT*1..5]->(y) RETURN y.id AS id",
        ]),
        ("OPTIONAL MATCH", [
            f"MATCH (n:TGProbe {{id: {a}}}) "
            "OPTIONAL MATCH (n)-[:MISSING]->(m) RETURN m.id AS mid",
        ]),
        ("aggregation count(*) over an edge pattern", [
            f"MATCH (x:TGProbe {{id: {a}}})-[:LINK]->(y:TGProbe {{id: {b}}}) "
            "RETURN count(*) AS c",
        ]),
        ("SET update", [
            f"MATCH (n:TGProbe {{id: {a}}}) SET n.flag = true",
            f"MATCH (n:TGProbe {{id: {a}}}) RETURN n.flag AS flag",
        ]),
        ("label/property-scoped DETACH DELETE (reset pattern)", [
            f"CREATE (x:TGProbe {{run: {run_int}, id: {e}}})"
            f"-[:TMP]->(y:TGProbe {{run: {run_int}, id: {f}}})",
            f"MATCH (n:TGProbe {{id: {e}}}) DETACH DELETE n",
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
        run_int = (int(run, 16) % 10**9) * 100
        db.query(f"MATCH (n:TGProbe {{run: {run_int}}}) DETACH DELETE n")
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
