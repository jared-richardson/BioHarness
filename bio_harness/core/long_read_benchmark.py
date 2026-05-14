"""Benchmark current long-read support across planning and execution.

This module benchmarks the current state of long-read support without
pretending the family is already first-class complete. It separates:

- planning route quality
- execution outcome
- primary artifact production
- missing-tool versus harness/implementation failure modes

The benchmark operates on the synthetic long-read corpus under
``workspace/benchmark_data/long_read`` and records an observed support tier for
each case.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from bio_harness.harness.config import HarnessConfig, WORKSPACE_ROOT
from scripts.run_agent_e2e_harness import AgentE2EHarness


@dataclass(frozen=True)
class LongReadBenchmarkCase:
    """One long-read benchmark case.

    Attributes:
        case_id: Stable case identifier.
        family: Long-read workflow family.
        description: Short human-readable description.
        prompt_path: Prompt file for the case.
        data_root: Input data directory for the case.
        expected_tools: Tools that indicate correct route selection.
        primary_artifact_suffixes: File suffixes used to detect a primary
            output artifact.
    """

    case_id: str
    family: str
    description: str
    prompt_path: str
    data_root: str
    expected_tools: tuple[str, ...]
    primary_artifact_suffixes: tuple[str, ...]


@dataclass(frozen=True)
class LongReadCaseResult:
    """Observed outcome for one long-read benchmark case.

    Attributes:
        case_id: Stable case identifier.
        family: Long-read workflow family.
        benchmark_policy: Active benchmark policy.
        selected_dir: Selected output directory for the run.
        planning_selected_dir: Working directory used for the planning-only
            analysis-spec pass.
        planning_analysis_type: Observed planning analysis type.
        planning_raw_chosen_method: Raw chosen-method string from the analysis
            spec.
        planning_chosen_method: Composite-aware chosen method string derived
            from the analysis spec and plan skeleton.
        planning_preferred_tools: Observed preferred tools from the analysis
            spec.
        planning_tool_sequence: Ordered tool sequence derived from the plan
            skeleton, when available.
        route_matches_expected: Whether the planning route contains the
            expected tools for the case family.
        run_status: Final status from ``result.json``.
        run_returncode: CLI return code from the execution run.
        run_elapsed_seconds: Wall-clock time for the execution run.
        run_dir: Persisted run directory path.
        primary_artifact_present: Whether a family-appropriate primary artifact
            was found under the selected directory or run directory.
        primary_artifact_paths: Matching artifact paths.
        missing_tools_detected: Missing tools reported by the harness.
        failure_root_cause: Stable failure-diagnosis root cause, when present.
        failure_suggested_fix: Stable failure-diagnosis suggested fix, when
            present.
        observed_support_tier: Summarized support tier for the case.
        error: Benchmark harness error text, if any.
        stdout_tail: Bounded subprocess stdout tail.
        stderr_tail: Bounded subprocess stderr tail.
    """

    case_id: str
    family: str
    benchmark_policy: str
    selected_dir: str
    planning_selected_dir: str
    planning_analysis_type: str
    planning_raw_chosen_method: str
    planning_chosen_method: str
    planning_preferred_tools: list[str]
    planning_tool_sequence: list[str]
    route_matches_expected: bool
    run_status: str
    run_returncode: int
    run_elapsed_seconds: float
    run_dir: str
    primary_artifact_present: bool
    primary_artifact_paths: list[str]
    missing_tools_detected: list[str]
    failure_root_cause: str
    failure_suggested_fix: str
    blocking_input_quality_detected: bool
    observed_support_tier: str
    error: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""


def default_long_read_benchmark_cases(
    *,
    dataset_root: Path | None = None,
) -> tuple[LongReadBenchmarkCase, ...]:
    """Return the default long-read benchmark case set.

    Args:
        dataset_root: Optional override for the benchmark dataset root.

    Returns:
        Ordered tuple of benchmark cases.
    """

    root = dataset_root or (WORKSPACE_ROOT / "benchmark_data" / "long_read")
    return (
        _case(root, "dna_sv", "structural_variant", "ONT long-read DNA SV calling benchmark."),
        _case(root, "assembly", "assembly", "ONT de novo assembly benchmark."),
        _case(root, "rna_isoform", "rna_isoform", "Long-read RNA isoform benchmark."),
        _case(root, "dna_sv_pacbio", "structural_variant", "PacBio phrasing stress case."),
        _case(root, "dna_sv_noisy_prompt", "structural_variant", "Vague structural-variant prompt stress case."),
        _case(root, "assembly_meta", "assembly", "Metagenome-phrased assembly stress case."),
        _case(root, "assembly_malformed", "assembly", "Malformed FASTQ assembly failure case."),
        _case(root, "rna_isoform_no_annot", "rna_isoform", "Long-read RNA without annotation stress case."),
        _case(root, "dna_sv_nested_output", "structural_variant", "Nested-output structural-variant case."),
    )


def run_long_read_benchmark(
    *,
    output_root: Path,
    project_root: Path,
    cases: tuple[LongReadBenchmarkCase, ...] | None = None,
    benchmark_policy: str = "scientific_harness",
    model_name: str | None = None,
    host: str | None = None,
    llm_backend: str | None = None,
    quiet: bool = True,
    command_timeout_seconds: float = 1800.0,
    harness_factory: Callable[[HarnessConfig], Any] = AgentE2EHarness,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Run the long-read support benchmark.

    Args:
        output_root: Directory where benchmark artifacts should be written.
        project_root: Repository root containing ``scripts/run_agent_e2e.py``.
        cases: Optional explicit case set.
        benchmark_policy: Benchmark policy passed to the harness.
        model_name: Optional model override.
        host: Optional backend host override.
        llm_backend: Optional backend provider override.
        quiet: Whether to pass ``--quiet`` to the CLI harness.
        command_timeout_seconds: Per-case execution timeout.
        harness_factory: Factory used for the planning-only harness path.
        runner: Subprocess runner used for the execution path.

    Returns:
        Summary payload for the benchmark run.
    """

    selected_cases = cases or default_long_read_benchmark_cases()
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


def _case(dataset_root: Path, case_id: str, family: str, description: str) -> LongReadBenchmarkCase:
    case_root = dataset_root / case_id
    if family == "structural_variant":
        expected_tools = ("sniffles_sv_call", "minimap2_align")
        suffixes = (".vcf", ".vcf.gz")
    elif family == "assembly":
        expected_tools = ("flye_assemble",)
        suffixes = (".fa", ".fasta", ".fna")
    else:
        expected_tools = ("minimap2_align",)
        suffixes = (".bam", ".bai") if case_id == "rna_isoform_no_annot" else (".gtf", ".tsv")
    return LongReadBenchmarkCase(
        case_id=case_id,
        family=family,
        description=description,
        prompt_path=str(case_root / "prompt.txt"),
        data_root=str(case_root / "data"),
        expected_tools=expected_tools,
        primary_artifact_suffixes=suffixes,
    )


def _run_case(
    case: LongReadBenchmarkCase,
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
) -> LongReadCaseResult:
    case_root = output_root / case.case_id
    case_root.mkdir(parents=True, exist_ok=True)
    planning_selected_dir = case_root / "planning_selected"
    planning_selected_dir.mkdir(parents=True, exist_ok=True)
    planning_analysis_type = ""
    planning_raw_chosen_method = ""
    planning_chosen_method = ""
    planning_preferred_tools: list[str] = []
    planning_tool_sequence: list[str] = []
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
        (
            planning_raw_chosen_method,
            planning_chosen_method,
            planning_preferred_tools,
            planning_tool_sequence,
        ) = _planning_method_metadata(spec)
        route_matches_expected = _route_matches_expected(
            case,
            planning_chosen_method,
            planning_preferred_tools,
            planning_tool_sequence,
        )
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
        result = LongReadCaseResult(
            case_id=case.case_id,
            family=case.family,
            benchmark_policy=benchmark_policy,
            selected_dir=str(selected_dir),
            planning_selected_dir=str(planning_selected_dir),
            planning_analysis_type=planning_analysis_type,
            planning_raw_chosen_method=planning_raw_chosen_method,
            planning_chosen_method=planning_chosen_method,
            planning_preferred_tools=planning_preferred_tools,
            planning_tool_sequence=planning_tool_sequence,
            route_matches_expected=route_matches_expected,
            run_status="failed",
            run_returncode=-1,
            run_elapsed_seconds=elapsed_seconds,
            run_dir="",
            primary_artifact_present=False,
            primary_artifact_paths=[],
            missing_tools_detected=[],
            failure_root_cause="",
            failure_suggested_fix="",
            blocking_input_quality_detected=False,
            observed_support_tier=_support_tier(
                route_matches_expected=route_matches_expected,
                run_status="failed",
                primary_artifact_present=False,
                missing_tools_detected=[],
                blocking_input_quality_detected=False,
                planning_error=planning_error or invocation_error,
            ),
            error=planning_error or invocation_error,
        )
        _write_case_result(case_root, result)
        return result

    payload = _read_json(result_json)
    if not payload:
        result = LongReadCaseResult(
            case_id=case.case_id,
            family=case.family,
            benchmark_policy=benchmark_policy,
            selected_dir=str(selected_dir),
            planning_selected_dir=str(planning_selected_dir),
            planning_analysis_type=planning_analysis_type,
            planning_raw_chosen_method=planning_raw_chosen_method,
            planning_chosen_method=planning_chosen_method,
            planning_preferred_tools=planning_preferred_tools,
            planning_tool_sequence=planning_tool_sequence,
            route_matches_expected=route_matches_expected,
            run_status="failed",
            run_returncode=int(completed.returncode),
            run_elapsed_seconds=elapsed_seconds,
            run_dir="",
            primary_artifact_present=False,
            primary_artifact_paths=[],
            missing_tools_detected=[],
            failure_root_cause="",
            failure_suggested_fix="",
            blocking_input_quality_detected=False,
            observed_support_tier=_support_tier(
                route_matches_expected=route_matches_expected,
                run_status="failed",
                primary_artifact_present=False,
                missing_tools_detected=[],
                blocking_input_quality_detected=False,
                planning_error=planning_error or "missing_or_invalid_result_json",
            ),
            error=planning_error or "missing_or_invalid_result_json",
            stdout_tail=_tail_text(completed.stdout),
            stderr_tail=_tail_text(completed.stderr),
        )
        _write_case_result(case_root, result)
        return result

    run_dir = Path(str(payload.get("run_dir", "") or "")).expanduser().resolve(strict=False)
    artifact_paths = _find_primary_artifacts(
        selected_dir=selected_dir,
        run_dir=run_dir,
        family=case.family,
        suffixes=case.primary_artifact_suffixes,
    )
    missing_tools_detected = [
        str(item).strip()
        for item in (payload.get("missing_tools_detected", []) or [])
        if str(item).strip()
    ]
    input_quality = payload.get("input_quality", {}) if isinstance(payload.get("input_quality", {}), dict) else {}
    blocking_input_quality_detected = bool(input_quality.get("has_blocking", False))
    failure_diagnosis = payload.get("failure_diagnosis", {}) if isinstance(payload.get("failure_diagnosis", {}), dict) else {}
    error_text = planning_error or str(payload.get("error", "") or "")
    result = LongReadCaseResult(
        case_id=case.case_id,
        family=case.family,
        benchmark_policy=benchmark_policy,
        selected_dir=str(selected_dir),
        planning_selected_dir=str(planning_selected_dir),
        planning_analysis_type=planning_analysis_type,
        planning_raw_chosen_method=planning_raw_chosen_method,
        planning_chosen_method=planning_chosen_method,
        planning_preferred_tools=planning_preferred_tools,
        planning_tool_sequence=planning_tool_sequence,
        route_matches_expected=route_matches_expected,
        run_status=str(payload.get("status", "") or ""),
        run_returncode=int(completed.returncode),
        run_elapsed_seconds=elapsed_seconds,
        run_dir=str(run_dir) if str(run_dir) else "",
        primary_artifact_present=bool(artifact_paths),
        primary_artifact_paths=artifact_paths,
        missing_tools_detected=missing_tools_detected,
        failure_root_cause=str(failure_diagnosis.get("root_cause", "") or ""),
        failure_suggested_fix=str(failure_diagnosis.get("suggested_fix", "") or ""),
        blocking_input_quality_detected=blocking_input_quality_detected,
        observed_support_tier=_support_tier(
            route_matches_expected=route_matches_expected,
            run_status=str(payload.get("status", "") or ""),
            primary_artifact_present=bool(artifact_paths),
            missing_tools_detected=missing_tools_detected,
            blocking_input_quality_detected=blocking_input_quality_detected,
            planning_error=planning_error,
        ),
        error=error_text,
        stdout_tail=_tail_text(completed.stdout),
        stderr_tail=_tail_text(completed.stderr),
    )
    _write_case_result(case_root, result)
    return result


def _plan_analysis_spec(
    *,
    case: LongReadBenchmarkCase,
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
    case: LongReadBenchmarkCase,
    chosen_method: str,
    preferred_tools: list[str],
    tool_sequence: list[str],
) -> bool:
    observed = {tool.strip() for tool in preferred_tools if tool.strip()}
    observed.update(tool.strip() for tool in tool_sequence if tool.strip())
    observed.update(
        token.strip()
        for token in chosen_method.replace("+", ",").split(",")
        if token.strip()
    )
    return set(case.expected_tools).issubset(observed)


def _planning_method_metadata(spec: dict[str, Any]) -> tuple[str, str, list[str], list[str]]:
    """Return benchmark-facing planning method metadata.

    Args:
        spec: Planning analysis specification payload.

    Returns:
        Tuple of raw chosen method, composite-aware chosen method, preferred
        tools, and normalized tool sequence.
    """

    raw_chosen_method = str(spec.get("chosen_method", "") or "").strip()
    preferred_tools = [
        str(item).strip()
        for item in (spec.get("preferred_tools", []) or [])
        if str(item).strip()
    ]
    tool_sequence = _tool_sequence_from_plan_skeleton(spec.get("plan_skeleton", []))
    if not tool_sequence:
        tool_sequence = _split_method_tokens(raw_chosen_method)
    if not tool_sequence:
        tool_sequence = list(preferred_tools)
    planning_chosen_method = raw_chosen_method
    if len(tool_sequence) > 1:
        planning_chosen_method = " + ".join(tool_sequence)
    elif not planning_chosen_method and tool_sequence:
        planning_chosen_method = tool_sequence[0]
    return raw_chosen_method, planning_chosen_method, preferred_tools, tool_sequence


def _tool_sequence_from_plan_skeleton(plan_skeleton: Any) -> list[str]:
    """Extract an ordered unique tool sequence from a plan skeleton."""

    if not isinstance(plan_skeleton, list):
        return []
    tool_sequence: list[str] = []
    seen: set[str] = set()
    for entry in plan_skeleton:
        tool_name = ""
        if isinstance(entry, (list, tuple)) and entry:
            tool_name = str(entry[0]).strip()
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        tool_sequence.append(tool_name)
    return tool_sequence


def _split_method_tokens(chosen_method: str) -> list[str]:
    """Split a chosen-method string into normalized tool tokens."""

    tokens: list[str] = []
    seen: set[str] = set()
    for token in chosen_method.replace("+", ",").split(","):
        normalized = token.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


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


def _find_primary_artifacts(
    *,
    selected_dir: Path,
    run_dir: Path,
    family: str,
    suffixes: tuple[str, ...],
) -> list[str]:
    candidates: list[Path] = []
    candidates.extend(_artifact_candidates(selected_dir, suffixes=suffixes))
    if str(run_dir):
        candidates.extend(_artifact_candidates(run_dir, suffixes=suffixes))
    unique: list[str] = []
    seen: set[str] = set()
    for path in sorted(candidates):
        rendered = str(path)
        if rendered in seen:
            continue
        seen.add(rendered)
        unique.append(rendered)
    if family == "rna_isoform":
        abundance_like = [path for path in unique if path.endswith(".tsv")]
        gtf_like = [path for path in unique if path.endswith(".gtf")]
        bam_like = [path for path in unique if path.endswith(".bam") or path.endswith(".bai")]
        if any(suffix in suffixes for suffix in (".bam", ".bai")):
            return bam_like
        return abundance_like or gtf_like
    return unique


def _artifact_candidates(root: Path, *, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.exists():
        return []
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
    matches: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored_parts for part in path.parts):
            continue
        if path.name in ignored_names:
            continue
        if any(str(path).endswith(suffix) for suffix in suffixes):
            matches.append(path)
    return matches


def _support_tier(
    *,
    route_matches_expected: bool,
    run_status: str,
    primary_artifact_present: bool,
    missing_tools_detected: list[str],
    blocking_input_quality_detected: bool,
    planning_error: str,
) -> str:
    if planning_error and not route_matches_expected:
        return "planning_failed"
    if route_matches_expected and run_status == "completed" and primary_artifact_present:
        return "executed_with_primary_artifact"
    if route_matches_expected and run_status == "completed":
        return "completed_without_primary_artifact"
    if route_matches_expected and blocking_input_quality_detected:
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


def _write_case_result(case_root: Path, result: LongReadCaseResult) -> None:
    (case_root / "case_result.json").write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")


def _summary(case_results: list[LongReadCaseResult]) -> dict[str, Any]:
    support_tier_counts: dict[str, int] = {}
    family_counts: dict[str, dict[str, int]] = {}
    for row in case_results:
        support_tier_counts[row.observed_support_tier] = support_tier_counts.get(row.observed_support_tier, 0) + 1
        family = family_counts.setdefault(
            row.family,
            {
                "cases": 0,
                "route_matches_expected": 0,
                "completed": 0,
                "primary_artifacts": 0,
            },
        )
        family["cases"] += 1
        family["route_matches_expected"] += int(row.route_matches_expected)
        family["completed"] += int(row.run_status == "completed")
        family["primary_artifacts"] += int(row.primary_artifact_present)
    return {
        "cases": [asdict(item) for item in case_results],
        "cases_total": len(case_results),
        "route_matches_expected_count": sum(1 for item in case_results if item.route_matches_expected),
        "completed_count": sum(1 for item in case_results if item.run_status == "completed"),
        "primary_artifact_count": sum(1 for item in case_results if item.primary_artifact_present),
        "missing_tool_count": sum(1 for item in case_results if item.missing_tools_detected),
        "support_tier_counts": support_tier_counts,
        "family_counts": family_counts,
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Long-Read Benchmark Summary",
        "",
        f"- Cases total: `{summary.get('cases_total', 0)}`",
        f"- Route matches expected: `{summary.get('route_matches_expected_count', 0)}`",
        f"- Completed runs: `{summary.get('completed_count', 0)}`",
        f"- Primary artifacts present: `{summary.get('primary_artifact_count', 0)}`",
        f"- Cases with missing tools: `{summary.get('missing_tool_count', 0)}`",
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
            f"`{item.get('case_id', '')}` family=`{item.get('family', '')}` "
            f"route=`{item.get('route_matches_expected', False)}` "
            f"status=`{item.get('run_status', '')}` "
            f"artifact=`{item.get('primary_artifact_present', False)}` "
            f"tier=`{item.get('observed_support_tier', '')}`"
        )
    return "\n".join(lines)


__all__ = [
    "LongReadBenchmarkCase",
    "LongReadCaseResult",
    "default_long_read_benchmark_cases",
    "run_long_read_benchmark",
]
