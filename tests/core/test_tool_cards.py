from __future__ import annotations

from pathlib import Path

from bio_harness.core.tool_cards import (
    read_tool_card,
    render_tool_card,
    tool_card_from_draft,
    write_tool_card,
)


def _demo_draft() -> dict:
    return {
        "skill_name": "demo_align",
        "description": "Demo aligner command",
        "risk_level": "medium",
        "tools_required": ["star"],
        "capabilities": ["alignment"],
        "parameters": {
            "genome_dir": {
                "type": "path",
                "description": "Genome index.",
                "required": True,
                "file_role": "reference_genome",
            },
            "reads_1": {
                "type": "path",
                "description": "Read 1.",
                "required": True,
                "file_role": "input_fastq_r1",
            },
            "threads": {
                "type": "integer",
                "description": "Threads.",
                "required": False,
                "default": 8,
            },
        },
        "command_template": "STAR --genomeDir {genome_dir} --readFilesIn {reads_1}",
        "when_to_use": "Use for splice-aware RNA-seq alignment.",
        "when_not_to_use": "Do not use for transcript quantification only.",
        "output_types": ["bam", "sj.out.tab"],
    }


def test_tool_card_from_draft_splits_required_and_optional_arguments() -> None:
    card = tool_card_from_draft(
        _demo_draft(),
        source_meta={"source": "https://example.org/star", "mode": "official_docs"},
        version="2.7.11a",
    )

    assert card.name == "demo_align"
    assert card.version == "2.7.11a"
    assert card.canonical_tool_name == "star"
    assert list(card.capabilities) == ["alignment"]
    assert [arg.name for arg in card.required_args] == ["genome_dir", "reads_1"]
    assert [arg.name for arg in card.optional_args] == ["threads"]
    assert list(card.canonical_outputs) == ["bam", "sj.out.tab"]
    assert "https://example.org/star" in card.source_documents


def test_tool_card_roundtrip_preserves_fields(tmp_path: Path) -> None:
    card = tool_card_from_draft(_demo_draft())

    path = write_tool_card(card, tool_cards_dir=tmp_path / "tool_cards")
    loaded = read_tool_card(path)

    assert loaded == card


def test_render_tool_card_supports_progressive_disclosure() -> None:
    card = tool_card_from_draft(_demo_draft())

    l1 = render_tool_card(card, detail_level="l1")
    l2 = render_tool_card(card, detail_level="l2")
    full = render_tool_card(card, detail_level="full")

    assert "required_args" not in l1
    assert "safe_example" not in l1
    assert "required_args" in l2
    assert "safe_example" in l2
    assert "source_documents" not in l2
    assert "source_documents" in full


def test_tool_card_from_draft_merges_manual_summary() -> None:
    card = tool_card_from_draft(
        _demo_draft(),
        manual_summary={
            "when_to_use": "Manual override use case.",
            "when_not_to_use": "Manual override avoid case.",
            "canonical_outputs": ["counts.tsv"],
            "dangerous_flags": ["--force"],
            "common_errors": [{"pattern": "missing index", "cause": "index absent", "fix": "build the index"}],
            "example_invocations": ["STAR --genomeDir ref --readFilesIn reads.fastq"],
            "source_documents": ["https://example.org/manual"],
        },
    )

    assert "counts.tsv" in card.canonical_outputs
    assert list(card.dangerous_flags) == ["--force"]
    assert card.common_errors[0]["pattern"] == "missing index"
    assert "https://example.org/manual" in card.source_documents
