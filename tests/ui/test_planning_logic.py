from __future__ import annotations

import threading
import time

import pytest

from bio_harness.ui.planning_logic import _call_with_timeout, build_ui_execution_plan


class _DummyOrchestrator:
    def build_analysis_spec(self, *args, **kwargs):
        return {
            "analysis_type": "generic",
            "selected_dir": kwargs.get("selected_dir", ""),
            "data_root": kwargs.get("data_root", ""),
        }


def test_build_ui_execution_plan_bootstraps_when_planner_returns_empty() -> None:
    payload = build_ui_execution_plan(
        planner_call=lambda _: {},
        orchestrator=_DummyOrchestrator(),
        user_text="Proceed with execution.",
        request_with_scope="Proceed with execution.",
        benchmark_prompt_active=False,
        benchmark_policy="scientific_harness",
        direct_request_contract={},
        scoped_contract={},
        selected_dir="/tmp/run",
        data_root="/tmp/data",
        project_root="/tmp/project",
        timeout_seconds=5,
    )

    assert payload["plan"]["plan"]
    assert payload["user_request"] == "Proceed with execution."


def test_call_with_timeout_raises_on_timeout() -> None:
    with pytest.raises(RuntimeError, match="timed out"):
        _call_with_timeout(
            lambda: time.sleep(10) or {},
            timeout_seconds=1,
            timeout_context="Test",
        )


def test_call_with_timeout_raises_when_cancel_event_set() -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match="timed out"):
        _call_with_timeout(
            lambda: {"plan": []},
            timeout_seconds=5,
            timeout_context="Test",
            cancel_event=cancel,
        )


def test_call_with_timeout_sets_cancel_event_on_timeout() -> None:
    cancel = threading.Event()
    with pytest.raises(RuntimeError, match="timed out"):
        _call_with_timeout(
            lambda: time.sleep(10) or {},
            timeout_seconds=1,
            timeout_context="Test",
            cancel_event=cancel,
        )
    assert cancel.is_set()


def test_call_with_timeout_succeeds_normally() -> None:
    result = _call_with_timeout(
        lambda: {"plan": [1, 2]},
        timeout_seconds=5,
        timeout_context="Test",
    )
    assert result == {"plan": [1, 2]}


def test_build_ui_execution_plan_respects_cancel_event() -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        build_ui_execution_plan(
            planner_call=lambda _: {"plan": [{"step": 1}]},
            orchestrator=_DummyOrchestrator(),
            user_text="test",
            request_with_scope="test",
            benchmark_prompt_active=False,
            benchmark_policy="scientific_harness",
            direct_request_contract={},
            scoped_contract={},
            selected_dir="/tmp/run",
            data_root="/tmp/data",
            project_root="/tmp/project",
            timeout_seconds=5,
            cancel_event=cancel,
        )


def test_build_ui_execution_plan_rejects_empty_request_context() -> None:
    with pytest.raises(RuntimeError, match="non-empty request context"):
        build_ui_execution_plan(
            planner_call=lambda _: {"plan": [{"step": 1}]},
            orchestrator=_DummyOrchestrator(),
            user_text="Proceed with execution now.",
            request_with_scope="",
            benchmark_prompt_active=False,
            benchmark_policy="scientific_harness",
            direct_request_contract={},
            scoped_contract={},
            selected_dir="/tmp/run",
            data_root="/tmp/data",
            project_root="/tmp/project",
            timeout_seconds=5,
        )


def test_build_ui_execution_plan_keeps_semantic_stringtie_tool_lock() -> None:
    payload = build_ui_execution_plan(
        planner_call=lambda _: {"plan": []},
        orchestrator=_DummyOrchestrator(),
        user_text=(
            "Proceed with execution now. I have an aligned BAM already. Quantify transcripts "
            "from /tmp/sample.bam using /tmp/genes.gtf."
        ),
        request_with_scope=(
            "Proceed with execution now. I have an aligned BAM already. Quantify transcripts "
            "from /tmp/sample.bam using /tmp/genes.gtf."
        ),
        benchmark_prompt_active=False,
        benchmark_policy="scientific_harness",
        direct_request_contract={},
        scoped_contract={
            "must_include_capabilities": ["quantification", "reference_inputs"],
            "explicit_tool_hints": ["stringtie_quant"],
            "required_tool_hints": ["stringtie_quant"],
        },
        selected_dir="/tmp/run",
        data_root="/tmp/data",
        project_root="/tmp/project",
        timeout_seconds=5,
    )

    assert payload["plan_contract"]["required_tool_hints"] == ["stringtie_quant"]
    assert payload["plan_contract"]["explicit_tool_hints"] == ["stringtie_quant"]
