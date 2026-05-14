from __future__ import annotations

import json
import re
from pathlib import Path

from bio_harness.core.uncommon_skill_framework import (
    build_uncommon_wrapper_command,
    uncommon_skill_specs,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "workflows" / "fixtures" / "uncommon_prompt_intents.json"
DOC_PATH = PROJECT_ROOT / "docs" / "uncommon_skills.md"
DESTRUCTIVE_PATTERN = re.compile(r"(^|\\s)rm\\s+-[a-zA-Z]*[rf][a-zA-Z]*\\s+/(\\s|$)", re.IGNORECASE)


def _fixture_ids() -> set[str]:
    rows = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    out: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id", "")).strip()
        if rid:
            out.add(rid)
    return out


def test_ci_guardrails_for_uncommon_skill_artifacts():
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    fixture_ids = _fixture_ids()

    for spec in uncommon_skill_specs():
        name = str(spec["name"])
        defs_path = PROJECT_ROOT / "bio_harness" / "skills" / "definitions" / f"{name}.md"
        lib_path = PROJECT_ROOT / "bio_harness" / "skills" / "library" / f"{name}.py"

        assert defs_path.exists(), f"missing skill definition: {defs_path}"
        assert lib_path.exists(), f"missing skill wrapper: {lib_path}"

        # Deterministic rendering check from sample args in spec catalog.
        sample_args = dict(spec.get("sample_args", {}))
        cmd_a = build_uncommon_wrapper_command(name, sample_args)
        cmd_b = build_uncommon_wrapper_command(name, sample_args)
        assert cmd_a == cmd_b, f"non-deterministic command rendering for {name}"
        assert not DESTRUCTIVE_PATTERN.search(f" {cmd_a} "), f"destructive pattern found in wrapper command for {name}"

        # Docs anchor presence.
        anchor = str(spec.get("docs_anchor", "")).strip()
        assert f"id=\"{anchor}\"" in doc_text

        # Fixture references exist.
        for fixture_ref in spec.get("fixtures", []):
            token = str(fixture_ref).strip().split("#", 1)[-1]
            if token:
                assert token in fixture_ids

        # Test files listed in the schema catalog must exist.
        for test_file in spec.get("test_files", []):
            p = PROJECT_ROOT / str(test_file)
            assert p.exists(), f"missing listed test file: {test_file}"
