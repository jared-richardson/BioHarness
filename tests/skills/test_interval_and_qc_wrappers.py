from __future__ import annotations

import pytest

from bio_harness.skills.library.bedtools_coverage import bedtools_coverage
from bio_harness.skills.library.bedtools_genomecov import bedtools_genomecov
from bio_harness.skills.library.bedtools_intersect import bedtools_intersect
from bio_harness.skills.library.samtools_flagstat import samtools_flagstat
from bio_harness.skills.library.samtools_idxstats import samtools_idxstats
from bio_harness.skills.library.samtools_stats import samtools_stats


def test_bedtools_intersect_renders_expected_command() -> None:
    cmd = bedtools_intersect(
        a_intervals="/tmp/a.bed",
        b_intervals="/tmp/b.bed",
        output_file="/tmp/intervals/intersect.tsv",
        report_mode="wao",
        sorted_input=True,
        min_overlap_fraction=0.5,
        require_reciprocal_overlap=True,
    )

    assert "set -euo pipefail;" in cmd
    assert "mkdir -p /tmp/intervals;" in cmd
    assert "intersect -a /tmp/a.bed -b /tmp/b.bed -wao -sorted -f 0.5 -r > /tmp/intervals/intersect.tsv" in cmd


def test_bedtools_intersect_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported bedtools intersect report_mode"):
        bedtools_intersect(
            a_intervals="/tmp/a.bed",
            b_intervals="/tmp/b.bed",
            output_file="/tmp/out.tsv",
            report_mode="invalid",
        )


def test_bedtools_coverage_renders_expected_command() -> None:
    cmd = bedtools_coverage(
        a_intervals="/tmp/windows.bed",
        b_features="/tmp/sample.bam",
        output_tsv="/tmp/coverage/summary.tsv",
        counts_only=True,
        split_alignments=True,
        sorted_input=True,
    )

    assert "set -euo pipefail;" in cmd
    assert "mkdir -p /tmp/coverage;" in cmd
    assert "coverage -a /tmp/windows.bed -b /tmp/sample.bam -counts -split -sorted > /tmp/coverage/summary.tsv" in cmd


def test_bedtools_genomecov_renders_bam_mode_command() -> None:
    cmd = bedtools_genomecov(
        input_bam="/tmp/sample.bam",
        output_file="/tmp/coverage/sample.bedgraph",
        report_mode="bedgraph_all",
        split_intervals=True,
    )

    assert "set -euo pipefail;" in cmd
    assert "mkdir -p /tmp/coverage;" in cmd
    assert "genomecov -ibam /tmp/sample.bam -bga -split > /tmp/coverage/sample.bedgraph" in cmd


def test_bedtools_genomecov_renders_interval_mode_command() -> None:
    cmd = bedtools_genomecov(
        input_bed="/tmp/intervals.bed",
        genome_file="/tmp/genome.sizes",
        output_file="/tmp/coverage/profile.txt",
        report_mode="histogram",
        strand="+",
    )

    assert "genomecov -i /tmp/intervals.bed -g /tmp/genome.sizes -strand + > /tmp/coverage/profile.txt" in cmd


def test_bedtools_genomecov_requires_exactly_one_input_mode() -> None:
    with pytest.raises(ValueError, match="Provide exactly one of input_bam or input_bed"):
        bedtools_genomecov(
            input_bam="/tmp/sample.bam",
            input_bed="/tmp/intervals.bed",
            output_file="/tmp/out.txt",
        )


def test_samtools_flagstat_renders_expected_command() -> None:
    cmd = samtools_flagstat(
        input_bam="/tmp/sample.bam",
        output_txt="/tmp/qc/flagstat.txt",
        threads=4,
    )

    assert "set -euo pipefail;" in cmd
    assert "mkdir -p /tmp/qc;" in cmd
    assert "flagstat -@ 4 /tmp/sample.bam > /tmp/qc/flagstat.txt" in cmd


def test_samtools_idxstats_renders_expected_command() -> None:
    cmd = samtools_idxstats(
        input_bam="/tmp/sample.bam",
        output_tsv="/tmp/qc/idxstats.tsv",
    )

    assert "set -euo pipefail;" in cmd
    assert "mkdir -p /tmp/qc;" in cmd
    assert "idxstats /tmp/sample.bam > /tmp/qc/idxstats.tsv" in cmd


def test_samtools_stats_renders_expected_command() -> None:
    cmd = samtools_stats(
        input_bam="/tmp/sample.cram",
        reference_fasta="/tmp/ref.fa",
        output_txt="/tmp/qc/stats.txt",
        threads=3,
    )

    assert "set -euo pipefail;" in cmd
    assert "mkdir -p /tmp/qc;" in cmd
    assert "stats -@ 3 -r /tmp/ref.fa /tmp/sample.cram > /tmp/qc/stats.txt" in cmd
