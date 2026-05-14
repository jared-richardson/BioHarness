"""Real-prompt benchmark runner for planner-time literature assistance.

This benchmark exercises the actual analysis-review path used by the harness,
without conflating planner-time literature assistance with full scientific task
execution. It uses real prompts, real data roots, the live harness analysis
spec builder, and the persisted planner-literature artifacts added during
planner-time literature productization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Callable

from bio_harness.core.analysis_spec import build_analysis_brief
from bio_harness.harness.config import HarnessConfig, WORKSPACE_ROOT
from scripts.run_agent_e2e_harness import AgentE2EHarness


@dataclass(frozen=True)
class PlannerLiteratureBenchmarkCase:
    """One real-prompt planner literature benchmark case."""

    case_id: str
    description: str
    prompt_template: str
    data_root: str
    benchmark_policy: str
    expected_status: str
    expected_visible: bool
    expected_query_class: str
    expected_reason_prefix: str


@dataclass(frozen=True)
class PlannerLiteratureCaseResult:
    """One case result from the planner literature benchmark."""

    case_id: str
    benchmark_policy: str
    status: str
    visible_to_planner: bool
    query_class: str
    trigger_reason: str
    evidence_sufficiency: str
    sources_consulted: int
    primary_literature_count: int
    trusted_web_count: int
    brief_has_literature_lines: bool
    artifact_written: bool
    manifest_visible_to_planner: bool
    manifest_query_class: str
    passed: bool
    selected_dir: str
    run_dir: str
    error: str = ""


def default_planner_literature_benchmark_cases() -> tuple[PlannerLiteratureBenchmarkCase, ...]:
    """Return the default real-prompt planner literature benchmark cases."""

    real_data_root = WORKSPACE_ROOT / "non_bioagent_real_data"
    exome_root = WORKSPACE_ROOT / "extended_test_data" / "exome"
    return (
        PlannerLiteratureBenchmarkCase(
            case_id="single_cell_resolution_scientific",
            description="Parameter assistance should trigger on cluster resolution guidance.",
            prompt_template=(
                "Using published methods, choose an appropriate cluster resolution for scanpy_workflow "
                f"on the processed AnnData file at {real_data_root}/pbmc3k_processed/"
                "pbmc3k_processed.h5ad. Use only scanpy_workflow and write outputs under "
                "{selected_dir}/scanpy_output."
            ),
            data_root=str(real_data_root / "pbmc3k_processed"),
            benchmark_policy="scientific_harness",
            expected_status="applied",
            expected_visible=True,
            expected_query_class="parameter_recommendation",
            expected_reason_prefix="parameter_question:",
        ),
        PlannerLiteratureBenchmarkCase(
            case_id="transcript_quant_protocol_scientific",
            description="Protocol assistance should trigger on published-methods workflow phrasing.",
            prompt_template=(
                "Use only the stringtie_quant tool on the coordinate-sorted BAM at "
                f"{real_data_root}/r_libs/RNAseqData.HNRNPC.bam.chr14/extdata/"
                "ERR127302_chr14.bam with the "
                f"annotation GTF at {real_data_root}/ucsc/hg19.chr14.knownGene.gtf. "
                "Based on published methods, keep this reference-guided and write the "
                "assembled transcript GTF to {selected_dir}/stringtie/assembled.gtf "
                "and the gene abundance table to {selected_dir}/stringtie/"
                "gene_abundances.tsv."
            ),
            data_root=str(real_data_root),
            benchmark_policy="scientific_harness",
            expected_status="applied",
            expected_visible=True,
            expected_query_class="protocol_choice",
            expected_reason_prefix="explicit_best_practice_request",
        ),
        PlannerLiteratureBenchmarkCase(
            case_id="variant_calling_no_trigger_scientific",
            description="Ordinary supported prompt should not trigger planner literature assistance.",
            prompt_template=(
                "Call germline variants from the reads at "
                f"{exome_root}/sample_R1.fastq "
                f"and {exome_root}/sample_R2.fastq. "
                f"Align with bwa_mem_align to reference {exome_root}/ref_genome.fa, "
                "then call variants with gatk_haplotypecaller. Write VCF to {selected_dir}/exome/variants.vcf."
            ),
            data_root=str(exome_root),
            benchmark_policy="scientific_harness",
            expected_status="skipped",
            expected_visible=False,
            expected_query_class="",
            expected_reason_prefix="no_literature_trigger",
        ),
        PlannerLiteratureBenchmarkCase(
            case_id="single_cell_resolution_blind",
            description="Blind benchmark policy must suppress planner literature assistance.",
            prompt_template=(
                "Using published methods, choose an appropriate cluster resolution for scanpy_workflow "
                f"on the processed AnnData file at {real_data_root}/pbmc3k_processed/"
                "pbmc3k_processed.h5ad. Use only scanpy_workflow and write outputs under "
                "{selected_dir}/scanpy_output."
            ),
            data_root=str(real_data_root / "pbmc3k_processed"),
            benchmark_policy="bioagentbench_planning_strict",
            expected_status="skipped",
            expected_visible=False,
            expected_query_class="",
            expected_reason_prefix="blind_benchmark_policy",
        ),
    )


def run_planner_literature_benchmark(
    *,
    output_root: Path,
    cases: tuple[PlannerLiteratureBenchmarkCase, ...] | None = None,
    model_name: str | None = None,
    host: str | None = None,
    llm_backend: str | None = None,
    harness_factory: Callable[[HarnessConfig], Any] = AgentE2EHarness,
) -> dict[str, Any]:
    """Run the planner-time literature assistance benchmark.

    Args:
        output_root: Benchmark output root.
        cases: Optional explicit case set.
        model_name: Optional model override for the harness orchestrator.
        host: Optional backend host override.
        llm_backend: Optional backend provider override.
        harness_factory: Optional harness factory for tests.

    Returns:
        Summary payload for the benchmark run.
    """

    selected_cases = cases or default_planner_literature_benchmark_cases()
    output_root.mkdir(parents=True, exist_ok=True)
    case_results = [
        _run_case(
            case,
            output_root=output_root,
            model_name=model_name,
            host=host,
            llm_backend=llm_backend,
            harness_factory=harness_factory,
        )
        for case in selected_cases
    ]
    summary = _summary(case_results)
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output_root / "summary.md").write_text(_summary_markdown(summary).strip() + "\n", encoding="utf-8")
    return summary


def _run_case(
    case: PlannerLiteratureBenchmarkCase,
    *,
    output_root: Path,
    model_name: str | None,
    host: str | None,
    llm_backend: str | None,
    harness_factory: Callable[[HarnessConfig], Any],
) -> PlannerLiteratureCaseResult:
    case_root = output_root / case.case_id
    selected_dir = case_root / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    prompt = case.prompt_template.format(selected_dir=str(selected_dir))
    cfg = HarnessConfig(
        prompt=prompt,
        selected_dir=selected_dir,
        data_root=Path(case.data_root),
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
        plan_path=None,
        result_json=selected_dir / "result.json",
        quiet=True,
        print_plan=False,
        benchmark_policy=case.benchmark_policy,
        llm_backend=llm_backend,
        path_graph_db=selected_dir / "knowledge" / "path_graph.sqlite",
        path_graph_user_key="default",
        path_graph_scope="global",
        path_graph_persist_preference_updates=False,
        auto_setup_isolated_tools=False,
    )
    harness = harness_factory(cfg)
    try:
        harness._init_run()
        harness._prepare_analysis_spec(contract={})
        harness._write_assistance_manifest()
        support = (
            harness.run.get("analysis_spec", {}).get("literature_planning_support", {})
            if isinstance(harness.run.get("analysis_spec", {}), dict)
            else {}
        )
        brief = build_analysis_brief(harness.run.get("analysis_spec", {}))
        manifest = harness._assistance_manifest_payload()
        run_dir = str(harness.run.get("run_files", {}).get("run_dir", "") or "")
        artifact_written = bool(str(support.get("json_path", "") or "").strip()) and Path(
            str(support.get("json_path", "") or "")
        ).exists()
        passed = (
            str(support.get("status", "") or "") == case.expected_status
            and bool(support.get("visible_to_planner", False)) is case.expected_visible
            and str(support.get("query_class", "") or "") == case.expected_query_class
            and str(support.get("trigger_reason", "") or "").startswith(case.expected_reason_prefix)
        )
        result = PlannerLiteratureCaseResult(
            case_id=case.case_id,
            benchmark_policy=case.benchmark_policy,
            status=str(support.get("status", "") or ""),
            visible_to_planner=bool(support.get("visible_to_planner", False)),
            query_class=str(support.get("query_class", "") or ""),
            trigger_reason=str(support.get("trigger_reason", "") or ""),
            evidence_sufficiency=str(support.get("evidence_sufficiency", "") or ""),
            sources_consulted=int(support.get("sources_consulted", 0) or 0),
            primary_literature_count=int(support.get("primary_literature_count", 0) or 0),
            trusted_web_count=int(support.get("trusted_web_count", 0) or 0),
            brief_has_literature_lines="literature_assistance_query_class=" in brief,
            artifact_written=artifact_written,
            manifest_visible_to_planner=bool(manifest.get("literature_planning_support_visible_to_planner", False)),
            manifest_query_class=str(manifest.get("literature_planning_support_query_class", "") or ""),
            passed=passed,
            selected_dir=str(selected_dir),
            run_dir=run_dir,
        )
    except Exception as exc:
        result = PlannerLiteratureCaseResult(
            case_id=case.case_id,
            benchmark_policy=case.benchmark_policy,
            status="failed",
            visible_to_planner=False,
            query_class="",
            trigger_reason="",
            evidence_sufficiency="",
            sources_consulted=0,
            primary_literature_count=0,
            trusted_web_count=0,
            brief_has_literature_lines=False,
            artifact_written=False,
            manifest_visible_to_planner=False,
            manifest_query_class="",
            passed=False,
            selected_dir=str(selected_dir),
            run_dir="",
            error=str(exc),
        )
    (case_root / "case_result.json").write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    return result


def _summary(case_results: list[PlannerLiteratureCaseResult]) -> dict[str, Any]:
    return {
        "cases_total": len(case_results),
        "cases_passed": sum(1 for row in case_results if row.passed),
        "cases_failed": sum(1 for row in case_results if not row.passed),
        "visible_to_planner_count": sum(1 for row in case_results if row.visible_to_planner),
        "artifact_written_count": sum(1 for row in case_results if row.artifact_written),
        "query_class_counts": _query_class_counts(case_results),
        "cases": [asdict(row) for row in case_results],
    }


def _query_class_counts(case_results: list[PlannerLiteratureCaseResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in case_results:
        key = row.query_class or "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Planner Literature Benchmark Summary",
        "",
        f"- Cases total: `{summary['cases_total']}`",
        f"- Cases passed: `{summary['cases_passed']}`",
        f"- Cases failed: `{summary['cases_failed']}`",
        f"- Visible to planner: `{summary['visible_to_planner_count']}`",
        f"- Artifact written: `{summary['artifact_written_count']}`",
        "",
    ]
    for row in summary["cases"]:
        lines.append(
            "- "
            f"`{row['case_id']}` status=`{row['status']}` "
            f"visible=`{row['visible_to_planner']}` "
            f"query_class=`{row['query_class'] or 'none'}` "
            f"reason=`{row['trigger_reason']}` "
            f"passed=`{row['passed']}`"
        )
    return "\n".join(lines)


__all__ = [
    "PlannerLiteratureBenchmarkCase",
    "PlannerLiteratureCaseResult",
    "default_planner_literature_benchmark_cases",
    "run_planner_literature_benchmark",
]
