"""Capture one durable source event."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from hydraclaim.source_events import SourceEventStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hydraclaim record")
    parser.add_argument("source_json", help="JSON file with one source event")
    args = parser.parse_args(argv)
    event = json.loads(Path(args.source_json).read_text(encoding="utf-8"))
    from hydraclaim.config import connect, require_settings

    require_settings(hydradb=True)
    with connect() as db:
        print(json.dumps(SourceEventStore(db).capture(event), sort_keys=True))
    return 0


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("record", main))
