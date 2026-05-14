"""Tests for the atomic Prodigal annotation helper."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bio_harness.pipeline_scripts import run_prodigal_annotate as helper


def test_resolve_prodigal_mode_uses_meta_for_short_contigs() -> None:
    assert helper.resolve_prodigal_mode("auto", sequence_bases=2_000) == "meta"
    assert helper.resolve_prodigal_mode("auto", sequence_bases=25_000) == "single"
    assert helper.resolve_prodigal_mode("metagenomic", sequence_bases=25_000) == "meta"


def test_run_prodigal_annotate_auto_mode_builds_meta_command_for_short_fasta(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fasta = tmp_path / "contigs.fa"
    output_gff = tmp_path / "annotation" / "genes.gff"
    output_faa = tmp_path / "annotation" / "proteins.faa"
    fasta.write_text(">ctg\n" + "A" * 2_000 + "\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess:
        commands.append(command)
        output_gff.write_text(
            "##gff-version 3\nctg\tProdigal\tCDS\t1\t300\t.\t+\t0\tID=1_1\n",
            encoding="utf-8",
        )
        output_faa.write_text(">1_1\nMKK\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(helper, "which_with_pixi", lambda name: "/opt/bin/prodigal")
    monkeypatch.setattr(helper.subprocess, "run", fake_run)

    exit_code = helper.run_prodigal_annotate(
        input_fasta=fasta,
        output_gff=output_gff,
        output_faa=output_faa,
    )

    assert exit_code == 0
    assert commands
    assert commands[0][-2:] == ["-p", "meta"]


def test_run_prodigal_annotate_rejects_empty_predictions_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fasta = tmp_path / "contigs.fa"
    output_gff = tmp_path / "annotation" / "genes.gff"
    output_faa = tmp_path / "annotation" / "proteins.faa"
    fasta.write_text(">ctg\n" + "A" * 2_000 + "\n", encoding="utf-8")

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess:
        output_gff.parent.mkdir(parents=True, exist_ok=True)
        output_gff.write_text("##gff-version 3\n", encoding="utf-8")
        output_faa.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(helper, "which_with_pixi", lambda name: "/opt/bin/prodigal")
    monkeypatch.setattr(helper.subprocess, "run", fake_run)

    exit_code = helper.run_prodigal_annotate(
        input_fasta=fasta,
        output_gff=output_gff,
        output_faa=output_faa,
    )

    assert exit_code == helper.EXIT_EMPTY_PREDICTIONS
