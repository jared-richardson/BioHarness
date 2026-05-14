from __future__ import annotations

import json
from pathlib import Path

from bio_harness.analysis.qwen_skill_coverage import build_qwen_skill_coverage


def _write_skill_index(project_root: Path) -> None:
    skills_dir = project_root / "bio_harness" / "skills" / "definitions"
    skills_dir.mkdir(parents=True)
    payload = {
        "version": 1,
        "skills_count": 3,
        "skills": [
            {"name": "bash_run"},
            {"name": "fastqc_run"},
            {"name": "salmon_quant"},
        ],
    }
    (skills_dir / "index.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_run(
    runs_root: Path,
    *,
    run_name: str,
    model_name: str,
    selected_dir: str,
    tool_names: list[str],
) -> None:
    run_dir = runs_root / run_name
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"model_name": model_name, "selected_dir": selected_dir}, indent=2),
        encoding="utf-8",
    )
    events = []
    for tool_name in tool_names:
        events.append(
            {
                "event_type": "STEP_FINISHED",
                "payload": {"tool_name": tool_name, "exit_code": 0},
            }
        )
    (run_dir / "events.jsonl").write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")


def test_build_qwen_skill_coverage_excludes_benchmark_runs(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_skill_index(project_root)
    runs_root = tmp_path / "runs"
    _write_run(
        runs_root,
        run_name="local_run",
        model_name="qwen3-coder-next:latest",
        selected_dir=str(tmp_path / "workspace" / "rna_seq"),
        tool_names=["fastqc_run", "salmon_quant"],
    )
    _write_run(
        runs_root,
        run_name="bench_run",
        model_name="qwen3-coder-next:latest",
        selected_dir=str(tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "official_runs" / "x"),
        tool_names=["bash_run"],
    )

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_coverage.SKILL_INDEX_PATH",
        project_root / "bio_harness" / "skills" / "definitions" / "index.json",
    )

    summary = build_qwen_skill_coverage(runs_root)

    assert summary["scanned_run_count"] == 1
    assert summary["covered_skills"] == ["fastqc_run", "salmon_quant"]
    assert "bash_run" in summary["missing_skills"]


def test_build_qwen_skill_coverage_can_include_benchmark_runs(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _write_skill_index(project_root)
    runs_root = tmp_path / "runs"
    _write_run(
        runs_root,
        run_name="bench_run",
        model_name="qwen3-coder-next:latest",
        selected_dir=str(tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "official_runs" / "x"),
        tool_names=["bash_run"],
    )

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_coverage.SKILL_INDEX_PATH",
        project_root / "bio_harness" / "skills" / "definitions" / "index.json",
    )

    summary = build_qwen_skill_coverage(runs_root, include_benchmark_runs=True)

    assert summary["scanned_run_count"] == 1
    assert summary["covered_skills"] == ["bash_run"]
