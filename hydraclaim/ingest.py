"""Write a generated scenario document into HydraDB as the claim/evidence graph.

This is the deterministic ingestion path used for development and for the
benchmark's oracle arm: ground-truth claims go through the same graph-write
code that the LLM extraction pipeline (reconcile.apply_plan) uses.

Write-path dialect (verified live, D1): every statement is a single one-hop
CREATE whose endpoints upsert by integer `id`. Nodes are skipped when they
already exist, so re-ingesting a document is safe. Edges are created only
between id-known endpoints.

CLI: python -m hydraclaim.ingest data/sessions/deadline_drift.json [...]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from hydraclaim.db import HydraDB
from hydraclaim.graph_write import GraphWriter


def ingest_document(db: HydraDB, doc: dict) -> dict:
    return GraphWriter(db).ingest_document(doc)


def main(argv: Sequence[str] | None = None) -> int | None:
    from hydraclaim.config import command_epilog

    parser = argparse.ArgumentParser(
        prog="hydraclaim ingest",
        epilog=command_epilog(hydradb=True),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("documents", nargs="+", help="scenario JSON files to ingest")
    args = parser.parse_args(argv)

    from hydraclaim import config

    try:
        config.require_settings(hydradb=True)
    except config.ConfigurationError as exc:
        parser.error(str(exc))

    from hydraclaim.config import connect

    with connect() as db:
        for path in args.documents:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            print(json.dumps(ingest_document(db, doc), indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
