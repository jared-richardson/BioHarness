#!/usr/bin/env python3
"""Print deterministic Bio-Harness help for users and model-facing debugging."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = PROJECT_ROOT / "bio_harness" / "skills" / "definitions"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact", action="store_true", help="Render a shorter help guide for prompt inspection.")
    parser.add_argument("--json", action="store_true", help="Emit the structured help payload as JSON instead of text.")
    return parser


def main() -> int:
    """Run the deterministic harness-help CLI."""
    args = build_parser().parse_args()
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from bio_harness.core.harness_help_context import (  # noqa: E402
        build_harness_help_context,
        build_harness_help_payload,
    )
    from bio_harness.skills.registry import SkillRegistry  # noqa: E402

    registry = SkillRegistry(SKILLS_DIR)
    if args.json:
        payload = build_harness_help_payload(registry._skills, compact=bool(args.compact))
        print(json.dumps(payload, indent=2, sort_keys=False))
        return 0
    print(build_harness_help_context(registry._skills, compact=bool(args.compact)).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
