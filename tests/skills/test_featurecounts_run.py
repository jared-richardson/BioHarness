from __future__ import annotations

import pytest

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.bash_single_operation_policy import check_single_operation
from bio_harness.skills.library.featurecounts_run import featurecounts_run


def test_featurecounts_run_splits_space_separated_bams():
    cmd = featurecounts_run(
        threads=4,
        annotation_gtf="/refs/genes.gtf",
        output_counts="/out/counts.txt",
        input_bams="/out/S1.bam /out/S6.bam",
    )
    assert "'/out/S1.bam /out/S6.bam'" not in cmd
    assert "/out/S1.bam" in cmd
    assert "/out/S6.bam" in cmd


def test_featurecounts_run_accepts_list_input_bams():
    cmd = featurecounts_run(
        threads=4,
        annotation_gtf="/refs/genes.gtf",
        output_counts="/out/counts.txt",
        input_bams=["/out/A.bam", "/out/B.bam"],
    )
    assert "/out/A.bam" in cmd
    assert "/out/B.bam" in cmd


def test_featurecounts_run_supports_paired_end_flags():
    cmd = featurecounts_run(
        threads=4,
        annotation_gtf="/refs/genes.gtf",
        output_counts="/out/counts.txt",
        input_bams="/out/S1.bam /out/S6.bam",
        is_paired_end=True,
    )
    assert "--paired-end" in cmd
    assert "--count-read-pairs" in cmd


def test_featurecounts_run_prepares_output_directory():
    cmd = featurecounts_run(
        threads=4,
        annotation_gtf="/refs/genes.gtf",
        output_counts="/out/counts.txt",
        input_bams="/out/S1.bam /out/S6.bam",
    )
    assert str(preferred_helper_python_executable()) in cmd
    assert "run_featurecounts.py" in cmd
    assert check_single_operation(cmd).passed is True


def test_featurecounts_run_switches_to_gff_mode_for_gff_annotations():
    cmd = featurecounts_run(
        threads=4,
        annotation_gtf="/refs/genes.gff",
        output_counts="/out/counts.txt",
        input_bams=["/out/A.bam", "/out/B.bam"],
        is_paired_end=True,
    )
    assert "--annotation-format GFF" in cmd


def test_featurecounts_run_uses_helper_wrapper_surface():
    cmd = featurecounts_run(
        threads=1,
        annotation_gtf="/refs/genes.gtf",
        output_counts="/out/counts.txt",
        input_bams="/out/S1.bam",
    )
    assert "run_featurecounts.py" in cmd
    assert "--threads 1" in cmd


def test_featurecounts_run_auto_detects_paired_end_with_single_bam():
    cmd = featurecounts_run(
        threads=1,
        annotation_gtf="/refs/genes.gtf",
        output_counts="/out/counts.txt",
        input_bams="/out/S1.bam",
    )
    assert "run_featurecounts.py" in cmd
    assert "--paired-end" not in cmd
    assert "--single-end" not in cmd
