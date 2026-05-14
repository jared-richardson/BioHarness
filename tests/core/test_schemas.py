"""Tests for bio_harness.core.schemas — shared disk artifact schemas."""

from __future__ import annotations

import pytest

from bio_harness.core.schemas import (
    TERMINAL_RUN_STATUSES,
    TRANSIENT_RUN_STATUSES,
    PlannerStatusSchema,
    RunEventSchema,
    RunExitSchema,
    RunManifestSchema,
    RunStateSchema,
    is_terminal_status,
    is_transient_status,
    safe_parse_manifest,
    safe_parse_planner_status,
    safe_parse_run_state,
)


# ---------------------------------------------------------------------------
# PlannerStatusSchema
# ---------------------------------------------------------------------------

class TestPlannerStatusSchema:

    def test_valid_planning_status(self) -> None:
        status = PlannerStatusSchema(
            run_id="abc123",
            status="planning",
            started_at="2026-03-27T10:00:00",
            updated_at="2026-03-27T10:00:10",
        )
        assert status.status == "planning"
        assert status.result_ready is False

    def test_valid_planned_status(self) -> None:
        status = PlannerStatusSchema(
            run_id="abc123",
            status="planned",
            result_ready=True,
            finished_at="2026-03-27T10:05:00",
        )
        assert status.status == "planned"
        assert status.result_ready is True

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(Exception):
            PlannerStatusSchema(run_id="abc", status="invalid_status")

    def test_allows_extra_fields(self) -> None:
        status = PlannerStatusSchema(
            run_id="abc",
            status="planning",
            some_future_field="value",
        )
        assert status.run_id == "abc"

    def test_safe_parse_returns_none_on_bad_data(self) -> None:
        assert safe_parse_planner_status({"status": "bogus"}) is None

    def test_safe_parse_returns_schema_on_good_data(self) -> None:
        result = safe_parse_planner_status({
            "run_id": "abc",
            "status": "planning",
        })
        assert result is not None
        assert result.run_id == "abc"

    def test_roundtrip_json(self) -> None:
        status = PlannerStatusSchema(
            run_id="test_run",
            status="planned",
            started_at="2026-03-27T10:00:00",
            updated_at="2026-03-27T10:05:00",
            result_ready=True,
        )
        json_str = status.model_dump_json()
        restored = PlannerStatusSchema.model_validate_json(json_str)
        assert restored.run_id == "test_run"
        assert restored.result_ready is True


# ---------------------------------------------------------------------------
# RunManifestSchema
# ---------------------------------------------------------------------------

class TestRunManifestSchema:

    def test_minimal_manifest(self) -> None:
        manifest = RunManifestSchema(run_id="r1")
        assert manifest.run_id == "r1"
        assert manifest.user_request == ""

    def test_full_manifest(self) -> None:
        manifest = RunManifestSchema(
            run_id="r1",
            user_request="Align reads",
            workspace_root="/workspace",
            benchmark_policy="strict",
        )
        assert manifest.user_request == "Align reads"

    def test_safe_parse_manifest_bad(self) -> None:
        assert safe_parse_manifest({"no_run_id": True}) is None

    def test_safe_parse_manifest_good(self) -> None:
        result = safe_parse_manifest({"run_id": "abc"})
        assert result is not None


# ---------------------------------------------------------------------------
# RunExitSchema
# ---------------------------------------------------------------------------

class TestRunExitSchema:

    def test_planning_exit(self) -> None:
        exit_data = RunExitSchema(
            run_id="r1",
            status="planning",
            started_at="2026-03-27T10:00:00",
        )
        assert exit_data.status == "planning"
        assert exit_data.finished_at is None

    def test_completed_exit(self) -> None:
        exit_data = RunExitSchema(
            run_id="r1",
            status="completed",
            finished_at="2026-03-27T11:00:00",
        )
        assert exit_data.status == "completed"


# ---------------------------------------------------------------------------
# RunEventSchema
# ---------------------------------------------------------------------------

class TestRunEventSchema:

    def test_basic_event(self) -> None:
        event = RunEventSchema(
            ts="2026-03-27T10:00:00",
            run_id="r1",
            event_type="PLAN_STARTED",
            agent="PlannerAgent",
        )
        assert event.event_type == "PLAN_STARTED"
        assert event.payload == {}

    def test_event_with_payload(self) -> None:
        event = RunEventSchema(
            ts="2026-03-27T10:00:00",
            run_id="r1",
            step_id=3,
            event_type="STEP_COMPLETED",
            severity="info",
            payload={"tool": "star_align", "duration_s": 120},
        )
        assert event.step_id == 3
        assert event.payload["tool"] == "star_align"


# ---------------------------------------------------------------------------
# RunStateSchema
# ---------------------------------------------------------------------------

class TestRunStateSchema:

    def test_minimal_state(self) -> None:
        state = RunStateSchema(run_id="r1")
        assert state.status == "initialized"
        assert state.step_statuses == []

    def test_full_state(self) -> None:
        state = RunStateSchema(
            run_id="r1",
            status="running",
            step_statuses=["completed", "running", "pending"],
            next_step_idx=1,
            policy_block_detected=True,
        )
        assert state.next_step_idx == 1
        assert state.policy_block_detected is True

    def test_safe_parse_state_good(self) -> None:
        result = safe_parse_run_state({"run_id": "abc"})
        assert result is not None

    def test_safe_parse_state_bad(self) -> None:
        assert safe_parse_run_state({"no_run_id": True}) is None

    def test_allows_extra_fields(self) -> None:
        state = RunStateSchema(
            run_id="r1",
            future_field_xyz="value",
        )
        assert state.run_id == "r1"


# ---------------------------------------------------------------------------
# Run lifecycle state machine
# ---------------------------------------------------------------------------

class TestRunLifecycle:

    def test_terminal_and_transient_are_disjoint(self) -> None:
        overlap = TERMINAL_RUN_STATUSES & TRANSIENT_RUN_STATUSES
        assert overlap == frozenset(), f"Statuses appear in both sets: {overlap}"

    def test_remediating_tools_is_transient(self) -> None:
        assert is_transient_status("remediating_tools")
        assert not is_terminal_status("remediating_tools")

    def test_repairing_is_transient(self) -> None:
        assert is_transient_status("repairing")
        assert not is_terminal_status("repairing")

    def test_completed_is_terminal(self) -> None:
        assert is_terminal_status("completed")
        assert not is_transient_status("completed")

    def test_failed_is_terminal(self) -> None:
        assert is_terminal_status("failed")

    def test_planning_is_transient(self) -> None:
        assert is_transient_status("planning")

    def test_running_is_transient(self) -> None:
        assert is_transient_status("running")

    def test_case_insensitive(self) -> None:
        assert is_terminal_status("COMPLETED")
        assert is_transient_status("Planning")
