#!/usr/bin/env python3
"""Run one benchmark suite under one harness variant.

This is a thin wrapper over the existing benchmark runners. It applies one
variant's environment overrides, delegates execution to the chosen suite
script, then records parsed results into the variant benchmark store.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.variant_benchmark import (  # noqa: E402
    ABLATION_VARIANTS,
    VariantBenchmarkStore,
    VariantResult,
    config_override_cli_args,
    config_override_env,
)

_SUITE_SCRIPTS: dict[str, Path] = {
    "feature": PROJECT_ROOT / "scripts" / "run_feature_benchmarks.py",
    "extended": PROJECT_ROOT / "scripts" / "run_extended_scientific_benchmark_suite.py",
    "official": PROJECT_ROOT / "scripts" / "run_bioagentbench_official.py",
}


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant-id", required=True, choices=sorted(ABLATION_VARIANTS))
    parser.add_argument("--suite", required=True, choices=sorted(_SUITE_SCRIPTS))
    parser.add_argument(
        "--store-path",
        default=str(PROJECT_ROOT / "workspace" / "variant_benchmarks" / "results.jsonl"),
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Path to the suite summary/report JSON produced by the delegated runner.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "runner_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the delegated benchmark runner.",
    )
    return parser


def main() -> int:
    """Run one suite under one variant and record parsed results."""

    args = build_parser().parse_args()
    variant = ABLATION_VARIANTS[args.variant_id]
    suite_script = _SUITE_SCRIPTS[args.suite]
    report_path = Path(args.report_path).expanduser().resolve(strict=False) if args.report_path else None
    command = _build_runner_command(
        suite_script,
        suite=args.suite,
        runner_args=list(args.runner_args or []),
        config_overrides=variant.config_overrides,
    )
    env = _variant_env(
        env_overrides=variant.env_overrides,
        config_overrides=variant.config_overrides,
    )
    if args.dry_run:
        payload = {
            "variant_id": variant.variant_id,
            "suite": args.suite,
            "command": command,
            "report_path": str(report_path) if report_path is not None else "",
            "env_overrides": variant.env_overrides,
            "config_overrides": variant.config_overrides,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if report_path is None:
        raise SystemExit("--report-path is required when not using --dry-run.")

    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        check=False,
    )
    results = _load_suite_results(
        suite=args.suite,
        report_path=report_path,
        variant_id=variant.variant_id,
    )
    store = VariantBenchmarkStore(args.store_path)
    if results:
        for result in results:
            store.record_result(result)
    else:
        store.record_result(
            VariantResult(
                variant_id=variant.variant_id,
                task_name=args.suite,
                status="error" if completed.returncode != 0 else "completed",
                score=0.0,
                error_message=(
                    f"Suite runner exited with code {completed.returncode} and no parsable report was found."
                ),
            )
        )
    return int(completed.returncode)


def _build_runner_command(
    suite_script: Path,
    *,
    suite: str,
    runner_args: list[str],
    config_overrides: dict[str, Any],
) -> list[str]:
    filtered_args = list(runner_args)
    if filtered_args and filtered_args[0] == "--":
        filtered_args = filtered_args[1:]
    cli_overrides = config_override_cli_args(config_overrides) if suite in {"official", "extended"} else []
    return [sys.executable, str(suite_script), *cli_overrides, *filtered_args]


def _variant_env(
    *,
    env_overrides: dict[str, str],
    config_overrides: dict[str, Any],
) -> dict[str, str]:
    env = dict(os.environ)
    for key, value in env_overrides.items():
        rendered = str(value)
        if rendered:
            env[str(key)] = rendered
        else:
            env.pop(str(key), None)
    for env_key, value in config_override_env(config_overrides).items():
        rendered = str(value)
        if rendered:
            env[env_key] = rendered
        else:
            env.pop(env_key, None)
    return env


def _load_suite_results(
    *,
    suite: str,
    report_path: Path,
    variant_id: str,
) -> list[VariantResult]:
    if not report_path.is_file():
        return []
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    if suite == "feature":
        return _feature_results_from_payload(payload, variant_id)
    if suite == "extended":
        return _extended_results_from_payload(payload, variant_id)
    if suite == "official":
        return _official_results_from_payload(payload, variant_id)
    return []


def _feature_results_from_payload(payload: dict[str, Any], variant_id: str) -> list[VariantResult]:
    scenarios = payload.get("scenarios", [])
    if not isinstance(scenarios, list):
        return []
    results: list[VariantResult] = []
    for item in scenarios:
        if not isinstance(item, dict):
            continue
        results.append(
            VariantResult(
                variant_id=variant_id,
                task_name=f"{str(item.get('feature', '')).strip()}:{str(item.get('scenario_id', '')).strip()}",
                status="pass" if bool(item.get("passed", False)) else "fail",
                score=float(item.get("score", 0.0) or 0.0),
                runtime_seconds=float(item.get("elapsed_seconds", 0.0) or 0.0),
                repairs_needed=0,
                error_message=str(item.get("error", "") or "").strip(),
                metadata={"feature": str(item.get("feature", "") or "").strip()},
            )
        )
    return results


def _extended_results_from_payload(payload: dict[str, Any], variant_id: str) -> list[VariantResult]:
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    results: list[VariantResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        passed = bool(item.get("passed", False))
        results.append(
            VariantResult(
                variant_id=variant_id,
                task_name=str(item.get("case_id", "") or "").strip(),
                status="pass" if passed else str(item.get("status", "") or "").strip().lower() or "fail",
                score=1.0 if passed else 0.0,
                runtime_seconds=0.0,
                repairs_needed=0,
                error_message=str(item.get("error", "") or "").strip(),
                metadata={"lane": str(item.get("lane", "") or "").strip()},
            )
        )
    return results


def _official_results_from_payload(payload: dict[str, Any], variant_id: str) -> list[VariantResult]:
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    results: list[VariantResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        bucket = str(item.get("official_report_bucket", "") or "").strip()
        passed = bucket == "official_blind_clean"
        results.append(
            VariantResult(
                variant_id=variant_id,
                task_name=str(item.get("task_id", "") or "").strip(),
                status="pass" if passed else bucket.lower() or "fail",
                score=1.0 if passed else 0.0,
                runtime_seconds=0.0,
                repairs_needed=0,
                error_message=str(item.get("harness_error", "") or "").strip(),
                metadata={
                    "official_report_bucket": bucket,
                    "validation_passed": bool(item.get("validation_passed", False)),
                },
            )
        )
    return results


if __name__ == "__main__":
    raise SystemExit(main())
