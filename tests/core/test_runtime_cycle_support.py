from __future__ import annotations

from pathlib import Path

from scripts.run_agent_e2e_runtime_cycle_support import (
    apply_successful_repair_cycle,
    checkpoint_resume_after_repair,
    record_preflight_failure,
)


def test_record_preflight_failure_sets_terminal_fields() -> None:
    run = {"status": "planned", "error": "", "finished_at": ""}
    events: list[dict[str, object]] = []

    record_preflight_failure(
        run,
        message="missing references",
        append_event=lambda **kwargs: events.append(kwargs),
    )

    assert run["status"] == "failed"
    assert run["error"] == "missing references"
    assert run["finished_at"]
    assert events[0]["event_type"] == "PRECHECK_FAILED"
    assert events[0]["payload"]["reason"] == "missing references"


def test_checkpoint_resume_after_repair_advances_when_outputs_exist(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (selected_dir / "outputs" / "done.tsv").write_text("ok\n", encoding="utf-8")
    run = {
        "plan": {
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "bash_run",
                    "arguments": {"command": "printf 'x\\n' > outputs/done.tsv"},
                },
                {
                    "step_id": 2,
                    "tool_name": "bash_run",
                    "arguments": {"command": "printf 'y\\n' > outputs/pending.tsv"},
                },
            ]
        },
        "step_statuses": ["pending", "pending"],
        "next_step_idx": 0,
    }
    emitted: list[str] = []

    meta = checkpoint_resume_after_repair(
        run,
        selected_dir=selected_dir,
        emit=lambda message, **_kwargs: emitted.append(str(message)),
        quiet=True,
    )

    assert meta["changed"] is True
    assert meta["resume_idx"] == 1
    assert run["step_statuses"][0] == "completed"
    assert run["next_step_idx"] == 1
    assert emitted


def test_apply_successful_repair_cycle_records_history_and_repair_event(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "run_uid": "run-1",
        "plan": {
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "bash_run",
                    "arguments": {"command": "echo pending"},
                }
            ]
        },
        "step_statuses": ["pending"],
        "next_step_idx": 0,
        "status": "failed",
        "error": "boom",
        "auto_repair_attempts": {},
        "auto_repair_history": [],
    }
    events: list[dict[str, object]] = []

    meta = apply_successful_repair_cycle(
        run,
        failure_class="runtime_step_failure",
        action="replan_with_failure_context",
        details={"why": "unit_test"},
        selected_dir=selected_dir,
        append_event=lambda **kwargs: events.append(kwargs),
        emit=lambda *_args, **_kwargs: None,
        quiet=True,
    )

    assert run["status"] == "planned"
    assert run["error"] == ""
    assert run["auto_repair_attempts"]["runtime_step_failure"] == 1
    assert len(run["auto_repair_history"]) == 1
    assert meta["event"]["action"] == "replan_with_failure_context"
    assert events[0]["event_type"] == "REPAIR_APPLIED"
