from __future__ import annotations

from pathlib import Path

from bio_harness.ui.preflight import (
    data_root_has_sample_metadata,
    plan_requires_filename_group_tags,
    plan_requires_sample_groups,
)


def test_plan_requires_sample_groups_for_group_comparison_wrappers() -> None:
    plan = {"plan": [{"tool_name": "deseq2_run", "arguments": {"counts_matrix": "counts.tsv"}}]}

    assert plan_requires_sample_groups(plan) is True


def test_plan_requires_sample_groups_for_bash_group_selection_commands() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "select_sample_r1.sh reads S1 out CONTROL && select_sample_r1.sh reads S6 out TREATMENT"
                },
            }
        ]
    }

    assert plan_requires_sample_groups(plan) is True


def test_plan_requires_sample_groups_is_false_for_transcript_quant() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "salmon_quant",
                "arguments": {
                    "reads_1": "reads_1.fq.gz",
                    "reads_2": "reads_2.fq.gz",
                    "transcriptome_fasta": "transcriptome.fa",
                },
            }
        ]
    }

    assert plan_requires_sample_groups(plan) is False


def test_data_root_has_sample_metadata_detects_known_metadata_files(tmp_path: Path) -> None:
    (tmp_path / "sample_metadata.tsv").write_text("sample\tcondition\nS1\tcontrol\n", encoding="utf-8")

    assert data_root_has_sample_metadata(str(tmp_path)) is True


def test_plan_requires_filename_group_tags_is_false_when_metadata_exists(tmp_path: Path) -> None:
    (tmp_path / "sample_metadata.tsv").write_text("sample\tcondition\nS1\tcontrol\n", encoding="utf-8")
    plan = {"plan": [{"tool_name": "deseq2_run", "arguments": {"counts_matrix": "counts.tsv"}}]}

    assert plan_requires_filename_group_tags(plan, data_root=str(tmp_path)) is False
