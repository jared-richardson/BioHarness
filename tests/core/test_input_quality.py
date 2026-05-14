"""Tests for deterministic input-quality scanning."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bio_harness.core.input_quality import (
    scan_annotation_input,
    scan_bam_input,
    scan_fastq_input,
    scan_metadata_table,
    scan_plan_inputs,
    scan_reference_fasta,
    scan_vcf_input,
)
from bio_harness.core.tool_env import which_with_pixi


_FASTQ_TEXT = textwrap.dedent(
    """\
    @read1
    ACGTACGTACGTACGTACGT
    +
    IIIIIIIIIIIIIIIIIIII
    @read2
    TTTTAAAACCCCGGGGTTTT
    +
    IIIIIIIIIIIIIIIIIIII
    """
)

_SHORT_FASTQ_TEXT = textwrap.dedent(
    """\
    @read1
    ACGTACGTAC
    +
    IIIIIIIIII
    """
)

_SAM_HEADER = textwrap.dedent(
    """\
    @HD\tVN:1.6\tSO:coordinate
    @SQ\tSN:chr1\tLN:100
    """
).rstrip("\n")

_SAM_READS = "read1\t0\tchr1\t1\t60\t8M\t*\t0\t0\tACGTACGT\tIIIIIIII"


def _run(cmd: list[str]) -> None:
    import subprocess

    subprocess.run(cmd, capture_output=True, check=True, text=True)


@pytest.fixture
def good_fastq(tmp_path: Path) -> Path:
    path = tmp_path / "sample_R1.fastq"
    path.write_text(_FASTQ_TEXT)
    return path


@pytest.fixture
def short_fastq(tmp_path: Path) -> Path:
    path = tmp_path / "short.fastq"
    path.write_text(_SHORT_FASTQ_TEXT)
    return path


@pytest.fixture
def reference_fasta(tmp_path: Path) -> Path:
    path = tmp_path / "reference.fa"
    path.write_text(
        ">chr1\n"
        + ("ACGT" * 25)
        + "\n>chr2\n"
        + ("TTAA" * 25)
        + "\n"
    )
    (tmp_path / "reference.fa.fai").write_text("chr1\t100\t6\t100\t101\nchr2\t100\t113\t100\t101\n")
    return path


@pytest.fixture
def good_metadata(tmp_path: Path) -> Path:
    path = tmp_path / "metadata.tsv"
    path.write_text("sample\tcondition\nsample\tcontrol\nsample_b\ttreatment\n")
    return path


@pytest.fixture
def tiny_bam(tmp_path: Path) -> Path:
    samtools = which_with_pixi("samtools")
    if not samtools:
        pytest.skip("samtools not available")

    sam = tmp_path / "input.sam"
    sam.write_text(_SAM_HEADER + "\n" + _SAM_READS + "\n")
    bam = tmp_path / "input.bam"
    sorted_bam = tmp_path / "sorted.bam"
    _run([samtools, "view", "-bS", str(sam), "-o", str(bam)])
    _run([samtools, "sort", str(bam), "-o", str(sorted_bam)])
    _run([samtools, "index", str(sorted_bam)])
    return sorted_bam


def test_scan_fastq_good_has_no_issues(good_fastq: Path) -> None:
    assert scan_fastq_input(good_fastq) == []


def test_scan_fastq_empty_reports_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.fastq"
    path.write_text("")

    issues = scan_fastq_input(path)

    assert len(issues) == 1
    assert issues[0].category == "empty_file"
    assert issues[0].severity == "error"


def test_scan_fastq_short_reads_warns(short_fastq: Path) -> None:
    issues = scan_fastq_input(short_fastq)

    assert any(issue.category == "short_reads" for issue in issues)


def test_scan_reference_good_has_no_issues(reference_fasta: Path) -> None:
    assert scan_reference_fasta(reference_fasta) == []


def test_scan_reference_tiny_warns(tmp_path: Path) -> None:
    path = tmp_path / "tiny.fa"
    path.write_text(">chr1\nACGT\n")

    issues = scan_reference_fasta(path)

    assert any(issue.category == "tiny_reference" for issue in issues)


def test_scan_reference_whitespace_only_errors(tmp_path: Path) -> None:
    path = tmp_path / "blank.fa"
    path.write_text("\n  \n")

    issues = scan_reference_fasta(path)

    assert any(issue.category == "empty_file" for issue in issues)


def test_scan_metadata_good_has_no_issues(good_metadata: Path) -> None:
    assert scan_metadata_table(good_metadata, analysis_type="rna_seq_differential_expression") == []


def test_scan_metadata_missing_sample_column_errors(tmp_path: Path) -> None:
    path = tmp_path / "metadata.tsv"
    path.write_text("condition\ncontrol\n")

    issues = scan_metadata_table(path, analysis_type="rna_seq_differential_expression")

    assert any(issue.category == "missing_required_column" for issue in issues)


def test_scan_metadata_missing_condition_column_errors(tmp_path: Path) -> None:
    path = tmp_path / "metadata.tsv"
    path.write_text("sample\nsample1\nsample2\n")

    issues = scan_metadata_table(path, analysis_type="rna_seq_differential_expression")

    assert any(issue.category == "missing_required_column" for issue in issues)


def test_scan_metadata_semantic_treatment_group_column_counts_as_condition(tmp_path: Path) -> None:
    path = tmp_path / "metadata.csv"
    path.write_text("id,treatment_group,batch\nsample_0,control,b1\nsample_1,treatment,b1\n")

    issues = scan_metadata_table(
        path,
        analysis_type="metabolomics",
        expected_sample_names=["sample_0", "sample_1"],
    )

    assert not any(issue.category == "missing_required_column" for issue in issues)


def test_scan_metadata_single_condition_errors(tmp_path: Path) -> None:
    path = tmp_path / "metadata.tsv"
    path.write_text("sample\tcondition\ns1\tcontrol\ns2\tcontrol\n")

    issues = scan_metadata_table(path, analysis_type="rna_seq_differential_expression")

    assert any(issue.category == "insufficient_samples" for issue in issues)


def test_scan_metadata_tab_delimited_csv_parses(tmp_path: Path) -> None:
    path = tmp_path / "metadata.csv"
    path.write_text("sample\tcondition\ns1\tcontrol\ns2\ttreatment\n")

    issues = scan_metadata_table(path, analysis_type="rna_seq_differential_expression")

    assert any(issue.category == "delimiter_mismatch" for issue in issues)


def test_scan_metadata_duplicate_samples_errors(tmp_path: Path) -> None:
    path = tmp_path / "metadata.csv"
    path.write_text("sample,condition\ns1,control\ns1,treatment\n")

    issues = scan_metadata_table(path, analysis_type="rna_seq_differential_expression")

    assert any(issue.category == "duplicate_sample_ids" for issue in issues)


def test_scan_metadata_numeric_suffix_samples_are_not_collapsed(tmp_path: Path) -> None:
    path = tmp_path / "metadata.csv"
    path.write_text("sample,condition\nsample_0,control\nsample_1,treatment\n")

    issues = scan_metadata_table(path, analysis_type="rna_seq_differential_expression")

    assert not any(issue.category == "duplicate_sample_ids" for issue in issues)


def test_scan_plan_inputs_ignores_design_formula_strings(tmp_path: Path) -> None:
    counts = tmp_path / "counts.tsv"
    metadata = tmp_path / "metadata.tsv"
    counts.write_text("gene\ts1\ts2\nGeneA\t1\t2\n")
    metadata.write_text("sample\tcondition\ns1\tcontrol\ns2\ttreatment\n")

    result = scan_plan_inputs(
        {
            "plan": [
                {
                    "tool_name": "deseq2_run",
                    "step_id": 1,
                    "arguments": {
                        "counts_matrix": str(counts),
                        "metadata_table": str(metadata),
                        "design_formula": "~ condition",
                        "contrast": '["condition", "treatment", "control"]',
                    },
                }
            ]
        },
        tmp_path,
        analysis_type="rna_seq_differential_expression",
    )

    assert isinstance(result.issues, tuple)
    assert result.has_blocking is False
    assert not any(issue.category == "missing_required_column" for issue in result.issues)


def test_scan_plan_inputs_flags_empty_count_matrix_without_metadata_false_positive(tmp_path: Path) -> None:
    counts = tmp_path / "counts.tsv"
    metadata = tmp_path / "metadata.tsv"
    counts.write_text("gene\ts1\ts2\n")
    metadata.write_text("sample\tcondition\ns1\tcontrol\ns2\ttreatment\n")

    result = scan_plan_inputs(
        {
            "plan": [
                {
                    "tool_name": "deseq2_run",
                    "step_id": 1,
                    "arguments": {
                        "counts_matrix": str(counts),
                        "metadata_table": str(metadata),
                    },
                }
            ]
        },
        tmp_path,
        analysis_type="rna_seq_differential_expression",
    )

    assert result.has_blocking is True
    categories = {issue.category for issue in result.issues}
    assert "empty_file" in categories
    assert "missing_required_column" not in categories


def test_scan_plan_inputs_infers_proteomics_sample_column_from_abundance_headers(tmp_path: Path) -> None:
    abundance = tmp_path / "abundance_matrix.csv"
    metadata = tmp_path / "metadata.csv"
    abundance.write_text("protein,sample_0,sample_1\nPROT_1,10,12\n")
    metadata.write_text("id,group\nsample_0,control\nsample_1,treatment\n")

    result = scan_plan_inputs(
        {
            "plan": [
                {
                    "tool_name": "proteomics_diff_abundance",
                    "step_id": 1,
                    "arguments": {
                        "abundance_matrix": str(abundance),
                        "metadata_table": str(metadata),
                        "output_dir": str(tmp_path / "out"),
                    },
                }
            ]
        },
        tmp_path,
        analysis_type="proteomics",
    )

    categories = {issue.category for issue in result.issues}
    assert "missing_required_column" not in categories
    assert result.has_blocking is False


def test_scan_plan_inputs_infers_metabolomics_sample_column_from_feature_headers(tmp_path: Path) -> None:
    feature_table = tmp_path / "feature_table.csv"
    metadata = tmp_path / "metadata.csv"
    feature_table.write_text("feature,sample_0,sample_1\nM_1,10,12\n")
    metadata.write_text("id,group\nsample_0,control\nsample_1,treatment\n")

    result = scan_plan_inputs(
        {
            "plan": [
                {
                    "tool_name": "metabolomics_diff_abundance",
                    "step_id": 1,
                    "arguments": {
                        "feature_table": str(feature_table),
                        "metadata_table": str(metadata),
                        "output_dir": str(tmp_path / "out"),
                    },
                }
            ]
        },
        tmp_path,
        analysis_type="metabolomics",
    )

    categories = {issue.category for issue in result.issues}
    assert "missing_required_column" not in categories
    assert result.has_blocking is False


def test_scan_plan_inputs_treats_single_cell_whitelist_as_auxiliary_text(tmp_path: Path) -> None:
    r1 = tmp_path / "sample_R1.fastq"
    r2 = tmp_path / "sample_R2.fastq"
    whitelist = tmp_path / "barcodes_whitelist.txt"
    reference = tmp_path / "reference.fa"
    annotation = tmp_path / "annotation.gtf"
    r1.write_text(_FASTQ_TEXT)
    r2.write_text(_FASTQ_TEXT)
    whitelist.write_text("AAAAAAAAAAAAAAAA\nCCCCCCCCCCCCCCCC\n", encoding="utf-8")
    reference.write_text(">chr1\n" + ("ACGT" * 25) + "\n", encoding="utf-8")
    annotation.write_text("chr1\tsrc\tgene\t1\t10\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")

    result = scan_plan_inputs(
        {
            "plan": [
                {
                    "tool_name": "sc_count_and_cluster",
                    "step_id": 1,
                    "arguments": {
                        "reads_1": str(r1),
                        "reads_2": str(r2),
                        "whitelist": str(whitelist),
                        "reference_fasta": str(reference),
                        "annotation_gtf": str(annotation),
                        "output_dir": str(tmp_path / "out"),
                    },
                }
            ]
        },
        tmp_path,
        analysis_type="single_cell_rna_seq",
    )

    categories = {issue.category for issue in result.issues}
    assert "missing_required_column" not in categories
    assert result.has_blocking is False


def test_scan_plan_inputs_still_treats_generic_txt_metadata_as_metadata(tmp_path: Path) -> None:
    counts = tmp_path / "counts.tsv"
    metadata = tmp_path / "metadata.txt"
    counts.write_text("gene\ts1\ts2\nGeneA\t1\t2\n", encoding="utf-8")
    metadata.write_text("condition\ncontrol\ntreatment\n", encoding="utf-8")

    result = scan_plan_inputs(
        {
            "plan": [
                {
                    "tool_name": "deseq2_run",
                    "step_id": 1,
                    "arguments": {
                        "counts_matrix": str(counts),
                        "metadata_table": str(metadata),
                    },
                }
            ]
        },
        tmp_path,
        analysis_type="rna_seq_differential_expression",
    )

    categories = {issue.category for issue in result.issues}
    assert "missing_required_column" in categories
    assert result.has_blocking is True


def test_scan_bam_good_has_no_errors(tiny_bam: Path) -> None:
    issues = scan_bam_input(tiny_bam)

    assert not any(issue.severity == "error" for issue in issues)


def test_scan_sam_unsorted_warns(tmp_path: Path) -> None:
    path = tmp_path / "aligned.sam"
    path.write_text(_SAM_HEADER.replace("SO:coordinate", "SO:unsorted") + "\n" + _SAM_READS + "\n")

    issues = scan_bam_input(path)

    assert any(issue.category == "unsorted_bam" for issue in issues)


def test_scan_annotation_wrong_format_errors(tmp_path: Path) -> None:
    path = tmp_path / "annotation.gff"
    path.write_text("chr1\t100\t200\tgene1\t0\t+\n")

    issues = scan_annotation_input(path)

    assert any(issue.category == "format_mismatch" for issue in issues)


def test_scan_vcf_malformed_header_errors(tmp_path: Path) -> None:
    path = tmp_path / "variants.vcf"
    path.write_text("##contig=<ID=chr1,length=100>\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")

    issues = scan_vcf_input(path)

    assert any(issue.category == "malformed_header" for issue in issues)


def test_scan_fastq_format_error_detects_mismatched_quality(tmp_path: Path) -> None:
    path = tmp_path / "reads.fastq"
    path.write_text("@r1\nACGTACGT\n+\nIIII\n")

    issues = scan_fastq_input(path)

    assert any(issue.category == "fastq_format_error" for issue in issues)


def test_scan_fastq_adapter_contamination_warns(tmp_path: Path) -> None:
    path = tmp_path / "reads.fastq"
    path.write_text(
        "@r1\nAGATCGGAAGAGACGTACGT\n+\nIIIIIIIIIIIIIIIIIIII\n"
        "@r2\nAGATCGGAAGAGTGCATGCA\n+\nIIIIIIIIIIIIIIIIIIII\n"
    )

    issues = scan_fastq_input(path)

    assert any(issue.category == "adapter_contamination" for issue in issues)


def test_scan_fastq_unusual_quality_encoding_warns(tmp_path: Path) -> None:
    path = tmp_path / "reads.fastq"
    path.write_text("@r1\nACGTACGT\n+\nhhhhhhhh\n")

    issues = scan_fastq_input(path)

    assert any(issue.category == "unusual_quality_encoding" for issue in issues)


def test_scan_plan_inputs_extracts_and_scans(
    good_fastq: Path,
    good_metadata: Path,
    reference_fasta: Path,
    tmp_path: Path,
) -> None:
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "deseq2_run",
                "arguments": {
                    "reads_1": str(good_fastq),
                    "metadata_table": str(good_metadata),
                    "reference_fasta": str(reference_fasta),
                    "output_dir": str(tmp_path / "outputs"),
                },
            }
        ]
    }

    result = scan_plan_inputs(
        plan,
        data_root=tmp_path,
        selected_dir=tmp_path / "outputs",
        analysis_type="rna_seq_differential_expression",
    )

    assert result.has_blocking is False
    assert result.issues == ()


def test_scan_plan_inputs_extracts_vcf_from_bash_command_with_shell_operators(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    vcf = tmp_path / "variants.vcf"
    vcf.write_text("##contig=<ID=chr1,length=100>\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir} && "
                        f"bcftools view {vcf} -Oz -o {selected_dir / 'out.vcf.gz'} && "
                        f"bcftools query -f '%CHROM\\n' {selected_dir / 'out.vcf.gz'} > {selected_dir / 'summary.csv'}"
                    ),
                },
            }
        ]
    }

    result = scan_plan_inputs(
        plan,
        data_root=tmp_path,
        selected_dir=selected_dir,
    )

    assert result.has_blocking is True
    assert any(issue.category == "malformed_header" for issue in result.issues)
    assert all("mkdir -p" not in issue.path for issue in result.issues)


def test_scan_plan_inputs_extracts_relative_vcf_from_simple_bash_command(tmp_path: Path) -> None:
    vcf = tmp_path / "variants.vcf"
    vcf.write_text("##contig=<ID=chr1,length=100>\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": "python analyze_variants.py variants.vcf summary.csv",
                },
            }
        ]
    }

    result = scan_plan_inputs(plan, data_root=tmp_path)

    assert result.has_blocking is True
    assert any(issue.category == "malformed_header" for issue in result.issues)
    assert all("python analyze_variants.py" not in issue.path for issue in result.issues)


def test_scan_plan_inputs_detects_paired_count_mismatch(tmp_path: Path) -> None:
    r1 = tmp_path / "reads_R1.fastq"
    r1.write_text("@r1/1\nACGTACGTACGTACGTACGT\n+\nIIIIIIIIIIIIIIIIIIII\n@r2/1\nACGTACGTACGTACGTACGT\n+\nIIIIIIIIIIIIIIIIIIII\n")
    r2 = tmp_path / "reads_R2.fastq"
    r2.write_text("@r1/2\nACGTACGTACGTACGTACGT\n+\nIIIIIIIIIIIIIIIIIIII\n")
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "align",
                "arguments": {
                    "reads_1": str(r1),
                    "reads_2": str(r2),
                },
            }
        ]
    }

    result = scan_plan_inputs(plan, data_root=tmp_path)

    assert any(issue.category == "read_count_mismatch" for issue in result.issues)


def test_scan_plan_inputs_detects_reference_mismatch(tmp_path: Path) -> None:
    sam = tmp_path / "aligned.sam"
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        "@SQ\tSN:chr1\tLN:100\n"
        "read1\t0\tchr1\t1\t60\t8M\t*\t0\t0\tACGTACGT\tIIIIIIII\n"
    )
    fasta = tmp_path / "reference.fa"
    fasta.write_text(">chrX\n" + ("ACGT" * 25) + "\n")
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "align",
                "arguments": {
                    "input_bam": str(sam),
                    "reference_fasta": str(fasta),
                },
            }
        ]
    }

    result = scan_plan_inputs(plan, data_root=tmp_path)

    assert any(issue.category == "reference_mismatch" for issue in result.issues)
