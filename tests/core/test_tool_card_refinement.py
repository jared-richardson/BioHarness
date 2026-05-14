from __future__ import annotations

from bio_harness.core.onboarding_fixtures import SmokeTestRecipe, SmokeTestResult
from bio_harness.core.tool_card_refinement import (
    apply_refined_card_to_draft,
    refine_tool_card_from_smoke_result,
)
from bio_harness.core.tool_cards import tool_card_from_draft


def _demo_draft() -> dict:
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
            "import shlex\n"
            "import string\n\n"
            "def _render_template(template: str, kwargs: dict) -> str:\n"
            "    rendered = {k: shlex.quote(str(v)) for k, v in kwargs.items() if v is not None}\n"
            "    return template.format(**rendered)\n\n"
            "def emit_file(**kwargs) -> str:\n"
            "    return _render_template(\"printf 'ok\\\\n' > {output_path}\", kwargs)\n"
        ),
    }


def test_refine_tool_card_from_success_records_outputs_and_example() -> None:
    card = tool_card_from_draft(_demo_draft())
    recipe = SmokeTestRecipe(name="emit", kwargs={"output_path": "/tmp/out.txt"}, expected_outputs=("/tmp/out.txt",))
    smoke = SmokeTestResult(
        name="emit",
        passed=True,
        command="printf 'ok\\n' > /tmp/out.txt",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=("/tmp/out.txt",),
        produced_outputs=("/tmp/out.txt",),
        timed_out=False,
        failure_reason="",
        duration_seconds=0.01,
    )

    refined = refine_tool_card_from_smoke_result(card, smoke, iteration=1, recipe=recipe)

    assert refined.safe_example == "printf 'ok\\n' > /tmp/out.txt"
    assert "out.txt" in refined.canonical_outputs
    assert len(refined.smoke_test_results) == 1
    assert "passed" in refined.refinement_history[0]
    assert "progress_score=" in refined.refinement_history[0]
    assert "focus=complete" in refined.refinement_history[0]
    assert "outputs=out.txt" in refined.refinement_history[0]
    assert refined.smoke_test_results[0]["progress_assessment"]["score"] > 0
    assert refined.smoke_test_results[0]["refinement_focus"] == "complete"


def test_refine_tool_card_from_failure_records_common_error() -> None:
    card = tool_card_from_draft(_demo_draft())
    recipe = SmokeTestRecipe(name="emit", kwargs={"output_path": "/tmp/out.txt"}, expected_outputs=("/tmp/out.txt",))
    smoke = SmokeTestResult(
        name="emit",
        passed=False,
        command="printf 'ok\\n' > /tmp/out.txt",
        return_code=2,
        stdout="",
        stderr="missing index",
        expected_outputs=("/tmp/out.txt",),
        produced_outputs=(),
        timed_out=False,
        failure_reason="unexpected_return_code:2",
        duration_seconds=0.02,
    )

    refined = refine_tool_card_from_smoke_result(card, smoke, iteration=2, recipe=recipe)

    assert refined.common_errors[0]["pattern"] == "missing index"
    assert refined.common_errors[0]["cause"] == "unexpected_return_code:2"
    assert refined.common_errors[0]["fix"] == "build the required index before rerunning the smoke test"
    assert refined.common_errors[0]["focus"] == "input_prerequisites"
    assert refined.smoke_test_results[0]["refinement_focus"] == "input_prerequisites"
    assert "failed" in refined.refinement_history[0]


def test_refine_tool_card_from_missing_output_failure_keeps_expected_output_name() -> None:
    card = tool_card_from_draft(_demo_draft())
    recipe = SmokeTestRecipe(name="emit", kwargs={}, expected_outputs=("/tmp/result.tsv",))
    smoke = SmokeTestResult(
        name="emit",
        passed=False,
        command="printf 'ok\\n'",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=("/tmp/result.tsv",),
        produced_outputs=(),
        timed_out=False,
        failure_reason="missing_expected_outputs",
        duration_seconds=0.03,
    )

    refined = refine_tool_card_from_smoke_result(card, smoke, iteration=1, recipe=recipe)

    assert "result.tsv" in refined.canonical_outputs
    assert refined.common_errors[0]["fix"] == (
        "focus on output-path binding so the command writes result.tsv to the requested locations"
    )
    assert refined.common_errors[0]["focus"] == "output_paths"


def test_refine_tool_card_from_partial_output_progress_marks_output_completeness() -> None:
    card = tool_card_from_draft(_demo_draft())
    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={},
        expected_outputs=("/tmp/a.tsv", "/tmp/b.tsv"),
        expected_substrings=("done",),
    )
    smoke = SmokeTestResult(
        name="emit",
        passed=False,
        command="printf 'done\\n'",
        return_code=0,
        stdout="done",
        stderr="",
        expected_outputs=("/tmp/a.tsv", "/tmp/b.tsv"),
        produced_outputs=("/tmp/a.tsv",),
        timed_out=False,
        failure_reason="missing_expected_outputs",
        duration_seconds=0.03,
    )

    refined = refine_tool_card_from_smoke_result(card, smoke, iteration=2, recipe=recipe)

    assert refined.common_errors[0]["focus"] == "output_completeness"
    assert refined.common_errors[0]["fix"] == (
        "preserve the existing command behavior and add the remaining expected outputs: b.tsv"
    )


def test_refine_tool_card_from_missing_marker_failure_prefers_output_marker_focus() -> None:
    card = tool_card_from_draft(_demo_draft())
    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={},
        expected_outputs=("/tmp/out.tsv",),
        expected_substrings=("DONE",),
    )
    smoke = SmokeTestResult(
        name="emit",
        passed=False,
        command="printf 'ok\\n' > /tmp/out.tsv",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=("/tmp/out.tsv",),
        produced_outputs=("/tmp/out.tsv",),
        timed_out=False,
        failure_reason="missing_expected_substring:DONE",
        duration_seconds=0.02,
    )

    refined = refine_tool_card_from_smoke_result(card, smoke, iteration=1, recipe=recipe)

    assert refined.common_errors[0]["focus"] == "output_markers"
    assert refined.common_errors[0]["fix"] == (
        "outputs are present; align stdout/stderr expectations or emit the required marker text"
    )


def test_refine_tool_card_from_command_extracts_dangerous_flags() -> None:
    card = tool_card_from_draft(_demo_draft())
    smoke = SmokeTestResult(
        name="emit",
        passed=True,
        command="tool --force --overwrite > /tmp/out.txt",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=("/tmp/out.txt",),
        produced_outputs=("/tmp/out.txt",),
        timed_out=False,
        failure_reason="",
        duration_seconds=0.01,
    )

    refined = refine_tool_card_from_smoke_result(card, smoke, iteration=1)

    assert list(refined.dangerous_flags) == ["--force", "--overwrite"]


def test_apply_refined_card_to_draft_refreshes_command_template_and_wrapper_code() -> None:
    draft = _demo_draft()
    card = tool_card_from_draft(draft)
    smoke = SmokeTestResult(
        name="emit",
        passed=True,
        command="printf 'done\\n' > /tmp/result.txt",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=("/tmp/result.txt",),
        produced_outputs=("/tmp/result.txt",),
        timed_out=False,
        failure_reason="",
        duration_seconds=0.01,
    )

    refined_card = refine_tool_card_from_smoke_result(card, smoke, iteration=1)
    updated_draft = apply_refined_card_to_draft(draft, refined_card)

    assert updated_draft["command_template"] == "printf 'done\\n' > /tmp/result.txt"
    assert "printf 'done\\\\n' > /tmp/result.txt" in updated_draft["wrapper_code"]
