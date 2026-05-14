#!/usr/bin/env python3
"""Audit reference FASTA, annotation, and index assets under a root directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.reference_manager import write_reference_audit  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_root", type=Path, help="Reference bundle directory to audit.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output JSON path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_path = write_reference_audit(args.reference_root, args.output_json)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
