from __future__ import annotations

from pathlib import Path

import bio_harness.skills.library._star_support as star_support_module
from bio_harness.skills.library.star_2pass_align import star_2pass_align
from bio_harness.skills.library.star_align import star_align
from bio_harness.skills.library.star_solo_count import star_solo_count


def test_star_align_omits_readfilescommand_for_plain_fastq():
    cmd = star_align(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq",
        reads_2="/data/S1_R2.fastq",
        output_prefix="/out/S1_",
    )
    assert "--readFilesCommand" not in cmd
    assert "--outSAMtype BAM Unsorted" in cmd


def test_star_align_keeps_readfilescommand_for_gz_fastq():
    cmd = star_align(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_prefix="/out/S1_",
    )
    assert "--readFilesCommand" not in cmd
    assert "gunzip -c" in cmd
    assert "/out/_cache/star_reads" in cmd
    assert "--outSAMtype BAM Unsorted" in cmd


def test_star_align_supports_quant_mode_and_annotation_gtf():
    cmd = star_align(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq",
        reads_2="/data/S1_R2.fastq",
        annotation_gtf="/refs/genes.gtf",
        quant_mode="GeneCounts",
        output_prefix="/out/S1_",
    )
    assert "--sjdbGTFfile /refs/genes.gtf" in cmd
    assert "--quantMode GeneCounts" in cmd
    assert 'counts="${prefix}ReadsPerGene.out.tab"' in cmd


def test_star_align_prefers_repo_pixi_star_bin(monkeypatch):
    repo_pixi_star = (
        Path(__file__).resolve().parents[2]
        / ".pixi"
        / "envs"
        / "default"
        / "bin"
        / "STAR"
    )
    repo_pixi_star_real = repo_pixi_star.resolve()

    def fake_isfile(path: str) -> bool:
        return Path(path).resolve() == repo_pixi_star_real or path == "/usr/local/bin/STAR"

    monkeypatch.setattr(star_support_module.os.path, "isfile", fake_isfile)
    monkeypatch.setattr(star_support_module.os, "access", lambda path, mode: fake_isfile(path))
    monkeypatch.setattr(
        star_support_module.shutil,
        "which",
        lambda name: "/usr/local/bin/STAR" if name == "STAR" else "",
    )
    monkeypatch.setattr(star_support_module, "which_with_pixi", lambda name: "")
    monkeypatch.delenv("BIO_HARNESS_STAR_BIN", raising=False)

    cmd = star_align(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq",
        reads_2="/data/S1_R2.fastq",
        output_prefix="/out/S1_",
    )
    assert str(repo_pixi_star_real) in cmd or str(repo_pixi_star) in cmd


def test_star_2pass_and_solo_adapt_to_plain_fastq():
    cmd_2pass = star_2pass_align(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq",
        reads_2="/data/S1_R2.fastq",
        output_prefix="/out/S1_",
    )
    cmd_solo = star_solo_count(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq",
        reads_2="/data/S1_R2.fastq",
        whitelist="/refs/wl.txt",
        output_prefix="/out/solo_",
    )
    assert "--readFilesCommand" not in cmd_2pass
    assert "--outSAMtype BAM Unsorted" in cmd_2pass
    assert "--readFilesCommand" not in cmd_solo


def test_star_2pass_creates_output_prefix_parent():
    cmd = star_2pass_align(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq",
        reads_2="/data/S1_R2.fastq",
        output_prefix="/out/star2/sample_",
    )
    assert "mkdir -p /out/star2" in cmd


def test_star_wrappers_use_resolved_absolute_read_command(monkeypatch):
    cmd_align = star_align(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_prefix="/out/S1_",
    )
    cmd_2pass = star_2pass_align(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        output_prefix="/out/S1_",
    )
    cmd_solo = star_solo_count(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        whitelist="/refs/wl.txt",
        output_prefix="/out/solo_",
    )

    assert "--readFilesCommand" not in cmd_align
    assert "--readFilesCommand" not in cmd_2pass
    assert "--readFilesCommand" not in cmd_solo
    assert "/out/_cache/star_reads" in cmd_align
    assert "/out/_cache/star_reads" in cmd_2pass
    assert "/out/_cache/star_reads" in cmd_solo
    assert "gunzip -c" in cmd_align
    assert "gunzip -c" in cmd_2pass
    assert "gunzip -c" in cmd_solo


def test_star_solo_can_build_index_from_reference_bundle():
    cmd = star_solo_count(
        threads=4,
        genome_dir="/out/star_index",
        reference_fasta="/refs/genome.fa",
        annotation_gtf="/refs/genes.gtf",
        star_index_cache_root="/out/_cache/star_indexes",
        sjdb_overhang=90,
        reads_1="/data/R1.fastq.gz",
        reads_2="/data/R2.fastq.gz",
        whitelist="/refs/whitelist.txt",
        output_prefix="/out/solo/sample_",
    )
    assert "build_star_index.sh /out/star_index /refs/genome.fa /refs/genes.gtf 4 /out/_cache/star_indexes 90" in cmd
    assert "gunzip -c" in cmd
    assert "/out/solo/_cache/star_reads" in cmd
    assert "--genomeDir /out/star_index" in cmd
    assert "--outSAMtype BAM Unsorted" in cmd
    assert 'matrix="${prefix}Solo.out/Gene/raw/matrix.mtx"' in cmd


def test_star_align_can_build_index_from_reference_bundle():
    cmd = star_align(
        threads=4,
        genome_dir="/out/star_index",
        reference_fasta="/refs/genome.fa",
        annotation_gtf="/refs/genes.gtf",
        star_index_cache_root="/out/_cache/star_indexes",
        sjdb_overhang=90,
        reads_1="/data/R1.fastq.gz",
        reads_2="/data/R2.fastq.gz",
        output_prefix="/out/aln/sample_",
    )
    assert "build_star_index.sh /out/star_index /refs/genome.fa /refs/genes.gtf 4 /out/_cache/star_indexes 90" in cmd
    assert "gunzip -c" in cmd
    assert "/out/aln/_cache/star_reads" in cmd
    assert "--genomeDir /out/star_index" in cmd


def test_star_2pass_can_build_index_from_reference_bundle():
    cmd = star_2pass_align(
        threads=4,
        genome_dir="/out/star_index",
        reference_fasta="/refs/genome.fa",
        annotation_gtf="/refs/genes.gtf",
        star_index_cache_root="/out/_cache/star_indexes",
        sjdb_overhang=90,
        reads_1="/data/R1.fastq.gz",
        reads_2="/data/R2.fastq.gz",
        output_prefix="/out/aln/sample_",
    )
    assert "build_star_index.sh /out/star_index /refs/genome.fa /refs/genes.gtf 4 /out/_cache/star_indexes 90" in cmd
    assert "gunzip -c" in cmd
    assert "/out/aln/_cache/star_reads" in cmd
    assert "--genomeDir /out/star_index" in cmd
    assert "--twopassMode Basic" in cmd


def test_star_solo_reuses_existing_outputs():
    cmd = star_solo_count(
        threads=2,
        genome_dir="/refs/star_idx",
        reads_1="/data/S1_R1.fastq.gz",
        reads_2="/data/S1_R2.fastq.gz",
        whitelist="/refs/wl.txt",
        output_prefix="/out/solo_",
    )
    assert "matrix=\"${prefix}Solo.out/Gene/raw/matrix.mtx\"" in cmd
    assert "barcodes=\"${prefix}Solo.out/Gene/raw/barcodes.tsv\"" in cmd
    assert "features=\"${prefix}Solo.out/Gene/raw/features.tsv\"" in cmd
