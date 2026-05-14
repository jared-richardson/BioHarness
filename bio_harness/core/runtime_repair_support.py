"""Support helpers for runtime repair and result payload assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bio_harness.core.failure_reporting import build_failure_diagnosis
from bio_harness.core.install_receipts import write_install_receipt


def write_runtime_receipt(
    run: dict[str, Any],
    *,
    prefix: str,
    payload: dict[str, Any],
) -> str | None:
    """Write one runtime receipt under the run directory.

    Args:
        run: Mutable run state dict.
        prefix: Receipt filename prefix.
        payload: Receipt payload to persist.

    Returns:
        The written receipt path, or ``None`` when no run directory exists.
    """

    run_files = run.get("run_files", {}) if isinstance(run, dict) else {}
    run_dir = str(run_files.get("run_dir", "")).strip()
    if not run_dir:
        return None
    target = write_install_receipt(
        payload,
        prefix=prefix,
        receipt_root=Path(run_dir) / "receipts",
    )
    return str(target)


def try_auto_install_tools(
    run: dict[str, Any],
    *,
    project_root: Path,
) -> tuple[bool, str]:
    """Attempt automatic installation for missing executable tools.

    Args:
        run: Mutable run state dict.
        project_root: Repository root used by the bootstrap helper.

    Returns:
        ``(ok, action_name)`` describing the remediation result.
    """

    missing_tools = [
        str(tool).strip()
        for tool in run.get("missing_tools_detected", [])
        if str(tool).strip()
    ]
    if not missing_tools:
        return False, "no_missing_tools_to_install"

    from bio_harness.core.environment_bootstrap import bootstrap_bioharness_environment
    from bio_harness.core.tool_env import requirement_available

    report = bootstrap_bioharness_environment(
        project_root=project_root,
        install_python=False,
        install_pixi=True,
        tool_names=missing_tools,
        dry_run=False,
    )
    run["auto_install_report"] = report
    receipt_path = write_runtime_receipt(
        run,
        prefix="auto_install_missing_tools",
        payload=report,
    )
    if receipt_path:
        run["auto_install_report_path"] = receipt_path
    if not report.get("success", False):
        if report.get("install_plan", {}).get("manual_install_required_tools", []):
            return False, "tool_install_requires_manual_steps"
        if bool(report.get("pixi_command_missing", False)):
            return False, "tool_install_requires_pixi"
        return False, "tool_install_failed"
    remaining = [
        tool_name
        for tool_name in missing_tools
        if not requirement_available(tool_name)
    ]
    run["missing_tools_detected"] = remaining
    return (not remaining), ("tool_install_completed" if not remaining else "tool_install_incomplete")


def try_auto_setup_isolated_tools(
    run: dict[str, Any],
    *,
    project_root: Path,
) -> tuple[bool, str]:
    """Attempt isolated-tool setup for missing runtime dependencies.

    Args:
        run: Mutable run state dict.
        project_root: Repository root for launcher/config paths.

    Returns:
        ``(ok, action_name)`` describing the remediation result.
    """

    missing_tools = [
        str(tool).strip()
        for tool in run.get("missing_tools_detected", [])
        if str(tool).strip()
    ]
    if not missing_tools:
        return False, "no_missing_tools_for_isolated_setup"

    from bio_harness.core.isolated_tool_recipes import setup_isolated_tools_for_missing
    from bio_harness.core.tool_env import requirement_available

    report = setup_isolated_tools_for_missing(
        missing_tools,
        config_path=project_root / "workspace" / "tool_launchers.json",
        env_root=project_root / ".tool-envs",
        install=True,
        dry_run=False,
    )
    run["isolated_tool_setup_report"] = report
    receipt_path = write_runtime_receipt(
        run,
        prefix="auto_setup_isolated_tools",
        payload=report,
    )
    if receipt_path:
        run["isolated_tool_setup_report_path"] = receipt_path
    remaining = [
        tool_name
        for tool_name in missing_tools
        if not requirement_available(tool_name)
    ]
    run["missing_tools_detected"] = remaining
    if not report.get("success", False):
        return False, "isolated_tool_setup_incomplete"
    return (not remaining), ("isolated_tool_setup_completed" if not remaining else "isolated_tool_setup_partial")


def build_runtime_result_payload(
    *,
    run: dict[str, Any],
    data_root: Path,
    selected_dir: Path,
    path_graph_db_path: Path,
    path_graph_user_key: str,
    path_graph_scope: str,
    benchmark_policy: str,
    assistance_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build the final runtime result payload exposed to callers.

    Args:
        run: Mutable run state dict.
        data_root: Active data root.
        selected_dir: Active selected output directory.
        path_graph_db_path: Path-graph database path.
        path_graph_user_key: Path-graph user key.
        path_graph_scope: Path-graph scope.
        benchmark_policy: Active benchmark policy string.
        assistance_manifest: Assistance manifest payload.

    Returns:
        Final result payload dict.
    """

    generic_template_fallback = (
        assistance_manifest.get("generic_template_fallback", {})
        if isinstance(assistance_manifest.get("generic_template_fallback", {}), dict)
        else {}
    )
    failure_diagnosis = build_failure_diagnosis(run)
    generic_template_pipeline_id = str(generic_template_fallback.get("selected_pipeline_id", "") or "").strip()
    if not generic_template_pipeline_id:
        nested_selection = (
            generic_template_fallback.get("selection", {})
            if isinstance(generic_template_fallback.get("selection", {}), dict)
            else {}
        )
        generic_template_pipeline_id = str(nested_selection.get("pipeline_id", "") or "").strip()

    return {
        "run_id": run.get("run_uid", ""),
        "status": run.get("status", ""),
        "error": run.get("error", ""),
        "benchmark_policy": benchmark_policy,
        "data_root": str(data_root),
        "selected_dir": str(selected_dir),
        "auto_repair_last_class": run.get("auto_repair_last_class", ""),
        "auto_repair_attempts": run.get("auto_repair_attempts", {}),
        "auto_repair_history_count": len(run.get("auto_repair_history", [])),
        "contract_validation": run.get("contract_validation", {}),
        "fallback_catalog_size": int(run.get("fallback_catalog_size", 0)),
        "fallback_catalog_summary": run.get("fallback_catalog_summary", []),
        "fallback_selection": run.get("fallback_selection", {}),
        "selected_path_id": run.get("selected_path_id", ""),
        "path_graph_db": str(path_graph_db_path),
        "path_graph_user_key": str(path_graph_user_key),
        "path_graph_scope": str(path_graph_scope),
        "run_dir": run.get("run_files", {}).get("run_dir", ""),
        "state_file": run.get("run_files", {}).get("state", ""),
        "events_file": run.get("run_files", {}).get("events", ""),
        "stdout_file": run.get("run_files", {}).get("stdout", ""),
        "stderr_file": run.get("run_files", {}).get("stderr", ""),
        "exec_file": run.get("run_files", {}).get("exec", ""),
        "exit_file": run.get("run_files", {}).get("exit", ""),
        "assistance_manifest_file": run.get("run_files", {}).get("assistance_manifest", ""),
        "summary_file": run.get("run_files", {}).get("summary", ""),
        "missing_tools_detected": run.get("missing_tools_detected", []),
        "missing_reference_detected": run.get("missing_reference_detected", []),
        "missing_sample_groups": run.get("missing_sample_groups", []),
        "missing_sample_group_signals": run.get("missing_sample_group_signals", []),
        "observed_sample_groups": run.get("observed_sample_groups", []),
        "observed_sample_group_sources": run.get("observed_sample_group_sources", {}),
        "no_fastq_found": bool(run.get("no_fastq_found", False)),
        "empty_bams_detected": run.get("empty_bams_detected", []),
        "planner_timeout_detected": bool(run.get("planner_timeout_detected", False)),
        "planner_failopen_used": bool(run.get("planner_failopen_used", False)),
        "generic_template_fallback_used": bool(assistance_manifest.get("generic_template_fallback_used", False)),
        "generic_template_fallback_pipeline_id": generic_template_pipeline_id,
        "generic_template_fallback_blocked": bool(
            assistance_manifest.get("generic_template_fallback_blocked", False)
        ),
        "generic_template_fallback_block_reason": str(
            assistance_manifest.get("generic_template_fallback_block_reason", "") or ""
        ),
        "protocol_template_fallback_used": bool(assistance_manifest.get("protocol_template_fallback_used", False)),
        "forbidden_benchmark_sources_visible": bool(
            assistance_manifest.get("forbidden_benchmark_sources_visible", False)
        ),
        "forbidden_benchmark_sources": assistance_manifest.get("forbidden_benchmark_sources", []),
        "local_model_loopback_blocked_detected": bool(run.get("local_model_loopback_blocked_detected", False)),
        "execution_stalled_detected": bool(run.get("execution_stalled_detected", False)),
        "failure_signatures": run.get("failure_signatures", []),
        "failure_diagnosis": failure_diagnosis,
        "input_quality": run.get("input_quality", {}),
        "research_report": run.get("research_report", {}),
        "planning_attempts": run.get("planning_attempts", []),
        "planner_strategy_used": str(run.get("planner_strategy_used", "")),
        "assistance_manifest": assistance_manifest,
    }


__all__ = [
    "build_runtime_result_payload",
    "try_auto_install_tools",
    "try_auto_setup_isolated_tools",
    "write_runtime_receipt",
]
