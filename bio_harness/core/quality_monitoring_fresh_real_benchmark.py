"""Fresh real-data benchmark runner for lifecycle quality-monitoring features.

This module benchmarks the fresh-run lifecycle artifacts introduced by the
quality-monitoring productization work. It exercises end-to-end runs,
report-bundle reconstruction, and completed-run follow-up responses on new runs
that are expected to persist the full lifecycle artifact set.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from bio_harness.harness.config import WORKSPACE_ROOT
from bio_harness.reporting.report_bundle import build_run_report_bundle
from bio_harness.ui.completed_run_followups import build_completed_run_followup_response


@dataclass(frozen=True)
class FreshQualityBenchmarkCase:
    """One fresh-run lifecycle benchmark case."""

    case_id: str
    description: str
    prompt_template: str
    data_root: str


@dataclass(frozen=True)
class FreshQualityCaseResult:
    """One case result from the fresh lifecycle benchmark."""

    case_id: str
    selected_dir: str
    run_dir: str
    status: str
    run_returncode: int
    run_elapsed_seconds: float
    lifecycle_artifacts: dict[str, bool]
    run_dir_context_mode: str
    selected_dir_context_mode: str
    preflight_followup_available: bool
    in_run_quality_followup_available: bool
    summary_followup_available: bool
    passed: bool
    error: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""


_REQUIRED_LIFECYCLE_ARTIFACTS = (
    "preflight_summary.json",
    "preflight_summary.md",
    "completed_run_context.json",
    "in_run_quality_summary.json",
    "in_run_quality_events.jsonl",
)


def default_fresh_quality_benchmark_cases() -> tuple[FreshQualityBenchmarkCase, ...]:
    """Return the default fresh-run lifecycle benchmark cases."""

    real_data_root = WORKSPACE_ROOT / "non_bioagent_real_data"
    exome_root = WORKSPACE_ROOT / "extended_test_data" / "exome"
    return (
        FreshQualityBenchmarkCase(
            case_id="transcript_quant",
            description="Reference-guided transcript quantification from BAM.",
            prompt_template=(
                "Use only the stringtie_quant tool on the coordinate-sorted BAM at "
                f"{real_data_root}/r_libs/RNAseqData.HNRNPC.bam.chr14/extdata/"
                "ERR127302_chr14.bam with the "
                f"annotation GTF at {real_data_root}/ucsc/hg19.chr14.knownGene.gtf. Write the assembled "
                "transcript GTF to {selected_dir}/stringtie/assembled.gtf and the gene "
                "abundance table to {selected_dir}/stringtie/gene_abundances.tsv. Keep this "
                "reference-guided only and do not add alignment, counting, or bash_run."
            ),
            data_root=str(real_data_root),
        ),
        FreshQualityBenchmarkCase(
            case_id="single_cell",
            description="Processed AnnData single-cell workflow.",
            prompt_template=(
                "Use only the scanpy_workflow tool on the processed AnnData file at "
                f"{real_data_root}/pbmc3k_processed/pbmc3k_processed.h5ad. Write outputs under "
                "{selected_dir}/scanpy_output using min_genes 3, min_cells 1, max_mito_pct 100, "
                "n_hvgs 48, and leiden_resolution 0.3. Do not add FASTQ processing, count matrix "
                "generation, or bash_run."
            ),
            data_root=str(real_data_root / "pbmc3k_processed"),
        ),
        FreshQualityBenchmarkCase(
            case_id="variant_vcf",
            description="Germline variant calling from paired FASTQ reads.",
            prompt_template=(
                "Call germline variants from the reads at "
                f"{exome_root}/sample_R1.fastq "
                f"and {exome_root}/sample_R2.fastq. "
                f"Align with bwa_mem_align to reference {exome_root}/ref_genome.fa, "
                "then call variants with gatk_haplotypecaller. Write VCF to {selected_dir}/exome/variants.vcf."
            ),
            data_root=str(exome_root),
        ),
    )


def run_fresh_quality_monitoring_benchmark(
    *,
    output_root: Path,
    project_root: Path,
    cases: tuple[FreshQualityBenchmarkCase, ...] | None = None,
    benchmark_policy: str = "scientific_harness",
    model_name: str | None = None,
    host: str | None = None,
    llm_backend: str | None = None,
    quiet: bool = True,
    command_timeout_seconds: float = 900.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Run the fresh lifecycle quality-monitoring benchmark.

    Args:
        output_root: Benchmark output root.
        project_root: Repository root for locating entrypoint scripts.
        cases: Optional explicit case set.
        benchmark_policy: Benchmark policy passed to the harness.
        model_name: Optional model override.
        host: Optional backend host override.
        llm_backend: Optional LLM backend override.
        quiet: Whether to pass ``--quiet`` to the harness.
        command_timeout_seconds: Per-case timeout for the harness invocation.
        runner: Subprocess runner used to launch the harness.

    Returns:
        Summary payload for the full benchmark run.
    """

    selected_cases = cases or default_fresh_quality_benchmark_cases()
    output_root.mkdir(parents=True, exist_ok=True)
    case_results: list[FreshQualityCaseResult] = []
    for case in selected_cases:
        case_results.append(
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
                runner=runner,
            )
        )
    summary = _benchmark_summary(case_results)
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output_root / "summary.md").write_text(_summary_markdown(summary).strip() + "\n", encoding="utf-8")
    return summary


def _run_case(
    case: FreshQualityBenchmarkCase,
    *,
    output_root: Path,
    project_root: Path,
    benchmark_policy: str,
    model_name: str | None,
    host: str | None,
    llm_backend: str | None,
    quiet: bool,
    command_timeout_seconds: float,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> FreshQualityCaseResult:
    """Run one fresh lifecycle benchmark case."""

    case_root = output_root / case.case_id
    case_root.mkdir(parents=True, exist_ok=True)
    selected_dir = case_root / "selected"
    result_json = selected_dir / "result.json"
    prompt = case.prompt_template.format(selected_dir=str(selected_dir))
    command = [
        sys.executable,
        str(project_root / "scripts" / "run_agent_e2e.py"),
        "--prompt",
        prompt,
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
    elapsed_started_at = time.monotonic()
    result, invocation_error = _invoke_harness_case(
        command=command,
        cwd=project_root,
        timeout_seconds=command_timeout_seconds,
        runner=runner,
    )
    elapsed_seconds = round(time.monotonic() - elapsed_started_at, 3)
    if invocation_error:
        case_result = FreshQualityCaseResult(
            case_id=case.case_id,
            selected_dir=str(selected_dir),
            run_dir="",
            status="failed",
            run_returncode=-1,
            run_elapsed_seconds=elapsed_seconds,
            lifecycle_artifacts={name: False for name in _REQUIRED_LIFECYCLE_ARTIFACTS},
            run_dir_context_mode="",
            selected_dir_context_mode="",
            preflight_followup_available=False,
            in_run_quality_followup_available=False,
            summary_followup_available=False,
            passed=False,
            error=invocation_error,
        )
        (case_root / "case_result.json").write_text(
            json.dumps(asdict(case_result), indent=2) + "\n",
            encoding="utf-8",
        )
        return case_result

    payload = _read_json(result_json)
    if not payload:
        case_result = FreshQualityCaseResult(
            case_id=case.case_id,
            selected_dir=str(selected_dir),
            run_dir="",
            status="failed",
            run_returncode=int(result.returncode),
            run_elapsed_seconds=elapsed_seconds,
            lifecycle_artifacts={name: False for name in _REQUIRED_LIFECYCLE_ARTIFACTS},
            run_dir_context_mode="",
            selected_dir_context_mode="",
            preflight_followup_available=False,
            in_run_quality_followup_available=False,
            summary_followup_available=False,
            passed=False,
            error="missing_or_invalid_result_json",
            stdout_tail=_tail_text(result.stdout),
            stderr_tail=_tail_text(result.stderr),
        )
        (case_root / "case_result.json").write_text(
            json.dumps(asdict(case_result), indent=2) + "\n",
            encoding="utf-8",
        )
        return case_result

    run_dir = Path(str(payload.get("run_dir", "") or "")).expanduser().resolve(strict=False)
    lifecycle = {
        name: (run_dir / name).exists() for name in _REQUIRED_LIFECYCLE_ARTIFACTS
    }
    run_dir_summary: dict[str, Any] = {}
    selected_dir_summary: dict[str, Any] = {}
    report_error = ""
    try:
        run_dir_report = build_run_report_bundle(run_dir, case_root / "run_dir_report")
        selected_dir_report = build_run_report_bundle(selected_dir, case_root / "selected_dir_report")
        run_dir_summary = _read_json(run_dir_report / "summary.json")
        selected_dir_summary = _read_json(selected_dir_report / "summary.json")
    except Exception as exc:  # pragma: no cover - defensive benchmark capture
        report_error = f"report_bundle_error:{exc}"

    run_mapping = {
        "status": str(payload.get("status", "") or ""),
        "run_uid": str(payload.get("run_id", "") or ""),
        "run_dir": str(run_dir),
        "selected_dir": str(selected_dir),
    }
    try:
        preflight_followup = build_completed_run_followup_response(
            run_mapping,
            "Can you summarize the preflight and input quality warnings?",
        )
        in_run_followup = build_completed_run_followup_response(
            run_mapping,
            "What happened during the run? Were there any suspicious zero-byte outputs?",
        )
        summary_followup = build_completed_run_followup_response(
            run_mapping,
            "Summarize the results for me.",
        )
    except Exception as exc:  # pragma: no cover - defensive benchmark capture
        preflight_followup = ""
        in_run_followup = ""
        summary_followup = ""
        report_error = report_error or f"completed_run_followup_error:{exc}"

    case_result = FreshQualityCaseResult(
        case_id=case.case_id,
        selected_dir=str(selected_dir),
        run_dir=str(run_dir),
        status=str(payload.get("status", "") or ""),
        run_returncode=int(result.returncode),
        run_elapsed_seconds=elapsed_seconds,
        lifecycle_artifacts=lifecycle,
        run_dir_context_mode=str(run_dir_summary.get("context_mode", "") or ""),
        selected_dir_context_mode=str(selected_dir_summary.get("context_mode", "") or ""),
        preflight_followup_available=bool(preflight_followup.strip()),
        in_run_quality_followup_available=bool(in_run_followup.strip()),
        summary_followup_available=bool(summary_followup.strip()),
        passed=_case_passed(
            status=str(payload.get("status", "") or ""),
            run_returncode=int(result.returncode),
            lifecycle=lifecycle,
            run_dir_context_mode=str(run_dir_summary.get("context_mode", "") or ""),
            selected_dir_context_mode=str(selected_dir_summary.get("context_mode", "") or ""),
            preflight_followup=preflight_followup,
            in_run_followup=in_run_followup,
            summary_followup=summary_followup,
        ),
        error=report_error,
        stdout_tail=_tail_text(result.stdout),
        stderr_tail=_tail_text(result.stderr),
    )
    (case_root / "case_result.json").write_text(
        json.dumps(asdict(case_result), indent=2) + "\n",
        encoding="utf-8",
    )
    return case_result


def _case_passed(
    *,
    status: str,
    run_returncode: int,
    lifecycle: Mapping[str, bool],
    run_dir_context_mode: str,
    selected_dir_context_mode: str,
    preflight_followup: str,
    in_run_followup: str,
    summary_followup: str,
) -> bool:
    """Return whether one fresh lifecycle case meets the benchmark bar."""

    return (
        run_returncode == 0
        and status == "completed"
        and all(bool(lifecycle.get(name, False)) for name in _REQUIRED_LIFECYCLE_ARTIFACTS)
        and run_dir_context_mode != "artifact_directory_only"
        and selected_dir_context_mode != "artifact_directory_only"
        and bool(preflight_followup.strip())
        and bool(in_run_followup.strip())
        and bool(summary_followup.strip())
    )


def _benchmark_summary(case_results: list[FreshQualityCaseResult]) -> dict[str, Any]:
    """Build the aggregate benchmark summary payload."""

    return {
        "cases": [asdict(item) for item in case_results],
        "cases_total": len(case_results),
        "cases_passed": sum(1 for item in case_results if item.passed),
        "cases_failed": sum(1 for item in case_results if not item.passed),
        "run_dir_rich_context_count": sum(
            1
            for item in case_results
            if item.run_dir_context_mode and item.run_dir_context_mode != "artifact_directory_only"
        ),
        "selected_dir_rich_context_count": sum(
            1
            for item in case_results
            if item.selected_dir_context_mode and item.selected_dir_context_mode != "artifact_directory_only"
        ),
        "preflight_followup_count": sum(1 for item in case_results if item.preflight_followup_available),
        "in_run_quality_followup_count": sum(
            1 for item in case_results if item.in_run_quality_followup_available
        ),
        "summary_followup_count": sum(1 for item in case_results if item.summary_followup_available),
        "required_lifecycle_artifacts": list(_REQUIRED_LIFECYCLE_ARTIFACTS),
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    """Render the aggregate fresh lifecycle benchmark summary as Markdown."""

    lines = [
        "# Fresh Quality Monitoring Benchmark",
        "",
        f"- Cases total: `{summary.get('cases_total', 0)}`",
        f"- Cases passed: `{summary.get('cases_passed', 0)}`",
        f"- Cases failed: `{summary.get('cases_failed', 0)}`",
        f"- Rich run-dir contexts: `{summary.get('run_dir_rich_context_count', 0)}`",
        f"- Rich selected-dir contexts: `{summary.get('selected_dir_rich_context_count', 0)}`",
        f"- Preflight follow-up responses: `{summary.get('preflight_followup_count', 0)}`",
        f"- In-run-quality follow-up responses: `{summary.get('in_run_quality_followup_count', 0)}`",
        "",
    ]
    for item in summary.get("cases", []):
        if not isinstance(item, dict):
            continue
        lifecycle = item.get("lifecycle_artifacts", {}) if isinstance(item.get("lifecycle_artifacts", {}), dict) else {}
        present = [name for name, ok in lifecycle.items() if bool(ok)]
        lines.append(
            "- "
            f"`{item.get('case_id', '')}` status=`{item.get('status', '')}` "
            f"rc=`{item.get('run_returncode', '')}` "
            f"passed=`{str(bool(item.get('passed', False))).lower()}` "
            f"run_dir_context=`{item.get('run_dir_context_mode', '')}` "
            f"selected_dir_context=`{item.get('selected_dir_context_mode', '')}` "
            f"lifecycle_present=`{len(present)}/{len(_REQUIRED_LIFECYCLE_ARTIFACTS)}`"
        )
        error = str(item.get("error", "") or "").strip()
        if error:
            lines.append(f"  error=`{error}`")
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    """Read one JSON file into a dictionary."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _invoke_harness_case(
    *,
    command: list[str],
    cwd: Path,
    timeout_seconds: float,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run one harness benchmark case with bounded execution capture."""

    try:
        result = runner(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1.0, float(timeout_seconds)),
        )
        return result, ""
    except subprocess.TimeoutExpired as exc:
        return (
            subprocess.CompletedProcess(
                exc.cmd if isinstance(exc.cmd, list) else command,
                returncode=-1,
                stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            ),
            f"run_timeout:{float(timeout_seconds):.1f}s",
        )
    except Exception as exc:  # pragma: no cover - defensive benchmark capture
        return (
            subprocess.CompletedProcess(command, returncode=-1, stdout="", stderr=""),
            f"runner_error:{exc}",
        )


def _tail_text(text: str | None, *, max_chars: int = 4000) -> str:
    """Return one bounded output tail for benchmark debugging surfaces."""

    if not text:
        return ""
    return text[-max_chars:]
