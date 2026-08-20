"""Show durable source-event processing state."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from hydraclaim.source_event_read import event_status


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(prog="hydraclaim status").parse_args(argv)
    from hydraclaim.config import connect, require_settings

    require_settings(hydradb=True)
    with connect() as db:
        print(json.dumps(event_status(db), sort_keys=True))
    return 0


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("status", main))
