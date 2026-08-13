"""CLI: python -m trustgraph.generate [--out data/sessions] [--seed 42]"""

from __future__ import annotations

import argparse

from trustgraph.generate.generator import write_dataset


def main() -> None:
    parser = argparse.ArgumentParser(prog="trustgraph.generate")
    parser.add_argument("--out", default="data/sessions", help="output directory")
    parser.add_argument("--seed", type=int, default=42, help="deterministic seed")
    args = parser.parse_args()

    for path in write_dataset(args.out, seed=args.seed):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
