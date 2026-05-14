#!/usr/bin/env python3
# ruff: noqa: E402
"""Update the repo-versioned repair advisory catalog."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.harness.repair_context import (
    REPAIR_ADVISORIES_PATH,
    load_repair_advisories,
    save_repair_advisories,
    upsert_repair_advisory,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        required=True,
        choices=("analysis", "tool"),
        help="Advisory scope to update.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Analysis type or tool name.",
    )
    parser.add_argument(
        "--summary",
        default="",
        help="Short summary for the advisory.",
    )
    parser.add_argument(
        "--repair-hint",
        action="append",
        default=[],
        help="Concrete repair hint. May be repeated.",
    )
    parser.add_argument(
        "--avoid-pattern",
        action="append",
        default=[],
        help="Known anti-pattern to avoid. May be repeated.",
    )
    parser.add_argument(
        "--source",
        default="manual",
        help="Provenance label for the advisory entry.",
    )
    return parser


def main() -> int:
    """Run the advisory update CLI."""
    parser = build_parser()
    args = parser.parse_args()
    catalog = load_repair_advisories(REPAIR_ADVISORIES_PATH)
    updated = upsert_repair_advisory(
        catalog,
        scope=args.scope,
        name=args.name,
        summary=args.summary,
        repair_hints=list(args.repair_hint or []),
        avoid_patterns=list(args.avoid_pattern or []),
        source=args.source,
    )
    save_repair_advisories(updated, REPAIR_ADVISORIES_PATH)
    print(REPAIR_ADVISORIES_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
