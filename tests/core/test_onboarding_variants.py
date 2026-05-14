from __future__ import annotations

from pathlib import Path

from bio_harness.core.onboarding_fixtures import SmokeTestRecipe
from bio_harness.core.onboarding_variants import (
    build_wrapper_variants,
    select_best_wrapper_variant,
)


def _broken_emit_draft() -> dict:
    return {
        "skill_name": "emit_file",
        "name": "emit_file",
        "description": "Emit a file for smoke testing.",
        "risk_level": "low",
        "tools_required": ["printf"],
        "capabilities": ["annotation"],
        "parameters": {
            "output_path": {"type": "path", "description": "Output path.", "required": True},
        },
        "command_template": "printf 'ok\\n' > {output_path}",
        "wrapper_code": (
            "from __future__ import annotations\n\n"
            "def emit_file(**kwargs) -> str:\n"
            "    return \"printf 'broken'\"\n"
        ),
    }


def test_build_wrapper_variants_includes_template_and_generic_candidates() -> None:
    variants = build_wrapper_variants(_broken_emit_draft())

    labels = [candidate.label for candidate in variants]

    assert labels == ["original", "template_stub", "generic_stub"]


def test_select_best_wrapper_variant_prefers_passing_template_stub(tmp_path: Path) -> None:
    output_path = tmp_path / "out.txt"
    selection = select_best_wrapper_variant(
        _broken_emit_draft(),
        smoke_recipes=(
            SmokeTestRecipe(
                name="emit",
                kwargs={"output_path": str(output_path)},
                expected_outputs=(str(output_path),),
            ),
        ),
    )

    assert selection.best_label == "template_stub"
    assert "printf 'ok\\n' > {output_path}" in str(selection.best_draft.get("command_template", ""))
    assert selection.runner_up_wrappers
    assert selection.evaluations[0].label == "template_stub"
