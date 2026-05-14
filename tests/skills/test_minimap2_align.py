from __future__ import annotations

import os
from pathlib import Path

import pytest

from bio_harness.skills.library.minimap2_align import minimap2_align


def test_minimap2_align_prefers_task_local_generated_artifacts(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    local_ref = output_dir / "viral_references_combined.fasta"
    local_r1 = output_dir / "trimmed_R1.fastq"
    local_r2 = output_dir / "trimmed_R2.fastq"
    local_ref.write_text(">virus\nACGT\n", encoding="utf-8")
    local_r1.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    local_r2.write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    old_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        command = minimap2_align(
            reference_fasta="/tmp/external/reference.fa",
            reads_1="/tmp/external/sample_R1.fastq.gz",
            reads_2="/tmp/external/sample_R2.fastq.gz",
            output_bam=str(output_dir / "aligned.bam"),
            preset="sr",
            threads=4,
        )
    finally:
        os.chdir(old_cwd)

    assert str(local_ref) in command
    assert str(local_r1) in command
    assert str(local_r2) in command
    assert "/tmp/external/reference.fa" not in command


def test_minimap2_align_preserves_current_run_inputs_when_already_local(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    local_ref = output_dir / "viral_references_combined.fasta"
    local_r1 = output_dir / "trimmed_R1.fastq"
    local_r2 = output_dir / "trimmed_R2.fastq"
    local_ref.write_text(">virus\nACGT\n", encoding="utf-8")
    local_r1.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    local_r2.write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    old_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        command = minimap2_align(
            reference_fasta=str(local_ref),
            reads_1=str(local_r1),
            reads_2=str(local_r2),
            output_bam=str(output_dir / "aligned.bam"),
            preset="sr",
            threads=4,
        )
    finally:
        os.chdir(old_cwd)

    assert str(local_ref) in command
    assert str(local_r1) in command
    assert str(local_r2) in command


def test_minimap2_align_prefers_execution_cwd_local_artifacts_without_process_chdir(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    local_ref = output_dir / "viral_references_combined.fasta"
    local_r1 = output_dir / "trimmed_R1.fastq"
    local_r2 = output_dir / "trimmed_R2.fastq"
    local_ref.write_text(">virus\nACGT\n", encoding="utf-8")
    local_r1.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    local_r2.write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    command = minimap2_align(
        execution_cwd=str(tmp_path),
        reference_fasta="/tmp/external/reference.fa",
        reads_1="/tmp/external/sample_R1.fastq.gz",
        reads_2="/tmp/external/sample_R2.fastq.gz",
        output_bam=str(output_dir / "aligned.bam"),
        preset="sr",
        threads=4,
    )

    assert str(local_ref) in command
    assert str(local_r1) in command
    assert str(local_r2) in command
    assert "/tmp/external/reference.fa" not in command


def test_minimap2_align_resolves_minimap2_and_samtools_from_shared_tool_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.minimap2_align.which_with_pixi",
        lambda name: {
            "minimap2": "/opt/tools/minimap2",
            "samtools": "/opt/tools/samtools",
        }.get(name),
    )
    command = minimap2_align(
        reference_fasta="/refs/reference.fa",
        reads_1="/reads/R1.fastq.gz",
        reads_2="/reads/R2.fastq.gz",
        output_bam="/out/aligned.bam",
        preset="sr",
        threads=2,
    )
    assert "/opt/tools/minimap2 -ax sr" in command
    assert "/opt/tools/samtools sort" in command
    assert "/opt/tools/samtools index" in command


def test_minimap2_align_allows_explicit_sam_output() -> None:
    command = minimap2_align(
        reference_fasta="/refs/MT-human.fa",
        reads="/refs/MT-orang.fa",
        output_bam="/out/orang_vs_human.sam",
        preset="asm5",
        threads=2,
    )

    assert "samtools sort" not in command
    assert "samtools index" not in command
    assert "/out/orang_vs_human.sam" in command


def test_minimap2_align_normalizes_pacbio_hifi_alias_to_map_hifi() -> None:
    command = minimap2_align(
        reference_fasta="/refs/reference.fa",
        reads="/reads/long.fastq.gz",
        output_bam="/out/aligned.bam",
        preset="hifi",
        threads=4,
    )

    assert "-ax map-hifi" in command
    assert "-ax hifi" not in command


def test_minimap2_align_normalizes_long_read_platform_aliases() -> None:
    ont_cmd = minimap2_align(
        reference_fasta="/refs/reference.fa",
        reads="/reads/ont.fastq.gz",
        output_bam="/out/ont.bam",
        preset="ont",
        threads=2,
    )
    pacbio_cmd = minimap2_align(
        reference_fasta="/refs/reference.fa",
        reads="/reads/pb.fastq.gz",
        output_bam="/out/pb.bam",
        preset="pacbio",
        threads=2,
    )

    assert "-ax map-ont" in ont_cmd
    assert "-ax map-pb" in pacbio_cmd
