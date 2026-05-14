#!/usr/bin/env python3
"""Update the curated scientific tool catalog."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.scientific_tool_catalog import (  # noqa: E402
    SCIENTIFIC_TOOL_CATALOG_PATH,
    load_curated_scientific_tool_catalog,
    save_curated_scientific_tool_catalog,
    upsert_scientific_tool_entry,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Primary tool name.")
    parser.add_argument("--support-tier", default="catalog_only", choices=("catalog_only", "helper_script"))
    parser.add_argument("--family", default="", help="High-level tool family.")
    parser.add_argument("--description", default="", help="Short tool summary.")
    parser.add_argument("--when-to-use", default="", help="When the tool is appropriate.")
    parser.add_argument("--when-not-to-use", default="", help="When the tool is not appropriate.")
    parser.add_argument("--alias", action="append", default=[], help="Additional alias. May be repeated.")
    parser.add_argument("--capability", action="append", default=[], help="Capability tag. May be repeated.")
    parser.add_argument("--analysis-category", action="append", default=[], help="Analysis category. May be repeated.")
    parser.add_argument("--input-type", action="append", default=[], help="Supported input type. May be repeated.")
    parser.add_argument("--output-type", action="append", default=[], help="Supported output type. May be repeated.")
    parser.add_argument("--required-parameter", action="append", default=[], help="Required parameter name.")
    parser.add_argument("--optional-parameter", action="append", default=[], help="Optional parameter name.")
    parser.add_argument("--executable", action="append", default=[], help="Executable name. May be repeated.")
    parser.add_argument("--repo-alternative", action="append", default=[], help="Nearby repo-supported alternative.")
    parser.add_argument("--augment-capability-catalog", action="store_true", help="Expose this tool in capability hints.")
    parser.add_argument("--documentation-url", default="", help="Official documentation URL.")
    return parser


def main() -> int:
    """Run the scientific tool update CLI."""
    parser = build_parser()
    args = parser.parse_args()
    catalog = load_curated_scientific_tool_catalog(SCIENTIFIC_TOOL_CATALOG_PATH)
    updated = upsert_scientific_tool_entry(
        catalog,
        {
            "name": args.name,
            "aliases": list(args.alias or []),
            "support_tier": args.support_tier,
            "family": args.family,
            "description": args.description,
            "when_to_use": args.when_to_use,
            "when_not_to_use": args.when_not_to_use,
            "capabilities": list(args.capability or []),
            "analysis_categories": list(args.analysis_category or []),
            "input_types": list(args.input_type or []),
            "output_types": list(args.output_type or []),
            "required_parameters": list(args.required_parameter or []),
            "optional_parameters": list(args.optional_parameter or []),
            "executables": list(args.executable or []),
            "repo_alternatives": list(args.repo_alternative or []),
            "augment_capability_catalog": bool(args.augment_capability_catalog),
            "documentation_url": args.documentation_url,
        },
    )
    save_curated_scientific_tool_catalog(updated, SCIENTIFIC_TOOL_CATALOG_PATH)
    print(SCIENTIFIC_TOOL_CATALOG_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
