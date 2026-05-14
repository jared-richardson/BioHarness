#!/usr/bin/env python3
"""Run the processed metabolomics benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.metabolomics_benchmark import (  # noqa: E402
    default_metabolomics_benchmark_cases,
    run_metabolomics_benchmark,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "workspace" / "benchmark_outputs" / "metabolomics",
        help="Directory where benchmark artifacts should be written.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Optional case-id filter. May be repeated.",
    )
    parser.add_argument(
        "--benchmark-policy",
        type=str,
        default="scientific_harness",
        help="Benchmark policy passed through to the harness.",
    )
    parser.add_argument("--model-name", type=str, default="", help="Optional harness model override.")
    parser.add_argument("--host", type=str, default="", help="Optional backend host override.")
    parser.add_argument("--llm-backend", type=str, default="", help="Optional backend provider override.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1200.0,
        help="Per-case execution timeout in seconds.",
    )
    return parser.parse_args()


def _select_cases(case_ids: list[str]) -> tuple:
    cases = default_metabolomics_benchmark_cases()
    if not case_ids:
        return cases
    selected = tuple(case for case in cases if case.case_id in set(case_ids))
    if not selected:
        raise SystemExit(f"No metabolomics benchmark cases matched: {', '.join(case_ids)}")
    return selected


def main() -> int:
    """Run the CLI entrypoint."""

    args = _parse_args()
    summary = run_metabolomics_benchmark(
        output_root=args.output.expanduser().resolve(),
        project_root=PROJECT_ROOT,
        cases=_select_cases(list(args.case_id)),
        benchmark_policy=str(args.benchmark_policy or "scientific_harness").strip() or "scientific_harness",
        model_name=str(args.model_name or "").strip() or None,
        host=str(args.host or "").strip() or None,
        llm_backend=str(args.llm_backend or "").strip() or None,
        command_timeout_seconds=float(args.timeout_seconds),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
