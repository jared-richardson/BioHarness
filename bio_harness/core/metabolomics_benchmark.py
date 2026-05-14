"""Benchmark table-first metabolomics support across planning and execution.

This module benchmarks the processed metabolomics tranche against the synthetic
feature-table corpus under ``workspace/benchmark_data/metabolomics``. It
measures:

- planning route quality
- execution success
- canonical artifact production
- differential-feature recovery against the synthetic truth set

The benchmark is intentionally scoped to processed metabolomics feature tables
and sample metadata. It does not claim raw LC-MS support.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from bio_harness.harness.config import HarnessConfig, WORKSPACE_ROOT
from scripts.run_agent_e2e_harness import AgentE2EHarness

_PRIMARY_ARTIFACT_NAMES: tuple[str, ...] = (
    "metabolomics_differential_abundance.csv",
    "metabolomics_qc_summary.json",
    "normalized_feature_matrix.tsv",
    "volcano_plot_data.tsv",
    "metabolomics_summary.md",
)
_DE_RECALL_TOP_K = 150
_DE_RECALL_THRESHOLD = 0.50


@dataclass(frozen=True)
class MetabolomicsBenchmarkCase:
    """One metabolomics benchmark case."""

    case_id: str
    description: str
    prompt_path: str
    data_root: str
    truth_path: str
    feature_table_path: str
    metadata_table_path: str
    expected_tools: tuple[str, ...]


@dataclass(frozen=True)
class MetabolomicsCaseResult:
    """Observed outcome for one metabolomics benchmark case."""

    case_id: str
    benchmark_policy: str
    selected_dir: str
    planning_selected_dir: str
    planning_analysis_type: str
    planning_chosen_method: str
    planning_preferred_tools: list[str]
    route_matches_expected: bool
    run_status: str
    run_returncode: int
    run_elapsed_seconds: float
    run_dir: str
    primary_artifact_present: bool
    primary_artifact_paths: list[str]
    de_recall_at150: float | None
    missing_tools_detected: list[str]
    failure_root_cause: str
    failure_suggested_fix: str
    observed_support_tier: str
    error: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""


def default_metabolomics_benchmark_cases(
    *,
    dataset_root: Path | None = None,
) -> tuple[MetabolomicsBenchmarkCase, ...]:
    """Return the default metabolomics benchmark cases."""

    root = dataset_root or (WORKSPACE_ROOT / "benchmark_data" / "metabolomics")
    ordered_ids = (
        "clean",
        "noisy_prompt",
        "nested_output",
        "metadata_ambiguity",
        "high_missingness",
        "malformed",
    )
    return tuple(_case(root, case_id) for case_id in ordered_ids)


def run_metabolomics_benchmark(
    *,
    output_root: Path,
    project_root: Path,
    cases: tuple[MetabolomicsBenchmarkCase, ...] | None = None,
    benchmark_policy: str = "scientific_harness",
    model_name: str | None = None,
    host: str | None = None,
    llm_backend: str | None = None,
    quiet: bool = True,
    command_timeout_seconds: float = 1200.0,
    harness_factory: Callable[[HarnessConfig], Any] = AgentE2EHarness,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Run the processed metabolomics benchmark."""

    selected_cases = cases or default_metabolomics_benchmark_cases()
    output_root.mkdir(parents=True, exist_ok=True)
    case_results = [
        _run_case(
            case,
            output_root=output_root,
            project_root=project_root,
            benchmark_policy=benchmark_policy,
            model_name=model_name,
            host=host,
            llm_backend=llm_backend,
            quiet=quiet,
            command_timeout_seconds=command_timeout_seconds,
            harness_factory=harness_factory,
            runner=runner,
        )
        for case in selected_cases
    ]
    summary = _summary(case_results)
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output_root / "summary.md").write_text(_summary_markdown(summary).strip() + "\n", encoding="utf-8")
    return summary


def _case(dataset_root: Path, case_id: str) -> MetabolomicsBenchmarkCase:
    case_root = dataset_root / case_id
    return MetabolomicsBenchmarkCase(
        case_id=case_id,
        description=f"Metabolomics benchmark case `{case_id}`.",
        prompt_path=str(case_root / "prompt.txt"),
        data_root=str(case_root / "data"),
        truth_path=str(case_root / "data" / "truth.json"),
        feature_table_path=str(case_root / "data" / "feature_table.csv"),
        metadata_table_path=str(case_root / "data" / "metadata.csv"),
        expected_tools=("metabolomics_diff_abundance",),
    )


def _run_case(
    case: MetabolomicsBenchmarkCase,
    *,
    output_root: Path,
    project_root: Path,
    benchmark_policy: str,
    model_name: str | None,
    host: str | None,
    llm_backend: str | None,
    quiet: bool,
    command_timeout_seconds: float,
    harness_factory: Callable[[HarnessConfig], Any],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> MetabolomicsCaseResult:
    case_root = output_root / case.case_id
    case_root.mkdir(parents=True, exist_ok=True)
    existing_result = _load_case_result(case_root)
    if existing_result is not None:
        return existing_result
    reconstructed_result = _reconstruct_case_result_from_artifacts(
        case=case,
        case_root=case_root,
        benchmark_policy=benchmark_policy,
    )
    if reconstructed_result is not None:
        _write_case_result(case_root, reconstructed_result)
        return reconstructed_result
    planning_selected_dir = case_root / "planning_selected"
    planning_selected_dir.mkdir(parents=True, exist_ok=True)
    planning_analysis_type = ""
    planning_chosen_method = ""
    planning_preferred_tools: list[str] = []
    planning_error = ""
    route_matches_expected = False

    try:
        spec = _plan_analysis_spec(
            case=case,
            planning_selected_dir=planning_selected_dir,
            benchmark_policy=benchmark_policy,
            model_name=model_name,
            host=host,
            llm_backend=llm_backend,
            harness_factory=harness_factory,
        )
        planning_analysis_type = str(spec.get("analysis_type", "") or "")
        planning_chosen_method = str(spec.get("chosen_method", "") or "")
        planning_preferred_tools = [
            str(item).strip()
            for item in (spec.get("preferred_tools", []) or [])
            if str(item).strip()
        ]
        route_matches_expected = _route_matches_expected(case, planning_chosen_method, planning_preferred_tools)
    except Exception as exc:  # pragma: no cover - defensive live benchmark capture
        planning_error = str(exc)

    selected_dir = case_root / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    result_json = selected_dir / "result.json"
    command = [
        sys.executable,
        str(project_root / "scripts" / "run_agent_e2e.py"),
        "--prompt-file",
        case.prompt_path,
        "--selected-dir",
        str(selected_dir),
        "--data-root",
        case.data_root,
        "--result-json",
        str(result_json),
        "--benchmark-policy",
        benchmark_policy,
    ]
    if quiet:
        command.append("--quiet")
    if model_name:
        command.extend(["--model-name", model_name])
    if host:
        command.extend(["--host", host])
    if llm_backend:
        command.extend(["--llm-backend", llm_backend])

    started_at = time.monotonic()
    completed, invocation_error = _invoke_case(
        command=command,
        cwd=project_root,
        timeout_seconds=command_timeout_seconds,
        runner=runner,
    )
    elapsed_seconds = round(time.monotonic() - started_at, 3)

    if invocation_error:
        result = MetabolomicsCaseResult(
            case_id=case.case_id,
            benchmark_policy=benchmark_policy,
            selected_dir=str(selected_dir),
            planning_selected_dir=str(planning_selected_dir),
            planning_analysis_type=planning_analysis_type,
            planning_chosen_method=planning_chosen_method,
            planning_preferred_tools=planning_preferred_tools,
            route_matches_expected=route_matches_expected,
            run_status="failed",
            run_returncode=-1,
            run_elapsed_seconds=elapsed_seconds,
            run_dir="",
            primary_artifact_present=False,
            primary_artifact_paths=[],
            de_recall_at150=None,
            missing_tools_detected=[],
            failure_root_cause="",
            failure_suggested_fix="",
            observed_support_tier=_support_tier(
                route_matches_expected=route_matches_expected,
                run_status="failed",
                primary_artifact_present=False,
                de_recall_at150=None,
                missing_tools_detected=[],
                failure_root_cause="",
                error_text=planning_error or invocation_error,
            ),
            error=planning_error or invocation_error,
        )
        _write_case_result(case_root, result)
        return result

    payload = _read_json(result_json)
    if not payload:
        result = MetabolomicsCaseResult(
            case_id=case.case_id,
            benchmark_policy=benchmark_policy,
            selected_dir=str(selected_dir),
            planning_selected_dir=str(planning_selected_dir),
            planning_analysis_type=planning_analysis_type,
            planning_chosen_method=planning_chosen_method,
            planning_preferred_tools=planning_preferred_tools,
            route_matches_expected=route_matches_expected,
            run_status="failed",
            run_returncode=int(completed.returncode),
            run_elapsed_seconds=elapsed_seconds,
            run_dir="",
            primary_artifact_present=False,
            primary_artifact_paths=[],
            de_recall_at150=None,
            missing_tools_detected=[],
            failure_root_cause="",
            failure_suggested_fix="",
            observed_support_tier=_support_tier(
                route_matches_expected=route_matches_expected,
                run_status="failed",
                primary_artifact_present=False,
                de_recall_at150=None,
                missing_tools_detected=[],
                failure_root_cause="",
                error_text=planning_error or "missing_or_invalid_result_json",
            ),
            error=planning_error or "missing_or_invalid_result_json",
            stdout_tail=_tail_text(completed.stdout),
            stderr_tail=_tail_text(completed.stderr),
        )
        _write_case_result(case_root, result)
        return result

    run_dir = Path(str(payload.get("run_dir", "") or "")).expanduser().resolve(strict=False)
    artifact_paths = _find_primary_artifacts(selected_dir=selected_dir, run_dir=run_dir)
    failure_diagnosis = payload.get("failure_diagnosis", {}) if isinstance(payload.get("failure_diagnosis", {}), dict) else {}
    missing_tools_detected = [
        str(item).strip()
        for item in (payload.get("missing_tools_detected", []) or [])
        if str(item).strip()
    ]
    de_recall = _compute_de_recall(artifact_paths, truth_path=Path(case.truth_path))
    error_text = planning_error or str(payload.get("error", "") or "")
    result = MetabolomicsCaseResult(
        case_id=case.case_id,
        benchmark_policy=benchmark_policy,
        selected_dir=str(selected_dir),
        planning_selected_dir=str(planning_selected_dir),
        planning_analysis_type=planning_analysis_type,
        planning_chosen_method=planning_chosen_method,
        planning_preferred_tools=planning_preferred_tools,
        route_matches_expected=route_matches_expected,
        run_status=str(payload.get("status", "") or ""),
        run_returncode=int(completed.returncode),
        run_elapsed_seconds=elapsed_seconds,
        run_dir=str(run_dir) if str(run_dir) else "",
        primary_artifact_present=_has_all_primary_artifacts(artifact_paths),
        primary_artifact_paths=artifact_paths,
        de_recall_at150=de_recall,
        missing_tools_detected=missing_tools_detected,
        failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
        failure_suggested_fix=str(failure_diagnosis.get("suggested_fix", "") or ""),
        observed_support_tier=_support_tier(
            route_matches_expected=route_matches_expected,
            run_status=str(payload.get("status", "") or ""),
            primary_artifact_present=_has_all_primary_artifacts(artifact_paths),
            de_recall_at150=de_recall,
            missing_tools_detected=missing_tools_detected,
            failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
            error_text=error_text,
        ),
        error=error_text,
        stdout_tail=_tail_text(completed.stdout),
        stderr_tail=_tail_text(completed.stderr),
    )
    _write_case_result(case_root, result)
    return result


def _load_case_result(case_root: Path) -> MetabolomicsCaseResult | None:
    """Load a previously written case result when present."""

    payload = _read_json(case_root / "case_result.json")
    if not payload:
        return None
    try:
        return MetabolomicsCaseResult(**payload)
    except TypeError:
        return None


def _reconstruct_case_result_from_artifacts(
    *,
    case: MetabolomicsBenchmarkCase,
    case_root: Path,
    benchmark_policy: str,
) -> MetabolomicsCaseResult | None:
    """Rebuild a missing case result from persisted run artifacts."""

    selected_dir = case_root / "selected"
    planning_selected_dir = case_root / "planning_selected"
    payload = _read_json(selected_dir / "result.json")
    run_dir = _resolve_run_dir(selected_dir=selected_dir, payload=payload)
    if not payload and run_dir is None:
        return None
    state_payload = _read_json(run_dir / "state.json") if run_dir is not None else {}
    exit_payload = _read_json(run_dir / "exit.json") if run_dir is not None else {}
    failure_diagnosis = payload.get("failure_diagnosis", {}) if isinstance(payload.get("failure_diagnosis", {}), dict) else {}
    if not failure_diagnosis and exit_payload.get("error"):
        failure_diagnosis = {"root_cause": str(exit_payload.get("error", "") or "")}
    missing_tools_detected = _extract_missing_tools(payload=payload, state_payload=state_payload)
    planning_analysis_type = _reconstruct_analysis_type(payload=payload, state_payload=state_payload, run_dir=run_dir)
    planning_chosen_method, planning_preferred_tools = _reconstruct_planning_method(
        payload=payload,
        state_payload=state_payload,
        run_dir=run_dir,
    )
    artifact_paths = _find_primary_artifacts(selected_dir=selected_dir, run_dir=run_dir)
    de_recall = _compute_de_recall(artifact_paths, truth_path=Path(case.truth_path))
    error_text = _reconstruct_error_text(payload=payload, exit_payload=exit_payload, run_dir=run_dir)
    run_status = _reconstruct_run_status(payload=payload, state_payload=state_payload, exit_payload=exit_payload)
    route_matches_expected = _route_matches_expected(case, planning_chosen_method, planning_preferred_tools)
    return MetabolomicsCaseResult(
        case_id=case.case_id,
        benchmark_policy=benchmark_policy,
        selected_dir=str(selected_dir),
        planning_selected_dir=str(planning_selected_dir),
        planning_analysis_type=planning_analysis_type,
        planning_chosen_method=planning_chosen_method,
        planning_preferred_tools=planning_preferred_tools,
        route_matches_expected=route_matches_expected,
        run_status=run_status,
        run_returncode=_infer_run_returncode(
            payload=payload,
            state_payload=state_payload,
            exit_payload=exit_payload,
        ),
        run_elapsed_seconds=_infer_elapsed_seconds(state_payload),
        run_dir=str(run_dir) if run_dir is not None else "",
        primary_artifact_present=_has_all_primary_artifacts(artifact_paths),
        primary_artifact_paths=artifact_paths,
        de_recall_at150=de_recall,
        missing_tools_detected=missing_tools_detected,
        failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
        failure_suggested_fix=str(failure_diagnosis.get("suggested_fix", "") or ""),
        observed_support_tier=_support_tier(
            route_matches_expected=route_matches_expected,
            run_status=run_status,
            primary_artifact_present=_has_all_primary_artifacts(artifact_paths),
            de_recall_at150=de_recall,
            missing_tools_detected=missing_tools_detected,
            failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
            error_text=error_text,
        ),
        error=error_text,
        stdout_tail=_tail_file(run_dir / "stdout.log") if run_dir is not None else "",
        stderr_tail=_tail_file(run_dir / "stderr.log") if run_dir is not None else "",
    )


def _plan_analysis_spec(
    *,
    case: MetabolomicsBenchmarkCase,
    planning_selected_dir: Path,
    benchmark_policy: str,
    model_name: str | None,
    host: str | None,
    llm_backend: str | None,
    harness_factory: Callable[[HarnessConfig], Any],
) -> dict[str, Any]:
    cfg = HarnessConfig(
        prompt=Path(case.prompt_path).read_text(encoding="utf-8").strip(),
        selected_dir=planning_selected_dir,
        data_root=Path(case.data_root).expanduser().resolve(),
        workspace_root=WORKSPACE_ROOT,
        max_repairs=0,
        heartbeat_seconds=8,
        stall_timeout_seconds=120,
        live_process_grace_seconds=20,
        model_name=model_name,
        host=host,
        auto_install_missing_tools=False,
        allow_replan=False,
        allow_canonicalize=True,
        benchmark_policy=benchmark_policy,
        plan_path=None,
        result_json=planning_selected_dir / "planning_result.json",
        quiet=True,
        print_plan=False,
        llm_backend=llm_backend,
        path_graph_db=planning_selected_dir / "knowledge" / "path_graph.sqlite",
        path_graph_user_key="default",
        path_graph_scope="global",
        path_graph_persist_preference_updates=False,
        auto_setup_isolated_tools=False,
    )
    harness = harness_factory(cfg)
    harness._init_run()
    harness._prepare_analysis_spec(contract={})
    spec = harness.run.get("analysis_spec", {}) if isinstance(harness.run.get("analysis_spec", {}), dict) else {}
    return dict(spec)


def _route_matches_expected(
    case: MetabolomicsBenchmarkCase,
    chosen_method: str,
    preferred_tools: list[str],
) -> bool:
    observed = {tool.strip() for tool in preferred_tools if tool.strip()}
    observed.update(token.strip() for token in chosen_method.replace("+", ",").split(",") if token.strip())
    return set(case.expected_tools).issubset(observed)


def _invoke_case(
    *,
    command: list[str],
    cwd: Path,
    timeout_seconds: float,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[subprocess.CompletedProcess[str], str]:
    try:
        completed = runner(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        return completed, ""
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(command, returncode=-1, stdout=exc.stdout or "", stderr=exc.stderr or ""), (
            f"timeout_after_{int(timeout_seconds)}s"
        )
    except Exception as exc:  # pragma: no cover - defensive live benchmark capture
        return subprocess.CompletedProcess(command, returncode=-1, stdout="", stderr=""), str(exc)


def _find_primary_artifacts(*, selected_dir: Path, run_dir: Path | None) -> list[str]:
    roots = [selected_dir]
    if run_dir is not None:
        roots.append(run_dir)
    seen: set[str] = set()
    matches: list[str] = []
    ignored_parts = {"planner", "knowledge", "report", "report_bundle", "selected_dir_report", "run_dir_report"}
    ignored_names = {
        "result.json",
        "summary.json",
        "summary.md",
        "state.json",
        "manifest.json",
        "assistance_manifest.json",
        "preflight_summary.json",
        "in_run_quality_summary.json",
        "completed_run_context.json",
    }
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name in ignored_names:
                continue
            if any(part in ignored_parts for part in path.parts):
                continue
            if path.name not in _PRIMARY_ARTIFACT_NAMES:
                continue
            rendered = str(path)
            if rendered in seen:
                continue
            seen.add(rendered)
            matches.append(rendered)
    return matches


def _has_all_primary_artifacts(artifact_paths: list[str]) -> bool:
    found_names = {Path(path).name for path in artifact_paths}
    return all(name in found_names for name in _PRIMARY_ARTIFACT_NAMES)


def _compute_de_recall(
    artifact_paths: list[str],
    *,
    truth_path: Path,
) -> float | None:
    result_path = next(
        (Path(path) for path in artifact_paths if Path(path).name == "metabolomics_differential_abundance.csv"),
        None,
    )
    if result_path is None or not result_path.exists() or not truth_path.exists():
        return None
    try:
        truth_payload = json.loads(truth_path.read_text(encoding="utf-8"))
        truth_features = {
            str(item).strip()
            for item in (truth_payload.get("de_features", []) if isinstance(truth_payload, dict) else [])
            if str(item).strip()
        }
        if not truth_features:
            return None
        table = pd.read_csv(result_path)
        if "feature_id" not in table.columns:
            return None
        top = {
            str(item).strip()
            for item in table.head(_DE_RECALL_TOP_K)["feature_id"].tolist()
            if str(item).strip()
        }
        return round(len(top.intersection(truth_features)) / float(len(truth_features)), 6)
    except Exception:
        return None


def _is_bad_input_failure(*, error_text: str, failure_root_cause: str) -> bool:
    rendered = " ".join(
        token
        for token in (
            str(error_text or "").lower(),
            str(failure_root_cause or "").lower(),
        )
        if token
    )
    return any(
        token in rendered
        for token in (
            "non-numeric values",
            "could not read tabular input",
            "input table is empty",
            "metadata is empty",
            "must contain exactly two groups",
            "missing feature-table samples",
            "at least two samples per group",
            "bad input",
            "format_input_error",
        )
    )


def _support_tier(
    *,
    route_matches_expected: bool,
    run_status: str,
    primary_artifact_present: bool,
    de_recall_at150: float | None,
    missing_tools_detected: list[str],
    failure_root_cause: str,
    error_text: str,
) -> str:
    if route_matches_expected and run_status == "completed" and primary_artifact_present:
        if de_recall_at150 is not None and de_recall_at150 >= _DE_RECALL_THRESHOLD:
            return "executed_with_primary_artifact"
        return "completed_with_degraded_quality"
    if route_matches_expected and run_status == "completed":
        return "completed_without_primary_artifact"
    if route_matches_expected and _is_bad_input_failure(error_text=error_text, failure_root_cause=failure_root_cause):
        return "blocked_bad_input"
    if route_matches_expected and missing_tools_detected:
        return "routed_but_missing_tools"
    if route_matches_expected:
        return "routed_but_failed"
    return "misrouted_or_unrouted"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _tail_text(text: str, limit: int = 4000) -> str:
    rendered = str(text or "")
    return rendered[-limit:]


def _tail_file(path: Path, limit: int = 4000) -> str:
    """Return the tail of a file when present."""

    try:
        rendered = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return rendered[-limit:]


def _write_case_result(case_root: Path, result: MetabolomicsCaseResult) -> None:
    (case_root / "case_result.json").write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")


def _resolve_run_dir(*, selected_dir: Path, payload: dict[str, Any]) -> Path | None:
    """Resolve the persisted run directory for a benchmark case."""

    raw_run_dir = str(payload.get("run_dir", "") or "").strip()
    if raw_run_dir:
        return Path(raw_run_dir).expanduser().resolve(strict=False)
    runs_root = WORKSPACE_ROOT / "runs"
    if not runs_root.exists():
        return None
    target_selected_dir = str(selected_dir.resolve(strict=False))
    candidates: list[Path] = []
    for run_dir in sorted(runs_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not run_dir.is_dir():
            continue
        manifest_payload = _read_json(run_dir / "manifest.json")
        if str(manifest_payload.get("selected_dir", "") or "") == target_selected_dir:
            candidates.append(run_dir)
            continue
        completed_payload = _read_json(run_dir / "completed_run_context.json")
        if str(completed_payload.get("selected_dir", "") or "") == target_selected_dir:
            candidates.append(run_dir)
    return candidates[0] if candidates else None


def _extract_missing_tools(*, payload: dict[str, Any], state_payload: dict[str, Any]) -> list[str]:
    """Extract missing tool names from result or state payloads."""

    candidates = payload.get("missing_tools_detected", [])
    if not candidates:
        candidates = state_payload.get("missing_tools_detected", [])
    return [str(item).strip() for item in (candidates or []) if str(item).strip()]


def _reconstruct_analysis_type(*, payload: dict[str, Any], state_payload: dict[str, Any], run_dir: Path) -> str:
    """Reconstruct the benchmark analysis type from persisted artifacts."""

    for value in (
        payload.get("analysis_type", ""),
        state_payload.get("analysis_type", ""),
    ):
        rendered = str(value or "").strip()
        if rendered:
            return rendered
    for planner_event in sorted((run_dir / "planner").glob("*structured_success.json")):
        event_payload = _read_json(planner_event)
        trace_context = event_payload.get("trace_context", {}) if isinstance(event_payload.get("trace_context", {}), dict) else {}
        rendered = str(trace_context.get("analysis_type", "") or "").strip()
        if rendered:
            return rendered
    return ""


def _reconstruct_planning_method(
    *,
    payload: dict[str, Any],
    state_payload: dict[str, Any],
    run_dir: Path,
) -> tuple[str, list[str]]:
    """Reconstruct the chosen method and preferred tools from persisted artifacts."""

    chosen_method = str(payload.get("chosen_method", "") or state_payload.get("chosen_method", "") or "").strip()
    preferred_tools = [
        str(item).strip()
        for item in (payload.get("preferred_tools", []) or state_payload.get("preferred_tools", []) or [])
        if str(item).strip()
    ]
    if chosen_method or preferred_tools:
        return chosen_method, preferred_tools
    for planner_event in sorted((run_dir / "planner").glob("*structured_success.json")):
        event_payload = _read_json(planner_event)
        payload_block = event_payload.get("payload", {}) if isinstance(event_payload.get("payload", {}), dict) else {}
        stage = str(payload_block.get("stage", "") or "").strip()
        if stage != "step_expansion":
            continue
        raw_content_file = Path(str(event_payload.get("raw_content_file", "") or ""))
        raw_payload = _read_json(raw_content_file)
        tool_name = str(raw_payload.get("tool_name", "") or "").strip()
        if tool_name:
            return tool_name, [tool_name]
        raw_excerpt = str(event_payload.get("raw_excerpt", "") or "").strip()
        try:
            parsed_excerpt = json.loads(raw_excerpt)
        except Exception:
            continue
        tool_name = str(parsed_excerpt.get("tool_name", "") or "").strip()
        if tool_name:
            return tool_name, [tool_name]
    return "", []


def _reconstruct_error_text(*, payload: dict[str, Any], exit_payload: dict[str, Any], run_dir: Path) -> str:
    """Reconstruct an error string from persisted result or run-dir artifacts."""

    error_text = str(payload.get("error", "") or "").strip()
    if error_text:
        return error_text
    exit_error = str(exit_payload.get("error", "") or "").strip()
    stderr_text = _tail_file(run_dir / "stderr.log").strip()
    if "__FORMAT_INPUT_ERROR__:" in stderr_text:
        return stderr_text.split("__FORMAT_INPUT_ERROR__:", 1)[1].strip()
    if stderr_text:
        return stderr_text
    return exit_error


def _reconstruct_run_status(
    *,
    payload: dict[str, Any],
    state_payload: dict[str, Any],
    exit_payload: dict[str, Any],
) -> str:
    """Reconstruct final run status from available persisted artifacts."""

    for candidate in (
        payload.get("status", ""),
        state_payload.get("status", ""),
        exit_payload.get("status", ""),
    ):
        rendered = str(candidate or "").strip()
        if rendered:
            return rendered
    return "failed"


def _infer_run_returncode(
    *,
    payload: dict[str, Any],
    state_payload: dict[str, Any],
    exit_payload: dict[str, Any],
) -> int:
    """Infer run return code from payload, state, or exit artifacts."""

    for candidate in (
        payload.get("returncode", None),
        state_payload.get("returncode", None),
        exit_payload.get("exit_code", None),
    ):
        if candidate is None or candidate == "":
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    exit_error = str(exit_payload.get("error", "") or "")
    match = re.search(r"exit code ([-]?\d+)", exit_error)
    if match:
        return int(match.group(1))
    return -1


def _infer_elapsed_seconds(state_payload: dict[str, Any]) -> float:
    """Infer elapsed runtime from persisted state timestamps when possible."""

    started_at = str(state_payload.get("started_at", "") or "").strip()
    finished_at = str(state_payload.get("finished_at", "") or "").strip()
    if not started_at or not finished_at:
        return 0.0
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return 0.0
    return round(max((finished - started).total_seconds(), 0.0), 3)


def _summary(case_results: list[MetabolomicsCaseResult]) -> dict[str, Any]:
    support_tier_counts: dict[str, int] = {}
    for row in case_results:
        support_tier_counts[row.observed_support_tier] = support_tier_counts.get(row.observed_support_tier, 0) + 1
    passing_quality_count = sum(
        1 for row in case_results if row.observed_support_tier == "executed_with_primary_artifact"
    )
    return {
        "cases": [asdict(item) for item in case_results],
        "cases_total": len(case_results),
        "route_matches_expected_count": sum(1 for item in case_results if item.route_matches_expected),
        "completed_count": sum(1 for item in case_results if item.run_status == "completed"),
        "primary_artifact_count": sum(1 for item in case_results if item.primary_artifact_present),
        "passing_quality_count": passing_quality_count,
        "support_tier_counts": support_tier_counts,
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Metabolomics Benchmark Summary",
        "",
        f"- Cases total: `{summary.get('cases_total', 0)}`",
        f"- Route matches expected: `{summary.get('route_matches_expected_count', 0)}`",
        f"- Completed runs: `{summary.get('completed_count', 0)}`",
        f"- Primary artifacts present: `{summary.get('primary_artifact_count', 0)}`",
        f"- Passing quality threshold: `{summary.get('passing_quality_count', 0)}`",
        "",
        "## Support Tiers",
        "",
    ]
    for tier, count in sorted((summary.get("support_tier_counts", {}) or {}).items()):
        lines.append(f"- `{tier}`: `{count}`")
    lines.extend(["", "## Cases", ""])
    for item in summary.get("cases", []):
        if not isinstance(item, dict):
            continue
        lines.append(
            "- "
            f"`{item.get('case_id', '')}` "
            f"route=`{item.get('route_matches_expected', False)}` "
            f"status=`{item.get('run_status', '')}` "
            f"artifact=`{item.get('primary_artifact_present', False)}` "
            f"de_recall_at150=`{item.get('de_recall_at150', None)}` "
            f"tier=`{item.get('observed_support_tier', '')}`"
        )
    return "\n".join(lines)


__all__ = [
    "MetabolomicsBenchmarkCase",
    "MetabolomicsCaseResult",
    "default_metabolomics_benchmark_cases",
    "run_metabolomics_benchmark",
]
