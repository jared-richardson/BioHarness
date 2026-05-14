#!/usr/bin/env python3
"""Export a completed Bio-Harness run as a lightweight RO-Crate bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.reporting.ro_crate import export_run_ro_crate  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_input", type=Path, help="Selected-dir path or result.json path.")
    parser.add_argument("--output", type=Path, default=None, help="Optional output directory.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = export_run_ro_crate(args.run_input, args.output)
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
