from __future__ import annotations

from pathlib import Path

from bio_harness.workflows.template_io_support import (
    bash_output_hints_for_command,
    normalize_structured_argument_aliases,
    pick_reference_file,
    repair_alignment_dependent_bam_input,
    repair_featurecounts_input_bams,
)


def test_normalize_structured_argument_aliases_maps_read_aliases() -> None:
    normalized, changed = normalize_structured_argument_aliases(
        "star_align",
        {"read1": "/tmp/a_R1.fastq.gz", "read2": "/tmp/a_R2.fastq.gz"},
    )

    assert changed is True
    assert normalized["reads_1"] == "/tmp/a_R1.fastq.gz"
    assert normalized["reads_2"] == "/tmp/a_R2.fastq.gz"


def test_bash_output_hints_for_command_captures_helper_script_and_output_dir() -> None:
    output_paths, output_roots = bash_output_hints_for_command(
        "python3 pipeline_scripts/normalize_gff_for_featurecounts.py refs/in.gff refs/out.gff ; "
        "python3 helper.py --output-dir outputs/final"
    )

    assert [str(path) for path in output_paths] == ["refs/out.gff"]
    assert [str(path) for path in output_roots] == ["outputs/final"]


def test_pick_reference_file_prefers_alias_under_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    data_root = workspace / "task" / "data"
    data_root.mkdir(parents=True)
    alias = workspace / "inputs_readonly" / "mouse_gtf"
    alias.parent.mkdir(parents=True)
    alias.write_text("gtf\n", encoding="utf-8")

    picked = pick_reference_file("missing.gtf", kind="gtf", data_root=str(data_root))

    assert picked == str(alias.resolve(strict=False))


def test_repair_featurecounts_input_bams_uses_alignment_hints() -> None:
    repaired, changed = repair_featurecounts_input_bams(
        "outputs/star/S1.Aligned.out.bam outputs/star/S2.Aligned.out.bam",
        alignment_bam_hints=[
            "/tmp/workspace/outputs/star/S1Aligned.out.bam",
            "/tmp/workspace/outputs/star/S2Aligned.out.bam",
        ],
    )

    assert changed is True
    assert "/tmp/workspace/outputs/star/S1Aligned.out.bam" in repaired
    assert "/tmp/workspace/outputs/star/S2Aligned.out.bam" in repaired


def test_repair_alignment_dependent_bam_input_prefers_unique_sample_match() -> None:
    repaired, changed = repair_alignment_dependent_bam_input(
        "/tmp/run/tumor/tumor.bam",
        alignment_bam_hints=[
            "/tmp/run/tumor/tumor_aligned.bam",
            "/tmp/run/normal/normal_sorted.bam",
        ],
        sample_tokens=("tumor",),
    )

    assert changed is True
    assert repaired == "/tmp/run/tumor/tumor_aligned.bam"
