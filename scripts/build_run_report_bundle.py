#!/usr/bin/env python3
"""Build an opt-in report bundle for a completed Bio-Harness run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.reporting.report_bundle import build_run_report_bundle  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_input", type=Path, help="Selected-dir path or result.json path.")
    parser.add_argument("--output", type=Path, default=None, help="Optional output directory.")
    parser.add_argument("--run-multiqc", action="store_true", help="Run MultiQC if it is installed.")
    parser.add_argument("--render-quarto", action="store_true", help="Render the generated Quarto report if Quarto is installed.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = build_run_report_bundle(
        args.run_input,
        args.output,
        run_multiqc=args.run_multiqc,
        render_quarto=args.render_quarto,
    )
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
