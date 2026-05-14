from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from bio_harness.core.benchmark_policy import OFFICIAL_BIOAGENTBENCH_POLICY


def resolve_manifest_entries(manifest_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Official manifest must contain a list of task entries.")
    project_root = manifest_path.resolve(strict=False).parents[1]
    resolved: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        for key in ("task_dir", "data_root", "runs_root", "validator_script"):
            raw = str(entry.get(key, "") or "").strip()
            if not raw:
                continue
            path = Path(raw)
            if not path.is_absolute():
                path = (project_root / path).resolve(strict=False)
            else:
                path = path.resolve(strict=False)
            entry[key] = str(path)
        resolved.append(entry)
    return resolved


def entry_input_files(entry: dict[str, Any]) -> list[str]:
    data_root = Path(str(entry.get("data_root", "") or "")).resolve(strict=False)
    if not data_root.exists() or not data_root.is_dir():
        return []
    files: list[str] = []
    for path in sorted(data_root.iterdir()):
        if path.is_file():
            files.append(path.name)
    return files


def _prompt_placeholder_values(*, entry: dict[str, Any], selected_dir: Path) -> dict[str, str]:
    """Return semantic placeholder values for benchmark prompts."""

    return {
        "selected_dir": "the selected output directory",
        "task_dir": "the task directory",
        "data_root": "the task data directory",
        "runs_root": "the benchmark runs directory",
        "task_id": str(entry.get("task_id", "") or "").strip(),
    }


def _render_prompt_text(value: str, *, entry: dict[str, Any], selected_dir: Path) -> str:
    """Render benchmark prompt text without exposing concrete filesystem paths."""

    rendered = str(value).format(**_prompt_placeholder_values(entry=entry, selected_dir=selected_dir))
    return " ".join(rendered.split())


def build_official_prompt(entry: dict[str, Any], *, selected_dir: Path) -> str:
    task_name = str(entry.get("task_name", "") or entry.get("task_id", "task")).strip()
    raw_task_prompt = str(entry.get("task_prompt", "") or "").strip()
    task_prompt = _render_prompt_text(raw_task_prompt, entry=entry, selected_dir=selected_dir) if raw_task_prompt else ""
    files = entry_input_files(entry)
    files_text = ", ".join(files) if files else "the provided input files"

    lines = [
        f"BioAgentBench official-mode task: {task_name}.",
        f"Available task input filenames include {files_text}.",
    ]
    if task_prompt:
        lines.append(task_prompt)
    output_requirements = [
        _render_prompt_text(str(item).strip(), entry=entry, selected_dir=selected_dir)
        for item in (entry.get("output_requirements", []) or [])
        if str(item).strip()
    ]
    if output_requirements:
        lines.extend(output_requirements)
    deliverables = entry.get("deliverables", []) if isinstance(entry.get("deliverables", []), list) else []
    for deliverable in deliverables:
        if not isinstance(deliverable, dict):
            continue
        rel_path = str(deliverable.get("path", "") or "").strip()
        if not rel_path:
            continue
        description = str(deliverable.get("description", "") or "").strip()
        line = f"Write the final deliverable in the selected output directory at canonical relative location {rel_path}."
        if description:
            line = f"{description} Place it in the selected output directory at canonical relative location {rel_path}."
        columns = [str(col).strip() for col in (deliverable.get("columns", []) or []) if str(col).strip()]
        if columns:
            line += f" The file must contain exactly these columns: {', '.join(columns)}."
        lines.append(line)
    lines.extend(
        [
            "Write all generated outputs under the selected output directory.",
            "Do not invent or emit filesystem paths in the plan; refer to the provided task inputs and canonical deliverables semantically.",
            "Do not read benchmark truth files, benchmark results files, or benchmark recipe files.",
            "Do not write anywhere outside the selected directory except reading the provided input files.",
        ]
    )

    deduped_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = " ".join(str(line).split())
        if not normalized or normalized in seen:
            continue
        deduped_lines.append(normalized)
        seen.add(normalized)
    return " ".join(deduped_lines)


def render_arg_template(value: str, *, entry: dict[str, Any], selected_dir: Path) -> str:
    replacements = {
        "selected_dir": str(selected_dir),
        "task_dir": str(entry.get("task_dir", "") or ""),
        "data_root": str(entry.get("data_root", "") or ""),
        "runs_root": str(entry.get("runs_root", "") or ""),
        "task_id": str(entry.get("task_id", "") or ""),
    }
    return str(value).format(**replacements)


def build_validator_argv(entry: dict[str, Any], *, selected_dir: Path, python_executable: str) -> list[str]:
    validator_script = str(entry.get("validator_script", "") or "").strip()
    if not validator_script:
        return []
    args = entry.get("validator_args", []) if isinstance(entry.get("validator_args", []), list) else []
    rendered = [render_arg_template(str(arg), entry=entry, selected_dir=selected_dir) for arg in args]
    return [python_executable, validator_script, *rendered]


def extract_assistance_summary(result_obj: dict[str, Any]) -> dict[str, Any]:
    assistance = result_obj.get("assistance_manifest", {}) if isinstance(result_obj.get("assistance_manifest", {}), dict) else {}
    fallback = assistance.get("generic_template_fallback", {}) if isinstance(assistance.get("generic_template_fallback", {}), dict) else {}
    pipeline_id = str(fallback.get("selected_pipeline_id", "") or "").strip()
    if not pipeline_id:
        nested = fallback.get("selection", {}) if isinstance(fallback.get("selection", {}), dict) else {}
        pipeline_id = str(nested.get("pipeline_id", "") or "").strip()
    return {
        "benchmark_policy": str(result_obj.get("benchmark_policy", "") or assistance.get("benchmark_policy", "")),
        "generic_template_fallback_used": bool(assistance.get("generic_template_fallback_used", False)),
        "generic_template_fallback_pipeline_id": pipeline_id,
        "protocol_template_fallback_used": bool(assistance.get("protocol_template_fallback_used", False)),
        "forbidden_benchmark_sources_visible": bool(assistance.get("forbidden_benchmark_sources_visible", False)),
        "forbidden_benchmark_sources": list(assistance.get("forbidden_benchmark_sources", []) or []),
        "leakage_guard_active": bool(assistance.get("leakage_guard_active", False)),
    }


def _load_json_dict(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_model_config(result_obj: dict[str, Any]) -> dict[str, Any]:
    run_dir_raw = str(result_obj.get("run_dir", "") or "").strip()
    run_dir = Path(run_dir_raw).resolve(strict=False) if run_dir_raw else None
    manifest_payload: dict[str, Any] = {}
    planner_payload: dict[str, Any] = {}
    if run_dir and run_dir.exists():
        manifest_payload = _load_json_dict(run_dir / "manifest.json")
        planner_dir = run_dir / "planner"
        if planner_dir.exists():
            planner_start_files = sorted(planner_dir.glob("*_planner_start.json"))
            if planner_start_files:
                planner_payload = _load_json_dict(planner_start_files[0])
    planner_start_payload = (
        planner_payload.get("payload", {}) if isinstance(planner_payload.get("payload", {}), dict) else {}
    )
    executor_model_name = (
        str(manifest_payload.get("model_name", "") or "").strip()
        or str(planner_start_payload.get("fast_model", "") or "").strip()
        or str(planner_payload.get("model_name", "") or "").strip()
    )
    planner_model_name = (
        str(planner_start_payload.get("planning_model", "") or "").strip()
        or executor_model_name
    )
    llm_backend = str(manifest_payload.get("llm_backend", "") or "").strip()
    host = str(manifest_payload.get("host", "") or "").strip()
    dual_model_active = bool(
        executor_model_name and planner_model_name and executor_model_name != planner_model_name
    )
    return {
        "executor_model_name": executor_model_name,
        "planner_model_name": planner_model_name,
        "llm_backend": llm_backend,
        "host": host,
        "dual_model_active": dual_model_active,
    }


def official_report_bucket(
    result_obj: dict[str, Any],
    *,
    validation_configured: bool = False,
    validation_passed: bool | None = None,
) -> str:
    assistance = extract_assistance_summary(result_obj)
    if assistance["benchmark_policy"] != OFFICIAL_BIOAGENTBENCH_POLICY:
        return "non_official_mode"
    if assistance["forbidden_benchmark_sources_visible"]:
        return "invalid_for_official_reporting"
    if str(result_obj.get("status", "failed") or "failed") != "completed":
        return "invalid_for_official_reporting"
    if assistance["generic_template_fallback_used"]:
        return "official_blind_with_generic_fallback"
    if assistance["protocol_template_fallback_used"]:
        return "invalid_for_official_reporting"
    if validation_configured and validation_passed is not True:
        return "invalid_for_official_reporting"
    return "official_blind_clean"


def summarize_official_run(
    *,
    entry: dict[str, Any],
    selected_dir: Path,
    result_obj: dict[str, Any],
    harness_exit_code: int,
    validator_exit_code: int | None = None,
    validator_stdout: str = "",
) -> dict[str, Any]:
    assistance = extract_assistance_summary(result_obj)
    model_config = extract_model_config(result_obj)
    validation_configured = bool(str(entry.get("validator_script", "") or "").strip())
    validation_passed = validator_exit_code == 0 if validator_exit_code is not None else None
    report_bucket = official_report_bucket(
        result_obj,
        validation_configured=validation_configured,
        validation_passed=validation_passed,
    )
    return {
        "task_id": str(entry.get("task_id", "") or ""),
        "task_name": str(entry.get("task_name", "") or entry.get("task_id", "")),
        "selected_dir": str(selected_dir),
        "harness_status": str(result_obj.get("status", "failed") or "failed"),
        "harness_exit_code": int(harness_exit_code),
        "benchmark_policy": assistance["benchmark_policy"],
        "generic_template_fallback_used": assistance["generic_template_fallback_used"],
        "generic_template_fallback_pipeline_id": assistance["generic_template_fallback_pipeline_id"],
        "protocol_template_fallback_used": assistance["protocol_template_fallback_used"],
        "forbidden_benchmark_sources_visible": assistance["forbidden_benchmark_sources_visible"],
        "forbidden_benchmark_sources": assistance["forbidden_benchmark_sources"],
        "leakage_guard_active": assistance["leakage_guard_active"],
        "official_report_bucket": report_bucket,
        "validation_configured": validation_configured,
        "validation_exit_code": validator_exit_code,
        "validation_passed": validation_passed,
        "validator_stdout": validator_stdout,
        "executor_model_name": model_config["executor_model_name"],
        "planner_model_name": model_config["planner_model_name"],
        "llm_backend": model_config["llm_backend"],
        "host": model_config["host"],
        "dual_model_active": model_config["dual_model_active"],
        "result_json": str(result_obj.get("result_json", "") or ""),
        "run_dir": str(result_obj.get("run_dir", "") or ""),
        "assistance_manifest_file": str(result_obj.get("assistance_manifest_file", "") or ""),
        "error": str(result_obj.get("error", "") or ""),
    }


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


def _rate_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    official_clean = sum(1 for row in rows if row.get("official_report_bucket") == "official_blind_clean")
    invalid = sum(1 for row in rows if row.get("official_report_bucket") == "invalid_for_official_reporting")
    validator_passed = sum(1 for row in rows if row.get("validation_passed") is True)
    validator_configured = sum(1 for row in rows if row.get("validation_configured") is True)
    fallback_assisted = sum(
        1 for row in rows if row.get("official_report_bucket") == "official_blind_with_generic_fallback"
    )
    return {
        "attempt_count": total,
        "official_blind_clean_count": official_clean,
        "official_blind_clean_rate": _safe_rate(official_clean, total),
        "invalid_for_official_reporting_count": invalid,
        "invalid_for_official_reporting_rate": _safe_rate(invalid, total),
        "validator_backed_scientific_pass_count": validator_passed,
        "validator_backed_scientific_pass_rate": _safe_rate(validator_passed, total),
        "validator_configured_count": validator_configured,
        "official_blind_with_generic_fallback_count": fallback_assisted,
        "official_blind_with_generic_fallback_rate": _safe_rate(fallback_assisted, total),
    }


def _group_model_configs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("executor_model_name", "") or "").strip(),
            str(row.get("planner_model_name", "") or "").strip(),
            str(row.get("llm_backend", "") or "").strip(),
            str(row.get("host", "") or "").strip(),
        )
        grouped[key].append(row)
    items: list[dict[str, Any]] = []
    for key in sorted(grouped.keys()):
        config_rows = grouped[key]
        executor_model_name, planner_model_name, llm_backend, host = key
        rate_block = _rate_block(config_rows)
        items.append(
            {
                "executor_model_name": executor_model_name,
                "planner_model_name": planner_model_name,
                "llm_backend": llm_backend,
                "host": host,
                "dual_model_active": bool(
                    executor_model_name and planner_model_name and executor_model_name != planner_model_name
                ),
                "task_ids": sorted({str(row.get("task_id", "") or "").strip() for row in config_rows if row.get("task_id")}),
                **rate_block,
            }
        )
    return items


def _enrich_scoreboard_row(row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    if any(str(enriched.get(key, "") or "").strip() for key in ("executor_model_name", "planner_model_name", "llm_backend", "host")):
        return enriched
    model_config = extract_model_config(enriched)
    for key, value in model_config.items():
        enriched[key] = value
    return enriched


def build_official_scoreboard(rows: list[dict[str, Any]]) -> dict[str, Any]:
    enriched_rows = [_enrich_scoreboard_row(row) for row in rows]
    sorted_rows = sorted(
        enriched_rows,
        key=lambda row: (
            str(row.get("task_id", "") or ""),
            str(row.get("selected_dir", "") or ""),
        ),
    )
    per_task: list[dict[str, Any]] = []
    grouped_tasks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted_rows:
        task_id = str(row.get("task_id", "") or "").strip() or "unknown"
        grouped_tasks[task_id].append(row)
    for task_id in sorted(grouped_tasks.keys()):
        task_rows = grouped_tasks[task_id]
        per_task.append(
            {
                "task_id": task_id,
                "task_name": str(task_rows[0].get("task_name", "") or task_id),
                **_rate_block(task_rows),
                "model_configs": _group_model_configs(task_rows),
            }
        )
    return {
        "attempt_count": len(sorted_rows),
        "task_count": len(per_task),
        "overall": _rate_block(sorted_rows),
        "model_configs": _group_model_configs(sorted_rows),
        "per_task": per_task,
    }


def render_official_scoreboard_markdown(scoreboard: dict[str, Any]) -> str:
    overall = scoreboard.get("overall", {}) if isinstance(scoreboard.get("overall", {}), dict) else {}
    lines = [
        "# BioAgentBench Official-Mode Scoreboard",
        "",
        "## Overall",
        "",
        "| Metric | Count | Rate |",
        "| --- | --- | --- |",
        f"| Official blind clean | {overall.get('official_blind_clean_count', 0)} | {overall.get('official_blind_clean_rate', 'n/a')} |",
        f"| Invalid for official reporting | {overall.get('invalid_for_official_reporting_count', 0)} | {overall.get('invalid_for_official_reporting_rate', 'n/a')} |",
        f"| Validator-backed scientific pass | {overall.get('validator_backed_scientific_pass_count', 0)} | {overall.get('validator_backed_scientific_pass_rate', 'n/a')} |",
        "",
        "## Model Configs",
        "",
        "| Executor | Planner | Backend | Attempts | Clean | Invalid | Validator Pass |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in scoreboard.get("model_configs", []) or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("executor_model_name", "") or ""),
                    str(item.get("planner_model_name", "") or ""),
                    str(item.get("llm_backend", "") or ""),
                    str(item.get("attempt_count", 0)),
                    str(item.get("official_blind_clean_count", 0)),
                    str(item.get("invalid_for_official_reporting_count", 0)),
                    str(item.get("validator_backed_scientific_pass_count", 0)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Per Task",
            "",
            "| Task | Attempts | Clean | Clean Rate | Invalid | Invalid Rate | Validator Pass | Validator Pass Rate |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in scoreboard.get("per_task", []) or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("task_id", "") or ""),
                    str(item.get("attempt_count", 0)),
                    str(item.get("official_blind_clean_count", 0)),
                    str(item.get("official_blind_clean_rate", "n/a")),
                    str(item.get("invalid_for_official_reporting_count", 0)),
                    str(item.get("invalid_for_official_reporting_rate", "n/a")),
                    str(item.get("validator_backed_scientific_pass_count", 0)),
                    str(item.get("validator_backed_scientific_pass_rate", "n/a")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"
