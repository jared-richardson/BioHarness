from __future__ import annotations

from bio_harness.workflows.template_command_rewrites import (
    extract_manifest_redirect,
    extract_star_genomegenerate,
    normalize_rmats_command,
    rewrite_rmats_to_wrapper,
    rewrite_star_alignreads_command,
    strip_destructive_segments,
)


def test_strip_destructive_segments_removes_cache_cleanup() -> None:
    command, removed = strip_destructive_segments(
        "rm -rf outputs/_cache/star_indexes ; STAR --runMode alignReads --genomeDir ref"
    )

    assert removed == ["rm -rf outputs/_cache/star_indexes"]
    assert command == "STAR --runMode alignReads --genomeDir ref"


def test_extract_star_genomegenerate_parses_required_flags() -> None:
    parsed = extract_star_genomegenerate(
        "STAR --runMode genomeGenerate --genomeDir idx --genomeFastaFiles ref.fa "
        "--sjdbGTFfile genes.gtf --runThreadN 8 --sjdbOverhang 99"
    )

    assert parsed == {
        "genome_dir": "idx",
        "fasta": "ref.fa",
        "gtf": "genes.gtf",
        "threads": "8",
        "sjdb_overhang": "99",
    }


def test_rewrite_star_alignreads_command_removes_readfilescommand_for_plain_fastq() -> None:
    rewritten, changed = rewrite_star_alignreads_command(
        "STAR --genomeDir idx --readFilesIn sample_R1.fastq sample_R2.fastq --readFilesCommand zcat"
    )

    assert changed is True
    assert "--readFilesCommand" not in rewritten


def test_normalize_rmats_command_adds_paired_defaults() -> None:
    rewritten, changed = normalize_rmats_command(
        "rmats.py --paired-end --b1 control.txt --b2 treatment.txt --gtf genes.gtf --od out --tmp tmp"
    )

    assert changed is True
    assert "-t paired" in rewritten
    assert "--readLength 150" in rewritten


def test_rewrite_rmats_to_wrapper_supports_inline_bam_lists() -> None:
    rewritten, changed = rewrite_rmats_to_wrapper(
        "rmats.py --b1 control1.bam,control2.bam --b2 treatment1.bam,treatment2.bam "
        "--gtf genes.gtf --od out --tmp tmp --readLength 101 --nthread 6"
    )

    assert changed is True
    assert "run_rmats_if_needed.sh" in rewritten
    assert "control_bams.auto.txt" in rewritten
    assert "treatment_bams.auto.txt" in rewritten


def test_extract_manifest_redirect_finds_fastq_manifest_target() -> None:
    redirect = extract_manifest_redirect("find data -name '*.fastq.gz' > outputs/fastq_manifest.txt")

    assert redirect == "outputs/fastq_manifest.txt"
