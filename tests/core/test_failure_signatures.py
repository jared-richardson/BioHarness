from __future__ import annotations

from pathlib import Path

from bio_harness.core.failure_signatures import (
    detect_plan_artifact_failure_signatures,
    detect_stream_failure_signatures,
)


def test_detect_stream_failure_signatures_for_missing_vcf_tag():
    signatures = detect_stream_failure_signatures('Error: the tag "IMPACT" is not defined in the VCF header')

    assert "vcf_filter_tag_missing_in_header" in signatures
    assert "vcf_filter_tag_missing_in_header:impact" in signatures


def test_detect_stream_failure_signatures_for_generic_missing_vcf_header_tag():
    signatures = detect_stream_failure_signatures(
        "Error: no such tag defined in the VCF header: INFO/EFFECT"
    )

    assert "vcf_filter_tag_missing_in_header" in signatures
    assert "vcf_filter_tag_missing_in_header:effect" in signatures


def test_detect_stream_failure_signatures_for_missing_bcftools_namespace_field():
    signatures = detect_stream_failure_signatures(
        "No such FORMAT field: AF"
    )

    assert "bcftools_missing_expression_namespace_field" in signatures
    assert "bcftools_missing_expression_namespace_field:format:af" in signatures


def test_detect_stream_failure_signatures_for_ambiguous_bcftools_expression():
    signatures = detect_stream_failure_signatures(
        "Error: ambiguous filtering expression, both INFO/DP and FORMAT/DP are defined in the VCF header."
    )

    assert "bcftools_ambiguous_expression_namespace" in signatures
    assert "bcftools_ambiguous_expression_namespace:dp" in signatures


def test_detect_stream_failure_signatures_for_invalid_bcftools_view_cli():
    signatures = detect_stream_failure_signatures(
        "Could not parse argument: --min-alleles -v"
    )

    assert "bcftools_invalid_view_cli" in signatures
    assert "bcftools_invalid_view_cli:min-alleles" in signatures


def test_detect_stream_failure_signatures_for_same_file_copy_guard_failure():
    signatures = detect_stream_failure_signatures(
        "cp: './evol1_subtracted_anc.vcf.gz' and '/tmp/selected/evol1_subtracted_anc.vcf.gz' are the same file"
    )

    assert "shell_copy_same_file" in signatures


def test_detect_stream_failure_signatures_for_invalid_snpeff_codon_table():
    signatures = detect_stream_failure_signatures(
        "java.lang.RuntimeException: Error parsing property 'ancestor.codonTable'. No such codon table '11'"
    )

    assert "snpeff_invalid_codon_table" in signatures
    assert "snpeff_invalid_codon_table:11" in signatures


def test_detect_stream_failure_signatures_for_validation_missing_tool():
    signatures = detect_stream_failure_signatures(
        "__VALIDATION_BLOCK__:missing_tool:vcf2csv:hint=No manual entry for vcf2csv"
    )

    assert "validation_block_missing_tool" in signatures
    assert "validation_block_missing_tool:vcf2csv" in signatures


def test_detect_stream_failure_signatures_for_flye_out_of_memory():
    signatures = detect_stream_failure_signatures(
        "[2026-04-12 23:53:54] ERROR: Looks like the system ran out of memory\n"
        "[2026-04-12 23:53:54] ERROR: Command '['flye-modules', 'assemble']' died with <Signals.SIGKILL: 9>.\n"
        "[2026-04-12 23:52:49] INFO: Estimated coverage: 0\n"
    )

    assert "runtime_out_of_memory" in signatures
    assert "flye_out_of_memory" in signatures
    assert "flye_zero_coverage_estimate" in signatures


def test_detect_stream_failure_signatures_for_spatial_coordinate_input_error():
    signatures = detect_stream_failure_signatures(
        "__FORMAT_INPUT_ERROR__:Spatial coordinates contain missing or non-finite values."
    )

    assert "format_input_error_marker" in signatures
    assert "spatial_coordinates_invalid" in signatures


def test_detect_plan_artifact_failure_signatures_for_snpeff_ann_filter_mismatch(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    variants_dir = selected_dir / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    annotated_a = variants_dir / "evolved1_annotated.vcf"
    annotated_b = variants_dir / "evolved2_annotated.vcf"
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "##INFO=<ID=ANN,Number=.,Type=String,Description=\"Annotation\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t60\tPASS\tANN=G|missense_variant|MODERATE|gene1|gene1|transcript|tx1|protein_coding|1/1|c.10A>G|p.Lys4Arg|10/100|10/100|4/33||\n"
    )
    annotated_a.write_text(vcf_text, encoding="utf-8")
    annotated_b.write_text(vcf_text, encoding="utf-8")

    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {"input_vcf": "variants/evolved1.vcf.gz", "output_vcf": str(annotated_a)},
                    "step_id": 1,
                },
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {"input_vcf": "variants/evolved2.vcf.gz", "output_vcf": str(annotated_b)},
                    "step_id": 2,
                },
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": (
                            f"bcftools view -i 'IMPACT=\"MODERATE\" || IMPACT=\"HIGH\"' {annotated_a} "
                            f"> {selected_dir / 'final' / 'shared.vcf'}"
                        )
                    },
                    "step_id": 3,
                },
            ]
        },
        "step_statuses": ["completed", "completed", "failed"],
        "next_step_idx": 2,
    }

    signatures = detect_plan_artifact_failure_signatures(run=run, selected_dir=selected_dir)

    assert "shared_variant_export_shell_fragility" in signatures
    assert "vcf_filter_tag_missing_in_header" in signatures
    assert "snpeff_ann_semantics_mismatch" in signatures
    assert "local_tail_repair_viable" in signatures
    assert "resume_from_existing_artifacts" in signatures


def test_detect_plan_artifact_failure_signatures_for_prodigal_like_annotation_namespace(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    variants_dir = selected_dir / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    annotated = variants_dir / "shared_annotated.vcf"
    annotated.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=ANN,Number=.,Type=String,Description=\"Annotation\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tANN=G|missense_variant|MODERATE|3_14|3_14|transcript|tx1|protein_coding|1/1|c.10A>G|p.Lys4Arg|10/100|10/100|4/33||\n"
        ),
        encoding="utf-8",
    )

    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {"command": f"bcftools query -f '%CHROM\\t%POS\\n' {annotated} > {selected_dir / 'results.csv'}"},
                    "step_id": 1,
                }
            ]
        },
        "step_statuses": ["failed"],
        "next_step_idx": 0,
    }

    signatures = detect_plan_artifact_failure_signatures(run=run, selected_dir=selected_dir)

    assert "annotation_namespace_prodigal_like" in signatures


def test_detect_plan_artifact_failure_signatures_for_ambiguous_bcftools_expression(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tDP=9\tDP\t9\n"
        ),
        encoding="utf-8",
    )

    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": (
                            f"bcftools filter -e 'QUAL<30 || DP<5' "
                            f"-Oz -o {selected_dir / 'filtered.vcf.gz'} {input_vcf}"
                        )
                    },
                    "step_id": 1,
                }
            ]
        },
        "step_statuses": ["failed"],
        "next_step_idx": 0,
    }

    signatures = detect_plan_artifact_failure_signatures(run=run, selected_dir=selected_dir)

    assert "bcftools_ambiguous_expression_namespace" in signatures
    assert "bcftools_ambiguous_expression_namespace:dp" in signatures


def test_detect_plan_artifact_failure_signatures_for_missing_bcftools_namespace_field(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=AF,Number=1,Type=Float,Description=\"Allele frequency\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tAF=0.9\tDP\t9\n"
        ),
        encoding="utf-8",
    )

    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": (
                            f"bcftools view -i 'QUAL>=30 && FORMAT/AF>=0.8' "
                            f"-Oz -o {selected_dir / 'filtered.vcf.gz'} {input_vcf}"
                        )
                    },
                    "step_id": 1,
                }
            ]
        },
        "step_statuses": ["failed"],
        "next_step_idx": 0,
    }

    signatures = detect_plan_artifact_failure_signatures(run=run, selected_dir=selected_dir)

    assert "bcftools_missing_expression_namespace_field" in signatures
    assert "bcftools_missing_expression_namespace_field:format:af" in signatures


def test_detect_plan_artifact_failure_signatures_for_invalid_bcftools_view_cli(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\t.\n"
        ),
        encoding="utf-8",
    )

    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": (
                            f"bcftools view -i 'QUAL>=30' -m -v snps,indels "
                            f"-Oz -o {selected_dir / 'filtered.vcf.gz'} {input_vcf}"
                        )
                    },
                    "step_id": 1,
                }
            ]
        },
        "step_statuses": ["failed"],
        "next_step_idx": 0,
    }

    signatures = detect_plan_artifact_failure_signatures(run=run, selected_dir=selected_dir)

    assert "bcftools_invalid_view_cli" in signatures
    assert "bcftools_invalid_view_cli:m" in signatures
