from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from bio_harness.core.onboarding_advisories import (
    build_tool_advisory_proposal,
    persist_tool_advisory_proposal,
)
from bio_harness.core.tool_cards import tool_card_from_draft


def _draft() -> dict:
    return {
        "skill_name": "flaggy_tool",
        "name": "flaggy_tool",
        "description": "A tool with flaky optional flags.",
        "risk_level": "low",
        "tools_required": ["flaggy_tool"],
        "capabilities": ["annotation"],
        "parameters": {
            "input_path": {"type": "path", "description": "Input file.", "required": True},
        },
        "command_template": "flaggy_tool --input {input_path}",
        "wrapper_code": "def flaggy_tool(**kwargs): return ''\n",
    }


def test_build_tool_advisory_proposal_uses_repeated_failure_focus() -> None:
    base = tool_card_from_draft(_draft(), source_meta={"source": "unit:test"}, validated=False)
    card = replace(
        base,
        common_errors=(
            {
                "pattern": "Unknown option",
                "cause": "Unsupported optional flags remained in the command template.",
                "fix": "Drop unsupported optional flags before the next broad smoke run.",
                "focus": "command_flags",
            },
        ),
        smoke_test_results=(
            {
                "refinement_focus": "command_flags",
                "progress_assessment": {"passed": False, "score": 0.2},
            },
            {
                "refinement_focus": "command_flags",
                "progress_assessment": {"passed": False, "score": 0.2},
            },
        ),
    )

    proposal = build_tool_advisory_proposal("", card, min_repeats=2)

    assert proposal is not None
    assert proposal["scope"] == "tool"
    assert proposal["name"] == "flaggy_tool"
    assert "command_flags" in proposal["summary"]
    assert proposal["repair_hints"] == ["Drop unsupported optional flags before the next broad smoke run."]
    assert any("unsupported optional flags" in value for value in proposal["avoid_patterns"])


def test_persist_tool_advisory_proposal_updates_catalog(tmp_path: Path) -> None:
    catalog_path = tmp_path / "repair_advisories.json"
    proposal = {
        "scope": "tool",
        "name": "flaggy_tool",
        "summary": "Repeated flag failures should trigger a narrowed diagnostic probe.",
        "repair_hints": ["Retry with required args only before restoring optional flags."],
        "avoid_patterns": ["Do not keep unsupported flags after a diagnostic probe fails."],
        "source": "tool_onboarding_refinement",
    }

    written = persist_tool_advisory_proposal(proposal, catalog_path=catalog_path)

    assert written == catalog_path
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert payload["tool_advisories"]["flaggy_tool"]["source"] == "tool_onboarding_refinement"
    assert "Retry with required args only before restoring optional flags." in (
        payload["tool_advisories"]["flaggy_tool"]["repair_hints"]
    )
