"""Focused tests for protocol repair helper behavior."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.protocol_grounding._repair import (
    _locked_intent_satisfied,
    _normalize_canonical_output_filenames,
    _preserve_current_plan,
)
from bio_harness.core.protocol_grounding._shared import _preferred_argument_value


def _one_step_plan(tool_name: str, **arguments: object) -> dict[str, object]:
    return {
        "plan": [
            {
                "step_id": 1,
                "tool_name": tool_name,
                "arguments": dict(arguments),
            }
        ]
    }


def test_normalize_canonical_output_filenames_rewrites_stringtie_basenames(tmp_path: Path) -> None:
    plan = _one_step_plan(
        "stringtie_quant",
        output_gtf=str(tmp_path / "stringtie" / "quantified.gtf"),
        gene_abundance_tsv=str(tmp_path / "stringtie" / "gene_abundance.tsv"),
    )

    repaired, meta = _normalize_canonical_output_filenames(plan, analysis_spec=None)

    assert repaired["plan"][0]["arguments"]["output_gtf"] == str(tmp_path / "stringtie" / "assembled.gtf")
    assert repaired["plan"][0]["arguments"]["gene_abundance_tsv"] == str(
        tmp_path / "stringtie" / "gene_abundances.tsv"
    )
    assert meta["changed"] is True
    assert meta["why"] == "canonical_output_filenames"


def test_normalize_canonical_output_filenames_appends_filename_for_directory_only_path(tmp_path: Path) -> None:
    plan = _one_step_plan(
        "stringtie_quant",
        output_gtf=str(tmp_path / "stringtie"),
    )

    repaired, _meta = _normalize_canonical_output_filenames(plan, analysis_spec=None)

    assert repaired["plan"][0]["arguments"]["output_gtf"] == str(tmp_path / "stringtie" / "assembled.gtf")


def test_normalize_canonical_output_filenames_leaves_canonical_path_unchanged(tmp_path: Path) -> None:
    plan = _one_step_plan(
        "stringtie_quant",
        output_gtf=str(tmp_path / "stringtie" / "assembled.gtf"),
    )

    repaired, meta = _normalize_canonical_output_filenames(plan, analysis_spec=None)

    assert repaired == plan
    assert meta["changed"] is False


def test_normalize_canonical_output_filenames_skips_tools_without_registry_defaults(tmp_path: Path) -> None:
    plan = _one_step_plan(
        "deseq2_run",
        output_dir=str(tmp_path / "results"),
    )

    repaired, meta = _normalize_canonical_output_filenames(plan, analysis_spec=None)

    assert repaired == plan
    assert meta["changed"] is False


def test_normalize_canonical_output_filenames_preserves_locked_user_filenames(tmp_path: Path) -> None:
    custom_gtf = tmp_path / "custom" / "my_stringtie_output.gtf"
    custom_abundance = tmp_path / "custom" / "my_gene_abundance.tsv"
    plan = _one_step_plan(
        "stringtie_quant",
        output_gtf=str(tmp_path / "stringtie" / "quantified.gtf"),
        gene_abundance_tsv=str(tmp_path / "stringtie" / "gene_abundance.tsv"),
    )
    analysis_spec = {
        "explicit_execution_intent": {
            "locked_argument_values": {
                "stringtie_quant": {
                    "output_gtf": str(custom_gtf),
                    "gene_abundance_tsv": str(custom_abundance),
                }
            }
        }
    }

    repaired, meta = _normalize_canonical_output_filenames(plan, analysis_spec=analysis_spec)

    assert repaired == plan
    assert meta["changed"] is False


def test_locked_intent_satisfied_requires_matching_tool_and_arguments(tmp_path: Path) -> None:
    output_dir = tmp_path / "scanpy_output"
    analysis_spec = {
        "explicit_execution_intent": {
            "locked_tools": ["scanpy_workflow"],
            "locked_argument_values": {
                "scanpy_workflow": {
                    "output_dir": str(output_dir),
                    "min_genes": 3,
                }
            },
        }
    }
    matching_plan = _one_step_plan(
        "scanpy_workflow",
        input_path=str(tmp_path / "pbmc3k.h5ad"),
        output_dir=str(output_dir),
        min_genes=3,
    )
    wrong_args_plan = _one_step_plan(
        "scanpy_workflow",
        input_path=str(tmp_path / "pbmc3k.h5ad"),
        output_dir=str(output_dir),
        min_genes=300,
    )
    wrong_tool_plan = _one_step_plan(
        "sc_count_and_cluster",
        input_path=str(tmp_path / "pbmc3k.h5ad"),
        output_dir=str(output_dir),
        min_genes=3,
    )

    assert _locked_intent_satisfied(matching_plan, analysis_spec) is True
    assert _locked_intent_satisfied(wrong_args_plan, analysis_spec) is False
    assert _locked_intent_satisfied(wrong_tool_plan, analysis_spec) is False
    assert _locked_intent_satisfied(matching_plan, {"explicit_execution_intent": {}}) is False


def test_preserve_current_plan_requires_locked_intent_and_matching_tool_sequence(tmp_path: Path) -> None:
    output_dir = tmp_path / "scanpy_output"
    analysis_spec = {
        "explicit_execution_intent": {
            "locked_tools": ["scanpy_workflow"],
            "locked_argument_values": {
                "scanpy_workflow": {
                    "output_dir": str(output_dir),
                }
            },
        }
    }
    current = _one_step_plan(
        "scanpy_workflow",
        input_path=str(tmp_path / "pbmc3k.h5ad"),
        output_dir=str(output_dir),
    )
    compiled_match = _one_step_plan(
        "scanpy_workflow",
        input_path=str(tmp_path / "pbmc3k.h5ad"),
        output_dir=str(output_dir),
    )
    compiled_other = _one_step_plan(
        "salmon_quant",
        reads_1=str(tmp_path / "sample_R1.fastq.gz"),
        reads_2=str(tmp_path / "sample_R2.fastq.gz"),
        output_dir=str(tmp_path / "quant"),
    )

    assert _preserve_current_plan(current, compiled_match, analysis_spec) is True
    assert _preserve_current_plan(current, compiled_other, analysis_spec) is False
    assert _preserve_current_plan(current, compiled_match, {"explicit_execution_intent": {}}) is False


def test_preferred_argument_value_prefers_plan_then_locked_intent_then_default(tmp_path: Path) -> None:
    plan = _one_step_plan(
        "scanpy_workflow",
        output_dir=str(tmp_path / "plan_out"),
        min_genes=3,
        min_cells="",
        max_mito_pct=None,
    )
    analysis_spec = {
        "explicit_execution_intent": {
            "locked_argument_values": {
                "scanpy_workflow": {
                    "output_dir": str(tmp_path / "locked_out"),
                    "min_cells": 2,
                    "max_mito_pct": 15,
                }
            }
        }
    }

    assert _preferred_argument_value(
        plan=plan,
        analysis_spec=analysis_spec,
        tool_name="scanpy_workflow",
        argument_key="output_dir",
        default=str(tmp_path / "default_out"),
    ) == str(tmp_path / "plan_out")
    assert _preferred_argument_value(
        plan=plan,
        analysis_spec=analysis_spec,
        tool_name="scanpy_workflow",
        argument_key="min_cells",
        default=1,
    ) == 2
    assert _preferred_argument_value(
        plan=plan,
        analysis_spec=analysis_spec,
        tool_name="scanpy_workflow",
        argument_key="max_mito_pct",
        default=5,
    ) == 15
    assert _preferred_argument_value(
        plan=plan,
        analysis_spec=analysis_spec,
        tool_name="scanpy_workflow",
        argument_key="n_hvgs",
        default=2000,
    ) == 2000
