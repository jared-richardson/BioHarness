"""Fast-model preflight helpers for the fast-signal ladder.

The fast-model preflight is an advisory gate for model-agnostic harness
changes. It can run the real harness against the tiny mini-benchmark suite,
validate contract-level outputs, and optionally record scorecard observations.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bio_harness.core.benchmark_policy import SCIENTIFIC_HARNESS_POLICY
from bio_harness.core.fast_signal_minibench import (
    DEFAULT_MINI_BENCHMARK_CONTRACTS,
    MINI_BENCHMARK_CASES,
    MiniBenchmarkContract,
    prepare_mini_benchmark_suite,
    validate_mini_benchmark_contract,
)
from bio_harness.core.fast_signal_scorecard import ScorecardRow, ScorecardStore

DEFAULT_FAST_MODEL = "qwen3-coder-next:latest"
DEFAULT_MINI_PREFLIGHT_BENCHMARK_POLICY = SCIENTIFIC_HARNESS_POLICY
DEFAULT_MINI_PREFLIGHT_PROTOCOL_GROUNDING_SCOPE = "local"
DEFAULT_PREFLIGHT_GATE = "fast_model_preflight"
DEFAULT_PREFLIGHT_MEASUREMENT_PURPOSE = "fast_model_preflight"
DEFAULT_PREFLIGHT_POLICY = (
    "Advisory for Qwen 3.6 readiness; primary only for model-agnostic changes "
    "such as binders, path resolution, contract adapters, deterministic wrapper "
    "logic, trace code, and scorecard code."
)
LEGACY_MINI_CASE_ALIASES: dict[str, str] = {
    "control_evolution": "control_evolution_mini",
    "evolution": "control_evolution_mini",
    "germline": "germline_vc_mini",
    "germline_vc": "germline_vc_mini",
    "germline-vc": "germline_vc_mini",
    "de": "de_mini",
    "deseq": "de_mini",
    "rna_seq": "de_mini",
}


@dataclass(frozen=True)
class PreflightCase:
    """One runnable preflight case.

    Attributes:
        case_id: Stable case identifier.
        analysis_family: Analysis-family tag used by relevance rules.
        command: Subprocess command used to run the case.
        selected_dir: Selected output directory, when applicable.
        data_root: Input data root, when applicable.
        prompt_file: Prompt file, when applicable.
        result_json: Harness result JSON path, when applicable.
        contract: Contract payload used for mini-benchmark validation.
    """

    case_id: str
    analysis_family: str
    command: tuple[str, ...]
    analysis_type: str = ""
    selected_dir: str = ""
    data_root: str = ""
    prompt_file: str = ""
    result_json: str = ""
    contract: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping."""
        return asdict(self)


@dataclass(frozen=True)
class PreflightPlan:
    """Runnable fast-model preflight plan.

    Attributes:
        suite: Preflight suite name, such as ``mini`` or ``domain``.
        model: Fast model used for the preflight.
        cases: Runnable case plans.
        env_overrides: Environment overrides needed for the harness run.
        policy: Human-readable policy statement for this advisory gate.
        metadata: Additional JSON-compatible diagnostics.
    """

    suite: str
    model: str
    cases: tuple[PreflightCase, ...]
    env_overrides: dict[str, str]
    policy: str = DEFAULT_PREFLIGHT_POLICY
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping."""
        payload = asdict(self)
        payload["cases"] = [case.to_mapping() for case in self.cases]
        return payload


@dataclass(frozen=True)
class PreflightCaseResult:
    """Result for one preflight case.

    Attributes:
        case_id: Stable case identifier.
        status: ``pass`` or ``fail``.
        returncode: Harness subprocess return code.
        elapsed_seconds: Runtime for this case.
        contract_validation: Contract-level validation payload.
    """

    case_id: str
    status: str
    returncode: int
    elapsed_seconds: float
    contract_validation: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping."""
        return asdict(self)


@dataclass(frozen=True)
class PreflightRunResult:
    """Aggregate result for a preflight run.

    Attributes:
        status: ``pass`` when every case passes, otherwise ``fail``.
        suite: Preflight suite name.
        model: Fast model used for the preflight.
        case_results: Per-case results.
        plan: JSON-compatible copy of the executed plan.
    """

    status: str
    suite: str
    model: str
    case_results: tuple[PreflightCaseResult, ...]
    plan: dict[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping."""
        payload = asdict(self)
        payload["case_results"] = [result.to_mapping() for result in self.case_results]
        return payload


def resolve_mini_case_ids(case_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Resolve user-facing mini case IDs and aliases.

    Args:
        case_ids: Requested case IDs. When empty, all mini cases are returned
            in the suite's canonical order.

    Returns:
        Resolved mini-benchmark case IDs.

    Raises:
        ValueError: If a case ID is unknown.
    """
    if not case_ids:
        return tuple(MINI_BENCHMARK_CASES)
    resolved: list[str] = []
    for raw_case_id in case_ids:
        case_id = str(raw_case_id or "").strip()
        if not case_id:
            continue
        case_id = LEGACY_MINI_CASE_ALIASES.get(case_id, case_id)
        if case_id not in MINI_BENCHMARK_CASES:
            known = sorted(set(MINI_BENCHMARK_CASES) | set(LEGACY_MINI_CASE_ALIASES))
            raise ValueError(f"Unknown mini preflight case_id={raw_case_id!r}; known={known}")
        if case_id not in resolved:
            resolved.append(case_id)
    return tuple(resolved)


def build_mini_preflight_plan(
    *,
    project_root: Path,
    mini_root: Path,
    case_ids: list[str] | tuple[str, ...],
    model: str = DEFAULT_FAST_MODEL,
    python_executable: str = sys.executable,
    prepare_suite: bool = True,
    overwrite_suite: bool = False,
    execution_mode: str = "stepwise",
    benchmark_policy: str = DEFAULT_MINI_PREFLIGHT_BENCHMARK_POLICY,
    heartbeat_seconds: int = 0,
    stall_timeout_seconds: int = 0,
    live_process_grace_seconds: int = 0,
    max_repairs: int | None = None,
    ollama_keep_alive: str = "",
    ollama_num_parallel: str = "",
) -> PreflightPlan:
    """Build a mini-suite fast-model preflight plan.

    Args:
        project_root: Repository root containing ``scripts/run_agent_e2e.py``.
        mini_root: Fast-signal mini-benchmark suite root.
        case_ids: Requested mini case IDs or aliases.
        model: Fast model tag to use for planning and execution.
        python_executable: Python executable used in subprocess commands.
        prepare_suite: Whether to prepare deterministic mini inputs first.
        overwrite_suite: Whether preparation may overwrite generated inputs.
        execution_mode: Harness execution mode.
        benchmark_policy: Harness benchmark policy.
        heartbeat_seconds: Optional heartbeat pass-through.
        stall_timeout_seconds: Optional stall timeout pass-through.
        live_process_grace_seconds: Optional live-process grace pass-through.
        max_repairs: Optional repair-count pass-through.
        ollama_keep_alive: Optional Ollama keep-alive environment value.
        ollama_num_parallel: Optional Ollama parallelism environment value.

    Returns:
        Runnable mini preflight plan.
    """
    root = project_root.expanduser().resolve(strict=False)
    suite_root = mini_root.expanduser().resolve(strict=False)
    if prepare_suite:
        prepare_mini_benchmark_suite(suite_root, overwrite=overwrite_suite)
    resolved_case_ids = resolve_mini_case_ids(tuple(case_ids))
    selected_root = _mini_preflight_selected_root(
        project_root=root,
        mini_root=suite_root,
    )
    env_overrides = _preflight_env_overrides(
        model=model,
        ollama_keep_alive=ollama_keep_alive,
        ollama_num_parallel=ollama_num_parallel,
    )
    env_overrides["BIO_HARNESS_PROTOCOL_GROUNDING_SCOPE"] = (
        DEFAULT_MINI_PREFLIGHT_PROTOCOL_GROUNDING_SCOPE
    )
    cases = tuple(
        _build_mini_case(
            project_root=root,
            mini_root=suite_root,
            selected_root=selected_root,
            case_id=case_id,
            model=model,
            python_executable=python_executable,
            execution_mode=execution_mode,
            benchmark_policy=benchmark_policy,
            heartbeat_seconds=heartbeat_seconds,
            stall_timeout_seconds=stall_timeout_seconds,
            live_process_grace_seconds=live_process_grace_seconds,
            max_repairs=max_repairs,
        )
        for case_id in resolved_case_ids
    )
    return PreflightPlan(
        suite="mini",
        model=model,
        cases=cases,
        env_overrides=env_overrides,
        metadata={
            "mini_root": str(suite_root),
            "selected_root": str(selected_root),
            "manifest_file": str(suite_root / "manifest.json"),
            "prepare_suite": prepare_suite,
            "overwrite_suite": overwrite_suite,
            "protocol_grounding_scope": DEFAULT_MINI_PREFLIGHT_PROTOCOL_GROUNDING_SCOPE,
        },
    )


def build_domain_preflight_plan(
    *,
    project_root: Path,
    manifest_file: Path,
    case_ids: list[str] | tuple[str, ...],
    model: str = DEFAULT_FAST_MODEL,
    attempt_label: str = "fast_signal_preflight",
    python_executable: str = sys.executable,
    ollama_keep_alive: str = "",
    ollama_num_parallel: str = "",
) -> PreflightPlan:
    """Build the legacy domain-manifest fast-model preflight plan.

    Args:
        project_root: Repository root containing the domain runner.
        manifest_file: Domain expansion manifest path.
        case_ids: Requested domain case IDs. Defaults to ``control_evolution``.
        model: Fast model tag to use.
        attempt_label: Attempt label passed to the domain runner.
        python_executable: Python executable used in subprocess commands.
        ollama_keep_alive: Optional Ollama keep-alive environment value.
        ollama_num_parallel: Optional Ollama parallelism environment value.

    Returns:
        Runnable domain preflight plan.
    """
    root = project_root.expanduser().resolve(strict=False)
    requested_case_ids = tuple(case_ids or ("control_evolution",))
    command = [
        python_executable,
        str(root / "scripts" / "run_domain_expansion_ablation.py"),
        "--variant",
        "qwen_true_no_templates",
        "--manifest-file",
        str(manifest_file.expanduser().resolve(strict=False)),
        "--planner-model-name",
        model,
        "--executor-model-name",
        model,
        "--execution-mode",
        "stepwise",
        "--attempt-label",
        attempt_label,
    ]
    for case_id in requested_case_ids:
        command.extend(["--case-id", case_id])
    env_overrides = _preflight_env_overrides(
        model=model,
        ollama_keep_alive=ollama_keep_alive,
        ollama_num_parallel=ollama_num_parallel,
    )
    return PreflightPlan(
        suite="domain",
        model=model,
        cases=(
            PreflightCase(
                case_id="domain_manifest",
                analysis_family="mixed",
                command=tuple(command),
            ),
        ),
        env_overrides=env_overrides,
        metadata={
            "manifest_file": str(manifest_file.expanduser().resolve(strict=False)),
            "case_ids": list(requested_case_ids),
            "attempt_label": attempt_label,
        },
    )


def run_preflight_plan(
    plan: PreflightPlan,
    *,
    project_root: Path,
    clean_selected_dirs: bool = True,
) -> PreflightRunResult:
    """Run a fast-model preflight plan.

    Args:
        plan: Runnable preflight plan.
        project_root: Repository root used as subprocess working directory.
        clean_selected_dirs: Whether mini selected directories should be
            cleaned before each case to prevent stale-artifact passes.

    Returns:
        Aggregate run result.

    Raises:
        ValueError: If selected-dir cleanup would operate outside the mini root.
    """
    root = project_root.expanduser().resolve(strict=False)
    case_results: list[PreflightCaseResult] = []
    for case in plan.cases:
        if plan.suite == "mini" and clean_selected_dirs and case.selected_dir:
            _clean_mini_selected_dir(
                selected_dir=Path(case.selected_dir),
                allowed_root=Path(
                    str(plan.metadata.get("selected_root") or plan.metadata.get("mini_root", ""))
                ),
            )
        started = time.monotonic()
        completed = subprocess.run(
            list(case.command),
            cwd=root,
            env=_subprocess_env(plan.env_overrides),
            check=False,
        )
        elapsed = time.monotonic() - started
        validation: dict[str, Any] = {}
        if plan.suite == "mini" and case.contract:
            validation = validate_mini_benchmark_contract(
                Path(case.selected_dir),
                _contract_for_case(case.case_id),
            )
        if plan.suite == "mini" and case.result_json:
            validation = _apply_preflight_policy_checks(
                validation=validation,
                result_payload=_load_result_payload(Path(case.result_json)),
            )
        status = _case_status(
            returncode=completed.returncode,
            contract_validation=validation,
        )
        case_results.append(
            PreflightCaseResult(
                case_id=case.case_id,
                status=status,
                returncode=completed.returncode,
                elapsed_seconds=elapsed,
                contract_validation=validation,
            )
        )
    aggregate_status = "pass" if all(result.status == "pass" for result in case_results) else "fail"
    return PreflightRunResult(
        status=aggregate_status,
        suite=plan.suite,
        model=plan.model,
        case_results=tuple(case_results),
        plan=plan.to_mapping(),
    )


def append_preflight_scorecard_rows(
    *,
    plan: PreflightPlan,
    run_result: PreflightRunResult,
    scorecard_path: Path,
    model_digest: str = "",
    backend_version: str = "",
    optimization_profile: str = "",
    measurement_purpose: str = DEFAULT_PREFLIGHT_MEASUREMENT_PURPOSE,
) -> None:
    """Append advisory preflight observations to the scorecard.

    Args:
        plan: Executed preflight plan.
        run_result: Aggregate result from ``run_preflight_plan``.
        scorecard_path: Scorecard JSONL path.
        model_digest: Backend-resolved model digest.
        backend_version: Backend version string.
        optimization_profile: Speed/measurement profile label.
        measurement_purpose: Measurement purpose recorded on each row.
    """
    store = ScorecardStore(scorecard_path)
    cases_by_id = {case.case_id: case for case in plan.cases}
    for result in run_result.case_results:
        case = cases_by_id.get(result.case_id)
        store.append(
            ScorecardRow(
                experiment_id=result.case_id,
                gate=DEFAULT_PREFLIGHT_GATE,
                status=result.status,
                elapsed_seconds=result.elapsed_seconds,
                metadata={
                    "suite": plan.suite,
                    "analysis_family": case.analysis_family if case else "",
                    "analysis_type": case.analysis_type if case else "",
                    "selected_dir": case.selected_dir if case else "",
                    "result_json": case.result_json if case else "",
                    "command": list(case.command) if case else [],
                    "contract_validation": result.contract_validation,
                    "policy": plan.policy,
                    "preflight_status": run_result.status,
                },
                model=plan.model,
                model_digest=model_digest,
                backend_version=backend_version,
                optimization_profile=optimization_profile,
                measurement_purpose=measurement_purpose,
            )
        )


def _build_mini_case(
    *,
    project_root: Path,
    mini_root: Path,
    selected_root: Path,
    case_id: str,
    model: str,
    python_executable: str,
    execution_mode: str,
    benchmark_policy: str,
    heartbeat_seconds: int,
    stall_timeout_seconds: int,
    live_process_grace_seconds: int,
    max_repairs: int | None,
) -> PreflightCase:
    case = MINI_BENCHMARK_CASES[case_id]
    task_root = mini_root / "tasks" / case.task_name
    selected_dir = selected_root / case.task_name / "attempt1"
    result_json = selected_dir / "fast_model_preflight_result.json"
    command = [
        python_executable,
        str(project_root / "scripts" / "run_agent_e2e.py"),
        "--prompt-file",
        str(task_root / "prompt.txt"),
        "--data-root",
        str(task_root / "data"),
        "--selected-dir",
        str(selected_dir),
        "--analysis-type",
        case.analysis_type,
        "--model-name",
        model,
        "--execution-mode",
        execution_mode,
        "--benchmark-policy",
        benchmark_policy,
        "--result-json",
        str(result_json),
        "--quiet",
    ]
    _append_positive_int(command, "--heartbeat-seconds", heartbeat_seconds)
    _append_positive_int(command, "--stall-timeout-seconds", stall_timeout_seconds)
    _append_positive_int(
        command,
        "--live-process-grace-seconds",
        live_process_grace_seconds,
    )
    if max_repairs is not None:
        command.extend(["--max-repairs", str(max_repairs)])
    return PreflightCase(
        case_id=case_id,
        analysis_family=case.analysis_family,
        analysis_type=case.analysis_type,
        command=tuple(command),
        selected_dir=str(selected_dir),
        data_root=str(task_root / "data"),
        prompt_file=str(task_root / "prompt.txt"),
        result_json=str(result_json),
        contract=DEFAULT_MINI_BENCHMARK_CONTRACTS[case_id].to_mapping(),
    )


def _mini_preflight_selected_root(*, project_root: Path, mini_root: Path) -> Path:
    """Return the allowed output root for mini preflight selected dirs."""
    workspace_root = project_root / "workspace"
    suite_selected_root = mini_root / "official_runs"
    if _path_is_within(suite_selected_root, workspace_root):
        return suite_selected_root.resolve(strict=False)
    return (workspace_root / "fast_signal_preflight" / "mini_runs").resolve(strict=False)


def _path_is_within(path: Path, root: Path) -> bool:
    resolved_path = path.expanduser().resolve(strict=False)
    resolved_root = root.expanduser().resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return False
    return True


def _append_positive_int(command: list[str], flag: str, value: int) -> None:
    if value > 0:
        command.extend([flag, str(value)])


def _preflight_env_overrides(
    *,
    model: str,
    ollama_keep_alive: str,
    ollama_num_parallel: str,
) -> dict[str, str]:
    overrides = {
        "BIO_HARNESS_MODEL": model,
        "BIO_HARNESS_MODEL_HEAVY": model,
    }
    if ollama_keep_alive:
        overrides["OLLAMA_KEEP_ALIVE"] = ollama_keep_alive
    if ollama_num_parallel:
        overrides["OLLAMA_NUM_PARALLEL"] = ollama_num_parallel
    return overrides


def _subprocess_env(overrides: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(overrides)
    return env


def _clean_mini_selected_dir(*, selected_dir: Path, allowed_root: Path) -> None:
    if not str(allowed_root):
        raise ValueError("Refusing to clean selected_dir without an allowed root")
    root = allowed_root.expanduser().resolve(strict=False)
    selected = selected_dir.expanduser().resolve(strict=False)
    if selected != root and root not in selected.parents:
        raise ValueError(
            f"Refusing to clean selected_dir outside allowed root: {selected} not under {root}"
        )
    if selected.exists():
        shutil.rmtree(selected)
    selected.mkdir(parents=True, exist_ok=True)


def _case_status(*, returncode: int, contract_validation: dict[str, Any]) -> str:
    if returncode != 0:
        return "fail"
    if contract_validation and not contract_validation.get("passed", False):
        return "fail"
    return "pass"


def _load_result_payload(result_json: Path) -> dict[str, Any]:
    try:
        payload = json.loads(result_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _apply_preflight_policy_checks(
    *,
    validation: dict[str, Any],
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    checked = dict(validation or {})
    if not result_payload:
        return checked
    visible, sources = _forbidden_source_visibility(result_payload)
    if not visible:
        return checked
    issues = list(checked.get("issues") or [])
    preview = ", ".join(sources[:3]) if sources else "source list unavailable"
    issues.append(f"forbidden benchmark sources visible: {preview}")
    checked["passed"] = False
    checked["issues"] = issues
    checked["forbidden_benchmark_sources_visible"] = True
    checked["forbidden_benchmark_sources"] = sources
    return checked


def _forbidden_source_visibility(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    manifest = payload.get("assistance_manifest")
    if not isinstance(manifest, dict):
        manifest = {}
    visible = bool(
        payload.get("forbidden_benchmark_sources_visible")
        or manifest.get("forbidden_benchmark_sources_visible")
    )
    sources = _coerce_string_list(payload.get("forbidden_benchmark_sources"))
    if not sources:
        sources = _coerce_string_list(manifest.get("forbidden_benchmark_sources"))
    return visible, sources


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if str(item).strip()]


def _contract_for_case(case_id: str) -> MiniBenchmarkContract:
    return DEFAULT_MINI_BENCHMARK_CONTRACTS[case_id]
