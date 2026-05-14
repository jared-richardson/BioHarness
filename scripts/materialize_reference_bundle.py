#!/usr/bin/env python3
"""Build deterministic reference indexes under a reference bundle root."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.reference_manager import write_reference_materialization_report  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_root", type=Path, help="Reference bundle directory to materialize.")
    parser.add_argument("--target", action="append", default=[], help="Reference target to build. Repeatable.")
    parser.add_argument("--include-extended", action="store_true", help="Also build STAR and Salmon indexes when inputs are available.")
    parser.add_argument("--dry-run", action="store_true", help="Render the materialization plan without running commands.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output JSON path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_path = write_reference_materialization_report(
        args.reference_root,
        args.output_json,
        targets=list(args.target or []),
        include_extended=bool(args.include_extended),
        dry_run=bool(args.dry_run),
    )
    if args.output_json is not None:
        print(output_path.resolve())
        return 0
    rendered = output_path.read_text(encoding="utf-8")
    print(rendered, end="")
    payload = json.loads(rendered)
    return 0 if payload.get("success", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
