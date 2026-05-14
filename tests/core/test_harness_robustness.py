"""Deterministic robustness regressions for protocol repair and deliverables."""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.core.analysis_spec import deterministic_analysis_spec
from bio_harness.core.protocol_grounding import deterministic_protocol_repair
from bio_harness.core.protocol_grounding._shared import (
    PARAMETER_KNOWLEDGE_BASE,
    _apply_parameter_knowledge_base,
)
from bio_harness.harness.deliverables import (
    _materialize_deseq_deliverable,
    _materialize_multi_model_dge_pathway_deliverable,
    _materialize_single_cell_deliverable,
    _materialize_transcript_quant_deliverable,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

_COMPILER_DATA_ROOTS = (
    ("bacterial_evolution_variant_calling", "workspace/benchmarks/bioagent-bench/tasks/evolution/data"),
    ("rna_seq_differential_expression", "workspace/benchmarks/bioagent-bench/tasks/deseq/data"),
    ("transcript_quantification", "workspace/benchmarks/bioagent-bench/tasks/transcript-quant/data"),
    ("metagenomics_classification", "workspace/benchmarks/bioagent-bench/tasks/metagenomics/data"),
    ("single_cell_rna_seq", "workspace/benchmarks/bioagent-bench/tasks/single-cell/data"),
    ("germline_variant_calling", "workspace/benchmarks/bioagent-bench/tasks/germline-vc/data"),
    ("variant_annotation", "workspace/benchmarks/bioagent-bench/tasks/cystic-fibrosis/data"),
    ("comparative_genomics", "benchmark_data/comparative_genomics"),
    ("viral_metagenomics", "workspace/benchmarks/bioagent-bench/tasks/viral-metagenomics/data"),
    ("multi_model_dge_pathway", "workspace/benchmarks/bioagent-bench/tasks/alzheimer-mouse/data"),
    ("phylogenetics", "workspace/benchmarks/bioagent-bench/tasks/phylogenetics/data"),
)


def _repo_path(relative_path: str) -> Path:
    """Return one repo-relative path."""

    return (PROJECT_ROOT / relative_path).resolve(strict=False)


@pytest.mark.parametrize(("analysis_type", "data_root_rel"), _COMPILER_DATA_ROOTS)
def test_deterministic_protocol_repair_is_idempotent_for_compiler_backed_types(
    tmp_path: Path,
    analysis_type: str,
    data_root_rel: str,
) -> None:
    """A second deterministic repair pass should not mutate a compiled plan."""

    data_root = _repo_path(data_root_rel)
    if not data_root.exists():
        pytest.skip(f"Missing benchmark data root: {data_root}")

    selected_dir = tmp_path / analysis_type
    selected_dir.mkdir(parents=True, exist_ok=True)
    analysis_spec = {
        "analysis_type": analysis_type,
        "protocol_grounding": {},
        "parameter_profile": [],
    }

    repaired_once, _ = deterministic_protocol_repair(
        {},
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )
    repaired_twice, _ = deterministic_protocol_repair(
        repaired_once,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert repaired_twice == repaired_once


@pytest.mark.parametrize(
    ("user_query", "available_skills", "plan", "expected_tool", "expected_arguments"),
    [
        (
            "Use only the scanpy_workflow tool on /tmp/pbmc3k_processed.h5ad and write outputs under /tmp/scanpy_output using min_genes 3 and min_cells 1.",
            ["scanpy_workflow", "sc_count_and_cluster", "bash_run"],
            {
                "thought_process": "direct scanpy wrapper",
                "plan": [
                    {
                        "step_id": 1,
                        "tool_name": "scanpy_workflow",
                        "arguments": {
                            "input_path": "/tmp/pbmc3k_processed.h5ad",
                            "output_dir": "/tmp/scanpy_output",
                            "min_genes": 3,
                            "min_cells": 1,
                        },
                    }
                ],
            },
            "scanpy_workflow",
            {"output_dir": "/tmp/scanpy_output", "min_genes": 3, "min_cells": 1},
        ),
        (
            "Use only the deseq2_run tool on counts table /tmp/airway_counts.tsv with metadata /tmp/airway_metadata.tsv for the dex comparison. Use design formula ~ dex and write intermediate outputs under /tmp/deseq_out.",
            ["deseq2_run", "featurecounts_run", "subread_align", "bash_run"],
            {
                "thought_process": "direct deseq2 wrapper",
                "plan": [
                    {
                        "step_id": 1,
                        "tool_name": "deseq2_run",
                        "arguments": {
                            "counts_matrix": "/tmp/airway_counts.tsv",
                            "metadata_table": "/tmp/airway_metadata.tsv",
                            "design_formula": "~ dex",
                            "contrast": "dex,trt,untrt",
                            "output_dir": "/tmp/deseq_out",
                        },
                    }
                ],
            },
            "deseq2_run",
            {
                "counts_matrix": "/tmp/airway_counts.tsv",
                "metadata_table": "/tmp/airway_metadata.tsv",
                "output_dir": "/tmp/deseq_out",
                "design_formula": "~ dex",
            },
        ),
        (
            "Use only the stringtie_quant tool on /tmp/aligned.bam with annotation /tmp/genes.gtf and write outputs under /tmp/stringtie.",
            ["stringtie_quant", "subread_align", "bash_run"],
            {
                "thought_process": "direct stringtie wrapper",
                "plan": [
                    {
                        "step_id": 1,
                        "tool_name": "stringtie_quant",
                        "arguments": {
                            "input_bam": "/tmp/aligned.bam",
                            "annotation_gtf": "/tmp/genes.gtf",
                            "output_gtf": "/tmp/stringtie/assembled.gtf",
                            "gene_abundance_tsv": "/tmp/stringtie/gene_abundances.tsv",
                        },
                    }
                ],
            },
            "stringtie_quant",
            {
                "output_gtf": "/tmp/stringtie/assembled.gtf",
                "gene_abundance_tsv": "/tmp/stringtie/gene_abundances.tsv",
            },
        ),
    ],
)
def test_deterministic_protocol_repair_preserves_locked_direct_wrapper_arguments(
    tmp_path: Path,
    user_query: str,
    available_skills: list[str],
    plan: dict[str, object],
    expected_tool: str,
    expected_arguments: dict[str, object],
) -> None:
    """Direct-wrapper plans should keep locked arguments during normalization."""

    analysis_spec = deterministic_analysis_spec(
        user_query,
        available_skill_names=available_skills,
    )

    repaired, _ = deterministic_protocol_repair(
        plan,
        analysis_spec=analysis_spec,
        selected_dir=tmp_path / "selected",
        data_root=tmp_path / "data",
    )

    step = repaired["plan"][0]
    assert step["tool_name"] == expected_tool
    for key, value in expected_arguments.items():
        assert step["arguments"][key] == value


@pytest.mark.parametrize("tool_name", sorted(PARAMETER_KNOWLEDGE_BASE))
def test_parameter_knowledge_base_never_overwrites_explicit_values(tool_name: str) -> None:
    """Knowledge-base defaults should only fill missing keys."""

    explicit_arguments: dict[str, object] = {}
    for key, default in PARAMETER_KNOWLEDGE_BASE[tool_name].items():
        if isinstance(default, bool):
            explicit_arguments[key] = not default
        elif isinstance(default, (int, float)):
            explicit_arguments[key] = default + 1
        else:
            explicit_arguments[key] = f"explicit_{default}"

    repaired, meta = _apply_parameter_knowledge_base(
        {
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": tool_name,
                    "arguments": explicit_arguments,
                }
            ]
        }
    )

    assert meta["changed"] is False
    assert repaired["plan"][0]["arguments"] == explicit_arguments


@pytest.mark.parametrize(
    ("materializer", "analysis_spec", "plan", "expected_reason"),
    [
        (
            _materialize_transcript_quant_deliverable,
            {"analysis_type": "transcript_quantification", "protocol_grounding": {}},
            {"plan": []},
            "no_quantification_source_found",
        ),
        (
            _materialize_deseq_deliverable,
            {"analysis_type": "rna_seq_differential_expression"},
            {"plan": []},
            "no_deseq2_results_source_found",
        ),
        (
            _materialize_single_cell_deliverable,
            {"analysis_type": "single_cell_rna_seq"},
            None,
            "single_cell_artifacts_missing",
        ),
        (
            _materialize_multi_model_dge_pathway_deliverable,
            {"analysis_type": "multi_model_dge_pathway"},
            None,
            "missing_model_enrichment_csvs",
        ),
    ],
)
def test_materializers_fail_gracefully_when_sources_are_missing(
    tmp_path: Path,
    materializer,
    analysis_spec: dict[str, object],
    plan: dict[str, object] | None,
    expected_reason: str,
) -> None:
    """Deliverable materializers should fail loudly instead of crashing."""

    selected_dir = tmp_path / "run"
    selected_dir.mkdir(parents=True, exist_ok=True)

    kwargs = {
        "selected_dir": selected_dir,
        "analysis_spec": analysis_spec,
    }
    if plan is not None:
        kwargs["plan"] = plan

    changed, meta = materializer(**kwargs)

    assert changed is False
    assert meta["why"] == expected_reason
