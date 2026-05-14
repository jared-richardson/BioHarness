"""Stepwise candidate-gate replay helpers for fast-signal fixtures.

These helpers execute deterministic candidate-gate checks against the real
stepwise harness methods. They are intentionally scoped to replaying saved
prefix/candidate states; they do not call an LLM and they do not synthesize a
scientific plan.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import tempfile
from typing import Any

from bio_harness.core.fast_signal import ReplayFixture, ReplayResult
from bio_harness.core.branch_stage_progress import (
    render_branch_stage_progress_hint,
    summarize_branch_stage_progress,
)
from bio_harness.harness.config import HarnessConfig
from scripts.run_agent_e2e_harness import AgentE2EHarness
from scripts import run_agent_e2e_stepwise_loop as stepwise_loop


def run_candidate_gate_duplicate_replay(
    fixture: ReplayFixture,
    *,
    workspace_root: Path | str | None = None,
) -> ReplayResult:
    """Replay a candidate fixture through the duplicate-detection gate.

    Args:
        fixture: Candidate-gate fixture to replay.
        workspace_root: Optional workspace root for materialized fixture files.

    Returns:
        Replay result comparing observed duplicate behavior to expectation.
    """

    return run_candidate_gate_replay(fixture, workspace_root=workspace_root)


def run_candidate_gate_auto_replay(
    fixture: ReplayFixture,
    *,
    workspace_root: Path | str | None = None,
) -> ReplayResult:
    """Replay a candidate fixture with the fixture-requested gate mode.

    Args:
        fixture: Candidate-gate fixture to replay.
        workspace_root: Optional workspace root for materialized fixture files.

    Returns:
        Replay result from either the gate-observation path or the full
        candidate-evaluator path.
    """

    replay_mode = str(fixture.metadata.get("replay_mode", "") or "").strip()
    if replay_mode == "stepwise_candidate_evaluation" or "accepted" in fixture.expected_outcome:
        return run_candidate_evaluation_replay(fixture, workspace_root=workspace_root)
    return run_candidate_gate_replay(fixture, workspace_root=workspace_root)


def run_candidate_gate_replay(
    fixture: ReplayFixture,
    *,
    workspace_root: Path | str | None = None,
) -> ReplayResult:
    """Replay a candidate fixture through deterministic stepwise gates.

    Args:
        fixture: Candidate-gate fixture to replay.
        workspace_root: Optional workspace root for materialized fixture files.

    Returns:
        Replay result comparing observed gate behavior to fixture expectations.
    """

    if fixture.kind != "candidate_gate":
        return ReplayResult(
            fixture_id=fixture.id,
            kind=fixture.kind,
            passed=False,
            expected=fixture.expected_outcome,
            reason=f"Unsupported fixture kind for candidate replay: {fixture.kind}",
        )
    with _workspace_context(workspace_root) as selected_dir:
        harness, candidate = _build_fixture_harness(
            fixture=fixture,
            selected_dir=selected_dir,
        )
        candidate_step = _first_candidate_step(candidate)
        duplicate_prior = harness._stepwise_duplicate_completed_step(  # noqa: SLF001
            candidate_step=candidate_step,
        )
        prerequisite_rejection = harness._stepwise_annotation_prerequisite_rejection_reason(  # noqa: SLF001
            candidate_step=candidate_step,
        )
        branch_stage_rejection = harness._stepwise_branch_stage_rejection_reason(  # noqa: SLF001
            candidate_step=candidate_step,
        )
        missing_inputs = harness._stepwise_missing_candidate_inputs(  # noqa: SLF001
            candidate_step=candidate_step,
        )
        frontier_allowed = harness._stepwise_branch_frontier_allowed_tool_names()  # noqa: SLF001
        prerequisite_allowed = harness._stepwise_annotation_prerequisite_allowed_tool_names(  # noqa: SLF001
            frontier_allowed_tool_names=frontier_allowed,
        )
        effective_allowed = prerequisite_allowed or frontier_allowed
        branch_progress = summarize_branch_stage_progress(
            steps=harness._stepwise_plan_steps(),  # noqa: SLF001
            statuses=list(harness.run.get("step_statuses", [])),
            analysis_spec=harness._runtime_binding_analysis_spec(),  # noqa: SLF001
        )
        branch_hint = render_branch_stage_progress_hint(branch_progress)
        bound_candidate_step, binding_meta = _bound_appended_candidate_step(
            harness=harness,
            candidate_step=candidate_step,
        )
        observed = {
            "duplicate_rejected": bool(duplicate_prior),
            "duplicate_prior": duplicate_prior,
            "prerequisite_rejected": bool(prerequisite_rejection),
            "prerequisite_rejection": prerequisite_rejection,
            "branch_stage_rejected": bool(branch_stage_rejection),
            "branch_stage_rejection": branch_stage_rejection,
            "missing_inputs_rejected": bool(missing_inputs),
            "missing_inputs": missing_inputs,
            "allowed_tool_names": sorted(effective_allowed),
            "branch_stage_progress": branch_progress,
            "branch_stage_hint": branch_hint,
            "bound_candidate_arguments": bound_candidate_step.get("arguments", {})
            if isinstance(bound_candidate_step.get("arguments", {}), dict)
            else {},
            "candidate_binding_meta": binding_meta,
        }
        passed, reason = _candidate_gate_expectation_result(
            observed=observed,
            expected=fixture.expected_outcome,
        )
        return ReplayResult(
            fixture_id=fixture.id,
            kind=fixture.kind,
            passed=passed,
            observed=observed,
            expected=fixture.expected_outcome,
            reason=reason,
        )


def run_candidate_evaluation_replay(
    fixture: ReplayFixture,
    *,
    workspace_root: Path | str | None = None,
) -> ReplayResult:
    """Replay a candidate through the live stepwise candidate evaluator.

    The ordinary candidate-gate replay samples individual deterministic guard
    methods. This replay path exercises ``_evaluate_stepwise_candidate()``
    end-to-end, which catches bugs where early gate repair, normalization,
    semantic guards, and contract checks interact.

    Args:
        fixture: Candidate-gate fixture to replay.
        workspace_root: Optional workspace root for materialized fixture files.

    Returns:
        Replay result comparing the live evaluator outcome to fixture
        expectations.
    """

    if fixture.kind != "candidate_gate":
        return ReplayResult(
            fixture_id=fixture.id,
            kind=fixture.kind,
            passed=False,
            expected=fixture.expected_outcome,
            reason=f"Unsupported fixture kind for candidate evaluation: {fixture.kind}",
        )
    with _workspace_context(workspace_root) as selected_dir:
        harness, candidate = _build_fixture_harness(
            fixture=fixture,
            selected_dir=selected_dir,
        )
        prefix_step_count = len(harness._stepwise_plan_steps())  # noqa: SLF001
        contract = (
            deepcopy(fixture.metadata.get("contract", {}))
            if isinstance(fixture.metadata.get("contract", {}), dict)
            else {}
        )
        with _offline_replay_tool_validation():
            accepted, payload, reason = harness._evaluate_stepwise_candidate(  # noqa: SLF001
                contract=contract,
                candidate=candidate,
            )
        accepted_steps = (
            payload.get("plan", {}).get("plan", [])
            if isinstance(payload.get("plan", {}), dict)
            else []
        )
        accepted_step = (
            dict(accepted_steps[prefix_step_count])
            if len(accepted_steps) > prefix_step_count
            and isinstance(accepted_steps[prefix_step_count], dict)
            else {}
        )
        accepted_args = (
            accepted_step.get("arguments", {})
            if isinstance(accepted_step.get("arguments", {}), dict)
            else {}
        )
        observed = {
            "accepted": bool(accepted),
            "rejection_reason": reason,
            "accepted_tool_name": str(accepted_step.get("tool_name", "") or ""),
            "accepted_branch_id": str(accepted_step.get("branch_id", "") or ""),
            "accepted_step": accepted_step,
            "accepted_arguments": accepted_args,
            "accepted_plan_step_count": len(accepted_steps)
            if isinstance(accepted_steps, list)
            else 0,
        }
        passed, failure_reason = _candidate_evaluation_expectation_result(
            observed=observed,
            expected=fixture.expected_outcome,
        )
        return ReplayResult(
            fixture_id=fixture.id,
            kind=fixture.kind,
            passed=passed,
            observed=observed,
            expected=fixture.expected_outcome,
            reason=failure_reason,
        )


def _candidate_gate_expectation_result(
    *,
    observed: dict[str, Any],
    expected: dict[str, Any],
) -> tuple[bool, str]:
    checks: list[tuple[bool, str]] = []
    if "duplicate_rejected" in expected:
        expected_duplicate = bool(expected.get("duplicate_rejected", False))
        observed_duplicate = bool(observed.get("duplicate_rejected", False))
        checks.append(
            (
                observed_duplicate == expected_duplicate,
                "duplicate_rejected observed="
                f"{observed_duplicate} expected={expected_duplicate}",
            )
        )
    if "missing_inputs_rejected" in expected:
        expected_missing = bool(expected.get("missing_inputs_rejected", False))
        observed_missing = bool(observed.get("missing_inputs_rejected", False))
        checks.append(
            (
                observed_missing == expected_missing,
                "missing_inputs_rejected observed="
                f"{observed_missing} expected={expected_missing}",
            )
        )
    if "branch_stage_rejected" in expected:
        expected_branch = bool(expected.get("branch_stage_rejected", False))
        observed_branch = bool(observed.get("branch_stage_rejected", False))
        checks.append(
            (
                observed_branch == expected_branch,
                "branch_stage_rejected observed="
                f"{observed_branch} expected={expected_branch}",
            )
        )
    if "prerequisite_rejected" in expected:
        expected_prerequisite = bool(expected.get("prerequisite_rejected", False))
        observed_prerequisite = bool(observed.get("prerequisite_rejected", False))
        checks.append(
            (
                observed_prerequisite == expected_prerequisite,
                "prerequisite_rejected observed="
                f"{observed_prerequisite} expected={expected_prerequisite}",
            )
        )
    expected_allowed = expected.get("allowed_tool_names")
    if isinstance(expected_allowed, list):
        observed_allowed = observed.get("allowed_tool_names", [])
        checks.append(
            (
                observed_allowed == expected_allowed,
                f"allowed_tool_names observed={observed_allowed!r} "
                f"expected={expected_allowed!r}",
            )
        )
    contains = [
        str(item)
        for item in expected.get("missing_inputs_contains", [])
        if str(item).strip()
    ]
    if contains:
        missing_text = "\n".join(str(item) for item in observed.get("missing_inputs", []))
        for needle in contains:
            checks.append(
                (
                    needle in missing_text,
                    f"missing_inputs did not contain expected text: {needle}",
                )
            )
    branch_reason_contains = [
        str(item)
        for item in expected.get("branch_stage_reason_contains", [])
        if str(item).strip()
    ]
    if branch_reason_contains:
        reason_text = str(observed.get("branch_stage_rejection", "") or "")
        for needle in branch_reason_contains:
            checks.append(
                (
                    needle in reason_text,
                    f"branch_stage_rejection did not contain expected text: {needle}",
                )
            )
    prerequisite_reason_contains = [
        str(item)
        for item in expected.get("prerequisite_reason_contains", [])
        if str(item).strip()
    ]
    if prerequisite_reason_contains:
        reason_text = str(observed.get("prerequisite_rejection", "") or "")
        for needle in prerequisite_reason_contains:
            checks.append(
                (
                    needle in reason_text,
                    f"prerequisite_rejection did not contain expected text: {needle}",
                )
            )
    bound_arguments_contains = expected.get("bound_arguments_contains", {})
    if isinstance(bound_arguments_contains, dict):
        bound_args = (
            observed.get("bound_candidate_arguments", {})
            if isinstance(observed.get("bound_candidate_arguments", {}), dict)
            else {}
        )
        for key, needle in bound_arguments_contains.items():
            observed_value = str(bound_args.get(str(key), "") or "")
            needle_text = str(needle or "")
            checks.append(
                (
                    needle_text in observed_value,
                    f"bound_candidate_arguments[{key!r}]={observed_value!r} "
                    f"did not contain {needle_text!r}",
                )
            )
    bound_arguments_equals = expected.get("bound_arguments_equals", {})
    if isinstance(bound_arguments_equals, dict):
        bound_args = (
            observed.get("bound_candidate_arguments", {})
            if isinstance(observed.get("bound_candidate_arguments", {}), dict)
            else {}
        )
        for key, expected_value in bound_arguments_equals.items():
            observed_value = bound_args.get(str(key))
            checks.append(
                (
                    observed_value == expected_value,
                    f"bound_candidate_arguments[{key!r}] observed={observed_value!r} "
                    f"expected={expected_value!r}",
                )
            )
    expected_next = expected.get("branch_progress_next")
    if isinstance(expected_next, dict) and expected_next:
        observed_next = (
            observed.get("branch_stage_progress", {}).get("next_cell", {})
            if isinstance(observed.get("branch_stage_progress", {}), dict)
            else {}
        )
        for key, expected_value in expected_next.items():
            observed_value = (
                observed_next.get(key)
                if isinstance(observed_next, dict)
                else None
            )
            checks.append(
                (
                    observed_value == expected_value,
                    f"branch_progress_next[{key!r}] observed={observed_value!r} "
                    f"expected={expected_value!r}",
                )
            )
    hint_contains = [
        str(item)
        for item in expected.get("branch_progress_hint_contains", [])
        if str(item).strip()
    ]
    if hint_contains:
        hint = str(observed.get("branch_stage_hint", "") or "")
        for needle in hint_contains:
            checks.append(
                (
                    needle in hint,
                    f"branch_stage_hint did not contain expected text: {needle}",
                )
            )

    failures = [reason for passed, reason in checks if not passed]
    return not failures, "; ".join(failures)


def _candidate_evaluation_expectation_result(
    *,
    observed: dict[str, Any],
    expected: dict[str, Any],
) -> tuple[bool, str]:
    checks: list[tuple[bool, str]] = []
    if "accepted" in expected:
        expected_accepted = bool(expected.get("accepted", False))
        observed_accepted = bool(observed.get("accepted", False))
        checks.append(
            (
                observed_accepted == expected_accepted,
                f"accepted observed={observed_accepted} expected={expected_accepted}",
            )
        )
    expected_tool = str(expected.get("accepted_tool_name", "") or "").strip()
    if expected_tool:
        observed_tool = str(observed.get("accepted_tool_name", "") or "")
        checks.append(
            (
                observed_tool == expected_tool,
                f"accepted_tool_name observed={observed_tool!r} expected={expected_tool!r}",
            )
        )
    expected_branch = str(expected.get("accepted_branch_id", "") or "").strip()
    if expected_branch:
        observed_branch = str(observed.get("accepted_branch_id", "") or "")
        checks.append(
            (
                observed_branch == expected_branch,
                "accepted_branch_id observed="
                f"{observed_branch!r} expected={expected_branch!r}",
            )
        )
    accepted_arguments_contains = expected.get("accepted_arguments_contains", {})
    if isinstance(accepted_arguments_contains, dict):
        accepted_args = (
            observed.get("accepted_arguments", {})
            if isinstance(observed.get("accepted_arguments", {}), dict)
            else {}
        )
        for key, needle in accepted_arguments_contains.items():
            observed_value = str(accepted_args.get(str(key), "") or "")
            needle_text = str(needle or "")
            checks.append(
                (
                    needle_text in observed_value,
                    f"accepted_arguments[{key!r}]={observed_value!r} "
                    f"did not contain {needle_text!r}",
                )
            )
    accepted_arguments_equals = expected.get("accepted_arguments_equals", {})
    if isinstance(accepted_arguments_equals, dict):
        accepted_args = (
            observed.get("accepted_arguments", {})
            if isinstance(observed.get("accepted_arguments", {}), dict)
            else {}
        )
        for key, expected_value in accepted_arguments_equals.items():
            observed_value = accepted_args.get(str(key))
            checks.append(
                (
                    observed_value == expected_value,
                    f"accepted_arguments[{key!r}] observed={observed_value!r} "
                    f"expected={expected_value!r}",
                )
            )
    bound_arguments_contains = expected.get("bound_arguments_contains", {})
    if isinstance(bound_arguments_contains, dict):
        accepted_args = (
            observed.get("accepted_arguments", {})
            if isinstance(observed.get("accepted_arguments", {}), dict)
            else {}
        )
        for key, needle in bound_arguments_contains.items():
            observed_value = str(accepted_args.get(str(key), "") or "")
            needle_text = str(needle or "")
            checks.append(
                (
                    needle_text in observed_value,
                    f"bound_arguments[{key!r}]={observed_value!r} "
                    f"did not contain {needle_text!r}",
                )
            )
    bound_arguments_equals = expected.get("bound_arguments_equals", {})
    if isinstance(bound_arguments_equals, dict):
        accepted_args = (
            observed.get("accepted_arguments", {})
            if isinstance(observed.get("accepted_arguments", {}), dict)
            else {}
        )
        for key, expected_value in bound_arguments_equals.items():
            observed_value = accepted_args.get(str(key))
            checks.append(
                (
                    observed_value == expected_value,
                    f"bound_arguments[{key!r}] observed={observed_value!r} "
                    f"expected={expected_value!r}",
                )
            )
    reason_contains = [
        str(item)
        for item in expected.get("rejection_reason_contains", [])
        if str(item).strip()
    ]
    if reason_contains:
        reason_text = str(observed.get("rejection_reason", "") or "")
        for needle in reason_contains:
            checks.append(
                (
                    needle in reason_text,
                    f"rejection_reason did not contain expected text: {needle}",
                )
            )

    failures = [reason for passed, reason in checks if not passed]
    return not failures, "; ".join(failures)


def _bound_appended_candidate_step(
    *,
    harness: AgentE2EHarness,
    candidate_step: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the candidate after stepwise appended-step runtime binding."""

    prefix_steps = harness._stepwise_plan_steps()  # noqa: SLF001
    candidate_plan = {"thought_process": "", "plan": [*prefix_steps, candidate_step]}
    rebound_plan, binding_meta = harness._stepwise_rebind_appended_candidate_step(  # noqa: SLF001
        plan=candidate_plan,
        existing_step_count=len(prefix_steps),
    )
    rebound_steps = rebound_plan.get("plan", []) if isinstance(rebound_plan, dict) else []
    if not isinstance(rebound_steps, list) or len(rebound_steps) <= len(prefix_steps):
        return dict(candidate_step), binding_meta
    rebound_step = rebound_steps[len(prefix_steps)]
    return dict(rebound_step) if isinstance(rebound_step, dict) else dict(candidate_step), binding_meta


def _build_fixture_harness(
    *,
    fixture: ReplayFixture,
    selected_dir: Path,
) -> tuple[AgentE2EHarness, dict[str, Any]]:
    data_root = (
        selected_dir.parent / "inputs_readonly"
        if selected_dir.name == "selected"
        else selected_dir / "inputs_readonly"
    )
    data_root.mkdir(parents=True, exist_ok=True)
    _materialize_workspace_files(
        fixture.metadata.get("workspace_files", []),
        selected_dir=selected_dir,
        data_root=data_root,
    )
    harness = _build_stepwise_harness(selected_dir=selected_dir, data_root=data_root)
    prefix_state = _render_placeholders(
        fixture.prefix_state,
        selected_dir=selected_dir,
        data_root=data_root,
    )
    candidate = _render_placeholders(
        fixture.candidate,
        selected_dir=selected_dir,
        data_root=data_root,
    )
    harness.run["plan"] = deepcopy(prefix_state.get("plan", {"plan": []}))
    harness.run["step_statuses"] = list(prefix_state.get("step_statuses", []))
    harness.run["analysis_spec"] = deepcopy(prefix_state.get("analysis_spec", {}))
    if str(fixture.metadata.get("user_request", "") or "").strip():
        harness.run["user_request"] = str(fixture.metadata.get("user_request", "") or "")
    if isinstance(fixture.metadata.get("plan_contract", {}), dict):
        harness.run["plan_contract"] = deepcopy(fixture.metadata.get("plan_contract", {}))
    _make_replay_tool_availability_deterministic(harness)
    return harness, candidate


def _make_replay_tool_availability_deterministic(harness: AgentE2EHarness) -> None:
    """Treat registry-backed tools as available during offline fixture replay."""

    # Replay tests exercise harness gates, binding, and repair logic. They must
    # not turn red on a clean public machine just because optional native tools
    # such as snpEff, Salmon, or Fastp are not installed.
    harness.orchestrator._tool_binary_available = lambda _tool_name: True  # noqa: SLF001
    harness.orchestrator._skill_tools_available = lambda _skill: True  # noqa: SLF001


@contextmanager
def _offline_replay_tool_validation() -> Iterator[None]:
    """Disable native executable checks while evaluating replay fixtures."""

    original_missing_tools = stepwise_loop._missing_exec_tools_for_plan  # noqa: SLF001
    stepwise_loop._missing_exec_tools_for_plan = lambda _plan: []  # noqa: SLF001
    try:
        yield
    finally:
        stepwise_loop._missing_exec_tools_for_plan = original_missing_tools  # noqa: SLF001


def _build_stepwise_harness(*, selected_dir: Path, data_root: Path) -> AgentE2EHarness:
    cfg = HarnessConfig(
        prompt="Replay a fast-signal candidate gate fixture.",
        selected_dir=selected_dir,
        data_root=data_root,
        workspace_root=selected_dir,
        max_repairs=0,
        heartbeat_seconds=15,
        stall_timeout_seconds=45,
        live_process_grace_seconds=900,
        model_name=None,
        host=None,
        llm_backend=None,
        auto_install_missing_tools=False,
        allow_replan=True,
        allow_canonicalize=True,
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
        execution_mode="stepwise",
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()  # noqa: SLF001
    return harness


class _workspace_context:
    def __init__(self, workspace_root: Path | str | None) -> None:
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve(strict=False)
            if workspace_root is not None
            else None
        )
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        if self.workspace_root is not None:
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            return self.workspace_root
        self._tempdir = tempfile.TemporaryDirectory()
        selected_dir = Path(self._tempdir.name).resolve(strict=False) / "selected"
        selected_dir.mkdir(parents=True, exist_ok=True)
        return selected_dir

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._tempdir is not None:
            self._tempdir.cleanup()


def _materialize_workspace_files(
    files: Any,
    *,
    selected_dir: Path,
    data_root: Path,
) -> None:
    if not isinstance(files, list):
        return
    for item in files:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(
            _render_string_placeholders(
                item,
                selected_dir=selected_dir,
                data_root=data_root,
            )
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("fast-signal fixture placeholder\n", encoding="utf-8")


def _first_candidate_step(candidate: dict[str, Any]) -> dict[str, Any]:
    steps = candidate.get("plan", [])
    if isinstance(steps, list) and steps and isinstance(steps[0], dict):
        return dict(steps[0])
    return dict(candidate) if isinstance(candidate, dict) else {}


def _render_placeholders(
    value: Any,
    *,
    selected_dir: Path,
    data_root: Path,
) -> Any:
    if isinstance(value, dict):
        return {
            key: _render_placeholders(
                item,
                selected_dir=selected_dir,
                data_root=data_root,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _render_placeholders(
                item,
                selected_dir=selected_dir,
                data_root=data_root,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _render_string_placeholders(
            value,
            selected_dir=selected_dir,
            data_root=data_root,
        )
    return value


def _render_string_placeholders(
    value: str,
    *,
    selected_dir: Path,
    data_root: Path,
) -> str:
    return (
        value.replace("{{selected_dir}}", str(selected_dir))
        .replace("{{data_root}}", str(data_root))
    )
