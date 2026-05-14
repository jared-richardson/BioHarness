from __future__ import annotations

from pathlib import Path

from bio_harness.ui.data_root import (
    discovery_root_for_path,
    latest_path_hints_from_messages,
    select_preferred_latest_root,
)


def test_discovery_root_for_path_keeps_directories(tmp_path: Path) -> None:
    assert discovery_root_for_path(tmp_path) == tmp_path


def test_discovery_root_for_path_uses_parent_for_files(tmp_path: Path) -> None:
    file_path = tmp_path / "transcriptome.fa"
    file_path.write_text(">tx1\nACGT\n", encoding="utf-8")

    assert discovery_root_for_path(file_path) == tmp_path


def test_select_preferred_latest_root_keeps_latest_explicit_path_even_without_fastq(
    tmp_path: Path,
) -> None:
    fasta_path = tmp_path / "sequences.fasta"
    fasta_path.write_text(">a\nACGT\n", encoding="utf-8")

    selected, count = select_preferred_latest_root(
        [fasta_path],
        fastq_counter=lambda _: 0,
    )

    assert selected == tmp_path
    assert count == 0


def test_latest_path_hints_from_messages_prefers_most_recent_path_bearing_user_turn() -> None:
    messages = [
        {"role": "user", "content": "Use workspace/benchmarks/task_a/input.fa"},
        {"role": "assistant", "content": "I can do that."},
        {"role": "user", "content": "Proceed."},
    ]

    selected = latest_path_hints_from_messages(
        messages,
        extractor=lambda text: [text] if "/" in text else [],
    )

    assert selected == ["Use workspace/benchmarks/task_a/input.fa"]
