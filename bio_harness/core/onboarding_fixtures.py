"""Smoke-test fixtures for deterministic tool onboarding.

This module provides a small execution layer for onboarding refinement loops.
It runs generated wrappers against bounded smoke-test recipes and records
structured results without introducing benchmark-path behavior changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping

from bio_harness.core.tool_probe import build_probe_env


@dataclass(frozen=True)
class SmokeTestRecipe:
    """One bounded smoke-test specification for onboarding.

    Attributes:
        name: Stable recipe identifier.
        kwargs: Wrapper keyword arguments for command rendering.
        expected_outputs: Paths that should exist after the smoke test.
        expected_substrings: Output substrings that should appear in stdout/stderr.
        forbidden_substrings: Output substrings that must not appear.
        expect_returncode: Expected process return code.
        timeout_seconds: Timeout for the smoke test.
        subprocess_calls: Expected subprocess count for policy budgeting.
        cwd: Optional working directory for command execution.
        description: Optional human-readable description.
        focus_tags: Optional refinement-focus hints this recipe best exercises.
        diagnostic_only: Whether a passing result should be treated as
            diagnostic evidence instead of full onboarding success.
    """

    name: str
    kwargs: Mapping[str, Any]
    expected_outputs: tuple[str, ...] = ()
    expected_substrings: tuple[str, ...] = ()
    forbidden_substrings: tuple[str, ...] = ()
    expect_returncode: int = 0
    timeout_seconds: int = 30
    subprocess_calls: int = 1
    cwd: str = ""
    description: str = ""
    focus_tags: tuple[str, ...] = ()
    diagnostic_only: bool = False


@dataclass(frozen=True)
class SmokeTestResult:
    """Structured result of one onboarding smoke test."""

    name: str
    passed: bool
    command: str
    return_code: int | None
    stdout: str
    stderr: str
    expected_outputs: tuple[str, ...]
    produced_outputs: tuple[str, ...]
    timed_out: bool
    failure_reason: str
    duration_seconds: float


@dataclass(frozen=True)
class SmokeProgressAssessment:
    """Deterministic progress snapshot for one smoke-test attempt.

    Attributes:
        score: Aggregate progress score; higher is better.
        output_hits: Number of expected outputs produced.
        output_total: Total expected output count.
        expected_substring_hits: Number of required substrings observed.
        expected_substring_total: Total required substring count.
        forbidden_substring_clear: Number of forbidden substrings absent.
        forbidden_substring_total: Total forbidden substring count.
        return_code_matched: Whether the observed return code matched the recipe.
        passed: Whether the smoke test passed completely.
    """

    score: int
    output_hits: int
    output_total: int
    expected_substring_hits: int
    expected_substring_total: int
    forbidden_substring_clear: int
    forbidden_substring_total: int
    return_code_matched: bool
    passed: bool


def run_wrapper_smoke_test(
    draft: Mapping[str, Any],
    recipe: SmokeTestRecipe,
    *,
    command_runner: Callable[..., Any] | None = None,
) -> SmokeTestResult:
    """Render and execute one generated-wrapper smoke test.

    Args:
        draft: Skill draft containing wrapper code and stable skill name.
        recipe: Smoke-test specification.
        command_runner: Optional injected command runner for tests.

    Returns:
        Structured smoke-test result.
    """

    started = time.monotonic()
    command = _render_wrapper_command(draft, recipe.kwargs)
    if not command:
        return SmokeTestResult(
            name=recipe.name,
            passed=False,
            command="",
            return_code=None,
            stdout="",
            stderr="",
            expected_outputs=tuple(recipe.expected_outputs),
            produced_outputs=(),
            timed_out=False,
            failure_reason="wrapper_render_failed",
            duration_seconds=round(time.monotonic() - started, 6),
        )

    runner = command_runner or _default_command_runner
    result = runner(
        command,
        cwd=recipe.cwd or None,
        timeout_seconds=recipe.timeout_seconds,
    )
    stdout = str(result.get("stdout", "") or "")
    stderr = str(result.get("stderr", "") or "")
    return_code = result.get("return_code")
    timed_out = bool(result.get("timed_out", False))

    expected_outputs = tuple(str(path) for path in recipe.expected_outputs)
    produced_outputs = tuple(path for path in expected_outputs if Path(path).exists())
    combined_output = f"{stdout}\n{stderr}"
    failure_reason = _determine_failure_reason(
        recipe=recipe,
        return_code=return_code,
        timed_out=timed_out,
        combined_output=combined_output,
        produced_outputs=produced_outputs,
    )
    return SmokeTestResult(
        name=recipe.name,
        passed=failure_reason == "",
        command=command,
        return_code=int(return_code) if return_code is not None else None,
        stdout=stdout,
        stderr=stderr,
        expected_outputs=expected_outputs,
        produced_outputs=produced_outputs,
        timed_out=timed_out,
        failure_reason=failure_reason,
        duration_seconds=round(time.monotonic() - started, 6),
    )


def assess_smoke_test_progress(
    recipe: SmokeTestRecipe,
    smoke_result: SmokeTestResult,
) -> SmokeProgressAssessment:
    """Score one smoke-test attempt for deterministic refinement decisions.

    Args:
        recipe: Recipe that defined the smoke-test expectations.
        smoke_result: Observed smoke-test result.

    Returns:
        Deterministic progress assessment.
    """

    combined_output = f"{smoke_result.stdout}\n{smoke_result.stderr}"
    output_total = len(recipe.expected_outputs)
    output_hits = len(smoke_result.produced_outputs)
    expected_total = len(recipe.expected_substrings)
    expected_hits = sum(1 for token in recipe.expected_substrings if token in combined_output)
    forbidden_total = len(recipe.forbidden_substrings)
    forbidden_clear = sum(1 for token in recipe.forbidden_substrings if token not in combined_output)
    return_code_matched = smoke_result.return_code == recipe.expect_returncode

    score = 0
    if smoke_result.command.strip():
        score += 5
    if not smoke_result.timed_out:
        score += 15
    if return_code_matched:
        score += 25
    score += 30 if output_total == 0 else round(30 * output_hits / output_total)
    score += 15 if expected_total == 0 else round(15 * expected_hits / expected_total)
    score += 10 if forbidden_total == 0 else round(10 * forbidden_clear / forbidden_total)
    if smoke_result.passed:
        score += 100

    return SmokeProgressAssessment(
        score=score,
        output_hits=output_hits,
        output_total=output_total,
        expected_substring_hits=expected_hits,
        expected_substring_total=expected_total,
        forbidden_substring_clear=forbidden_clear,
        forbidden_substring_total=forbidden_total,
        return_code_matched=return_code_matched,
        passed=smoke_result.passed,
    )


def _render_wrapper_command(draft: Mapping[str, Any], kwargs: Mapping[str, Any]) -> str:
    """Render one wrapper command from a skill draft."""

    wrapper_code = str(draft.get("wrapper_code", "") or "").strip()
    func_name = str(draft.get("skill_name", "") or draft.get("name", "")).strip()
    if not wrapper_code or not func_name:
        return ""
    namespace: dict[str, Any] = {}
    exec(wrapper_code, namespace)
    candidate = namespace.get(func_name)
    if not callable(candidate):
        return ""
    return str(candidate(**dict(kwargs))).strip()


def _default_command_runner(
    command: str,
    *,
    cwd: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Run one smoke-test command with Pixi tools available."""

    env = build_probe_env()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=cwd or None,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "return_code": None,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
            "timed_out": True,
        }
    return {
        "return_code": int(completed.returncode),
        "stdout": str(completed.stdout or ""),
        "stderr": str(completed.stderr or ""),
        "timed_out": False,
    }


def _determine_failure_reason(
    *,
    recipe: SmokeTestRecipe,
    return_code: int | None,
    timed_out: bool,
    combined_output: str,
    produced_outputs: tuple[str, ...],
) -> str:
    """Return a stable failure label for a smoke-test result."""

    if timed_out:
        return "timed_out"
    if return_code != recipe.expect_returncode:
        return f"unexpected_return_code:{return_code}"
    for token in recipe.expected_substrings:
        if token not in combined_output:
            return f"missing_expected_substring:{token}"
    for token in recipe.forbidden_substrings:
        if token and token in combined_output:
            return f"forbidden_substring_present:{token}"
    if len(produced_outputs) != len(recipe.expected_outputs):
        return "missing_expected_outputs"
    return ""


__all__ = [
    "SmokeProgressAssessment",
    "SmokeTestRecipe",
    "SmokeTestResult",
    "assess_smoke_test_progress",
    "run_wrapper_smoke_test",
]
