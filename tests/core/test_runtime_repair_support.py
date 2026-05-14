from __future__ import annotations

from pathlib import Path

from bio_harness.core.runtime_repair_support import (
    build_runtime_result_payload,
    write_runtime_receipt,
)


def test_write_runtime_receipt_returns_none_without_run_dir(tmp_path: Path) -> None:
    run = {"run_files": {}}

    assert write_runtime_receipt(run, prefix="test", payload={"ok": True}) is None


def test_write_runtime_receipt_writes_under_run_receipts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = {"run_files": {"run_dir": str(run_dir)}}

    receipt_path = write_runtime_receipt(run, prefix="test_receipt", payload={"ok": True})

    assert receipt_path is not None
    assert Path(receipt_path).exists()
    assert (run_dir / "receipts") in Path(receipt_path).parents


def test_build_runtime_result_payload_prefers_selected_pipeline_id() -> None:
    payload = build_runtime_result_payload(
        run={
            "run_uid": "run-123",
            "status": "completed",
            "error": "",
            "run_files": {"run_dir": "/tmp/run"},
        },
        data_root=Path("/tmp/data"),
        selected_dir=Path("/tmp/selected"),
        path_graph_db_path=Path("/tmp/path_graph.db"),
        path_graph_user_key="user",
        path_graph_scope="scope",
        benchmark_policy="strict",
        assistance_manifest={
            "generic_template_fallback_used": True,
            "generic_template_fallback": {"selected_pipeline_id": "pipeline_a"},
        },
    )

    assert payload["run_id"] == "run-123"
    assert payload["generic_template_fallback_pipeline_id"] == "pipeline_a"
    assert payload["benchmark_policy"] == "strict"


def test_build_runtime_result_payload_uses_nested_pipeline_selection_when_needed() -> None:
    payload = build_runtime_result_payload(
        run={"run_files": {}},
        data_root=Path("/tmp/data"),
        selected_dir=Path("/tmp/selected"),
        path_graph_db_path=Path("/tmp/path_graph.db"),
        path_graph_user_key="user",
        path_graph_scope="scope",
        benchmark_policy="strict",
        assistance_manifest={
            "generic_template_fallback": {
                "selection": {"pipeline_id": "pipeline_b"},
            },
        },
    )

    assert payload["generic_template_fallback_pipeline_id"] == "pipeline_b"


def test_build_runtime_result_payload_includes_failure_diagnosis_and_input_quality(tmp_path: Path) -> None:
    stderr_path = tmp_path / "stderr.log"
    stderr_path.write_text("Permission denied: /tmp/protected.bam\n", encoding="utf-8")

    payload = build_runtime_result_payload(
        run={
            "status": "failed",
            "error": "Step 1 failed with exit code 13",
            "step_statuses": ["failed"],
            "plan": {"plan": [{"tool_name": "samtools_flagstat", "arguments": {"input_bam": "/tmp/protected.bam"}}]},
            "run_files": {"stderr": str(stderr_path)},
            "input_quality": {"summary": "Detected 1 issue.", "issues": [{"category": "missing_file"}]},
            "research_report": {"question": "What is DESeq2?"},
        },
        data_root=Path("/tmp/data"),
        selected_dir=Path("/tmp/selected"),
        path_graph_db_path=Path("/tmp/path_graph.db"),
        path_graph_user_key="user",
        path_graph_scope="scope",
        benchmark_policy="strict",
        assistance_manifest={},
    )

    assert payload["failure_diagnosis"]["tool_name"] == "samtools_flagstat"
    assert payload["input_quality"]["summary"] == "Detected 1 issue."
    assert payload["research_report"]["question"] == "What is DESeq2?"
