"""Query CLI — the demo surface.

    python -m hydraclaim.ask "Who owns the payments integration?"
    python -m hydraclaim.ask "What is the current launch deadline?" --verbose

Requires a live HydraDB node populated via ingest or pipeline. Answers are
deterministic; no LLM key is needed at query time. --llm enables LLM
question classification (the keyword heuristic is the default).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from hydraclaim.errors import ValidationError


def _llm_classifier(question: str) -> dict:
    from hydraclaim.router import llm_classifier

    return llm_classifier(question)


def _print_result(result: dict, verbose: bool) -> None:
    if verbose:
        print(
            json.dumps(
                {
                    "route": result["route"],
                    "classification": result["classification"],
                    "probe": result["probe"],
                },
                indent=2,
            )
        )
        print()
    print(result["answer"])
    for citation in result["citations"]:
        print(
            f"  [{citation['claim_id']}] {citation['source_kind']}/"
            f'{citation["author"]}: "{citation["quote"]}"'
        )


def main(argv: Sequence[str] | None = None) -> int | None:
    from hydraclaim.config import command_epilog

    parser = argparse.ArgumentParser(
        prog="hydraclaim ask",
        epilog=command_epilog(hydradb=True, llm="optional"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "question", nargs="?", help="natural-language question (required unless --repl)"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="print classification, probe result, and route",
    )
    parser.add_argument(
        "--llm", action="store_true", help="use the LLM for question classification"
    )
    parser.add_argument(
        "--repl", action="store_true", help="read questions from stdin in a loop"
    )
    args = parser.parse_args(argv)

    classification_mode = "llm" if args.llm else "heuristic"
    llm_fn = _llm_classifier if args.llm else None

    from hydraclaim import config

    config.require_settings(hydradb=True, llm=args.llm)

    from hydraclaim.config import connect
    from hydraclaim.retrieve import answer

    if args.repl:
        with connect() as db:
            while True:
                try:
                    question = input("hydraclaim> ")
                except EOFError:
                    break
                if not question.strip():
                    break
                _print_result(
                    answer(
                        db,
                        question,
                        classification_mode=classification_mode,
                        llm_fn=llm_fn,
                    ),
                    args.verbose,
                )
        return

    if not args.question:
        raise ValidationError("question is required unless --repl is used")

    with connect() as db:
        result = answer(
            db, args.question, classification_mode=classification_mode, llm_fn=llm_fn
        )

    _print_result(result, args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
