from __future__ import annotations

from bio_harness.harness.contract_prompt_intent import (
    blocked_tool_hints_from_text,
    downstream_capability_hints_from_text,
    is_completed_output_report_prompt,
    required_tool_hints_from_text,
    strip_upstream_capabilities_for_direct_wrapper_prompt,
)


def test_blocked_tool_hints_from_text_detects_negated_pseudoalignment() -> None:
    blocked = blocked_tool_hints_from_text(
        "Do not pseudoalign with Salmon or Kallisto. Use stringtie_quant on the existing BAM.",
        ["stringtie_quant", "salmon", "kallisto"],
    )

    assert blocked == ["salmon", "kallisto"]


def test_blocked_tool_hints_from_text_detects_do_not_run_featurecounts() -> None:
    blocked = blocked_tool_hints_from_text(
        "Do not run featureCounts. Use DESeq2 on the provided count matrix and metadata.",
        ["deseq2_run", "featurecounts"],
    )

    assert blocked == ["featurecounts"]


def test_blocked_tool_hints_from_text_detects_do_not_rerun_featurecounts() -> None:
    blocked = blocked_tool_hints_from_text(
        (
            "Do not rerun alignment or featureCounts. Use the provided count matrix and metadata "
            "for the group comparison."
        ),
        ["deseq2_run", "featurecounts"],
    )

    assert blocked == ["featurecounts"]


def test_required_tool_hints_from_text_keeps_positive_wrapper_request() -> None:
    required = required_tool_hints_from_text(
        "Use stringtie_quant on the existing BAM with the supplied annotation.",
        ["stringtie_quant"],
    )

    assert required == ["stringtie_quant"]


def test_required_tool_hints_from_text_ignores_negated_featurecounts_clause() -> None:
    required = required_tool_hints_from_text(
        "Do not run featureCounts. Use DESeq2 on the provided count matrix and metadata.",
        ["deseq2_run", "featurecounts"],
    )

    assert required == ["deseq2_run"]


def test_downstream_capability_hints_from_text_marks_advisory_de_context() -> None:
    assert downstream_capability_hints_from_text(
        "Quantify transcripts now for downstream differential expression later."
    ) == ["differential_analysis", "group_comparison"]


def test_strip_upstream_capabilities_for_direct_wrapper_prompt_removes_alignment_for_count_matrix_de() -> None:
    trimmed = strip_upstream_capabilities_for_direct_wrapper_prompt(
        "Use only deseq2_run on /tmp/counts.tsv with metadata /tmp/meta.tsv. Do not align reads.",
        ["alignment", "differential_analysis", "quantification", "reference_inputs"],
        explicit_tools=["deseq2_run"],
        required_tools=["deseq2_run"],
    )

    assert trimmed == ["differential_analysis"]


def test_is_completed_output_report_prompt_detects_multiqc_bundle_request() -> None:
    assert is_completed_output_report_prompt(
        (
            "Build a MultiQC report bundle from the completed FastQC outputs in /tmp/run_001 "
            "and keep all generated files in the current run directory."
        )
    )
