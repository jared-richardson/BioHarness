from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.onboarding_fixtures import SmokeTestRecipe, run_wrapper_smoke_test
from bio_harness.core.onboarding_orchestrator import (
    OnboardingBudget,
    _adapt_recipe_for_focus,
    run_onboarding_refinement_loop,
)
from bio_harness.core.tool_cards import read_tool_card


def _successful_draft() -> dict:
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


def _failing_draft() -> dict:
    return {
        "skill_name": "fail_file",
        "name": "fail_file",
        "description": "Fail for smoke testing.",
        "risk_level": "low",
        "tools_required": ["python3"],
        "capabilities": ["annotation"],
        "parameters": {
            "unused": {"type": "string", "description": "Unused.", "required": False},
        },
        "command_template": "python3 -c \"import sys; sys.stderr.write('missing index\\n'); sys.exit(2)\"",
        "wrapper_code": (
            "from __future__ import annotations\n\n"
            "def fail_file(**kwargs) -> str:\n"
            "    return \"python3 -c \\\"import sys; sys.stderr.write('missing index\\\\n'); sys.exit(2)\\\"\"\n"
        ),
    }


def _broken_wrapper_emit_draft() -> dict:
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


def test_run_wrapper_smoke_test_executes_generated_command(tmp_path: Path) -> None:
    output_path = tmp_path / "out.txt"
    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={"output_path": str(output_path)},
        expected_outputs=(str(output_path),),
    )

    result = run_wrapper_smoke_test(_successful_draft(), recipe)

    assert result.passed is True
    assert output_path.read_text() == "ok\n"
    assert result.failure_reason == ""


def test_run_onboarding_refinement_loop_installs_refined_tool_card(tmp_path: Path) -> None:
    defs = tmp_path / "defs"
    lib = tmp_path / "lib"
    cat = tmp_path / "catalog" / "catalog.json"
    tool_cards_dir = tmp_path / "cards"
    defs.mkdir(parents=True)
    lib.mkdir(parents=True)
    cat.parent.mkdir(parents=True)
    output_path = tmp_path / "result.txt"

    outcome = run_onboarding_refinement_loop(
        _successful_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=(
            SmokeTestRecipe(
                name="emit",
                kwargs={"output_path": str(output_path)},
                expected_outputs=(str(output_path),),
            ),
        ),
        install=True,
        skills_definitions_dir=defs,
        skills_library_dir=lib,
        capability_catalog_path=cat,
        tool_cards_dir=tool_cards_dir,
    )

    assert outcome.success is True
    assert outcome.installed is True
    assert outcome.iterations == 1
    assert outcome.stalled is False
    assert output_path.exists()
    card = read_tool_card(tool_cards_dir / "emit_file.json")
    assert len(card.smoke_test_results) == 1
    assert card.safe_example.startswith("printf")
    assert "result.txt" in card.canonical_outputs


def test_run_onboarding_refinement_loop_stops_after_budget_exhaustion() -> None:
    recipe = SmokeTestRecipe(name="fail", kwargs={})

    outcome = run_onboarding_refinement_loop(
        _failing_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=(recipe, recipe, recipe),
        budget=OnboardingBudget(max_iterations=2, max_total_seconds=60),
    )

    assert outcome.success is False
    assert outcome.installed is False
    assert outcome.iterations == 2
    assert outcome.budget_exhausted is False
    assert outcome.stalled is True
    assert "repeated identical failures" in outcome.message
    assert len(outcome.final_card.common_errors) >= 1


def test_run_onboarding_refinement_loop_rejects_recipe_over_subprocess_budget() -> None:
    outcome = run_onboarding_refinement_loop(
        _successful_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=(
            SmokeTestRecipe(
                name="emit",
                kwargs={"output_path": "/tmp/unused.txt"},
                subprocess_calls=6,
            ),
        ),
        budget=OnboardingBudget(max_iterations=3, max_total_seconds=60, max_subprocess_calls_per_cycle=5),
    )

    assert outcome.success is False
    assert outcome.budget_exhausted is True
    assert outcome.stalled is False
    assert outcome.iterations == 0


def test_run_onboarding_refinement_loop_persists_repeated_advisory(tmp_path: Path) -> None:
    catalog_path = tmp_path / "repair_advisories.json"
    recipe = SmokeTestRecipe(name="fail", kwargs={})

    outcome = run_onboarding_refinement_loop(
        _failing_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=(recipe, recipe),
        budget=OnboardingBudget(max_iterations=2, max_total_seconds=60),
        advisory_catalog_path=catalog_path,
        advisory_repeat_threshold=2,
    )

    assert outcome.success is False
    assert outcome.advisory_path == catalog_path
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert "fail_file" in payload["tool_advisories"]
    advisory = payload["tool_advisories"]["fail_file"]
    assert advisory["source"] == "tool_onboarding_refinement"
    assert "input_prerequisites" in advisory["summary"]
    assert any("index" in hint for hint in advisory["repair_hints"])


def test_run_onboarding_refinement_loop_selects_better_wrapper_variant(tmp_path: Path) -> None:
    output_path = tmp_path / "result.txt"

    outcome = run_onboarding_refinement_loop(
        _broken_wrapper_emit_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=(
            SmokeTestRecipe(
                name="emit",
                kwargs={"output_path": str(output_path)},
                expected_outputs=(str(output_path),),
            ),
        ),
        budget=OnboardingBudget(max_iterations=2, max_total_seconds=60),
    )

    assert outcome.success is True
    assert output_path.exists()
    assert outcome.final_card.runner_up_wrappers
    selection = outcome.final_card.probe_observations[0]["variant_selection"]
    assert selection["best_label"] == "template_stub"
    assert any(item["label"] == "original" for item in selection["evaluations"])


def test_run_onboarding_refinement_loop_continues_when_score_improves(tmp_path: Path) -> None:
    first_output = tmp_path / "a.txt"
    second_output = tmp_path / "b.txt"
    calls = {"count": 0}

    def _runner(command: str, *, cwd=None, timeout_seconds=30):
        del command, cwd, timeout_seconds
        calls["count"] += 1
        if calls["count"] == 1:
            first_output.write_text("first", encoding="utf-8")
            return {"return_code": 0, "stdout": "done", "stderr": "", "timed_out": False}
        second_output.write_text("second", encoding="utf-8")
        return {"return_code": 0, "stdout": "done", "stderr": "", "timed_out": False}

    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={"output_path": str(first_output)},
        expected_outputs=(str(first_output), str(second_output)),
        expected_substrings=("done",),
    )

    outcome = run_onboarding_refinement_loop(
        _successful_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=(recipe, recipe),
        command_runner=_runner,
        budget=OnboardingBudget(max_iterations=2, max_total_seconds=60),
        select_variants=False,
    )

    assert outcome.success is True
    assert outcome.stalled is False
    assert outcome.iterations == 2


def test_run_onboarding_refinement_loop_reorders_pending_recipes_by_focus(tmp_path: Path) -> None:
    calls = {"count": 0}

    def _runner(command: str, *, cwd=None, timeout_seconds=30):
        del command, cwd, timeout_seconds
        calls["count"] += 1
        return {"return_code": 0, "stdout": "", "stderr": "", "timed_out": False}

    recipes = (
        SmokeTestRecipe(
            name="generic",
            kwargs={"output_path": str(tmp_path / "generic.txt")},
            expected_outputs=(str(tmp_path / "generic.txt"),),
        ),
        SmokeTestRecipe(
            name="markers",
            kwargs={"output_path": str(tmp_path / "markers.txt")},
            expected_outputs=(str(tmp_path / "markers.txt"),),
            focus_tags=("output_markers",),
        ),
        SmokeTestRecipe(
            name="paths",
            kwargs={"output_path": str(tmp_path / "paths.txt")},
            expected_outputs=(str(tmp_path / "paths.txt"),),
            focus_tags=("output_paths",),
        ),
    )

    outcome = run_onboarding_refinement_loop(
        _successful_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=recipes,
        command_runner=_runner,
        budget=OnboardingBudget(max_iterations=2, max_total_seconds=60),
    )

    assert outcome.success is False
    assert [result.name for result in outcome.smoke_results] == ["generic", "paths"]


def test_run_onboarding_refinement_loop_allows_more_repeats_for_output_path_focus(tmp_path: Path) -> None:
    def _runner(command: str, *, cwd=None, timeout_seconds=30):
        del command, cwd, timeout_seconds
        return {"return_code": 0, "stdout": "", "stderr": "", "timed_out": False}

    recipe = SmokeTestRecipe(
        name="paths",
        kwargs={"output_path": str(tmp_path / "result.txt")},
        expected_outputs=(str(tmp_path / "result.txt"),),
        focus_tags=("output_paths",),
    )

    outcome = run_onboarding_refinement_loop(
        _successful_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=(recipe, recipe, recipe),
        command_runner=_runner,
        budget=OnboardingBudget(max_iterations=3, max_total_seconds=60),
    )

    assert outcome.success is False
    assert outcome.stalled is True
    assert outcome.budget_exhausted is False
    assert outcome.iterations == 3


def test_adapt_recipe_for_output_focus_narrows_to_missing_outputs(tmp_path: Path) -> None:
    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={"output_path": str(tmp_path / "a.txt")},
        expected_outputs=(str(tmp_path / "a.txt"), str(tmp_path / "b.txt")),
        description="broad recipe",
    )
    from bio_harness.core.onboarding_fixtures import SmokeTestResult

    smoke_result = SmokeTestResult(
        name="emit",
        passed=False,
        command="printf 'ok\\n' > a.txt",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=(str(tmp_path / "a.txt"), str(tmp_path / "b.txt")),
        produced_outputs=(str(tmp_path / "a.txt"),),
        timed_out=False,
        failure_reason="missing_expected_outputs",
        duration_seconds=0.01,
    )

    adapted = _adapt_recipe_for_focus(
        recipe,
        draft=_successful_draft(),
        current_focus="output_completeness",
        latest_smoke_result=smoke_result,
    )

    assert adapted.expected_outputs == (str(tmp_path / "b.txt"),)
    assert "narrowed to missing outputs" in adapted.description
    assert adapted.diagnostic_only is False


def test_adapt_recipe_for_marker_focus_narrows_missing_markers_and_clears_outputs(tmp_path: Path) -> None:
    output_path = tmp_path / "out.txt"
    output_path.write_text("ok\n", encoding="utf-8")
    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={"output_path": str(output_path)},
        expected_outputs=(str(output_path),),
        expected_substrings=("DONE", "SUMMARY"),
        description="marker recipe",
    )

    from bio_harness.core.onboarding_fixtures import SmokeTestResult

    smoke_result = SmokeTestResult(
        name="emit",
        passed=False,
        command="printf 'DONE\\n' > out.txt",
        return_code=0,
        stdout="DONE",
        stderr="",
        expected_outputs=(str(output_path),),
        produced_outputs=(str(output_path),),
        timed_out=False,
        failure_reason="missing_expected_substring:SUMMARY",
        duration_seconds=0.01,
    )

    adapted = _adapt_recipe_for_focus(
        recipe,
        draft=_successful_draft(),
        current_focus="output_markers",
        latest_smoke_result=smoke_result,
    )

    assert adapted.expected_outputs == ()
    assert adapted.expected_substrings == ("SUMMARY",)
    assert "narrowed to missing markers" in adapted.description
    assert adapted.diagnostic_only is False


def test_adapt_recipe_for_command_flag_focus_uses_required_kwargs_and_diagnostic_mode(tmp_path: Path) -> None:
    draft = _successful_draft()
    draft["parameters"]["threads"] = {
        "type": "integer",
        "description": "Threads.",
        "required": False,
    }
    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={"output_path": str(tmp_path / "out.txt"), "threads": 8},
        expected_outputs=(str(tmp_path / "out.txt"),),
        expected_substrings=("DONE",),
    )
    from bio_harness.core.onboarding_fixtures import SmokeTestResult

    smoke_result = SmokeTestResult(
        name="emit",
        passed=False,
        command="tool --threads 8",
        return_code=2,
        stdout="",
        stderr="unrecognized option --threads",
        expected_outputs=(str(tmp_path / "out.txt"),),
        produced_outputs=(),
        timed_out=False,
        failure_reason="unexpected_return_code:2",
        duration_seconds=0.01,
    )

    adapted = _adapt_recipe_for_focus(
        recipe,
        draft=draft,
        current_focus="command_flags",
        latest_smoke_result=smoke_result,
    )

    assert adapted.kwargs == {"output_path": str(tmp_path / "out.txt")}
    assert adapted.expected_outputs == ()
    assert adapted.expected_substrings == ()
    assert adapted.diagnostic_only is True
    assert "diagnostic required-args probe" in adapted.description


def test_run_onboarding_refinement_loop_diagnostic_pass_does_not_count_as_full_success(tmp_path: Path) -> None:
    commands: list[str] = []

    def _runner(command: str, *, cwd=None, timeout_seconds=30):
        del cwd, timeout_seconds
        commands.append(command)
        return {"return_code": 0, "stdout": "", "stderr": "", "timed_out": False}

    recipe = SmokeTestRecipe(
        name="diagnostic",
        kwargs={"output_path": str(tmp_path / "out.txt")},
        expected_outputs=(),
        diagnostic_only=True,
    )

    outcome = run_onboarding_refinement_loop(
        _successful_draft(),
        {"source": "integration:test", "mode": "test"},
        smoke_recipes=(recipe,),
        command_runner=_runner,
        budget=OnboardingBudget(max_iterations=1, max_total_seconds=60),
        select_variants=False,
    )

    assert len(commands) == 1
    assert outcome.success is False
    assert outcome.stalled is False
    assert outcome.iterations == 1
