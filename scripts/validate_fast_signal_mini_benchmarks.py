#!/usr/bin/env python3
"""Validate fast-signal mini-benchmark outputs at contract granularity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fast_signal_minibench import (  # noqa: E402
    DEFAULT_MINI_BENCHMARK_CONTRACTS,
    validate_mini_benchmark_contract,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the mini-benchmark validation CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument(
        "--selected-dir",
        action="append",
        required=True,
        help="Selected output directory. Repeat for multiple cases.",
    )
    return parser


def main() -> int:
    """Validate requested mini-benchmark outputs."""

    args = build_parser().parse_args()
    case_ids = args.case_id or list(DEFAULT_MINI_BENCHMARK_CONTRACTS)
    if len(args.selected_dir) not in {1, len(case_ids)}:
        raise SystemExit("--selected-dir must be supplied once or once per --case-id")
    results = []
    for index, case_id in enumerate(case_ids):
        contract = DEFAULT_MINI_BENCHMARK_CONTRACTS.get(case_id)
        if contract is None:
            raise SystemExit(f"Unknown mini-benchmark case_id: {case_id}")
        selected_dir = args.selected_dir[index] if len(args.selected_dir) > 1 else args.selected_dir[0]
        results.append(validate_mini_benchmark_contract(Path(selected_dir), contract))
    print(json.dumps({"results": results}, indent=2, sort_keys=True))
    return 0 if all(result.get("passed", False) for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
