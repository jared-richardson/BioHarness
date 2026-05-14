from __future__ import annotations

from pathlib import Path

from bio_harness.core.artifact_inspectors import (
    _extract_expected_outputs,
    _inspect_expected_output_path,
    can_resume_after_failed_step,
    infer_resumable_step_index,
    inspect_vcf_field_namespaces,
)


def test_extract_expected_outputs_for_deseq2_run_uses_results_file():
    outputs = _extract_expected_outputs(
        {
            "tool_name": "deseq2_run",
            "arguments": {"output_dir": "/tmp/deseq2_results"},
        }
    )

    assert outputs == ["/tmp/deseq2_results/deseq2_results.tsv"]


def test_extract_expected_outputs_for_salmon_quant_ignores_buildable_index_dir() -> None:
    outputs = _extract_expected_outputs(
        {
            "tool_name": "salmon_quant",
            "arguments": {
                "index_dir": "/tmp/salmon_index",
                "output_dir": "/tmp/salmon_quant_out",
            },
        }
    )

    assert outputs == ["/tmp/salmon_quant_out/quant.sf"]


def test_extract_expected_outputs_for_spades_assemble_uses_scaffolds_marker() -> None:
    outputs = _extract_expected_outputs(
        {
            "tool_name": "spades_assemble",
            "arguments": {"output_dir": "/tmp/spades_out"},
        }
    )

    assert outputs == [
        "/tmp/spades_out/contigs.fasta",
        "/tmp/spades_out/scaffolds.fasta",
    ]


def test_inspect_vcf_field_namespaces_reads_info_format_and_samples(tmp_path: Path) -> None:
    vcf_path = tmp_path / "calls.vcf"
    vcf_path.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "##INFO=<ID=ANN,Number=.,Type=String,Description=\"Annotation\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample_a\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tDP=8;ANN=G|missense\tDP\t8\n"
        ),
        encoding="utf-8",
    )

    inspection = inspect_vcf_field_namespaces(vcf_path)

    assert inspection.exists is True
    assert inspection.has_ann is True
    assert inspection.info_tags == ("ANN", "DP")
    assert inspection.format_tags == ("DP",)
    assert inspection.sample_names == ("sample_a",)


def test_extract_expected_outputs_for_flye_assemble_uses_assembly_marker() -> None:
    outputs = _extract_expected_outputs(
        {
            "tool_name": "flye_assemble",
            "arguments": {"output_dir": "/tmp/flye_out"},
        }
    )

    assert outputs == ["/tmp/flye_out/assembly.fasta"]


def test_extract_expected_outputs_for_edger_run_uses_results_file():
    outputs = _extract_expected_outputs(
        {
            "tool_name": "edger_run",
            "arguments": {"output_dir": "/tmp/edger_results"},
        }
    )

    assert outputs == ["/tmp/edger_results/edger_results.tsv"]


def test_extract_expected_outputs_for_limma_voom_run_uses_results_file():
    outputs = _extract_expected_outputs(
        {
            "tool_name": "limma_voom_run",
            "arguments": {"output_dir": "/tmp/limma_results"},
        }
    )

    assert outputs == ["/tmp/limma_results/limma_voom_results.tsv"]


def test_extract_expected_outputs_for_bash_run_uses_output_flags() -> None:
    outputs = _extract_expected_outputs(
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    "python3 pipeline_scripts/compare_pathways.py "
                    "--input-a inputs/a.tsv "
                    "--output-csv outputs/final/pathway_comparison.csv "
                    "--outdir outputs/final_bundle"
                )
            },
        }
    )

    assert outputs == [
        "outputs/final/pathway_comparison.csv",
        "outputs/final_bundle",
    ]


def test_extract_expected_outputs_for_bash_run_captures_normalize_gff_output() -> None:
    outputs = _extract_expected_outputs(
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    "python3 pipeline_scripts/normalize_gff_for_featurecounts.py "
                    "references/genes.gff "
                    "references/annotation_for_featurecounts.gff"
                )
            },
        }
    )

    assert outputs == ["references/annotation_for_featurecounts.gff"]


def test_extract_expected_outputs_for_bash_run_captures_short_o_and_bcftools_isec_prefix() -> None:
    outputs = _extract_expected_outputs(
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    "cd outputs && "
                    "bcftools view -Oz -o filtered/evol1_filtered.vcf.gz calls/evol1_raw.vcf && "
                    "bcftools isec -w1 -p filtered/intersected filtered/evol1_filtered.vcf.gz filtered/anc_filtered.vcf.gz"
                )
            },
        }
    )

    assert outputs == [
        "filtered/evol1_filtered.vcf.gz",
        "filtered/intersected",
    ]


def test_extract_expected_outputs_for_bash_run_ignores_filter_comparisons_and_fd_redirects() -> None:
    outputs = _extract_expected_outputs(
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    "if command -v vcffilter >/dev/null 2>&1; then "
                    "zcat -f variants/raw.vcf | vcffilter -f "
                    "'QUAL > 1 & QUAL / AO > 10 && INFO/RPL > 1' "
                    "| bgzip > variants/filtered.vcf.gz; "
                    "fi"
                )
            },
        }
    )

    assert outputs == ["variants/filtered.vcf.gz"]


def test_extract_expected_outputs_for_bash_run_ignores_heredoc_body_tokens() -> None:
    outputs = _extract_expected_outputs(
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    "python3 helper.py --output-report outputs/classification.tsv <<'PYEOF'\n"
                    "print('Detected viruses (>=10% coverage): 2')\n"
                    "print('token = value')\n"
                    "PYEOF"
                )
            },
        }
    )

    assert outputs == ["outputs/classification.tsv"]


def test_inspect_expected_output_path_rejects_empty_files_and_accepts_nonempty_dirs(tmp_path: Path) -> None:
    empty_file = tmp_path / "empty.vcf"
    empty_file.write_text("", encoding="utf-8")
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    (output_dir / "result.tsv").write_text("ok\n", encoding="utf-8")

    empty_info = _inspect_expected_output_path(str(empty_file), selected_dir=tmp_path)
    dir_info = _inspect_expected_output_path(str(output_dir), selected_dir=tmp_path)

    assert empty_info["exists"] is True
    assert empty_info["valid"] is False
    assert dir_info["exists"] is True
    assert dir_info["is_dir"] is True
    assert dir_info["valid"] is True


def test_infer_resumable_step_index_does_not_skip_partial_spades_output(tmp_path: Path) -> None:
    assembly_dir = tmp_path / "assembly" / "ancestor_spades"
    assembly_dir.mkdir(parents=True)
    (assembly_dir / "tmp.marker").write_text("partial\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {"output_dir": str(assembly_dir)},
            },
            {
                "tool_name": "prokka_annotate",
                "arguments": {"output_dir": str(tmp_path / "annotation" / "prokka")},
            },
        ]
    }

    assert infer_resumable_step_index(tmp_path, plan) == 0


def test_infer_resumable_step_index_skips_completed_spades_output(tmp_path: Path) -> None:
    assembly_dir = tmp_path / "assembly" / "ancestor_spades"
    assembly_dir.mkdir(parents=True)
    (assembly_dir / "contigs.fasta").write_text(">contig1\nACGT\n", encoding="utf-8")
    (assembly_dir / "scaffolds.fasta").write_text(">contig1\nACGT\n", encoding="utf-8")
    annotation_dir = tmp_path / "annotation" / "prokka"
    annotation_dir.mkdir(parents=True)

    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {"output_dir": str(assembly_dir)},
            },
            {
                "tool_name": "prokka_annotate",
                "arguments": {"output_dir": str(annotation_dir)},
            },
        ]
    }

    assert infer_resumable_step_index(tmp_path, plan) == 1


def test_can_resume_after_failed_step_requires_materialized_outputs(tmp_path: Path) -> None:
    annotation_dir = tmp_path / "annotation" / "prokka"
    annotation_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan": [
            {
                "tool_name": "prokka_annotate",
                "arguments": {"output_dir": str(annotation_dir)},
            },
        ]
    }

    assert can_resume_after_failed_step(tmp_path, plan, 0) is False


def test_can_resume_after_failed_step_allows_completed_output_contract(tmp_path: Path) -> None:
    output_dir = tmp_path / "deseq2_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "deseq2_results.tsv").write_text("gene\tlog2fc\n", encoding="utf-8")
    plan = {
        "plan": [
            {
                "tool_name": "deseq2_run",
                "arguments": {"output_dir": str(output_dir)},
            },
        ]
    }

    assert can_resume_after_failed_step(tmp_path, plan, 0) is True
