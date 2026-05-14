from __future__ import annotations

from pathlib import Path

from bio_harness.core.failure_reporting import build_failure_diagnosis


def test_build_failure_diagnosis_uses_failed_step_and_stderr(tmp_path: Path) -> None:
    stderr_path = tmp_path / "stderr.log"
    stderr_path.write_text("No such file or directory: /tmp/missing.bam\n", encoding="utf-8")

    payload = build_failure_diagnosis(
        {
            "status": "failed",
            "error": "Step 2 failed with exit code 1",
            "step_statuses": ["completed", "failed"],
            "plan": {
                "plan": [
                    {"tool_name": "samtools_index", "arguments": {"input_bam": "/tmp/input.bam"}},
                    {"tool_name": "samtools_flagstat", "arguments": {"input_bam": "/tmp/missing.bam"}},
                ]
            },
            "run_files": {"stderr": str(stderr_path)},
        }
    )

    assert payload["failure_class"] == "runtime_step_failure"
    assert payload["failed_step_number"] == 2
    assert payload["tool_name"] == "samtools_flagstat"
    assert payload["exit_code"] == 1
    assert "Input file not found" in payload["root_cause"]


def test_build_failure_diagnosis_returns_empty_for_non_failed_run() -> None:
    assert build_failure_diagnosis({"status": "completed"}) == {}
