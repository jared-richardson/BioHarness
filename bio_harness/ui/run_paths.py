"""Helpers for resolving UI run paths.

These helpers keep benchmark-oriented UI runs aligned with the backend's
selected-directory semantics without changing the normal interactive workflow.
"""

from __future__ import annotations

from typing import Any, Mapping

from bio_harness.core.benchmark_policy import is_blind_bioagentbench_policy


def resolve_effective_chat_selected_dir(
    run: Mapping[str, Any] | None,
    *,
    session_selected_dir: str,
    benchmark_policy: str,
) -> str:
    """Return the selected directory the chat run should actually execute in.

    For normal interactive UI use, the selected directory remains whatever the
    user chose in the workspace. For blind BioAgentBench modes, the effective
    selected directory must be the concrete run bundle so the prompt's
    ``current run directory`` language maps to one deterministic output root.

    Args:
        run: In-memory run record, or ``None`` when not yet available.
        session_selected_dir: Directory currently selected in the UI session.
        benchmark_policy: Active benchmark policy token.

    Returns:
        The directory that should be passed into plan normalization and
        execution.
    """
    selected_dir = str(session_selected_dir or "").strip()
    if not is_blind_bioagentbench_policy(benchmark_policy):
        return selected_dir
    if not isinstance(run, Mapping):
        return selected_dir
    run_dir = str(run.get("run_dir", "") or "").strip()
    return run_dir or selected_dir
