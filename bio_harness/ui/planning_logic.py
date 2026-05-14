"""Background-safe execution-plan construction for the chat UI."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Mapping

from bio_harness.core.contracts import assess_plan_contract
from bio_harness.ui.auto_plan import is_actionable_execution_plan, normalize_ui_auto_plan
from bio_harness.ui.bioagentbench_ui_support import apply_benchmark_prompt_contract_seed
from bio_harness.ui.direct_skill_requests import (
    build_direct_single_skill_plan,
    decorate_direct_single_skill_request,
    select_execution_contract,
)
from bio_harness.workflows.templates import build_bootstrap_execution_plan

logger = logging.getLogger(__name__)


def build_ui_execution_plan(
    *,
    planner_call: Callable[[str], dict[str, Any]],
    orchestrator: Any,
    user_text: str,
    request_with_scope: str,
    benchmark_prompt_active: bool,
    benchmark_policy: str,
    direct_request_contract: Mapping[str, Any] | None,
    scoped_contract: Mapping[str, Any] | None,
    selected_dir: str,
    data_root: str,
    project_root: str,
    timeout_seconds: int,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Build a normalized executable plan for one chat execution request.

    Args:
        planner_call: Callable used for model-backed plan generation.
        orchestrator: Shared orchestrator instance.
        user_text: Original user text.
        request_with_scope: Scoped request text used for planning.
        benchmark_prompt_active: Whether the request is a UI benchmark prompt.
        benchmark_policy: Benchmark policy to apply during normalization.
        direct_request_contract: Contract inferred from the direct request text.
        scoped_contract: Contract inferred from the scoped request text.
        selected_dir: Run-selected output directory.
        data_root: Resolved input data root.
        project_root: Repository root path.
        timeout_seconds: Maximum planning time before failing.
        cancel_event: Optional threading event checked between phases; raised
            as ``RuntimeError`` when set.

    Returns:
        Planner payload containing the normalized plan and derived metadata.

    Raises:
        RuntimeError: When planning fails, times out, or is cancelled.
    """
    if not str(request_with_scope or "").strip():
        raise RuntimeError("Planning requires a non-empty request context.")
    direct_contract = dict(direct_request_contract or {})
    scoped_contract_payload = dict(scoped_contract or {})
    if benchmark_prompt_active:
        direct_contract = apply_benchmark_prompt_contract_seed(direct_contract, user_text)
        scoped_contract_payload = apply_benchmark_prompt_contract_seed(scoped_contract_payload, user_text)

    effective_contract = select_execution_contract(
        request_with_scope if benchmark_prompt_active else user_text,
        direct_contract,
        scoped_contract_payload,
    )
    def _check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Planning cancelled.")

    direct_plan = build_direct_single_skill_plan(
        request_with_scope if benchmark_prompt_active else user_text,
        effective_contract,
    )
    _check_cancelled()
    if direct_plan is not None:
        auto_plan = direct_plan
    else:
        prompt_text = request_with_scope
        if not benchmark_prompt_active:
            prompt_text = decorate_direct_single_skill_request(prompt_text, direct_contract)
        auto_plan = _call_with_timeout(
            lambda: planner_call(prompt_text),
            timeout_seconds=timeout_seconds,
            timeout_context="Auto-plan generation",
            cancel_event=cancel_event,
        )

    _check_cancelled()
    auto_plan, auto_plan_meta = normalize_ui_auto_plan(
        auto_plan if isinstance(auto_plan, dict) else {},
        orchestrator=orchestrator,
        user_request=request_with_scope,
        contract=effective_contract,
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        benchmark_policy=benchmark_policy,
    )
    auto_steps = auto_plan.get("plan", []) if isinstance(auto_plan, dict) else []
    if (not auto_steps) or (not is_actionable_execution_plan(auto_plan)):
        logger.warning(
            "Planner returned empty or non-actionable plan (steps=%d, actionable=%s). "
            "Falling back to bootstrap execution plan.  User request: %.120s",
            len(auto_steps),
            is_actionable_execution_plan(auto_plan) if auto_steps else "N/A",
            request_with_scope,
        )
        auto_plan = build_bootstrap_execution_plan(data_root)
    coverage = assess_plan_contract(auto_plan, effective_contract)
    return {
        "plan": auto_plan,
        "plan_contract": effective_contract,
        "contract_validation": coverage,
        "user_request": request_with_scope,
        "analysis_spec": auto_plan_meta.get("analysis_spec", {}),
        "protocol_validation": auto_plan_meta.get("protocol_validation", {}),
        "semantic_validation": auto_plan_meta.get("semantic_validation", {}),
        "protocol_normalization_meta": auto_plan_meta,
        "benchmark_policy": auto_plan_meta.get("benchmark_policy", benchmark_policy),
    }


def _call_with_timeout(
    callable_fn: Callable[[], dict[str, Any]],
    *,
    timeout_seconds: int,
    timeout_context: str,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run one planning callable with a hard timeout.

    If *cancel_event* is provided it is checked after the join and, when
    set, treated identically to a timeout so the caller gets a clean
    RuntimeError instead of stale results.
    """
    result_q: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result_q.put(("ok", callable_fn()))
        except Exception as exc:
            result_q.put(("err", exc))

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout=max(1, int(timeout_seconds)))
    if worker.is_alive() or (cancel_event is not None and cancel_event.is_set()):
        if cancel_event is not None and not cancel_event.is_set():
            cancel_event.set()
        raise RuntimeError(
            f"{timeout_context} timed out after {int(timeout_seconds)}s. "
            "Please retry with a shorter request or raise BIO_HARNESS_UI_PLAN_TIMEOUT_SECONDS."
        )
    if result_q.empty():
        raise RuntimeError(f"{timeout_context} failed without returning a result.")
    kind, payload = result_q.get_nowait()
    if kind == "err":
        if isinstance(payload, Exception):
            raise payload
        raise RuntimeError(str(payload))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{timeout_context} returned a non-dict planning payload.")
    return payload
