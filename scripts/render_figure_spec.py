#!/usr/bin/env python3
"""Render a reusable figure spec to SVG and PNG outputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.analysis.figure_factory import render_figure_spec  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="Path to a JSON figure spec.")
    parser.add_argument("--output", type=Path, default=None, help="Optional SVG output path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_path = render_figure_spec(args.spec, args.output)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
