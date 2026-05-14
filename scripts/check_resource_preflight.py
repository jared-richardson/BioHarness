#!/usr/bin/env python3
"""Check whether current machine resources meet selected skill requirements."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.resource_preflight import assess_resource_preflight  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", action="append", default=[], help="Skill name to include in the preflight check. Repeatable.")
    parser.add_argument("--selected-dir", type=Path, default=Path.cwd(), help="Directory whose filesystem should be checked for free space.")
    parser.add_argument("--min-free-disk-gb", type=float, default=20.0, help="Minimum desired free disk capacity in GiB.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output JSON path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = assess_resource_preflight(
        list(args.skill or []),
        selected_dir=args.selected_dir,
        min_free_disk_gb=float(args.min_free_disk_gb),
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered, encoding="utf-8")
        print(args.output_json.resolve())
        return 0
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
