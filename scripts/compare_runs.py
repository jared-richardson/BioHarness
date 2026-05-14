#!/usr/bin/env python3
"""Compare two completed Bio-Harness runs and write a compact diff bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.reporting.run_compare import write_run_comparison  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_a", type=Path, help="First selected-dir or result.json path.")
    parser.add_argument("run_b", type=Path, help="Second selected-dir or result.json path.")
    parser.add_argument("--output", type=Path, default=None, help="Optional comparison output directory.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = write_run_comparison(args.run_a, args.run_b, args.output)
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
