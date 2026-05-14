from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_agent_e2e_plan_bootstrap_support import (
    acquire_initial_plan,
    initialize_plan_preparation_state,
    validate_initial_plan_shape,
)


class _Cfg:
    def __init__(self, *, plan_path: Path | None, quiet: bool = True) -> None:
        self.plan_path = plan_path
        self.quiet = quiet


class _BioLLM:
    backend_name = "ollama"
    backend_label = "Ollama"
    host = "http://127.0.0.1:11434"


def test_initialize_plan_preparation_state_resets_run_bootstrap_fields() -> None:
    run = {
        "planning_attempts": ["stale"],
        "planner_strategy_used": "old",
        "fallback_catalog_summary": [{"x": 1}],
        "fallback_catalog_size": 1,
    }

    initialize_plan_preparation_state(run, catalog_summary=[{"name": "a"}, {"name": "b"}])

    assert run["planning_attempts"] == []
    assert run["planner_strategy_used"] == ""
    assert run["fallback_catalog_summary"] == [{"name": "a"}, {"name": "b"}]
    assert run["fallback_catalog_size"] == 2


def test_acquire_initial_plan_prefers_plan_path(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"plan": [{"tool_name": "bash_run"}]}), encoding="utf-8")

    result = acquire_initial_plan(
        cfg=_Cfg(plan_path=plan_path),
        run={},
        contract={},
        strict_llm_planning=False,
        generate_plan_with_supervision=lambda _contract: (_ for _ in ()).throw(AssertionError("unused")),
        build_contract_template_repair=lambda _cls: (_ for _ in ()).throw(AssertionError("unused")),
        is_local_model_loopback_blocked=lambda _exc: False,
        note_failure_signature=lambda _name: None,
        append_event=lambda **_kwargs: None,
        emit=lambda *_args, **_kwargs: None,
        biollm=_BioLLM(),
    )

    assert result.plan["plan"][0]["tool_name"] == "bash_run"
    assert result.planner_strategy_used == ""


def test_acquire_initial_plan_uses_supervised_plan_strategy() -> None:
    result = acquire_initial_plan(
        cfg=_Cfg(plan_path=None),
        run={},
        contract={"required_tool_hints": ["scanpy_workflow"]},
        strict_llm_planning=False,
        generate_plan_with_supervision=lambda _contract: (
            {"plan": [{"tool_name": "scanpy_workflow"}]},
            {"strategy": "hierarchical"},
        ),
        build_contract_template_repair=lambda _cls: (None, "", {}),
        is_local_model_loopback_blocked=lambda _exc: False,
        note_failure_signature=lambda _name: None,
        append_event=lambda **_kwargs: None,
        emit=lambda *_args, **_kwargs: None,
        biollm=_BioLLM(),
    )

    assert result.plan["plan"][0]["tool_name"] == "scanpy_workflow"
    assert result.planner_strategy_used == "hierarchical"


def test_acquire_initial_plan_uses_fallback_after_timeout_when_not_strict() -> None:
    run: dict[str, object] = {"run_uid": "run-1", "planning_attempts": [{"attempt": 1}]}
    noted: list[str] = []
    events: list[dict[str, object]] = []
    emitted: list[str] = []

    result = acquire_initial_plan(
        cfg=_Cfg(plan_path=None),
        run=run,
        contract={},
        strict_llm_planning=False,
        generate_plan_with_supervision=lambda _contract: (_ for _ in ()).throw(
            TimeoutError("planner timed out while thinking")
        ),
        build_contract_template_repair=lambda _cls: (
            {"plan": [{"tool_name": "bash_run"}]},
            "catalog_fallback",
            {"selected_pipeline_id": "pipeline-1"},
        ),
        is_local_model_loopback_blocked=lambda _exc: False,
        note_failure_signature=noted.append,
        append_event=lambda **kwargs: events.append(kwargs),
        emit=lambda message, **_kwargs: emitted.append(str(message)),
        biollm=_BioLLM(),
    )

    assert result.plan["plan"][0]["tool_name"] == "bash_run"
    assert run["planner_timeout_detected"] is True
    assert run["fallback_selection"] == {"selected_pipeline_id": "pipeline-1"}
    assert noted == ["planner_timeout"]
    assert events[0]["event_type"] == "REPAIR_APPLIED"
    assert events[0]["payload"]["action"] == "preplanning_catalog_fallback"
    assert "Planner unavailable" in emitted[0]


def test_acquire_initial_plan_raises_loopback_runtime_error_and_emits_event() -> None:
    run: dict[str, object] = {}
    noted: list[str] = []
    events: list[dict[str, object]] = []

    with pytest.raises(RuntimeError, match="Local model loopback access is blocked"):
        acquire_initial_plan(
            cfg=_Cfg(plan_path=None),
            run=run,
            contract={},
            strict_llm_planning=False,
            generate_plan_with_supervision=lambda _contract: (_ for _ in ()).throw(
                RuntimeError("loopback failure")
            ),
            build_contract_template_repair=lambda _cls: (None, "", {}),
            is_local_model_loopback_blocked=lambda _exc: True,
            note_failure_signature=noted.append,
            append_event=lambda **kwargs: events.append(kwargs),
            emit=lambda *_args, **_kwargs: None,
            biollm=_BioLLM(),
        )

    assert run["local_model_loopback_blocked_detected"] is True
    assert noted == ["local_model_loopback_blocked"]
    assert events[0]["event_type"] == "LOCAL_MODEL_LOOPBACK_BLOCKED"
    assert events[0]["payload"]["backend_name"] == "ollama"


def test_validate_initial_plan_shape_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="plan dictionary"):
        validate_initial_plan_shape([])


def test_validate_initial_plan_shape_rejects_missing_plan_key() -> None:
    with pytest.raises(ValueError, match="missing `plan` key"):
        validate_initial_plan_shape({"thought_process": "x"})
