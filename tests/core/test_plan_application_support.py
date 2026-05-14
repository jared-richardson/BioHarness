from __future__ import annotations

from scripts.run_agent_e2e_plan_application_support import (
    install_candidate_plan,
    plan_step_count,
    plan_step_diff_summary,
    plans_are_distinct,
)


def test_plan_step_count_handles_missing_or_nonlist_steps() -> None:
    assert plan_step_count(None) == 0
    assert plan_step_count({}) == 0
    assert plan_step_count({"plan": "not-a-list"}) == 0
    assert plan_step_count({"plan": [{"step_id": 1}, {"step_id": 2}]}) == 2


def test_plan_step_diff_summary_uses_after_plan_step_count() -> None:
    summary = plan_step_diff_summary(
        before_step_count=3,
        after_plan={"plan": [{"step_id": 1}]},
    )

    assert summary == {"before_step_count": 3, "after_step_count": 1}


def test_plans_are_distinct_compares_stable_json_payloads() -> None:
    assert plans_are_distinct({"plan": [{"step_id": 1}]}, {"plan": [{"step_id": 1}]}) is False
    assert plans_are_distinct({"plan": [{"step_id": 1}]}, {"plan": [{"step_id": 2}]}) is True


def test_install_candidate_plan_resets_run_state_when_requested() -> None:
    run = {
        "plan": {"plan": [{"step_id": 1}]},
        "step_statuses": ["failed"],
        "next_step_idx": 1,
        "status": "failed",
        "error": "boom",
    }
    candidate = {"plan": [{"step_id": 1}, {"step_id": 2}]}

    install_candidate_plan(
        run,
        candidate,
        reset_step_state=True,
        mark_planned=True,
        clear_error=True,
    )

    assert run["plan"] == candidate
    assert run["step_statuses"] == ["pending", "pending"]
    assert run["next_step_idx"] == 0
    assert run["status"] == "planned"
    assert run["error"] == ""
