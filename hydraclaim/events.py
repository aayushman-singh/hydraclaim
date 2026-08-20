"""Inspect durable source events."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from hydraclaim.source_event_read import list_events, read_event


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hydraclaim events")
    commands = parser.add_subparsers(dest="operation", required=True)
    list_parser = commands.add_parser("list")
    list_parser.add_argument("--limit", type=int, default=20)
    show_parser = commands.add_parser("show")
    show_parser.add_argument("event_key")
    args = parser.parse_args(argv)
    from hydraclaim.config import connect, require_settings

    require_settings(hydradb=True)
    with connect() as db:
        result = (
            {"events": list_events(db, limit=args.limit)}
            if args.operation == "list"
            else read_event(db, args.event_key)
        )
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("events", main))
