from __future__ import annotations

import pytest

from bio_harness.skills.library.cutadapt_run import cutadapt_run


def test_cutadapt_run_single_end_basic() -> None:
    command = cutadapt_run(
        reads_1="reads.fastq.gz",
        output_reads_1="trimmed/reads.trimmed.fastq.gz",
        adapter_3prime_r1="AGATCGGAAGAGC",
    )

    assert command == (
        "mkdir -p trimmed && "
        "cutadapt -a AGATCGGAAGAGC -o trimmed/reads.trimmed.fastq.gz reads.fastq.gz"
    )


def test_cutadapt_run_paired_end_with_reports_and_filters() -> None:
    command = cutadapt_run(
        reads_1="reads_R1.fastq.gz",
        reads_2="reads_R2.fastq.gz",
        output_reads_1="trimmed/reads_R1.trimmed.fastq.gz",
        output_reads_2="trimmed/reads_R2.trimmed.fastq.gz",
        adapter_3prime_r1=["AGATCGGAAGAGC", "CTGTCTCTTATA"],
        adapter_3prime_r2="AGATCGGAAGAGC",
        front_adapter_r1="TTTT",
        front_adapter_r2="CCCC",
        minimum_length=30,
        quality_cutoff="20,20",
        threads=8,
        discard_untrimmed=True,
        json_report="reports/cutadapt.json",
    )

    assert command.startswith("bash -c ")
    assert "cutadapt --help 2>&1 | grep -q -- --json" in command
    assert "--json reports/cutadapt.json" in command
    assert "json_not_supported" in command


def test_cutadapt_run_supports_json_encoded_adapter_lists() -> None:
    command = cutadapt_run(
        reads_1="reads.fastq.gz",
        output_reads_1="trimmed/reads.trimmed.fastq.gz",
        adapter_3prime_r1='["AGATCGGAAGAGC", "CTGTCTCTTATA"]',
    )

    assert "-a AGATCGGAAGAGC -a CTGTCTCTTATA" in command


def test_cutadapt_run_requires_adapter() -> None:
    with pytest.raises(ValueError, match="At least one adapter parameter"):
        cutadapt_run(
            reads_1="reads.fastq.gz",
            output_reads_1="trimmed/reads.trimmed.fastq.gz",
        )


def test_cutadapt_run_requires_complete_paired_end_outputs() -> None:
    with pytest.raises(ValueError, match="Paired-end cutadapt requires both reads_2 and output_reads_2"):
        cutadapt_run(
            reads_1="reads_R1.fastq.gz",
            reads_2="reads_R2.fastq.gz",
            output_reads_1="trimmed/reads_R1.trimmed.fastq.gz",
            adapter_3prime_r1="AGATCGGAAGAGC",
        )


def test_cutadapt_run_rejects_r2_adapters_for_single_end() -> None:
    with pytest.raises(ValueError, match="Single-end cutadapt cannot accept R2 adapter parameters"):
        cutadapt_run(
            reads_1="reads.fastq.gz",
            output_reads_1="trimmed/reads.trimmed.fastq.gz",
            adapter_3prime_r1="AGATCGGAAGAGC",
            adapter_3prime_r2="AGATCGGAAGAGC",
        )
