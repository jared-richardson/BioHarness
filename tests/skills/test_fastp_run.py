import pytest

from bio_harness.skills.library.fastp_run import fastp_run


def test_fastp_run_renders_paired_end_command_with_reports():
    command = fastp_run(
        reads_1="reads_R1.fastq.gz",
        reads_2="reads_R2.fastq.gz",
        output_reads_1="workspace/trimmed/sample_R1.trim.fastq.gz",
        output_reads_2="workspace/trimmed/sample_R2.trim.fastq.gz",
        detect_adapter_for_pe=True,
        cut_front=True,
        cut_tail=True,
        correction=True,
        cut_mean_quality=20,
        length_required=30,
        threads=8,
        json_report="workspace/qc/sample.fastp.json",
        html_report="workspace/qc/sample.fastp.html",
    )

    assert command.startswith("mkdir -p ")
    assert "fastp" in command
    assert "--detect_adapter_for_pe" in command
    assert "--cut_front" in command
    assert "--cut_tail" in command
    assert "--correction" in command
    assert "--cut_mean_quality 20" in command
    assert "--length_required 30" in command
    assert "--thread 8" in command
    assert "--json workspace/qc/sample.fastp.json" in command
    assert "--html workspace/qc/sample.fastp.html" in command
    assert "-I reads_R2.fastq.gz" in command
    assert "-O workspace/trimmed/sample_R2.trim.fastq.gz" in command


def test_fastp_run_renders_single_end_command():
    command = fastp_run(
        reads_1="reads.fastq.gz",
        output_reads_1="workspace/trimmed/reads.trim.fastq.gz",
        adapter_sequence="AGATCGGAAGAGC",
        cut_right=True,
    )

    assert "-i reads.fastq.gz" in command
    assert "-o workspace/trimmed/reads.trim.fastq.gz" in command
    assert "--adapter_sequence AGATCGGAAGAGC" in command
    assert "--cut_right" in command
    assert "-I" not in command
    assert "--adapter_sequence_r2" not in command


def test_fastp_run_requires_paired_outputs_when_reads_2_present():
    with pytest.raises(ValueError, match="Paired-end fastp requires both reads_2 and output_reads_2"):
        fastp_run(
            reads_1="reads_R1.fastq.gz",
            reads_2="reads_R2.fastq.gz",
            output_reads_1="workspace/trimmed/sample_R1.trim.fastq.gz",
        )


def test_fastp_run_rejects_r2_adapter_without_paired_inputs():
    with pytest.raises(ValueError, match="Single-end fastp cannot accept adapter_sequence_r2"):
        fastp_run(
            reads_1="reads.fastq.gz",
            output_reads_1="workspace/trimmed/reads.trim.fastq.gz",
            adapter_sequence_r2="AGATCGGAAGAGC",
        )
