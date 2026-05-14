"""Benchmark current spatial transcriptomics support across planning and execution.

This module benchmarks the processed-input spatial transcriptomics tranche
against the synthetic Visium-style corpus under
``workspace/benchmark_data/spatial``. It measures:

- planning route quality
- execution success
- canonical artifact production
- spatial domain quality against truth labels
- marker recovery against truth marker sets

The benchmark is intentionally scoped to processed AnnData inputs and does not
claim raw-image or raw-FASTQ spatial support.
"""

from __future__ import annotations

import csv
from datetime import datetime
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import anndata as ad
from sklearn.metrics import adjusted_rand_score

from bio_harness.harness.config import HarnessConfig, WORKSPACE_ROOT
from scripts.run_agent_e2e_harness import AgentE2EHarness

_PRIMARY_ARTIFACT_NAMES: tuple[str, ...] = (
    "spatial_domain_assignments.csv",
    "spatial_marker_genes.csv",
    "spatial_results.h5ad",
)
_DOMAIN_ARI_THRESHOLD = 0.75
_MARKER_RECALL_THRESHOLD = 0.40


@dataclass(frozen=True)
class SpatialBenchmarkCase:
    """One spatial transcriptomics benchmark case.

    Attributes:
        case_id: Stable case identifier.
        description: Human-readable description.
        prompt_path: Prompt file used for the case.
        data_root: Input data directory for the case.
        truth_path: Truth JSON for marker evaluation.
        input_h5ad_path: Processed spatial AnnData input.
        expected_tools: Tools that indicate correct route selection.
    """

    case_id: str
    description: str
    prompt_path: str
    data_root: str
    truth_path: str
    input_h5ad_path: str
    expected_tools: tuple[str, ...]


@dataclass(frozen=True)
class SpatialCaseResult:
    """Observed outcome for one spatial benchmark case."""

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
    domain_ari: float | None
    marker_recall_at5: float | None
    missing_tools_detected: list[str]
    failure_root_cause: str
    failure_suggested_fix: str
    observed_support_tier: str
    error: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""


def default_spatial_benchmark_cases(
    *,
    dataset_root: Path | None = None,
) -> tuple[SpatialBenchmarkCase, ...]:
    """Return the default processed-input spatial benchmark cases."""

    root = dataset_root or (WORKSPACE_ROOT / "benchmark_data" / "spatial")
    ordered_ids = (
        "clean_visium",
        "noisy_prompt",
        "coordinate_ambiguity",
        "mild_fragmentation",
        "nested_output",
        "malformed_coords",
    )
    return tuple(_case(root, case_id) for case_id in ordered_ids)


def run_spatial_benchmark(
    *,
    output_root: Path,
    project_root: Path,
    cases: tuple[SpatialBenchmarkCase, ...] | None = None,
    benchmark_policy: str = "scientific_harness",
    model_name: str | None = None,
    host: str | None = None,
    llm_backend: str | None = None,
    quiet: bool = True,
    command_timeout_seconds: float = 1200.0,
    harness_factory: Callable[[HarnessConfig], Any] = AgentE2EHarness,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Run the processed-input spatial transcriptomics benchmark."""

    selected_cases = cases or default_spatial_benchmark_cases()
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


def _case(dataset_root: Path, case_id: str) -> SpatialBenchmarkCase:
    case_root = dataset_root / case_id
    return SpatialBenchmarkCase(
        case_id=case_id,
        description=f"Spatial benchmark case `{case_id}`.",
        prompt_path=str(case_root / "prompt.txt"),
        data_root=str(case_root / "data"),
        truth_path=str(case_root / "data" / "truth.json"),
        input_h5ad_path=str(case_root / "data" / "visium_data.h5ad"),
        expected_tools=("spatial_transcriptomics_workflow",),
    )


def _run_case(
    case: SpatialBenchmarkCase,
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
) -> SpatialCaseResult:
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
    except Exception as exc:  # pragma: no cover - defensive capture for live benchmark
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
        result = SpatialCaseResult(
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
            domain_ari=None,
            marker_recall_at5=None,
            missing_tools_detected=[],
            failure_root_cause="",
            failure_suggested_fix="",
            observed_support_tier=_support_tier(
                route_matches_expected=route_matches_expected,
                run_status="failed",
                primary_artifact_present=False,
                domain_ari=None,
                marker_recall_at5=None,
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
        result = SpatialCaseResult(
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
            domain_ari=None,
            marker_recall_at5=None,
            missing_tools_detected=[],
            failure_root_cause="",
            failure_suggested_fix="",
            observed_support_tier=_support_tier(
                route_matches_expected=route_matches_expected,
                run_status="failed",
                primary_artifact_present=False,
                domain_ari=None,
                marker_recall_at5=None,
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
    domain_ari = _compute_domain_ari(artifact_paths, input_h5ad_path=Path(case.input_h5ad_path))
    marker_recall = _compute_marker_recall(
        artifact_paths,
        input_h5ad_path=Path(case.input_h5ad_path),
        truth_path=Path(case.truth_path),
    )
    error_text = planning_error or str(payload.get("error", "") or "")
    result = SpatialCaseResult(
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
        domain_ari=domain_ari,
        marker_recall_at5=marker_recall,
        missing_tools_detected=missing_tools_detected,
        failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
        failure_suggested_fix=str(failure_diagnosis.get("suggested_fix", "") or ""),
        observed_support_tier=_support_tier(
            route_matches_expected=route_matches_expected,
            run_status=str(payload.get("status", "") or ""),
            primary_artifact_present=_has_all_primary_artifacts(artifact_paths),
            domain_ari=domain_ari,
            marker_recall_at5=marker_recall,
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


def _load_case_result(case_root: Path) -> SpatialCaseResult | None:
    """Load a previously written case result when present."""

    payload = _read_json(case_root / "case_result.json")
    if not payload:
        return None
    try:
        return SpatialCaseResult(**payload)
    except TypeError:
        return None


def _reconstruct_case_result_from_artifacts(
    *,
    case: SpatialBenchmarkCase,
    case_root: Path,
    benchmark_policy: str,
) -> SpatialCaseResult | None:
    """Rebuild a missing case result from persisted run artifacts.

    This allows interrupted benchmark runs to resume safely without rerunning
    completed model calls.
    """

    selected_dir = case_root / "selected"
    planning_selected_dir = case_root / "planning_selected"
    payload = _read_json(selected_dir / "result.json")
    if not payload:
        return None
    run_dir = Path(str(payload.get("run_dir", "") or "")).expanduser().resolve(strict=False)
    state_payload = _read_json(run_dir / "state.json") if str(run_dir) else {}
    failure_diagnosis = payload.get("failure_diagnosis", {}) if isinstance(payload.get("failure_diagnosis", {}), dict) else {}
    missing_tools_detected = [
        str(item).strip()
        for item in (payload.get("missing_tools_detected", []) or [])
        if str(item).strip()
    ]
    planning_analysis_type = _reconstruct_analysis_type(payload=payload, state_payload=state_payload)
    planning_chosen_method, planning_preferred_tools = _reconstruct_planning_method(
        payload=payload,
        state_payload=state_payload,
    )
    artifact_paths = _find_primary_artifacts(selected_dir=selected_dir, run_dir=run_dir)
    domain_ari = _compute_domain_ari(artifact_paths, input_h5ad_path=Path(case.input_h5ad_path))
    marker_recall = _compute_marker_recall(
        artifact_paths,
        input_h5ad_path=Path(case.input_h5ad_path),
        truth_path=Path(case.truth_path),
    )
    error_text = str(payload.get("error", "") or "")
    run_status = str(payload.get("status", "") or "")
    route_matches_expected = _route_matches_expected(case, planning_chosen_method, planning_preferred_tools)
    run_returncode = _infer_run_returncode(
        payload=payload,
        state_payload=state_payload,
        run_dir=run_dir,
    )
    elapsed_seconds = _infer_elapsed_seconds(state_payload)
    return SpatialCaseResult(
        case_id=case.case_id,
        benchmark_policy=benchmark_policy,
        selected_dir=str(selected_dir),
        planning_selected_dir=str(planning_selected_dir),
        planning_analysis_type=planning_analysis_type,
        planning_chosen_method=planning_chosen_method,
        planning_preferred_tools=planning_preferred_tools,
        route_matches_expected=route_matches_expected,
        run_status=run_status,
        run_returncode=run_returncode,
        run_elapsed_seconds=elapsed_seconds,
        run_dir=str(run_dir) if str(run_dir) else "",
        primary_artifact_present=_has_all_primary_artifacts(artifact_paths),
        primary_artifact_paths=artifact_paths,
        domain_ari=domain_ari,
        marker_recall_at5=marker_recall,
        missing_tools_detected=missing_tools_detected,
        failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
        failure_suggested_fix=str(failure_diagnosis.get("suggested_fix", "") or ""),
        observed_support_tier=_support_tier(
            route_matches_expected=route_matches_expected,
            run_status=run_status,
            primary_artifact_present=_has_all_primary_artifacts(artifact_paths),
            domain_ari=domain_ari,
            marker_recall_at5=marker_recall,
            missing_tools_detected=missing_tools_detected,
            failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
            error_text=error_text,
        ),
        error=error_text,
        stdout_tail=_tail_file(run_dir / "stdout.log"),
        stderr_tail=_tail_file(run_dir / "stderr.log"),
    )


def _plan_analysis_spec(
    *,
    case: SpatialBenchmarkCase,
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
    case: SpatialBenchmarkCase,
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


def _find_primary_artifacts(*, selected_dir: Path, run_dir: Path) -> list[str]:
    roots = [selected_dir]
    if str(run_dir):
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


def _compute_domain_ari(artifact_paths: list[str], *, input_h5ad_path: Path) -> float | None:
    assignments_path = next((Path(path) for path in artifact_paths if Path(path).name == "spatial_domain_assignments.csv"), None)
    if assignments_path is None or not assignments_path.exists() or not input_h5ad_path.exists():
        return None
    try:
        truth = ad.read_h5ad(str(input_h5ad_path))
        truth_labels = {
            str(spot_id): str(label)
            for spot_id, label in zip(truth.obs_names, truth.obs["domain_truth"], strict=True)
        }
        predicted_spots: list[str] = []
        predicted_labels: list[str] = []
        with assignments_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                spot_id = str(row.get("spot_id", "") or "").strip()
                domain = str(row.get("domain", "") or "").strip()
                if not spot_id or not domain or spot_id not in truth_labels:
                    continue
                predicted_spots.append(spot_id)
                predicted_labels.append(domain)
        if not predicted_spots:
            return None
        truth_ordered = [truth_labels[spot_id] for spot_id in predicted_spots]
        return round(float(adjusted_rand_score(truth_ordered, predicted_labels)), 6)
    except Exception:
        return None


def _compute_marker_recall(
    artifact_paths: list[str],
    *,
    input_h5ad_path: Path,
    truth_path: Path,
) -> float | None:
    assignments_path = next((Path(path) for path in artifact_paths if Path(path).name == "spatial_domain_assignments.csv"), None)
    markers_path = next((Path(path) for path in artifact_paths if Path(path).name == "spatial_marker_genes.csv"), None)
    if assignments_path is None or markers_path is None or not truth_path.exists() or not input_h5ad_path.exists():
        return None
    try:
        truth_payload = json.loads(truth_path.read_text(encoding="utf-8"))
        truth_markers_raw = truth_payload.get("markers", {}) if isinstance(truth_payload, dict) else {}
        truth_markers = {
            str(domain): {str(gene).strip() for gene in genes if str(gene).strip()}
            for domain, genes in truth_markers_raw.items()
            if isinstance(genes, list)
        }
        truth = ad.read_h5ad(str(input_h5ad_path))
        truth_labels = {
            str(spot_id): str(label)
            for spot_id, label in zip(truth.obs_names, truth.obs["domain_truth"], strict=True)
        }
        predicted_to_truth = _majority_domain_mapping(assignments_path, truth_labels)
        if not predicted_to_truth:
            return None
        markers_by_predicted: dict[str, list[str]] = {}
        with markers_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                domain = str(row.get("domain", "") or "").strip()
                gene = str(row.get("gene", "") or "").strip()
                if not domain or not gene:
                    continue
                markers_by_predicted.setdefault(domain, []).append(gene)
        recalls: list[float] = []
        for predicted_domain, truth_domain in predicted_to_truth.items():
            predicted_markers = markers_by_predicted.get(predicted_domain, [])[:5]
            truth_marker_set = truth_markers.get(truth_domain, set())
            if not predicted_markers or not truth_marker_set:
                continue
            overlap = sum(1 for gene in predicted_markers if gene in truth_marker_set)
            recalls.append(overlap / float(len(truth_marker_set)))
        if not recalls:
            return None
        return round(sum(recalls) / len(recalls), 6)
    except Exception:
        return None


def _majority_domain_mapping(assignments_path: Path, truth_labels: dict[str, str]) -> dict[str, str]:
    predicted_votes: dict[str, dict[str, int]] = {}
    with assignments_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            spot_id = str(row.get("spot_id", "") or "").strip()
            predicted = str(row.get("domain", "") or "").strip()
            truth_label = truth_labels.get(spot_id, "")
            if not predicted or not truth_label:
                continue
            predicted_votes.setdefault(predicted, {})
            predicted_votes[predicted][truth_label] = predicted_votes[predicted].get(truth_label, 0) + 1
    mapping: dict[str, str] = {}
    for predicted, votes in predicted_votes.items():
        if not votes:
            continue
        mapping[predicted] = sorted(votes.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return mapping


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
            "spatial coordinates contain missing or non-finite values",
            "missing `obsm['spatial']` coordinates",
            "coordinates must be a two-column matrix",
            "malformed",
            "non-finite values",
            "missing coordinate",
            "bad input",
        )
    )


def _support_tier(
    *,
    route_matches_expected: bool,
    run_status: str,
    primary_artifact_present: bool,
    domain_ari: float | None,
    marker_recall_at5: float | None,
    missing_tools_detected: list[str],
    failure_root_cause: str,
    error_text: str,
) -> str:
    if route_matches_expected and run_status == "completed" and primary_artifact_present:
        if (
            domain_ari is not None
            and domain_ari >= _DOMAIN_ARI_THRESHOLD
            and marker_recall_at5 is not None
            and marker_recall_at5 >= _MARKER_RECALL_THRESHOLD
        ):
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
    """Return the tail of a text file when it exists."""

    if not path.exists():
        return ""
    try:
        return _tail_text(path.read_text(encoding="utf-8"), limit=limit)
    except Exception:
        return ""


def _write_case_result(case_root: Path, result: SpatialCaseResult) -> None:
    (case_root / "case_result.json").write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")


def _reconstruct_analysis_type(*, payload: dict[str, Any], state_payload: dict[str, Any]) -> str:
    """Infer the planned analysis type from persisted state."""

    protocol_validation = state_payload.get("protocol_validation", {})
    if isinstance(protocol_validation, dict):
        task_name = str(protocol_validation.get("task_name", "") or "").strip()
        if task_name:
            return task_name
    plan_contract = state_payload.get("plan_contract", {})
    if isinstance(plan_contract, dict):
        capabilities = plan_contract.get("must_include_capabilities", []) or []
        for capability in capabilities:
            rendered = str(capability).strip()
            if rendered:
                return rendered
    return str(payload.get("analysis_type", "") or "")


def _reconstruct_planning_method(
    *,
    payload: dict[str, Any],
    state_payload: dict[str, Any],
) -> tuple[str, list[str]]:
    """Infer the chosen wrapper and preferred tool list from persisted state."""

    contract_validation = state_payload.get("contract_validation", {})
    if isinstance(contract_validation, dict):
        direct_tools = [
            str(item).strip()
            for item in (contract_validation.get("direct_wrapper_compatible_tools", []) or [])
            if str(item).strip()
        ]
        if direct_tools:
            return direct_tools[0], direct_tools
    plan_contract = state_payload.get("plan_contract", {})
    if isinstance(plan_contract, dict):
        tool_hints = [
            str(item).strip()
            for item in (
                (plan_contract.get("required_tool_hints", []) or [])
                + (plan_contract.get("explicit_tool_hints", []) or [])
            )
            if str(item).strip()
        ]
        if tool_hints:
            unique_tools = list(dict.fromkeys(tool_hints))
            return unique_tools[0], unique_tools
    chosen_method = str(payload.get("chosen_method", "") or "").strip()
    preferred_tools = [
        str(item).strip()
        for item in (payload.get("preferred_tools", []) or [])
        if str(item).strip()
    ]
    if preferred_tools:
        return chosen_method or preferred_tools[0], preferred_tools
    return chosen_method, []


def _infer_run_returncode(
    *,
    payload: dict[str, Any],
    state_payload: dict[str, Any],
    run_dir: Path,
) -> int:
    """Infer a stable run return code from persisted benchmark artifacts."""

    failure_diagnosis = payload.get("failure_diagnosis", {})
    if isinstance(failure_diagnosis, dict):
        exit_code = failure_diagnosis.get("exit_code")
        if isinstance(exit_code, int):
            return exit_code
    exit_payload = _read_json(run_dir / "exit.json") if str(run_dir) else {}
    if str(exit_payload.get("status", "") or "") == "completed":
        return 0
    if str(payload.get("status", "") or "") == "completed":
        return 0
    return 1


def _infer_elapsed_seconds(state_payload: dict[str, Any]) -> float:
    """Infer elapsed runtime seconds from persisted state timestamps."""

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


def _summary(case_results: list[SpatialCaseResult]) -> dict[str, Any]:
    support_tier_counts: dict[str, int] = {}
    for row in case_results:
        support_tier_counts[row.observed_support_tier] = support_tier_counts.get(row.observed_support_tier, 0) + 1
    passing_quality_count = sum(
        1
        for row in case_results
        if row.observed_support_tier == "executed_with_primary_artifact"
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
        "# Spatial Benchmark Summary",
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
            f"ari=`{item.get('domain_ari', None)}` "
            f"marker_recall=`{item.get('marker_recall_at5', None)}` "
            f"tier=`{item.get('observed_support_tier', '')}`"
        )
    return "\n".join(lines)


__all__ = [
    "SpatialBenchmarkCase",
    "SpatialCaseResult",
    "default_spatial_benchmark_cases",
    "run_spatial_benchmark",
]
