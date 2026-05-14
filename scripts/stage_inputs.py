#!/usr/bin/env python3
"""Stage local files or directories into ``workspace/inputs_readonly``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.input_staging import stage_inputs, write_stage_receipt  # noqa: E402


DEFAULT_DEST_ROOT = PROJECT_ROOT / "workspace" / "inputs_readonly"
DEFAULT_RECEIPT = PROJECT_ROOT / "workspace" / "input_stage_reports" / "latest_stage_inputs.json"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for input staging."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", help="Files or directories to stage into workspace/inputs_readonly.")
    parser.add_argument(
        "--dest-root",
        default=str(DEFAULT_DEST_ROOT),
        help="Destination root for staged inputs. Defaults to workspace/inputs_readonly.",
    )
    parser.add_argument(
        "--link-mode",
        choices=["symlink", "copy"],
        default="symlink",
        help="Stage via symlink or copy. Defaults to symlink.",
    )
    parser.add_argument(
        "--receipt",
        default=str(DEFAULT_RECEIPT),
        help="JSON receipt path. Defaults to workspace/input_stage_reports/latest_stage_inputs.json.",
    )
    return parser


def main() -> int:
    """Run the input-staging CLI."""

    parser = build_parser()
    args = parser.parse_args()
    receipt = stage_inputs(
        args.sources,
        dest_root=args.dest_root,
        link_mode=args.link_mode,
    )
    receipt_path = write_stage_receipt(receipt, args.receipt)
    print(f"[stage-inputs] staged={len(receipt)} receipt={receipt_path}")
    for row in receipt:
        print(f"[stage-inputs] {row['link_mode']} {row['source']} -> {row['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
