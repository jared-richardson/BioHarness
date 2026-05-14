from __future__ import annotations

from bio_harness.skills.library import featurecounts_run as featurecounts_module
from bio_harness.skills.library import freebayes_call as freebayes_module
from bio_harness.skills.library import methylation_bismark_style as bismark_module
from bio_harness.skills.library import vep_annotate as vep_module


def test_freebayes_compressed_output_requires_bgzip_and_tabix(monkeypatch):
    monkeypatch.setattr(freebayes_module, "which_with_pixi", lambda name: None)
    monkeypatch.setattr(freebayes_module.shutil, "which", lambda name: None)

    try:
        freebayes_module.freebayes_call(
            reference_fasta="/tmp/ref.fa",
            input_bam="/tmp/sample.bam",
            output_vcf_gz="/tmp/sample.vcf.gz",
        )
    except ValueError as exc:
        assert "requires helper tool 'bgzip'" in str(exc)
    else:  # pragma: no cover - defensive failure path
        raise AssertionError("Expected compressed freebayes path to require bgzip.")


def test_vep_custom_mode_requires_bgzip_and_tabix(monkeypatch):
    monkeypatch.setattr(vep_module, "which_with_pixi", lambda name: "/usr/bin/vep" if name == "vep" else None)
    monkeypatch.setattr(vep_module.shutil, "which", lambda name: None)

    try:
        vep_module.vep_annotate(
            input_vcf="/tmp/in.vcf",
            output_vcf="/tmp/out.vcf",
            reference_fasta="/tmp/ref.fa",
            annotation_gff="/tmp/genes.gff3",
        )
    except ValueError as exc:
        assert "requires helper tool 'bgzip'" in str(exc)
    else:  # pragma: no cover - defensive failure path
        raise AssertionError("Expected custom VEP path to require bgzip.")


def test_bismark_wrapper_includes_helper_binary_checks():
    command = bismark_module.methylation_bismark_style(
        reads_1="/tmp/S1_R1.fastq.gz",
        reads_2="/tmp/S1_R2.fastq.gz",
        genome_folder="/tmp/genome",
        output_dir="/tmp/out",
        output_report="/tmp/out/report.tsv",
    )

    assert "Missing helper binary: bismark_genome_preparation" in command
    assert "Missing helper binary: bowtie2-build" in command
    assert "Missing helper binary: samtools" in command


def test_featurecounts_single_bam_falls_back_cleanly_without_samtools(monkeypatch):
    command = featurecounts_module.featurecounts_run(
        annotation_gtf="/tmp/genes.gtf",
        input_bams="/tmp/sample.bam",
        output_counts="/tmp/counts.tsv",
        threads=2,
    )

    assert "run_featurecounts.py" in command
    assert "samtools" not in command
