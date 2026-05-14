#!/usr/bin/env python3
"""Profile the schema of a completed artifact and emit a JSON data dictionary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.reporting.artifact_schema import write_artifact_schema_profile  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", type=Path, help="Artifact path to profile.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional output JSON path.")
    parser.add_argument("--sample-rows", type=int, default=25, help="Number of rows to sample for type inference.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_path = write_artifact_schema_profile(
        args.input_path,
        args.output_json,
        sample_rows=max(1, int(args.sample_rows)),
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
