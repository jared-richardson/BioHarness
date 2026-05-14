"""Run the cold novel-tool onboarding case study.

This script currently implements the ``sylph`` case study described in the
research plan. It establishes a cold state, runs the bounded onboarding loop,
persists the resulting artifacts, and writes concise JSON/Markdown summaries
for manuscript use.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the cold novel-tool case-study runner.

    Returns:
        Configured argument parser.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", default="sylph", help="Novel tool case study to run.")
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=PROJECT_ROOT / "benchmark_data" / "novel_tool_case_study" / "sylph",
        help="Fixture directory used for the cold onboarding smoke recipes.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=PROJECT_ROOT / "workspace" / "novel_tool_case_study" / "sylph",
        help="Working directory for smoke outputs.",
    )
    parser.add_argument(
        "--skills-definitions-dir",
        type=Path,
        default=PROJECT_ROOT / "bio_harness" / "skills" / "definitions",
        help="Skill-definition directory receiving installed skills.",
    )
    parser.add_argument(
        "--skills-library-dir",
        type=Path,
        default=PROJECT_ROOT / "bio_harness" / "skills" / "library",
        help="Skill-library directory receiving installed wrappers.",
    )
    parser.add_argument(
        "--capability-catalog-path",
        type=Path,
        default=PROJECT_ROOT / "bio_harness" / "core" / "capability_catalog.json",
        help="Capability catalog updated by onboarding installs.",
    )
    parser.add_argument(
        "--tool-cards-dir",
        type=Path,
        default=PROJECT_ROOT / "workspace" / "tool_cards",
        help="Directory receiving persisted tool-card JSON files.",
    )
    parser.add_argument(
        "--advisory-catalog-path",
        type=Path,
        default=PROJECT_ROOT / "bio_harness" / "harness" / "repair_advisories.json",
        help="Repair-advisory catalog to update on repeated onboarding failures.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=PROJECT_ROOT / "docs" / "manuscript_assets" / "cold_onboarding_sylph_run.md",
        help="Markdown narrative output path.",
    )
    parser.add_argument(
        "--json-path",
        type=Path,
        default=PROJECT_ROOT / "docs" / "manuscript_assets" / "cold_onboarding_sylph_run.json",
        help="JSON summary output path.",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Run the smoke-only onboarding loop without persisting installed artifacts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the configured cold novel-tool case study.

    Args:
        argv: Optional CLI argument list.

    Returns:
        Process exit code.
    """

    parser = build_parser()
    args = parser.parse_args(argv)
    if str(args.tool).strip().lower() != "sylph":
        parser.error("Only the 'sylph' cold case study is implemented right now.")

    from bio_harness.core.novel_tool_case_study import (
        run_cold_sylph_case_study,
        write_case_study_json,
    )

    outcome = run_cold_sylph_case_study(
        fixtures_dir=args.fixtures_dir,
        work_dir=args.work_dir,
        skills_definitions_dir=args.skills_definitions_dir,
        skills_library_dir=args.skills_library_dir,
        capability_catalog_path=args.capability_catalog_path,
        tool_cards_dir=args.tool_cards_dir,
        summary_path=args.summary_path,
        advisory_catalog_path=args.advisory_catalog_path,
        install=not args.no_install,
    )
    write_case_study_json(args.json_path, outcome)

    print(f"Summary markdown: {outcome.summary_path}")
    print(f"Summary json: {args.json_path}")
    print(f"Tool found: {outcome.tool_found}")
    print(f"Retrieval before: {outcome.retrieval_before}")
    print(f"Retrieval after: {outcome.retrieval_after}")
    if outcome.onboarding_outcome is not None:
        print(f"Onboarding success: {outcome.onboarding_outcome.success}")
        return 0 if outcome.onboarding_outcome.success else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
