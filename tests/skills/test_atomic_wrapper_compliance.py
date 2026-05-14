from __future__ import annotations

from bio_harness.core.bash_single_operation_policy import check_single_operation
from bio_harness.skills.library.bcftools_filter_run import bcftools_filter_run
from bio_harness.skills.library.bcftools_call import bcftools_call
from bio_harness.skills.library.featurecounts_run import featurecounts_run
from bio_harness.skills.library.freebayes_call import freebayes_call


def test_atomic_wrapper_surface_for_bcftools_filter_run() -> None:
    command = bcftools_filter_run(
        input_vcf="/tmp/sample_raw.vcf.gz",
        output_vcf="/tmp/sample_filtered.vcf.gz",
        filter_expression="QUAL > 1",
    )

    assert "run_bcftools_filter.py" in command
    assert check_single_operation(command).passed is True


def test_atomic_wrapper_surface_for_bcftools_call() -> None:
    command = bcftools_call(
        reference_fasta="/refs/genome.fa",
        input_bam="/tmp/sample.bam",
        output_vcf_gz="/tmp/out/sample.vcf.gz",
    )

    assert "run_bcftools_call.py" in command
    assert check_single_operation(command).passed is True


def test_atomic_wrapper_surface_for_freebayes_call() -> None:
    command = freebayes_call(
        reference_fasta="/refs/genome.fa",
        input_bam="/tmp/sample.bam",
        output_vcf="/tmp/out/sample.vcf",
    )

    assert "run_freebayes_call.py" in command
    assert check_single_operation(command).passed is True


def test_atomic_wrapper_surface_for_featurecounts_run() -> None:
    command = featurecounts_run(
        threads=2,
        annotation_gtf="/refs/genes.gtf",
        output_counts="/tmp/out/counts.tsv",
        input_bams=["/tmp/A.bam", "/tmp/B.bam"],
    )

    assert "run_featurecounts.py" in command
    assert check_single_operation(command).passed is True
