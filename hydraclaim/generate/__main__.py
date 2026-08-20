"""CLI: python -m hydraclaim.generate [--out data/sessions] [--seed 42]"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from hydraclaim.generate.generator import write_dataset


def main(argv: Sequence[str] | None = None) -> int | None:
    parser = argparse.ArgumentParser(prog="hydraclaim generate")
    parser.add_argument("--out", default="data/sessions", help="output directory")
    parser.add_argument("--seed", type=int, default=42, help="deterministic seed")
    args = parser.parse_args(argv)

    for path in write_dataset(args.out, seed=args.seed):
        print(f"wrote {path}")


if __name__ == "__main__":
    from hydraclaim.cli import run_module

    raise SystemExit(run_module("generate", main))
