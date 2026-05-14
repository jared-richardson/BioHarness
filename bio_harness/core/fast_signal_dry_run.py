"""Scripted dry-run scenarios for fast-signal candidate fixtures.

The dry-run layer sequences curated candidate fixtures through the real
stepwise gate/evaluator paths without LLM calls or tool execution. Each turn
keeps its own saved prefix state, which makes the scenario durable and
replayable from historical traces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from bio_harness.core.fast_signal import ReplayFixture, ReplayResult
from bio_harness.core.fast_signal_stepwise import run_candidate_gate_auto_replay


@dataclass(frozen=True)
class ScriptedDryRunResult:
    """Outcome from running a scripted fast-signal dry-run scenario.

    Attributes:
        scenario_id: Stable scenario identifier.
        passed: Whether every scripted turn matched its expected outcome.
        turn_results: Per-turn replay results.
        reason: Human-readable summary of failing turns.
    """

    scenario_id: str
    passed: bool
    turn_results: list[ReplayResult] = field(default_factory=list)
    reason: str = ""


def run_scripted_candidate_gate_scenario(
    *,
    scenario_id: str,
    fixtures: Sequence[ReplayFixture],
    workspace_root: Path | str | None = None,
) -> ScriptedDryRunResult:
    """Run an ordered candidate-gate dry-run scenario.

    Args:
        scenario_id: Stable scenario identifier.
        fixtures: Ordered candidate fixtures. Each fixture includes the prefix
            state for its turn.
        workspace_root: Optional parent workspace for materialized fixture
            files. When provided, each turn receives a unique child directory.

    Returns:
        Scenario result with all per-turn replay observations.
    """

    root = (
        Path(workspace_root).expanduser().resolve(strict=False)
        if workspace_root is not None
        else None
    )
    results: list[ReplayResult] = []
    for index, fixture in enumerate(fixtures, start=1):
        turn_root = None
        if root is not None:
            turn_root = root / f"turn_{index:03d}_{fixture.id}"
        results.append(
            run_candidate_gate_auto_replay(fixture, workspace_root=turn_root)
        )
    failures = [
        f"{result.fixture_id}: {result.reason or 'failed'}"
        for result in results
        if not result.passed
    ]
    return ScriptedDryRunResult(
        scenario_id=scenario_id,
        passed=not failures,
        turn_results=results,
        reason="; ".join(failures),
    )
