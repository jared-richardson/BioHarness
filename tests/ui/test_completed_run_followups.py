from __future__ import annotations

import json
from pathlib import Path

from bio_harness.ui.completed_run_followups import (
    build_completed_run_followup_response,
    should_route_completed_run_followup,
)


def test_should_route_completed_run_followup_requires_completed_run() -> None:
    run = {"status": "running", "run_dir": "/tmp/run"}

    assert (
        should_route_completed_run_followup(
            run,
            "Inspect the gene abundance table and explain what it contains.",
        )
        is False
    )


def test_should_route_completed_run_followup_detects_result_explanation_prompt(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "gene_abundances.tsv").write_text(
        "Gene ID\tCoverage\tFPKM\tTPM\nENST1\t1.0\t2.0\t3.0\n",
        encoding="utf-8",
    )
    run = {
        "status": "completed",
        "run_dir": str(run_dir),
    }

    assert should_route_completed_run_followup(
        run,
        "Inspect the gene abundance table and explain what it contains.",
    )


def test_should_route_completed_run_followup_detects_preflight_prompt(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = {
        "status": "completed",
        "run_dir": str(run_dir),
        "input_quality": {
            "has_blocking": False,
            "summary": "Detected 1 input issue(s); blocking=false.",
            "issues": [
                {
                    "path": "/tmp/sample.fastq.gz",
                    "severity": "warning",
                    "category": "short_reads",
                    "message": "FASTQ contains very short reads.",
                    "suggestion": "Confirm the assay supports short reads.",
                }
            ],
        },
    }

    assert should_route_completed_run_followup(
        run,
        "Can you summarize the preflight and input quality warnings?",
    )


def test_should_route_completed_run_followup_detects_in_run_quality_prompt(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = {
        "status": "completed",
        "run_dir": str(run_dir),
        "in_run_quality_summary": {
            "active_step_id": 2,
            "tool_name": "bash_run",
            "zero_byte_outputs": ["final/results.tsv"],
            "suspicious_event_count": 1,
        },
    }

    assert should_route_completed_run_followup(
        run,
        "Did anything suspicious happen during the run, like zero-byte outputs?",
    )


def test_build_completed_run_followup_response_profiles_gene_abundance_artifact(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "gene_abundances.tsv").write_text(
        (
            "Gene ID\tGene Name\tReference\tCoverage\tFPKM\tTPM\n"
            "ENST1\tGENE1\tchr14\t1.0\t2.0\t3.0\n"
        ),
        encoding="utf-8",
    )
    run = {
        "status": "completed",
        "run_uid": "run_123",
        "run_dir": str(run_dir),
        "plan": {"plan": []},
        "analysis_spec": {"analysis_type": "transcript_quantification"},
    }

    response = build_completed_run_followup_response(
        run,
        "Inspect the gene abundance table and explain what it contains.",
    )

    assert "gene_abundances.tsv" in response
    assert "StringTie abundance estimates" in response
    assert "`Coverage`" in response
    assert "`TPM`" in response


def test_build_completed_run_followup_response_summarizes_completed_run(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "deseq_results.csv").write_text(
        (
            "gene,log2FoldChange,pvalue,padj\n"
            "ENSG1,2.5,0.001,0.01\n"
            "ENSG2,-1.7,0.002,0.02\n"
            "ENSG3,0.1,0.3,0.6\n"
        ),
        encoding="utf-8",
    )
    run = {
        "status": "completed",
        "run_uid": "run_456",
        "run_dir": str(run_dir),
        "plan": {"plan": []},
        "analysis_spec": {"analysis_type": "differential_expression"},
    }

    response = build_completed_run_followup_response(
        run,
        "Summarize the results for me.",
    )

    assert "completed run `run_456`" in response
    assert "differential expression" in response.lower()
    assert "Recommended next step:" in response
    assert "`accept`" in response
    assert "Recent outputs:" in response
    assert "final/deseq_results.csv" in response


def test_build_completed_run_followup_response_summarizes_preflight(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = {
        "status": "completed",
        "run_uid": "run_preflight",
        "run_dir": str(run_dir),
        "plan": {"plan": [{"tool_name": "salmon_quant", "arguments": {}}]},
        "analysis_spec": {"analysis_type": "transcript_quantification"},
        "input_quality": {
            "has_blocking": False,
            "summary": "Detected 1 input issue(s); blocking=false.",
            "issues": [
                {
                    "path": "/tmp/sample.fastq.gz",
                    "severity": "warning",
                    "category": "short_reads",
                    "message": "FASTQ contains very short reads.",
                    "suggestion": "Confirm the assay supports short reads.",
                }
            ],
        },
    }

    response = build_completed_run_followup_response(
        run,
        "Can you explain the preflight and resource warnings for this run?",
    )

    assert "completed run `run_preflight`" in response
    assert "Recommendation:" in response
    assert "`review_before_run`" in response
    assert "Input issues:" in response
    assert "`short_reads`" in response


def test_build_completed_run_followup_response_summarizes_in_run_quality(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = {
        "status": "completed",
        "run_uid": "run_in_run_quality",
        "run_dir": str(run_dir),
        "in_run_quality_summary": {
            "active_step_id": 2,
            "tool_name": "bash_run",
            "recent_output_count": 1,
            "new_output_count": 1,
            "expected_output_count": 1,
            "expected_outputs_present": [],
            "expected_outputs_missing": ["final/results.tsv"],
            "zero_byte_outputs": ["final/results.tsv"],
            "suspicious_event_count": 1,
            "latest_output_mtime": 100.0,
            "scanned_files": 1,
        },
    }

    response = build_completed_run_followup_response(
        run,
        "What happened during the run? Were there any suspicious zero-byte outputs?",
    )

    assert "completed run `run_in_run_quality`" in response
    assert "reporting-only" in response
    assert "Suspicious zero-byte outputs:" in response
    assert "`final/results.tsv`" in response


def test_build_completed_run_followup_response_reconstructs_preflight_from_run_dir(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "reads.fastq.gz").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "selected_dir": str(selected_dir),
                "data_root": str(data_root),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "analysis_spec": {"analysis_type": "transcript_quantification"},
                "input_quality": {
                    "has_blocking": False,
                    "summary": "Detected 1 input issue(s); blocking=false.",
                    "issues": [
                        {
                            "path": str(data_root / "reads.fastq.gz"),
                            "severity": "warning",
                            "category": "short_reads",
                            "message": "FASTQ contains very short reads.",
                            "suggestion": "Confirm the assay supports short reads.",
                        }
                    ],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    run = {
        "status": "completed",
        "run_uid": "run_manifest_preflight",
        "run_dir": str(run_dir),
    }

    response = build_completed_run_followup_response(
        run,
        "Can you explain the preflight and resource warnings for this run?",
    )

    assert "run_manifest_preflight" in response
    assert "Recommendation:" in response
    assert "`short_reads`" in response


def test_build_completed_run_followup_response_prefers_persisted_preflight_summary(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "preflight_summary.json").write_text(
        json.dumps(
            {
                "analysis_type": "transcript_quantification",
                "selected_dir": str(tmp_path / "selected"),
                "data_root": str(tmp_path / "data"),
                "tool_names": ["salmon_quant"],
                "input_scan_source": "persisted",
                "input_scan": {
                    "has_blocking": False,
                    "summary": "Detected 1 input issue(s); blocking=false.",
                    "issues": [
                        {
                            "path": "/tmp/sample.fastq.gz",
                            "severity": "warning",
                            "category": "short_reads",
                            "message": "FASTQ contains very short reads.",
                            "suggestion": "Confirm assay compatibility.",
                        }
                    ],
                },
                "resource_report_source": "unavailable",
                "resource_report": None,
                "recommendation": "review_before_run",
                "rationale": "Persisted input-quality issues should be reviewed before rerunning.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    run = {
        "status": "completed",
        "run_uid": "run_prefersist",
        "run_dir": str(run_dir),
    }

    response = build_completed_run_followup_response(
        run,
        "Can you summarize the preflight warnings?",
    )

    assert "`review_before_run`" in response
    assert "short_reads" in response


def test_build_completed_run_followup_response_reconstructs_in_run_quality_from_state(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "in_run_quality_summary": {
                    "active_step_id": 3,
                    "tool_name": "bash_run",
                    "zero_byte_outputs": ["final/counts.tsv"],
                    "suspicious_event_count": 1,
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    run = {
        "status": "completed",
        "run_uid": "run_manifest_in_run",
        "run_dir": str(run_dir),
    }

    response = build_completed_run_followup_response(
        run,
        "Did anything suspicious happen during the run, like zero-byte outputs?",
    )

    assert "run_manifest_in_run" in response
    assert "`final/counts.tsv`" in response


def test_build_completed_run_followup_response_returns_empty_for_unrelated_prompt(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "gene_abundances.tsv").write_text(
        "Gene ID\tCoverage\tFPKM\tTPM\nENST1\t1.0\t2.0\t3.0\n",
        encoding="utf-8",
    )
    run = {
        "status": "completed",
        "run_dir": str(run_dir),
    }

    response = build_completed_run_followup_response(
        run,
        "What capabilities does the harness support?",
    )

    assert response == ""
