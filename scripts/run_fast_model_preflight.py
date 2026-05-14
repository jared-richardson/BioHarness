#!/usr/bin/env python3
"""Run the advisory fast-model preflight for model-agnostic harness changes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fast_signal_preflight import (  # noqa: E402
    DEFAULT_FAST_MODEL,
    DEFAULT_MINI_PREFLIGHT_BENCHMARK_POLICY,
    DEFAULT_PREFLIGHT_MEASUREMENT_PURPOSE,
    PreflightPlan,
    append_preflight_scorecard_rows,
    build_domain_preflight_plan,
    build_mini_preflight_plan,
    run_preflight_plan,
)

DEFAULT_MANIFEST = PROJECT_ROOT / "workspace" / "benchmark_data" / "ablation_manifest_24.json"
DEFAULT_MINI_ROOT = PROJECT_ROOT / "workspace" / "benchmark_data" / "fast_signal_mini"
DEFAULT_SCORECARD = PROJECT_ROOT / "workspace" / "studies" / "scorecard.jsonl"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "workspace" / "studies" / "fast_model_preflight_latest.json"


def build_parser() -> argparse.ArgumentParser:
    """Build the fast-model preflight CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("mini", "domain"), default="mini")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--manifest-file", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--mini-root", type=Path, default=DEFAULT_MINI_ROOT)
    parser.add_argument("--model", default=DEFAULT_FAST_MODEL)
    parser.add_argument("--attempt-label", default="fast_signal_preflight")
    parser.add_argument("--execution-mode", choices=("batch", "stepwise"), default="stepwise")
    parser.add_argument(
        "--benchmark-policy",
        default=DEFAULT_MINI_PREFLIGHT_BENCHMARK_POLICY,
    )
    parser.add_argument("--heartbeat-seconds", type=int, default=0)
    parser.add_argument("--stall-timeout-seconds", type=int, default=0)
    parser.add_argument("--live-process-grace-seconds", type=int, default=0)
    parser.add_argument("--max-repairs", type=int, default=None)
    parser.add_argument("--ollama-keep-alive", default="")
    parser.add_argument("--ollama-num-parallel", default="")
    parser.add_argument(
        "--prepare-mini-suite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prepare deterministic mini-benchmark inputs before planning.",
    )
    parser.add_argument("--overwrite-mini-suite", action="store_true")
    parser.add_argument(
        "--keep-selected-dirs",
        action="store_true",
        help="Do not clean mini selected dirs before running. Useful only for diagnostics.",
    )
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--record-scorecard", action="store_true")
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--model-digest", default="")
    parser.add_argument("--backend-version", default="")
    parser.add_argument("--optimization-profile", default="")
    parser.add_argument(
        "--measurement-purpose",
        default=DEFAULT_PREFLIGHT_MEASUREMENT_PURPOSE,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Run or print a fast-model preflight plan."""
    args = build_parser().parse_args()
    plan = _build_plan(args)
    if args.dry_run:
        print(json.dumps(plan.to_mapping(), indent=2, sort_keys=True))
        return 0
    result = run_preflight_plan(
        plan,
        project_root=PROJECT_ROOT,
        clean_selected_dirs=not args.keep_selected_dirs,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result.to_mapping(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.record_scorecard:
        append_preflight_scorecard_rows(
            plan=plan,
            run_result=result,
            scorecard_path=args.scorecard,
            model_digest=args.model_digest,
            backend_version=args.backend_version,
            optimization_profile=args.optimization_profile,
            measurement_purpose=args.measurement_purpose,
        )
    print(json.dumps(result.to_mapping(), indent=2, sort_keys=True))
    return 0 if result.status == "pass" else 1


def _build_plan(args: argparse.Namespace) -> PreflightPlan:
    if args.suite == "domain":
        return build_domain_preflight_plan(
            project_root=PROJECT_ROOT,
            manifest_file=args.manifest_file,
            case_ids=tuple(args.case_id),
            model=args.model,
            attempt_label=args.attempt_label,
            ollama_keep_alive=args.ollama_keep_alive,
            ollama_num_parallel=args.ollama_num_parallel,
        )
    return build_mini_preflight_plan(
        project_root=PROJECT_ROOT,
        mini_root=args.mini_root,
        case_ids=tuple(args.case_id),
        model=args.model,
        prepare_suite=args.prepare_mini_suite,
        overwrite_suite=args.overwrite_mini_suite,
        execution_mode=args.execution_mode,
        benchmark_policy=args.benchmark_policy,
        heartbeat_seconds=args.heartbeat_seconds,
        stall_timeout_seconds=args.stall_timeout_seconds,
        live_process_grace_seconds=args.live_process_grace_seconds,
        max_repairs=args.max_repairs,
        ollama_keep_alive=args.ollama_keep_alive,
        ollama_num_parallel=args.ollama_num_parallel,
    )


if __name__ == "__main__":
    raise SystemExit(main())
