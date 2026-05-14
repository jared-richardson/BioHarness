from __future__ import annotations

from bio_harness.core.onboarding_fixtures import (
    SmokeTestRecipe,
    SmokeTestResult,
    assess_smoke_test_progress,
)


def test_assess_smoke_test_progress_rewards_partial_output_progress() -> None:
    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={},
        expected_outputs=("a.txt", "b.txt"),
        expected_substrings=("done",),
    )
    initial = SmokeTestResult(
        name="emit",
        passed=False,
        command="emit",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=("a.txt", "b.txt"),
        produced_outputs=(),
        timed_out=False,
        failure_reason="missing_expected_substring:done",
        duration_seconds=0.01,
    )
    improved = SmokeTestResult(
        name="emit",
        passed=False,
        command="emit",
        return_code=0,
        stdout="done",
        stderr="",
        expected_outputs=("a.txt", "b.txt"),
        produced_outputs=("a.txt",),
        timed_out=False,
        failure_reason="missing_expected_outputs",
        duration_seconds=0.01,
    )

    initial_score = assess_smoke_test_progress(recipe, initial)
    improved_score = assess_smoke_test_progress(recipe, improved)

    assert improved_score.score > initial_score.score
    assert improved_score.output_hits == 1
    assert improved_score.expected_substring_hits == 1


def test_assess_smoke_test_progress_strongly_rewards_full_pass() -> None:
    recipe = SmokeTestRecipe(
        name="emit",
        kwargs={},
        expected_outputs=("a.txt",),
        forbidden_substrings=("error",),
    )
    partial = SmokeTestResult(
        name="emit",
        passed=False,
        command="emit",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=("a.txt",),
        produced_outputs=(),
        timed_out=False,
        failure_reason="missing_expected_outputs",
        duration_seconds=0.01,
    )
    passed = SmokeTestResult(
        name="emit",
        passed=True,
        command="emit",
        return_code=0,
        stdout="",
        stderr="",
        expected_outputs=("a.txt",),
        produced_outputs=("a.txt",),
        timed_out=False,
        failure_reason="",
        duration_seconds=0.01,
    )

    assert assess_smoke_test_progress(recipe, passed).score > assess_smoke_test_progress(recipe, partial).score
