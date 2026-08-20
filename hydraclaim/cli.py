"""Dispatch the public HydraClaim command line interface."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import sys
from collections.abc import Sequence


COMMANDS = {
    "ask": "hydraclaim.ask",
    "serve": "hydraclaim.serve",
    "schema": "hydraclaim.schema",
    "generate": "hydraclaim.generate.__main__",
    "ingest": "hydraclaim.ingest",
    "extract": "hydraclaim.extract",
    "evaluate": "hydraclaim.evaluate",
    "pipeline": "hydraclaim.pipeline",
    "benchmark": "hydraclaim.benchmark",
    "longmemeval": "hydraclaim.longmemeval",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hydraclaim",
        description="Conflict-aware temporal memory for AI agents.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="show the installed HydraClaim version and exit",
    )
    parser.add_argument(
        "command",
        nargs="?",
        help="command to run: " + ", ".join(COMMANDS),
    )
    parser.add_argument("command_args", nargs=argparse.REMAINDER, metavar="ARGS")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one public command and return its exit status."""
    parser = _parser()
    supplied = list(sys.argv[1:] if argv is None else argv)

    if supplied and supplied[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    if supplied and supplied[0] == "--version":
        print(f"hydraclaim {importlib.metadata.version('hydraclaim')}")
        return 0

    parsed = parser.parse_args(supplied)
    if parsed.command is None:
        parser.print_help()
        return 0

    module_name = COMMANDS.get(parsed.command)
    if module_name is None:
        print(f"hydraclaim: error: unknown command '{parsed.command}'", file=sys.stderr)
        parser.print_usage(sys.stderr)
        return 2

    command_module = importlib.import_module(module_name)
    result = command_module.main(parsed.command_args)
    return 0 if result is None else result
