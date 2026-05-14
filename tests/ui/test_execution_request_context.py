from __future__ import annotations

from bio_harness.ui.execution_request_context import (
    build_execution_request_context,
    strip_execution_preamble,
)


def test_strip_execution_preamble_keeps_specific_instruction() -> None:
    assert (
        strip_execution_preamble(
            "Proceed with execution now. Build a MultiQC report bundle from the completed outputs."
        )
        == "Build a MultiQC report bundle from the completed outputs."
    )


def test_build_execution_request_context_uses_recent_user_messages_only() -> None:
    snapshot = {
        "messages": [
            {"role": "user", "content": "Quantify transcripts from /tmp/sample.bam using /tmp/genes.gtf."},
            {"role": "assistant", "content": "Execution requested. Starting now.\n- Data root: `/tmp`"},
            {"role": "user", "content": "Proceed with execution now."},
        ]
    }

    rendered = build_execution_request_context(snapshot, "Proceed with execution now.")

    assert "Recent user instructions:" in rendered
    assert "Quantify transcripts from /tmp/sample.bam using /tmp/genes.gtf." in rendered
    assert "Execution requested. Starting now." not in rendered
    assert "Data root" not in rendered


def test_build_execution_request_context_returns_latest_instruction_when_no_history() -> None:
    rendered = build_execution_request_context({}, "Proceed with execution now. Use DESeq2 on /tmp/counts.tsv.")

    assert rendered == "Use DESeq2 on /tmp/counts.tsv."
