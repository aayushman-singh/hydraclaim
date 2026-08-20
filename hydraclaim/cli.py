"""Dispatch the public HydraClaim command line interface."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import logging
import sys
from collections.abc import Sequence

from hydraclaim.config import ConfigurationError
from hydraclaim.db import HydraDBError
from hydraclaim.errors import GraphIntegrityError
from hydraclaim.llm import LLMError
from hydraclaim.claim_read import ClaimReadLimitError


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

logger = logging.getLogger(__name__)


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

    try:
        command_module = importlib.import_module(module_name)
        result = command_module.main(parsed.command_args)
    except ConfigurationError as exc:
        return _report_expected_failure(parsed.command, "configuration_error", exc)
    except GraphIntegrityError as exc:
        return _report_expected_failure(parsed.command, "graph_integrity_error", exc)
    except ClaimReadLimitError as exc:
        return _report_expected_failure(parsed.command, "claim_limit_exceeded", exc)
    except HydraDBError as exc:
        return _report_expected_failure(parsed.command, "graph_backend_failed", exc)
    except LLMError as exc:
        return _report_expected_failure(parsed.command, "llm_failed", exc)
    except json.JSONDecodeError as exc:
        return _report_expected_failure(parsed.command, "invalid_json", exc)
    except FileNotFoundError as exc:
        return _report_expected_failure(parsed.command, "file_error", exc)
    except OSError as exc:
        return _report_expected_failure(parsed.command, "file_error", exc)
    except ValueError as exc:
        return _report_expected_failure(parsed.command, "validation_error", exc)
    return 0 if result is None else result


def _report_expected_failure(command: str, code: str, exc: Exception) -> int:
    logger.exception(
        "command failed command=%s code=%s exception_type=%s",
        command,
        code,
        type(exc).__name__,
    )
    message = str(exc) or code.replace("_", " ")
    print(f"hydraclaim: error: {code}: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
