"""Shared official BioAgentBench harness invocation helpers.

This module centralizes delegated `run_agent_e2e.py` invocation assembly for
official BioAgentBench tasks so the official runner and ablation runner stay in
lockstep on prompt construction, benchmark-policy flags, and manifest runner
defaults.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from bio_harness.core.benchmark_policy import (
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    OFFICIAL_BIOAGENTBENCH_POLICY,
    normalize_benchmark_policy,
)
from bio_harness.core.bioagentbench_official import build_official_prompt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_SCRIPT = PROJECT_ROOT / "scripts" / "run_agent_e2e.py"
DEFAULT_BENCHMARK_LLM_TIMEOUT_SECONDS = 120
DEFAULT_BENCHMARK_PLANNER_ATTEMPT_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class OfficialHarnessInvocationOptions:
    """Command and env settings for delegated official benchmark runs.

    Attributes:
        benchmark_policy: Normalized benchmark-policy token to pass through.
        strict_llm_planning: Whether to force planner-only execution.
        planner_model_name: Optional planning model override.
        executor_model_name: Optional executor model override.
        model_name: Optional delegated `--model-name` CLI override.
        llm_backend: Optional delegated backend override.
        host: Optional delegated host override.
        planner_attempt_timeout_seconds: Optional planner timeout override.
        llm_timeout_seconds: Optional LLM timeout override.
        max_repairs: Maximum automatic repair cycles.
        heartbeat_seconds: Delegated heartbeat interval.
        stall_timeout_seconds: Delegated stall timeout.
        live_process_grace_seconds: Delegated live-process grace period.
        no_replan: Whether to disable replanning.
        no_canonicalize: Whether to disable canonicalization.
        quiet: Whether to reduce delegated runner output.
    """

    benchmark_policy: str = OFFICIAL_BIOAGENTBENCH_POLICY
    strict_llm_planning: bool = False
    planner_model_name: str = ""
    executor_model_name: str = ""
    model_name: str = ""
    llm_backend: str = ""
    host: str = ""
    planner_attempt_timeout_seconds: int = 0
    llm_timeout_seconds: int = 0
    max_repairs: int = 3
    heartbeat_seconds: int = 15
    stall_timeout_seconds: int = 45
    live_process_grace_seconds: int = 900
    no_replan: bool = False
    no_canonicalize: bool = False
    quiet: bool = False


def invocation_options_from_args(args: Any) -> OfficialHarnessInvocationOptions:
    """Return official invocation options from an argparse-style object.

    Args:
        args: Namespace-like object with benchmark runner attributes.

    Returns:
        A normalized options object for delegated harness execution.
    """

    return OfficialHarnessInvocationOptions(
        benchmark_policy=normalize_benchmark_policy(getattr(args, "benchmark_policy", "")),
        strict_llm_planning=bool(getattr(args, "strict_llm_planning", False)),
        planner_model_name=str(getattr(args, "planner_model_name", "") or "").strip(),
        executor_model_name=str(getattr(args, "executor_model_name", "") or "").strip(),
        model_name=str(getattr(args, "model_name", "") or "").strip(),
        llm_backend=str(getattr(args, "llm_backend", "") or "").strip(),
        host=str(getattr(args, "host", "") or "").strip(),
        planner_attempt_timeout_seconds=_coerce_int(
            getattr(args, "planner_attempt_timeout_seconds", 0),
        ),
        llm_timeout_seconds=_coerce_int(getattr(args, "llm_timeout_seconds", 0)),
        max_repairs=_coerce_int(getattr(args, "max_repairs", 3), default=3),
        heartbeat_seconds=_coerce_int(getattr(args, "heartbeat_seconds", 15), default=15),
        stall_timeout_seconds=_coerce_int(getattr(args, "stall_timeout_seconds", 45), default=45),
        live_process_grace_seconds=_coerce_int(
            getattr(args, "live_process_grace_seconds", 900),
            default=900,
        ),
        no_replan=bool(getattr(args, "no_replan", False)),
        no_canonicalize=bool(getattr(args, "no_canonicalize", False)),
        quiet=bool(getattr(args, "quiet", False)),
    )


def build_harness_env(
    options: OfficialHarnessInvocationOptions,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the delegated harness environment for one benchmark run.

    Args:
        options: Delegated invocation settings.
        environ: Optional source environment. Defaults to `os.environ`.

    Returns:
        A mutable environment mapping for the harness subprocess.
    """

    env = dict(environ or os.environ)
    benchmark_policy = normalize_benchmark_policy(options.benchmark_policy)
    default_model_name = "qwen3-coder-next:latest"
    if options.strict_llm_planning:
        env["BIO_HARNESS_STRICT_LLM_PLANNING"] = "1"
    if benchmark_policy == BIOAGENTBENCH_PLANNING_STRICT_POLICY:
        env.setdefault("BIO_HARNESS_PLANNER_HIERARCHICAL_MODE", "always")
    if options.planner_model_name:
        env["BIO_HARNESS_MODEL_HEAVY"] = options.planner_model_name
    if options.executor_model_name:
        env["BIO_HARNESS_MODEL"] = options.executor_model_name
    if benchmark_policy == BIOAGENTBENCH_PLANNING_STRICT_POLICY and str(env.get("BIO_HARNESS_MODEL", "")).strip() == "":
        env["BIO_HARNESS_MODEL"] = default_model_name
    if (
        benchmark_policy == BIOAGENTBENCH_PLANNING_STRICT_POLICY
        and not options.planner_model_name
        and str(env.get("BIO_HARNESS_MODEL_HEAVY", "")).strip() == ""
    ):
        fallback_planner = options.executor_model_name or str(env.get("BIO_HARNESS_MODEL", "")).strip()
        if fallback_planner:
            env["BIO_HARNESS_MODEL_HEAVY"] = fallback_planner
    if options.planner_attempt_timeout_seconds > 0:
        env["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] = str(options.planner_attempt_timeout_seconds)
    if options.llm_timeout_seconds > 0:
        env["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] = str(options.llm_timeout_seconds)
    return env


def apply_manifest_runner_defaults(
    env: Mapping[str, str],
    *,
    entry: dict[str, Any],
    options: OfficialHarnessInvocationOptions,
) -> dict[str, str]:
    """Apply manifest runner defaults on top of a delegated harness env.

    Args:
        env: Base harness environment.
        entry: Manifest task entry.
        options: Delegated invocation settings.

    Returns:
        An environment with manifest runner defaults merged in.
    """

    merged = dict(env)
    defaults = entry.get("runner_defaults", {}) if isinstance(entry.get("runner_defaults", {}), dict) else {}

    strict_default = defaults.get("strict_llm_planning")
    if (
        not options.strict_llm_planning
        and str(merged.get("BIO_HARNESS_STRICT_LLM_PLANNING", "")).strip() == ""
        and strict_default is not None
    ):
        merged["BIO_HARNESS_STRICT_LLM_PLANNING"] = "1" if bool(strict_default) else "0"

    planner_timeout_default = _coerce_int(defaults.get("planner_attempt_timeout_seconds", 0))
    if (
        options.planner_attempt_timeout_seconds <= 0
        and str(merged.get("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", "")).strip() == ""
        and planner_timeout_default > 0
    ):
        merged["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] = str(planner_timeout_default)

    llm_timeout_default = _coerce_int(defaults.get("llm_timeout_seconds", 0))
    if (
        options.llm_timeout_seconds <= 0
        and str(merged.get("BIO_HARNESS_LLM_TIMEOUT_SECONDS", "")).strip() == ""
        and llm_timeout_default > 0
    ):
        merged["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] = str(llm_timeout_default)

    hierarchical_default = str(defaults.get("planner_hierarchical_mode", "") or "").strip().lower()
    if hierarchical_default in {"off", "auto", "always", "hierarchical"} and str(
        merged.get("BIO_HARNESS_PLANNER_HIERARCHICAL_MODE", "")
    ).strip() == "":
        merged["BIO_HARNESS_PLANNER_HIERARCHICAL_MODE"] = (
            "always" if hierarchical_default == "hierarchical" else hierarchical_default
        )

    planner_model_default = str(defaults.get("planner_model_name", "") or "").strip()
    if (
        planner_model_default
        and not options.planner_model_name
        and str(merged.get("BIO_HARNESS_MODEL_HEAVY", "")).strip() == ""
    ):
        merged["BIO_HARNESS_MODEL_HEAVY"] = planner_model_default

    executor_model_default = str(defaults.get("executor_model_name", "") or "").strip()
    if (
        executor_model_default
        and not options.executor_model_name
        and str(merged.get("BIO_HARNESS_MODEL", "")).strip() == ""
    ):
        merged["BIO_HARNESS_MODEL"] = executor_model_default

    benchmark_policy = normalize_benchmark_policy(options.benchmark_policy)
    if benchmark_policy in {BIOAGENTBENCH_PLANNING_STRICT_POLICY, OFFICIAL_BIOAGENTBENCH_POLICY}:
        merged.setdefault(
            "BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS",
            str(DEFAULT_BENCHMARK_PLANNER_ATTEMPT_TIMEOUT_SECONDS),
        )
        merged.setdefault(
            "BIO_HARNESS_LLM_TIMEOUT_SECONDS",
            str(DEFAULT_BENCHMARK_LLM_TIMEOUT_SECONDS),
        )
    return merged


def build_official_harness_command(
    entry: dict[str, Any],
    *,
    selected_dir: Path,
    options: OfficialHarnessInvocationOptions,
    result_json: Path | None = None,
    extra_cli_args: Sequence[str] = (),
) -> list[str]:
    """Build the delegated `run_agent_e2e.py` command for one official task.

    Args:
        entry: Manifest task entry.
        selected_dir: Selected output directory for the run.
        options: Delegated invocation settings.
        result_json: Optional explicit result JSON path.
        extra_cli_args: Additional delegated CLI args layered last.

    Returns:
        The command argv for the delegated harness subprocess.
    """

    resolved_result_json = result_json or (selected_dir / "result.json")
    cmd = [
        sys.executable,
        str(HARNESS_SCRIPT),
        "--prompt",
        build_official_prompt(entry, selected_dir=selected_dir),
        "--selected-dir",
        str(selected_dir),
        "--data-root",
        str(Path(str(entry.get("data_root", "") or "")).resolve(strict=False)),
        "--benchmark-policy",
        normalize_benchmark_policy(options.benchmark_policy),
        "--max-repairs",
        str(int(options.max_repairs)),
        "--heartbeat-seconds",
        str(int(options.heartbeat_seconds)),
        "--stall-timeout-seconds",
        str(int(options.stall_timeout_seconds)),
        "--live-process-grace-seconds",
        str(int(options.live_process_grace_seconds)),
        "--result-json",
        str(resolved_result_json),
    ]
    if options.model_name:
        cmd.extend(["--model-name", options.model_name])
    if options.llm_backend:
        cmd.extend(["--llm-backend", options.llm_backend])
    if options.host:
        cmd.extend(["--host", options.host])
    cmd.extend(str(arg) for arg in extra_cli_args)
    if options.no_replan:
        cmd.append("--no-replan")
    if options.no_canonicalize:
        cmd.append("--no-canonicalize")
    if options.quiet:
        cmd.append("--quiet")
    return cmd


def _coerce_int(value: Any, *, default: int = 0) -> int:
    """Return an int value or a fallback default.

    Args:
        value: Value to convert.
        default: Fallback when conversion fails.

    Returns:
        An integer representation of `value` or `default`.
    """

    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)
