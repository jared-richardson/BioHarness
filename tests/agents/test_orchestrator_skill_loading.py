from __future__ import annotations

import sys
from pathlib import Path

from bio_harness.agents.orchestrator_skill_loading import (
    load_skill_functions,
    load_tools_context,
)


def test_load_tools_context_concatenates_skill_files(tmp_path: Path) -> None:
    skill_a = tmp_path / "skill_a.md"
    skill_b = tmp_path / "skill_b.md"
    skill_a.write_text("alpha", encoding="utf-8")
    skill_b.write_text("beta", encoding="utf-8")

    context = load_tools_context(
        {
            "skill_a": {"file_path": str(skill_a)},
            "skill_b": {"file_path": str(skill_b)},
        }
    )

    assert "## Bio-Harness User Help" in context
    assert "--- Skill: skill_a ---" in context
    assert "alpha" in context
    assert "--- Skill: skill_b ---" in context
    assert "beta" in context


def test_load_tools_context_resolves_repo_relative_file_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    skill_path = project_root / "bio_harness" / "skills" / "definitions" / "skill_a.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("alpha", encoding="utf-8")
    monkeypatch.setattr(
        "bio_harness.agents.orchestrator_skill_loading.PROJECT_ROOT",
        project_root,
    )

    context = load_tools_context(
        {
            "skill_a": {"file_path": "bio_harness/skills/definitions/skill_a.md"},
        }
    )

    assert "--- Skill: skill_a ---" in context
    assert "alpha" in context


def test_load_skill_functions_discovers_named_and_grouped_functions(tmp_path: Path) -> None:
    (tmp_path / "module_a.py").write_text(
        "def skill_a():\n    return 'a'\n\n"
        "def grouped_skill():\n    return 'grouped'\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    loaded = load_skill_functions(
        ["skill_a", "grouped_skill", "missing_skill"],
        tmp_path,
    )

    assert set(loaded) == {"skill_a", "grouped_skill"}
    assert loaded["skill_a"]() == "a"
    assert loaded["grouped_skill"]() == "grouped"


def test_load_skill_functions_restores_sys_path(tmp_path: Path) -> None:
    (tmp_path / "module_b.py").write_text("def skill_b():\n    return 'b'\n", encoding="utf-8")
    original_path = list(sys.path)

    load_skill_functions(["skill_b"], tmp_path)

    assert sys.path == original_path
