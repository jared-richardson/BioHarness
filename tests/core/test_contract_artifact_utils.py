"""Tests for contract artifact-path helpers."""

from __future__ import annotations

from pathlib import Path

from bio_harness.harness.contract_artifact_utils import (
    _collect_planned_output_paths,
    _missing_input_paths_for_plan,
    _verify_run_outputs,
)


def test_self_built_index_dir_is_not_reported_missing(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "reads_1.fq.gz").write_text("r1", encoding="utf-8")
    (data_root / "reads_2.fq.gz").write_text("r2", encoding="utf-8")
    (data_root / "transcriptome.fa").write_text(">tx1\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "salmon_quant",
                "arguments": {
                    "index_dir": "outputs/transcript_quant/salmon_index",
                    "transcriptome_fasta": str(data_root / "transcriptome.fa"),
                    "reads_1": str(data_root / "reads_1.fq.gz"),
                    "reads_2": str(data_root / "reads_2.fq.gz"),
                    "threads": 2,
                    "output_dir": "outputs/transcript_quant/salmon",
                },
            }
        ]
    }

    missing = _missing_input_paths_for_plan(plan, selected_dir, data_root)
    assert missing == []


def test_self_built_subread_index_base_is_not_reported_missing(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "reads_1.fastq").write_text("r1", encoding="utf-8")
    (data_root / "reads_2.fastq").write_text("r2", encoding="utf-8")
    (data_root / "reference.fa").write_text(">chr1\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "subread_align",
                "arguments": {
                    "index_base": str(selected_dir / "outputs" / "subread_index" / "genome"),
                    "reference_fasta": str(data_root / "reference.fa"),
                    "reads_1": str(data_root / "reads_1.fastq"),
                    "reads_2": str(data_root / "reads_2.fastq"),
                    "output_bam": str(selected_dir / "alignments" / "sample.bam"),
                },
            }
        ]
    }

    missing = _missing_input_paths_for_plan(plan, selected_dir, data_root)
    assert missing == []


def test_collect_planned_output_paths_includes_normalized_featurecounts_gff(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace"
    normalized_gff = selected_dir / "references" / "annotation_for_featurecounts.gff"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 /repo/bio_harness/pipeline_scripts/normalize_gff_for_featurecounts.py "
                        "/refs/genes.gff "
                        f"{normalized_gff}"
                    )
                },
            }
        ]
    }

    planned = _collect_planned_output_paths(plan, selected_dir)

    assert str(normalized_gff) in planned


def test_missing_input_paths_for_plan_allows_upstream_normalized_featurecounts_gff(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    reference_fasta = data_root / "reference.fa"
    reference_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    (data_root / "reads_1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (data_root / "reads_2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    normalized_gff = selected_dir / "references" / "annotation_for_featurecounts.gff"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 /repo/bio_harness/pipeline_scripts/normalize_gff_for_featurecounts.py "
                        f"{data_root / 'genes.gff'} "
                        f"{normalized_gff}"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "featurecounts_run",
                "arguments": {
                    "input_bams": [str(selected_dir / "alignments" / "sample.bam")],
                    "annotation_gtf": str(normalized_gff),
                    "annotation_format": "GFF",
                    "feature_type": "gene",
                    "attribute_type": "ID",
                    "output_counts": str(selected_dir / "counts" / "gene_counts.txt"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "subread_align",
                "arguments": {
                    "index_base": str(selected_dir / "subread_index" / "genome"),
                    "reference_fasta": str(reference_fasta),
                    "reads_1": str(data_root / "reads_1.fastq"),
                    "reads_2": str(data_root / "reads_2.fastq"),
                    "output_bam": str(selected_dir / "alignments" / "sample.bam"),
                },
            },
        ]
    }

    missing = _missing_input_paths_for_plan(plan, selected_dir, data_root)

    assert all("featurecounts_run.annotation_gtf" not in item for item in missing)


def test_verify_run_outputs_ignores_transient_isec_export_paths(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "evol1_anc_subtracted.vcf.gz").write_text("vcf", encoding="utf-8")
    (selected_dir / "evol2_anc_subtracted.vcf.gz").write_text("vcf", encoding="utf-8")

    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools isec -w1 -n=2 -p .isec_export_evol1_anc_subtracted "
                        "evol1_variants.vcf.gz anc_filtered.vcf.gz -Oz && "
                        "mv -f .isec_export_evol1_anc_subtracted/0000.vcf.gz evol1_anc_subtracted.vcf.gz && "
                        "rm -rf .isec_export_evol1_anc_subtracted && "
                        "bcftools isec -w1 -n=2 -p .isec_export_evol2_anc_subtracted "
                        "evol2_variants.vcf.gz anc_filtered.vcf.gz -Oz && "
                        "mv -f .isec_export_evol2_anc_subtracted/0000.vcf.gz evol2_anc_subtracted.vcf.gz && "
                        "rm -rf .isec_export_evol2_anc_subtracted"
                    )
                },
            }
        ]
    }

    ok, message = _verify_run_outputs(selected_dir, plan)

    assert ok is True
    assert message == ""
