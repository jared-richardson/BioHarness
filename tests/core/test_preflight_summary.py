"""Tests for standardized preflight-summary reporting."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.input_quality import InputIssue
from bio_harness.core.preflight_summary import (
    build_preflight_summary,
    preflight_summary_from_json,
    preflight_summary_to_json,
    preflight_summary_to_markdown,
)


def test_build_preflight_summary_uses_persisted_input_quality_and_resource_warnings(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Persisted input-quality state should be preferred in report summaries."""

    monkeypatch.setattr(
        "bio_harness.core.preflight_summary.assess_resource_preflight",
        lambda tool_names, selected_dir: {
            "selected_dir": str(selected_dir),
            "skill_names": list(tool_names),
            "skills_found": ["salmon_quant"],
            "missing_skills": [],
            "requirements": {
                "min_ram_gb": 8.0,
                "min_cores": 4,
                "min_free_disk_gb": 20.0,
                "estimated_free_disk_gb": 20.0,
            },
            "system": {
                "available_mem_gb": 6.0,
                "available_cores": 8,
                "free_disk_gb": 200.0,
            },
            "disk_estimate": {},
            "warnings": ["available memory 6.00 GiB is below required minimum 8.00 GiB"],
            "ok": False,
        },
    )

    summary = build_preflight_summary(
        {
            "plan": [
                {
                    "tool_name": "salmon_quant",
                    "arguments": {"reads_1": "sample_R1.fastq.gz"},
                }
            ]
        },
        selected_dir=tmp_path,
        analysis_type="transcript_quantification",
        persisted_input_quality={
            "has_blocking": False,
            "summary": "Detected 1 input issue(s); blocking=false.",
            "issues": [
                {
                    "path": "/tmp/sample_R1.fastq.gz",
                    "severity": "warning",
                    "category": "short_reads",
                    "message": "FASTQ contains very short reads.",
                    "suggestion": "Confirm the assay supports short reads.",
                }
            ],
        },
    )

    assert summary.input_scan_source == "persisted"
    assert summary.resource_report_source == "estimated"
    assert summary.recommendation == "review_before_run"
    payload = preflight_summary_to_json(summary)
    assert payload["tool_names"] == ["salmon_quant"]
    markdown = preflight_summary_to_markdown(summary)
    assert "short_reads" in markdown
    assert "Resource Warnings" in markdown


def test_build_preflight_summary_rescans_when_data_root_is_available(tmp_path: Path) -> None:
    """Report bundles should be able to reproduce an input scan when needed."""

    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    fastq = tmp_path / "sample_R1.fastq"
    fastq.write_text(
        "@read1\nACGTACGTACGTACGTACGT\n+\nIIIIIIIIIIIIIIIIIIII\n"
        "@read2\nTTTTAAAACCCCGGGGTTTT\n+\nIIIIIIIIIIIIIIIIIIII\n",
        encoding="utf-8",
    )

    summary = build_preflight_summary(
        {
            "plan": [
                {
                    "tool_name": "salmon_quant",
                    "arguments": {
                        "reads_1": str(fastq),
                    },
                }
            ]
        },
        selected_dir=selected_dir,
        data_root=tmp_path.parent,
        analysis_type="transcript_quantification",
    )

    assert summary.input_scan_source == "rescanned"
    assert summary.input_scan is not None
    assert summary.input_scan.issues == ()
    assert summary.recommendation == "proceed"


def test_build_preflight_summary_marks_do_not_start_on_blocking_input_issue(tmp_path: Path) -> None:
    """Blocking input findings should produce a do-not-start recommendation."""

    summary = build_preflight_summary(
        {"plan": [{"tool_name": "star_align", "arguments": {"reads_1": "missing.fastq.gz"}}]},
        selected_dir=tmp_path,
        analysis_type="rna_seq_alignment",
        persisted_input_quality={
            "has_blocking": True,
            "issues": [
                InputIssue(
                    path="/tmp/missing.fastq.gz",
                    severity="error",
                    category="missing_file",
                    message="FASTQ file does not exist.",
                    suggestion="Check the input path and ensure the FASTQ is present.",
                ).__dict__,
            ],
            "summary": "Detected 1 input issue(s); blocking=true.",
        },
    )

    assert summary.recommendation == "do_not_start"
    assert "blocking issue" in summary.rationale


def test_build_preflight_summary_hides_unknown_tool_resource_noise(tmp_path: Path) -> None:
    """Unknown wrapper names alone should not create fake resource warnings."""

    summary = build_preflight_summary(
        {"plan": [{"tool_name": "totally_unknown_wrapper", "arguments": {}}]},
        selected_dir=tmp_path,
        analysis_type="variant_annotation",
    )

    assert summary.resource_report is None
    assert summary.resource_report_source == "unavailable"
    assert summary.recommendation == "unavailable"


def test_preflight_summary_from_json_round_trip(tmp_path: Path) -> None:
    """Persisted preflight payloads should deserialize into typed summaries."""

    summary = build_preflight_summary(
        {"plan": [{"tool_name": "salmon_quant", "arguments": {}}]},
        selected_dir=tmp_path,
        analysis_type="transcript_quantification",
        persisted_input_quality={
            "has_blocking": False,
            "summary": "Detected 0 input issue(s); blocking=false.",
            "issues": [],
        },
    )

    restored = preflight_summary_from_json(preflight_summary_to_json(summary))

    assert restored is not None
    assert restored.analysis_type == summary.analysis_type
    assert restored.recommendation == summary.recommendation
    assert restored.input_scan is not None
