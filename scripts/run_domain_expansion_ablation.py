#!/usr/bin/env python3
"""Run the 24-case domain-expansion ablation suite.

This runner executes the mixed control/domain/stress manifest under named
variants, records one JSON case result per run, and writes attempt-scoped
status and aggregate summaries suitable for overnight monitoring.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.domain_expansion_ablation import (  # noqa: E402
    case_result_to_variant_result,
    DOMAIN_EXPANSION_ABLATION_VARIANTS,
    DomainExpansionCase,
    DomainExpansionCaseResult,
    load_domain_expansion_manifest,
    PRIMARY_MATRIX_VARIANT_IDS,
    render_template_lift_by_band,
    render_variant_markdown,
    SECONDARY_STRESS_VARIANT_IDS,
    summarize_variant_results,
)
from bio_harness.core.subprocess_watchdog import (  # noqa: E402
    run_subprocess_with_watchdog,
)
from bio_harness.core.variant_benchmark import (  # noqa: E402
    VariantBenchmarkStore,
    config_override_cli_args,
    config_override_env,
)

HARNESS_SCRIPT = PROJECT_ROOT / "scripts" / "run_agent_e2e.py"
DEFAULT_MANIFEST = PROJECT_ROOT / "workspace" / "benchmark_data" / "ablation_manifest_24.json"
DEFAULT_ATTEMPT_ROOT = PROJECT_ROOT / "workspace" / "ablation_results" / "domain_expansion_ablation"
TERMINATION_GRACE_SECONDS = 15
_SPATIAL_PRIMARY_ARTIFACTS = (
    "spatial_domain_assignments.csv",
    "spatial_marker_genes.csv",
    "spatial_results.h5ad",
)
_PROTEOMICS_PRIMARY_ARTIFACTS = (
    "proteomics_differential_abundance.csv",
    "proteomics_qc_summary.json",
    "normalized_abundance_matrix.tsv",
    "volcano_plot_data.tsv",
    "proteomics_summary.md",
)
_METABOLOMICS_PRIMARY_ARTIFACTS = (
    "metabolomics_differential_abundance.csv",
    "metabolomics_qc_summary.json",
    "normalized_feature_matrix.tsv",
    "volcano_plot_data.tsv",
    "metabolomics_summary.md",
)
# Fix #18: the bacterial evolution benchmark requires the ancestor-subtracted
# + annotated shared-variant CSV under selected/final/. Without this gate the
# evaluator silently passed runs where the planner stopped after annotating
# a single evolved branch (skipping evol2, isec, the final CSV, etc.).
_EVOLUTION_PRIMARY_ARTIFACTS = ("variants_shared.csv",)
_EVOLUTION_PRIMARY_ARTIFACT_MIN_BYTES = 32  # header row alone is ~32 bytes
_EXPECTED_BAD_INPUT_MARKERS = (
    "format_input_error",
    "non-numeric values",
    "malformed fastq",
    "truncated fastq",
    "__format_input_error__",
    "bad input",
)
_EXPECTED_BAD_INPUT_PRECHECK_CATEGORIES = (
    "truncated_file",
    "fastq_format_error",
    "format_mismatch",
)


def build_parser() -> argparse.ArgumentParser:
    """Build the domain-expansion ablation CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", action="append", default=[], help="Run one named variant. Repeatable.")
    parser.add_argument("--all-primary", action="store_true", help="Run the 4-cell primary matrix.")
    parser.add_argument(
        "--include-no-recovery-stress",
        action="store_true",
        help="Also run the 18-case stress-band no-recovery variants overnight.",
    )
    parser.add_argument("--manifest-file", type=str, default=str(DEFAULT_MANIFEST))
    parser.add_argument("--attempt-label", type=str, default="")
    parser.add_argument(
        "--model-name",
        type=str,
        default="",
        help="Override both executor and planner models for each harness subprocess.",
    )
    parser.add_argument(
        "--planner-model-name",
        type=str,
        default="",
        help="Override the planning model used by the harness subprocess (maps to BIO_HARNESS_MODEL_HEAVY).",
    )
    parser.add_argument(
        "--executor-model-name",
        type=str,
        default="",
        help="Override the executor model used by the harness subprocess (maps to BIO_HARNESS_MODEL).",
    )
    parser.add_argument("--llm-backend", type=str, default="")
    parser.add_argument("--host", type=str, default="")
    parser.add_argument(
        "--execution-mode",
        type=str,
        choices=("batch", "stepwise"),
        default="",
        help="Override the harness execution mode for each case subprocess.",
    )
    parser.add_argument("--heartbeat-seconds", type=int, default=15)
    parser.add_argument("--stall-timeout-seconds", type=int, default=45)
    parser.add_argument("--live-process-grace-seconds", type=int, default=900)
    parser.add_argument("--case-timeout-seconds", type=int, default=1800)
    parser.add_argument(
        "--planner-attempt-timeout-seconds",
        type=int,
        default=0,
        help="Override BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS for each harness subprocess.",
    )
    parser.add_argument(
        "--llm-timeout-seconds",
        type=int,
        default=0,
        help="Override BIO_HARNESS_LLM_TIMEOUT_SECONDS for each harness subprocess.",
    )
    parser.add_argument("--case-id", action="append", default=[], help="Restrict to one case id. Repeatable.")
    parser.add_argument("--band", action="append", default=[], type=int, help="Restrict to one band. Repeatable.")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    """Run the requested domain-expansion ablation sweeps."""

    args = build_parser().parse_args()
    variants = _variants_for_args(args)
    attempt_label = args.attempt_label.strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    attempt_dir = (DEFAULT_ATTEMPT_ROOT / attempt_label).resolve()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(args.manifest_file).expanduser().resolve()
    all_cases = load_domain_expansion_manifest(manifest_path=manifest_path, project_root=PROJECT_ROOT)

    plan_payload = _build_plan_payload(variants=variants, cases=all_cases, args=args)
    _write_json(attempt_dir / "plan.json", plan_payload)
    store = VariantBenchmarkStore(attempt_dir / "results.jsonl")

    summaries = []
    status_payload = {
        "attempt_label": attempt_label,
        "state": "running",
        "manifest_file": str(manifest_path),
        "variants_total": len(variants),
        "variants_completed": 0,
        "current_variant": "",
        "current_case": "",
        "variant_statuses": [],
    }
    _write_json(attempt_dir / "status.json", status_payload)

    try:
        for variant in variants:
            cases = _select_cases_for_variant(all_cases=all_cases, variant_id=variant.variant_id, args=args)
            variant_result = run_variant_sweep(
                args=args,
                variant=variant,
                cases=cases,
                attempt_dir=attempt_dir,
                store=store,
                status_payload=status_payload,
            )
            summaries.append(variant_result)
            status_payload["variants_completed"] = int(status_payload.get("variants_completed", 0)) + 1
            _write_json(attempt_dir / "status.json", status_payload)
    except Exception as exc:
        status_payload["state"] = "failed"
        status_payload["error"] = str(exc)
        _write_json(attempt_dir / "status.json", status_payload)
        raise

    attempt_summary = {
        "attempt_label": attempt_label,
        "variants": [item.to_dict() for item in summaries],
    }
    template_lift_rows, template_lift_markdown = render_template_lift_by_band(summaries)
    attempt_summary["template_lift_by_band"] = template_lift_rows
    _write_json(attempt_dir / "summary.json", attempt_summary)
    (attempt_dir / "summary.md").write_text(render_variant_markdown(summaries).strip() + "\n", encoding="utf-8")
    _write_json(attempt_dir / "template_lift_by_band.json", {"rows": template_lift_rows})
    (attempt_dir / "template_lift_by_band.md").write_text(template_lift_markdown.strip() + "\n", encoding="utf-8")

    status_payload["state"] = "completed"
    status_payload["current_variant"] = ""
    status_payload["current_case"] = ""
    _write_json(attempt_dir / "status.json", status_payload)
    print(json.dumps(attempt_summary, indent=2, sort_keys=True))
    return 0


def run_variant_sweep(
    *,
    args: argparse.Namespace,
    variant,
    cases: tuple[DomainExpansionCase, ...],
    attempt_dir: Path,
    store: VariantBenchmarkStore,
    status_payload: dict[str, Any],
):
    """Run one domain-expansion ablation sweep under one variant."""

    variant_root = attempt_dir / variant.variant_id
    variant_root.mkdir(parents=True, exist_ok=True)
    case_results: list[DomainExpansionCaseResult] = []
    status_payload["current_variant"] = variant.variant_id
    status_payload["current_case"] = ""
    variant_status = {
        "variant_id": variant.variant_id,
        "cases_total": len(cases),
        "cases_completed": 0,
        "cases_failed": 0,
        "current_case": "",
    }
    status_payload.setdefault("variant_statuses", []).append(variant_status)
    _write_json(attempt_dir / "status.json", status_payload)

    for case in cases:
        status_payload["current_case"] = case.case_id
        variant_status["current_case"] = case.case_id
        _write_json(attempt_dir / "status.json", status_payload)
        result = _run_case(
            args=args,
            variant=variant,
            case=case,
            variant_root=variant_root,
        )
        case_results.append(result)
        store.record_result(case_result_to_variant_result(result))
        variant_status["cases_completed"] = int(variant_status.get("cases_completed", 0)) + 1
        if not result.passed:
            variant_status["cases_failed"] = int(variant_status.get("cases_failed", 0)) + 1
            if args.stop_on_failure:
                raise RuntimeError(f"{variant.variant_id}:{case.case_id} failed: {result.error or result.status}")
        _write_json(variant_root / "suite_summary.json", {"items": [asdict(item) for item in case_results]})
        _write_json(attempt_dir / "status.json", status_payload)

    summary = summarize_variant_results(variant=variant, results=case_results)
    _write_json(variant_root / "ablation_summary.json", summary.to_dict())
    variant_status["current_case"] = ""
    return summary


def _run_case(
    *,
    args: argparse.Namespace,
    variant,
    case: DomainExpansionCase,
    variant_root: Path,
) -> DomainExpansionCaseResult:
    case_root = variant_root / case.case_id
    case_root.mkdir(parents=True, exist_ok=True)
    case_result_path = case_root / "case_result.json"
    if case_result_path.is_file():
        return DomainExpansionCaseResult(**json.loads(case_result_path.read_text(encoding="utf-8")))

    selected_dir = case_root / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    result_json = selected_dir / "result.json"
    log_file = case_root / "harness.log"
    env = _build_variant_env(variant, args=args)
    command = _build_harness_command(
        args=args,
        case=case,
        selected_dir=selected_dir,
        result_json=result_json,
        variant=variant,
    )
    if args.dry_run:
        payload = DomainExpansionCaseResult(
            case_id=case.case_id,
            band=case.band,
            variant_id=variant.variant_id,
            benchmark_policy="scientific_harness",
            expected_outcome=case.expected_outcome,
            selected_dir=str(selected_dir),
            result_json=str(result_json),
            log_file=str(log_file),
            run_dir="",
            status="dry_run",
            passed=False,
            timed_out=False,
            harness_exit_code=0,
            elapsed_seconds=0.0,
            primary_artifact_present=False,
            primary_artifact_paths=[],
        )
        _write_json(case_result_path, asdict(payload))
        return payload

    started_at = time.monotonic()
    exit_code, timed_out = _run_case_subprocess(
        cmd=command,
        env=env,
        log_path=log_file,
        timeout_seconds=int(args.case_timeout_seconds),
        progress_paths=(selected_dir,),
        progress_path_resolver=lambda: _watchdog_progress_paths(selected_dir=selected_dir),
    )
    elapsed_seconds = round(time.monotonic() - started_at, 3)
    payload = _load_result_json(result_json, exit_code=exit_code, timed_out=timed_out)
    run_dir = _resolve_run_dir(selected_dir=selected_dir, payload=payload)
    state_payload = _read_json(run_dir / "state.json") if run_dir is not None else {}
    exit_payload = _read_json(run_dir / "exit.json") if run_dir is not None else {}
    assistance_payload = _read_json(run_dir / "assistance_manifest.json") if run_dir is not None else {}
    failure_diagnosis = payload.get("failure_diagnosis", {}) if isinstance(payload.get("failure_diagnosis", {}), dict) else {}
    if not failure_diagnosis:
        failure_diagnosis = state_payload.get("failure_diagnosis", {}) if isinstance(state_payload.get("failure_diagnosis", {}), dict) else {}

    status = _reconstruct_status(payload=payload, state_payload=state_payload, exit_payload=exit_payload)
    error_text = _reconstruct_error_text(payload=payload, exit_payload=exit_payload, run_dir=run_dir)
    artifact_paths = _find_primary_artifacts(case=case, selected_dir=selected_dir, run_dir=run_dir)
    primary_artifact_present = _primary_artifact_present(case=case, artifact_paths=artifact_paths)
    passed, reasons = _evaluate_case(
        case=case,
        status=status,
        primary_artifact_present=primary_artifact_present,
        error_text=error_text,
        failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
    )

    result = DomainExpansionCaseResult(
        case_id=case.case_id,
        band=case.band,
        variant_id=variant.variant_id,
        benchmark_policy="scientific_harness",
        expected_outcome=case.expected_outcome,
        selected_dir=str(selected_dir),
        result_json=str(result_json),
        log_file=str(log_file),
        run_dir=str(run_dir) if run_dir is not None else "",
        status=status,
        passed=passed,
        timed_out=timed_out,
        harness_exit_code=int(exit_code),
        elapsed_seconds=elapsed_seconds,
        primary_artifact_present=primary_artifact_present,
        primary_artifact_paths=artifact_paths,
        error=error_text,
        failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
        failure_suggested_fix=str(failure_diagnosis.get("suggested_fix", "") or ""),
        auto_repair_history_count=len(state_payload.get("auto_repair_history", []) or []),
        planner_failopen_used=bool(
            assistance_payload.get("planner_failopen_used", False) or state_payload.get("planner_failopen_used", False)
        ),
        generic_template_fallback_used=bool(
            assistance_payload.get("generic_template_fallback_used", False)
            or state_payload.get("generic_template_fallback_used", False)
        ),
        protocol_template_fallback_used=bool(
            assistance_payload.get("protocol_template_fallback_used", False)
            or state_payload.get("protocol_template_fallback_used", False)
        ),
        reasons=reasons,
    )
    _write_json(case_result_path, asdict(result))
    return result


def _variants_for_args(args: argparse.Namespace):
    variant_ids: list[str] = []
    if args.all_primary:
        variant_ids.extend(PRIMARY_MATRIX_VARIANT_IDS)
    if args.include_no_recovery_stress:
        variant_ids.extend(SECONDARY_STRESS_VARIANT_IDS)
    variant_ids.extend(str(item).strip() for item in args.variant if str(item).strip())
    if not variant_ids:
        raise SystemExit("Select variants with --all-primary, --include-no-recovery-stress, or --variant.")
    seen: set[str] = set()
    ordered: list[str] = []
    for variant_id in variant_ids:
        if variant_id in seen:
            continue
        if variant_id not in DOMAIN_EXPANSION_ABLATION_VARIANTS:
            raise SystemExit(
                f"Unknown variant '{variant_id}'. Available: {', '.join(sorted(DOMAIN_EXPANSION_ABLATION_VARIANTS))}"
            )
        seen.add(variant_id)
        ordered.append(variant_id)
    return [DOMAIN_EXPANSION_ABLATION_VARIANTS[variant_id] for variant_id in ordered]


def _select_cases_for_variant(
    *,
    all_cases: tuple[DomainExpansionCase, ...],
    variant_id: str,
    args: argparse.Namespace,
) -> tuple[DomainExpansionCase, ...]:
    wanted_case_ids = {str(item).strip() for item in args.case_id if str(item).strip()}
    wanted_bands = {int(item) for item in args.band if int(item)}
    if variant_id in SECONDARY_STRESS_VARIANT_IDS:
        wanted_bands.update({2, 3})
    selected = [
        case
        for case in all_cases
        if (not wanted_case_ids or case.case_id in wanted_case_ids) and (not wanted_bands or case.band in wanted_bands)
    ]
    return tuple(selected)


def _build_plan_payload(*, variants, cases: tuple[DomainExpansionCase, ...], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "manifest_file": str(Path(args.manifest_file).expanduser().resolve()),
        "variants": [
            {
                "variant_id": variant.variant_id,
                "description": variant.description,
                "case_ids": [case.case_id for case in _select_cases_for_variant(all_cases=cases, variant_id=variant.variant_id, args=args)],
            }
            for variant in variants
        ],
    }


def _build_variant_env(variant, *, args: argparse.Namespace) -> dict[str, str]:
    env = dict(os.environ)
    env.update({str(key): str(value) for key, value in variant.env_overrides.items() if str(value)})
    env.update(config_override_env(variant.config_overrides))
    _apply_explicit_model_overrides(env, args=args)
    planner_timeout = int(getattr(args, "planner_attempt_timeout_seconds", 0) or 0)
    llm_timeout = int(getattr(args, "llm_timeout_seconds", 0) or 0)
    if planner_timeout > 0:
        env["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] = str(planner_timeout)
    if llm_timeout > 0:
        env["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] = str(llm_timeout)
    return env


def _apply_explicit_model_overrides(env: dict[str, str], *, args: argparse.Namespace) -> None:
    """Apply explicit model overrides after variant defaults.

    Args:
        env: Mutable harness subprocess environment.
        args: Parsed CLI arguments.
    """

    model_name = str(getattr(args, "model_name", "") or "").strip()
    executor_model_name = str(getattr(args, "executor_model_name", "") or "").strip()
    planner_model_name = str(getattr(args, "planner_model_name", "") or "").strip()
    if model_name:
        env["BIO_HARNESS_MODEL"] = model_name
        env["BIO_HARNESS_MODEL_HEAVY"] = model_name
    if executor_model_name:
        env["BIO_HARNESS_MODEL"] = executor_model_name
    if planner_model_name:
        env["BIO_HARNESS_MODEL_HEAVY"] = planner_model_name


def _resolved_executor_model_name(args: argparse.Namespace) -> str:
    """Return the executor model override to pass through to the child runner."""

    executor_model_name = str(getattr(args, "executor_model_name", "") or "").strip()
    if executor_model_name:
        return executor_model_name
    return str(getattr(args, "model_name", "") or "").strip()


def _build_harness_command(
    *,
    args: argparse.Namespace,
    case: DomainExpansionCase,
    selected_dir: Path,
    result_json: Path,
    variant,
) -> list[str]:
    command = [
        sys.executable,
        str(HARNESS_SCRIPT),
        "--prompt-file",
        case.prompt_file,
        "--selected-dir",
        str(selected_dir),
        "--data-root",
        case.data_root,
        "--result-json",
        str(result_json),
        "--benchmark-policy",
        "scientific_harness",
        "--heartbeat-seconds",
        str(int(args.heartbeat_seconds)),
        "--stall-timeout-seconds",
        str(int(args.stall_timeout_seconds)),
        "--live-process-grace-seconds",
        str(int(args.live_process_grace_seconds)),
        *config_override_cli_args(variant.config_overrides),
        "--quiet",
    ]
    if str(args.llm_backend).strip():
        command.extend(["--llm-backend", str(args.llm_backend).strip()])
    if str(args.host).strip():
        command.extend(["--host", str(args.host).strip()])
    if str(getattr(args, "execution_mode", "") or "").strip():
        command.extend(["--execution-mode", str(args.execution_mode).strip()])
    executor_model_name = _resolved_executor_model_name(args)
    if executor_model_name:
        command.extend(["--model-name", executor_model_name])
    return command


def _run_case_subprocess(
    *,
    cmd: list[str],
    env: dict[str, str],
    log_path: Path,
    timeout_seconds: int,
    progress_paths: tuple[Path, ...] = (),
    progress_path_resolver: Callable[[], tuple[Path, ...]] | None = None,
) -> tuple[int, bool]:
    """Run one harness case with a bounded progress-aware watchdog."""

    return run_subprocess_with_watchdog(
        cmd=cmd,
        cwd=PROJECT_ROOT,
        env=env,
        log_path=log_path,
        timeout_seconds=timeout_seconds,
        termination_grace_seconds=float(TERMINATION_GRACE_SECONDS),
        timeout_message=(
            f"[domain-expansion] Case watchdog exceeded {int(timeout_seconds)}s; sending SIGTERM."
        ),
        kill_message="[domain-expansion] Grace period expired; sending SIGKILL.",
        progress_paths=progress_paths,
        progress_path_resolver=progress_path_resolver,
    )


def _load_result_json(result_path: Path, *, exit_code: int, timed_out: bool) -> dict[str, Any]:
    if result_path.exists():
        payload = _read_json(result_path)
        if payload:
            return payload
    error = f"Harness did not write result JSON (exit={exit_code})"
    if timed_out:
        error = f"Harness timed out and did not write result JSON (exit={exit_code})"
    return {"status": "failed", "error": error, "run_dir": ""}


def _resolve_run_dir(*, selected_dir: Path, payload: dict[str, Any]) -> Path | None:
    raw_run_dir = str(payload.get("run_dir", "") or "").strip()
    if raw_run_dir:
        return Path(raw_run_dir).expanduser().resolve(strict=False)
    return _find_run_dir_for_selected_dir(selected_dir=selected_dir)


def _find_run_dir_for_selected_dir(*, selected_dir: Path) -> Path | None:
    """Return the newest run dir associated with one selected directory."""

    runs_root = PROJECT_ROOT / "workspace" / "runs"
    if not runs_root.exists():
        return None
    target_selected_dir = str(selected_dir.resolve(strict=False))
    for run_dir in sorted(runs_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not run_dir.is_dir():
            continue
        manifest_payload = _read_json(run_dir / "manifest.json")
        if str(manifest_payload.get("selected_dir", "") or "") == target_selected_dir:
            return run_dir
        completed_payload = _read_json(run_dir / "completed_run_context.json")
        if str(completed_payload.get("selected_dir", "") or "") == target_selected_dir:
            return run_dir
    return None


def _watchdog_progress_paths(*, selected_dir: Path) -> tuple[Path, ...]:
    """Return dynamic progress paths for the outer case watchdog."""

    paths: list[Path] = [selected_dir]
    run_dir = _find_run_dir_for_selected_dir(selected_dir=selected_dir)
    if run_dir is None:
        return tuple(paths)
    for name in (
        "events.jsonl",
        "state.json",
        "exit.json",
        "completed_run_context.json",
        "assistance_manifest.json",
    ):
        path = run_dir / name
        if path.exists():
            paths.append(path)
    return tuple(paths)


def _reconstruct_status(*, payload: dict[str, Any], state_payload: dict[str, Any], exit_payload: dict[str, Any]) -> str:
    for candidate in (payload.get("status", ""), state_payload.get("status", ""), exit_payload.get("status", "")):
        rendered = str(candidate or "").strip()
        if rendered:
            return rendered
    return "failed"


def _reconstruct_error_text(*, payload: dict[str, Any], exit_payload: dict[str, Any], run_dir: Path | None) -> str:
    for candidate in (payload.get("error", ""), exit_payload.get("error", "")):
        rendered = str(candidate or "").strip()
        if rendered:
            return rendered
    if run_dir is None:
        return ""
    stderr_text = _tail_file(run_dir / "stderr.log")
    if "__FORMAT_INPUT_ERROR__:" in stderr_text:
        return stderr_text.split("__FORMAT_INPUT_ERROR__:", 1)[1].strip()
    return stderr_text.strip()


def _find_primary_artifacts(*, case: DomainExpansionCase, selected_dir: Path, run_dir: Path | None) -> list[str]:
    roots = [selected_dir]
    if run_dir is not None:
        roots.append(run_dir)
    seen: set[str] = set()
    matches: list[str] = []
    ignored_parts = {"planner", "knowledge", "report", "report_bundle", "selected_dir_report", "run_dir_report"}
    prefix = _case_prefix(case)
    names: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    if prefix == "spatial":
        names = _SPATIAL_PRIMARY_ARTIFACTS
    elif prefix == "proteomics":
        names = _PROTEOMICS_PRIMARY_ARTIFACTS
    elif prefix == "metabolomics":
        names = _METABOLOMICS_PRIMARY_ARTIFACTS
    elif prefix == "evolution":
        names = _EVOLUTION_PRIMARY_ARTIFACTS
    elif prefix == "long_read_sv":
        suffixes = (".vcf", ".vcf.gz")
    elif prefix == "long_read_assembly":
        suffixes = (".fa", ".fasta", ".fna")
    elif prefix == "long_read_rna":
        suffixes = (".gtf", ".tsv", ".bam", ".bai")
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in ignored_parts for part in path.parts):
                continue
            if names and path.name not in names:
                continue
            if suffixes and not any(path.name.endswith(item) for item in suffixes):
                continue
            if not names and not suffixes:
                continue
            rendered = str(path)
            if rendered in seen:
                continue
            seen.add(rendered)
            matches.append(rendered)
    return matches


def _primary_artifact_present(*, case: DomainExpansionCase, artifact_paths: list[str]) -> bool:
    prefix = _case_prefix(case)
    if prefix == "spatial":
        found = {Path(item).name for item in artifact_paths}
        return all(name in found for name in _SPATIAL_PRIMARY_ARTIFACTS)
    if prefix == "proteomics":
        found = {Path(item).name for item in artifact_paths}
        return all(name in found for name in _PROTEOMICS_PRIMARY_ARTIFACTS)
    if prefix == "metabolomics":
        found = {Path(item).name for item in artifact_paths}
        return all(name in found for name in _METABOLOMICS_PRIMARY_ARTIFACTS)
    if prefix == "evolution":
        # Fix #18: the evolution benchmark requires the final shared-variant
        # CSV to exist AND contain at least a header row — an empty file is
        # treated as "missing" to catch planners that stopped short. If the
        # CSV lives somewhere other than selected/final we still accept it,
        # but we insist on non-empty contents.
        found = {Path(item).name: item for item in artifact_paths}
        for name in _EVOLUTION_PRIMARY_ARTIFACTS:
            if name not in found:
                return False
            try:
                size = Path(found[name]).stat().st_size
            except OSError:
                return False
            if size < _EVOLUTION_PRIMARY_ARTIFACT_MIN_BYTES:
                return False
        return True
    if prefix.startswith("long_read"):
        return bool(artifact_paths)
    return False


def _evaluate_case(
    *,
    case: DomainExpansionCase,
    status: str,
    primary_artifact_present: bool,
    error_text: str,
    failure_root_cause: str,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    status_token = str(status or "").strip().lower()
    if case.expected_outcome == "blocked_bad_input":
        bad_input = _is_bad_input_failure(error_text=error_text, failure_root_cause=failure_root_cause)
        if bad_input:
            return True, ["expected_bad_input_block"]
        reasons.append("expected_bad_input_block_missing")
        return False, reasons
    if status_token == "completed":
        # Fix #18: include "evolution" in the strict-artifact set so the
        # bacterial-evolution case cannot pass without the final shared-
        # variant CSV (previously any status=="completed" generic case
        # passed regardless of artifact presence).
        if _case_prefix(case) in {
            "spatial",
            "proteomics",
            "metabolomics",
            "evolution",
            "long_read_sv",
            "long_read_assembly",
            "long_read_rna",
        }:
            if primary_artifact_present:
                return True, ["completed_with_primary_artifact"]
            reasons.append("missing_primary_artifact")
            return False, reasons
        return True, ["completed"]
    reasons.append(f"status={status_token or 'unknown'}")
    return False, reasons


def _case_prefix(case: DomainExpansionCase) -> str:
    data_root = case.data_root
    if "/workspace/benchmark_data/spatial/" in data_root:
        return "spatial"
    if "/workspace/benchmark_data/proteomics/" in data_root:
        return "proteomics"
    if "/workspace/benchmark_data/metabolomics/" in data_root:
        return "metabolomics"
    if "/workspace/benchmark_data/long_read/" in data_root:
        if "assembly" in case.case_id:
            return "long_read_assembly"
        if "rna" in case.case_id or "isoform" in case.case_id:
            return "long_read_rna"
        return "long_read_sv"
    # Fix #18: recognize the bacterial-evolution benchmark so it can be gated
    # on its final shared-variant CSV. The bioagent-bench evolution task
    # lives under external/bioagent-bench paths rather than benchmark_data/.
    if "/tasks/evolution/" in data_root or case.case_id == "control_evolution":
        return "evolution"
    return "generic"


def _is_bad_input_failure(*, error_text: str, failure_root_cause: str) -> bool:
    rendered = " ".join(token for token in (str(error_text).lower(), str(failure_root_cause).lower()) if token)
    if any(token in rendered for token in _EXPECTED_BAD_INPUT_MARKERS):
        return True
    if "pipeline aborted" in rendered and ".fastq" in rendered and " line " in rendered:
        return True
    if "preflight blocked execution due to blocking input-quality issues" not in rendered:
        return False
    return any(category in rendered for category in _EXPECTED_BAD_INPUT_PRECHECK_CATEGORIES)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _tail_file(path: Path, limit: int = 4000) -> str:
    try:
        rendered = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return rendered[-limit:]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
