from __future__ import annotations

from pathlib import Path

from bio_harness.ui.path_text import extract_paths_from_text


def test_extract_paths_from_text_supports_absolute_paths() -> None:
    text = "Use /tmp/project/data/reads_1.fq.gz and /tmp/project/data/reads_2.fq.gz."
    assert extract_paths_from_text(text) == [
        "/tmp/project/data/reads_1.fq.gz",
        "/tmp/project/data/reads_2.fq.gz",
    ]


def test_extract_paths_from_text_resolves_repo_relative_workspace_paths(tmp_path: Path) -> None:
    project_root = tmp_path
    target = project_root / "workspace" / "benchmarks" / "case" / "data" / "reads_1.fq.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")

    paths = extract_paths_from_text(
        "Use workspace/benchmarks/case/data/reads_1.fq.gz for this run.",
        project_root=project_root,
    )

    assert paths == [str(target)]


def test_extract_paths_from_text_preserves_unknown_repo_relative_candidates() -> None:
    paths = extract_paths_from_text(
        "Check workspace/benchmarks/bioagent-bench/tasks/transcript-quant/data next.",
        project_root=Path("/tmp/nonexistent-root"),
    )
    assert paths == ["workspace/benchmarks/bioagent-bench/tasks/transcript-quant/data"]
