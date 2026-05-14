#!/usr/bin/env python3
"""Prepare deterministic tiny inputs for fast-signal mini-benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fast_signal_minibench import (  # noqa: E402
    prepare_mini_benchmark_suite,
)

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "workspace" / "benchmark_data" / "fast_signal_mini"


def build_parser() -> argparse.ArgumentParser:
    """Build the mini-benchmark preparation CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    """Prepare the mini-benchmark suite and print its manifest payload."""

    args = build_parser().parse_args()
    payload = prepare_mini_benchmark_suite(
        args.output_root,
        overwrite=bool(args.overwrite),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
