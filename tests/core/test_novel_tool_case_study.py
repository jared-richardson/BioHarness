"""Tests for the cold novel-tool onboarding case-study helpers."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.novel_tool_case_study import (
    build_sylph_smoke_recipes,
    registry_contains_skill,
    remove_cold_start_artifacts,
    run_cold_sylph_case_study,
)


_FIXTURES_DIR = (
    Path(__file__).resolve().parents[2] / "benchmark_data" / "novel_tool_case_study" / "sylph"
)


def _write_skill_definition(target: Path, name: str) -> None:
    target.write_text(
        f"""---
name: {name}
description: test skill
risk_level: low
parameters:
  input:
    type: path
analysis_categories:
- metagenomics
capabilities:
- taxonomic_profiling
system_requirements:
  min_ram_gb: 1
  min_cores: 1
---
content
""",
        encoding="utf-8",
    )


def _fake_sylph_runner(command: str, *, cwd: str | None = None, timeout_seconds: int = 30) -> dict[str, object]:
    del timeout_seconds
    work_dir = Path(cwd or ".")
    if "sylph sketch" in command:
        (work_dir / "toy_database.syldb").write_text("db\n", encoding="utf-8")
        (work_dir / "sample_reads.fastq.sylsp").write_text("sample\n", encoding="utf-8")
        return {"return_code": 0, "stdout": "sketch ok\n", "stderr": "", "timed_out": False}
    if "sylph profile" in command:
        (work_dir / "profiling.tsv").write_text("taxon\tabundance\nmock\t1.0\n", encoding="utf-8")
        return {"return_code": 0, "stdout": "profile ok\n", "stderr": "", "timed_out": False}
    return {"return_code": 1, "stdout": "", "stderr": f"unexpected command: {command}", "timed_out": False}


def test_build_sylph_smoke_recipes_requires_fixture_files(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    try:
        build_sylph_smoke_recipes(fixtures_dir=fixtures_dir, work_dir=tmp_path / "work")
    except FileNotFoundError as exc:
        assert "reference_genome.fa" in str(exc)
    else:
        raise AssertionError("Expected missing fixtures to raise FileNotFoundError.")


def test_remove_cold_start_artifacts_only_removes_target_skill(tmp_path: Path) -> None:
    defs = tmp_path / "defs"
    lib = tmp_path / "lib"
    cards = tmp_path / "cards"
    defs.mkdir()
    lib.mkdir()
    cards.mkdir()
    _write_skill_definition(defs / "sylph_classify.md", "sylph_classify")
    _write_skill_definition(defs / "keep_skill.md", "keep_skill")
    (lib / "sylph_classify.py").write_text("def sylph_classify():\n    return ''\n", encoding="utf-8")
    (lib / "keep_skill.py").write_text("def keep_skill():\n    return ''\n", encoding="utf-8")
    (cards / "sylph_classify.json").write_text("{}\n", encoding="utf-8")
    (cards / "keep_skill.json").write_text("{}\n", encoding="utf-8")

    removed = remove_cold_start_artifacts(
        "sylph_classify",
        skills_definitions_dir=defs,
        skills_library_dir=lib,
        tool_cards_dir=cards,
    )

    assert any(path.endswith("sylph_classify.md") for path in removed)
    assert any(path.endswith("sylph_classify.py") for path in removed)
    assert any(path.endswith("sylph_classify.json") for path in removed)
    assert (defs / "keep_skill.md").exists()
    assert (lib / "keep_skill.py").exists()
    assert (cards / "keep_skill.json").exists()


def test_registry_contains_skill_detects_installed_definition(tmp_path: Path) -> None:
    defs = tmp_path / "defs"
    defs.mkdir()
    _write_skill_definition(defs / "sylph_classify.md", "sylph_classify")

    assert registry_contains_skill("sylph_classify", skills_definitions_dir=defs) is True
    assert registry_contains_skill("missing_skill", skills_definitions_dir=defs) is False


def test_run_cold_sylph_case_study_writes_missing_tool_summary(tmp_path: Path, monkeypatch) -> None:
    defs = tmp_path / "defs"
    lib = tmp_path / "lib"
    cards = tmp_path / "cards"
    cat = tmp_path / "catalog" / "capability_catalog.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setattr("bio_harness.core.novel_tool_case_study.shutil.which", lambda _: None)

    outcome = run_cold_sylph_case_study(
        fixtures_dir=_FIXTURES_DIR,
        work_dir=tmp_path / "work",
        skills_definitions_dir=defs,
        skills_library_dir=lib,
        capability_catalog_path=cat,
        tool_cards_dir=cards,
        summary_path=summary,
    )

    assert outcome.tool_found is False
    assert outcome.onboarding_outcome is None
    assert outcome.retrieval_before is False
    assert outcome.retrieval_after is False
    assert summary.exists()
    assert "Tool found" not in summary.read_text(encoding="utf-8")


def test_run_cold_sylph_case_study_installs_and_updates_retrieval(tmp_path: Path, monkeypatch) -> None:
    defs = tmp_path / "defs"
    lib = tmp_path / "lib"
    cards = tmp_path / "cards"
    cat = tmp_path / "catalog" / "capability_catalog.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setattr("bio_harness.core.novel_tool_case_study.shutil.which", lambda _: "/usr/bin/sylph")

    outcome = run_cold_sylph_case_study(
        fixtures_dir=_FIXTURES_DIR,
        work_dir=tmp_path / "work",
        skills_definitions_dir=defs,
        skills_library_dir=lib,
        capability_catalog_path=cat,
        tool_cards_dir=cards,
        summary_path=summary,
        command_runner=_fake_sylph_runner,
    )

    assert outcome.tool_found is True
    assert outcome.onboarding_outcome is not None
    assert outcome.onboarding_outcome.success is True
    assert outcome.onboarding_outcome.installed is True
    assert outcome.retrieval_before is False
    assert outcome.retrieval_after is True
    assert (defs / "sylph_classify.md").exists()
    assert (lib / "sylph_classify.py").exists()
    assert (cards / "sylph_classify.json").exists()
    assert (tmp_path / "work" / "toy_database.syldb").exists()
    assert (tmp_path / "work" / "profiling.tsv").exists()
    assert "Retrieval after onboarding: `True`" in summary.read_text(encoding="utf-8")
