"""Historical benchmark analysis helpers for manuscript assets.

This module mines BioAgentBench run artifacts and converts them into compact
tables that are stable enough for manuscript reporting and figure generation.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd


_BENCHMARK_PASSED_TRUE_RE = re.compile(r"BENCHMARK PASSED:\s*True", re.IGNORECASE)
_BENCHMARK_PASSED_CHECKS_RE = re.compile(r"BENCHMARK PASSED \(\d+/\d+ checks\)", re.IGNORECASE)


@dataclass(frozen=True)
class RunHistoryRow:
    """Compact record for one benchmark run."""

    task: str
    label: str
    selected_dir: str
    run_dir: str
    benchmark_policy: str
    llm_backend: str
    status: str
    validator_passed: bool | None
    auto_repair_history_count: int | None
    generic_template_fallback_used: bool
    protocol_template_fallback_used: bool
    planner_failopen_used: bool
    forbidden_benchmark_sources_visible: bool
    planning_model: str
    planning_model_group: str
    fast_model: str
    planner_attempts_started: int
    planner_attempts_succeeded: int
    planner_attempts_failed: int
    planner_attempts_timed_out: int
    first_attempt_succeeded: bool | None
    total_planning_elapsed_seconds: float | None
    max_planning_attempt_elapsed_seconds: float | None
    failure_category: str
    failure_excerpt: str


def classify_failure_category(error_text: str, failure_signatures: list[str] | None = None) -> str:
    """Classify a historical failure into a manuscript-friendly category."""

    error_l = str(error_text or "").lower()
    signatures_l = [str(item or "").lower() for item in (failure_signatures or [])]

    if "generic template fallback is disabled" in error_l:
        return "forbidden_deterministic_fallback"
    if "failed protocol grounding" in error_l:
        return "protocol_grounding_failure"
    if "failed contract validation" in error_l:
        return "contract_validation_failure"
    if "failed semantic validation" in error_l or "strict semantic validation blocked execution" in error_l:
        return "semantic_validation_failure"
    if "plan references missing inputs" in error_l:
        return "missing_input_handoff"
    if "missing local scripts" in error_l:
        return "missing_local_script"
    if "planner did not produce a usable plan" in error_l or "empty workflow" in error_l:
        return "planner_timeout_or_empty_plan"
    if "blocked by validation agent" in error_l:
        if any("missing_tool" in token for token in signatures_l):
            return "validation_block_missing_tool"
        return "runtime_validation_block"
    if "failed with exit code" in error_l:
        return "runtime_step_failure"
    if "loopback access is blocked" in error_l:
        return "backend_loopback_block"
    if "referenced before assignment" in error_l:
        return "internal_bug"
    if not error_l:
        return "none"
    return "other_failure"


def normalize_model_group(model_name: str) -> str:
    """Collapse related model tags into a smaller set of manuscript labels."""

    token = str(model_name or "").strip().lower()
    if not token:
        return "unknown"
    if token.startswith("qwen3.5:122b-a10b"):
        return "qwen3.5_122b"
    if token.startswith("qwen3-coder-next"):
        return "qwen3_coder_next"
    if token.startswith("codellama"):
        return "codellama"
    return token.replace(":", "_")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows


def _validator_passed(validator_log: Path) -> bool | None:
    if not validator_log.is_file():
        return None
    try:
        text = validator_log.read_text(encoding="utf-8")
    except Exception:
        return None
    if _BENCHMARK_PASSED_TRUE_RE.search(text):
        return True
    if _BENCHMARK_PASSED_CHECKS_RE.search(text):
        return True
    if "BENCHMARK PASSED: False" in text:
        return False
    return None


def _planner_trace_models(run_dir: Path) -> tuple[str, str]:
    planner_dir = run_dir / "planner"
    starts = sorted(planner_dir.glob("*planner_start.json"))
    if not starts:
        return "", ""
    payload = _read_json(starts[0]).get("payload", {})
    if not isinstance(payload, dict):
        return "", ""
    return (
        str(payload.get("planning_model", "") or "").strip(),
        str(payload.get("fast_model", "") or "").strip(),
    )


def _planner_event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    started = [row for row in events if str(row.get("event_type", "")).strip() == "PLANNER_ATTEMPT_STARTED"]
    succeeded = [row for row in events if str(row.get("event_type", "")).strip() == "PLANNER_ATTEMPT_SUCCEEDED"]
    failed = [row for row in events if str(row.get("event_type", "")).strip() == "PLANNER_ATTEMPT_FAILED"]
    timed_out = [row for row in events if str(row.get("event_type", "")).strip() == "PLANNER_ATTEMPT_TIMEOUT_FORCED"]
    elapsed_values: list[float] = []
    for row in [*succeeded, *failed]:
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            continue
        try:
            elapsed_values.append(float(payload.get("elapsed_seconds", 0) or 0))
        except Exception:
            continue
    attempt_ids_succeeded = {
        int(row.get("payload", {}).get("attempt"))
        for row in succeeded
        if isinstance(row.get("payload", {}), dict) and row.get("payload", {}).get("attempt") is not None
    }
    return {
        "planner_attempts_started": len(started),
        "planner_attempts_succeeded": len(succeeded),
        "planner_attempts_failed": len(failed),
        "planner_attempts_timed_out": len(timed_out),
        "first_attempt_succeeded": 1 in attempt_ids_succeeded if started else None,
        "total_planning_elapsed_seconds": sum(elapsed_values) if elapsed_values else None,
        "max_planning_attempt_elapsed_seconds": max(elapsed_values) if elapsed_values else None,
    }


def build_run_history_dataframe(official_runs_root: Path, workspace_runs_root: Path) -> pd.DataFrame:
    """Build the raw historical run table for manuscript analysis."""

    rows: list[RunHistoryRow] = []
    for task_dir in sorted(path for path in official_runs_root.iterdir() if path.is_dir()):
        task = task_dir.name
        for label_dir in sorted(path for path in task_dir.iterdir() if path.is_dir()):
            result_json = label_dir / "result.json"
            if not result_json.is_file():
                continue
            result = _read_json(result_json)
            run_dir_raw = str(result.get("run_dir", "") or "").strip()
            run_dir = Path(run_dir_raw).resolve(strict=False) if run_dir_raw else Path("")
            events = _read_jsonl(run_dir / "events.jsonl") if run_dir_raw else []
            planning_model, fast_model = _planner_trace_models(run_dir) if run_dir_raw else ("", "")
            planner_summary = _planner_event_summary(events)
            error_text = str(result.get("error", "") or "").strip()
            failure_signatures = (
                list(result.get("failure_signatures", []))
                if isinstance(result.get("failure_signatures", []), list)
                else []
            )
            rows.append(
                RunHistoryRow(
                    task=task,
                    label=label_dir.name,
                    selected_dir=str(label_dir.resolve(strict=False)),
                    run_dir=str(run_dir),
                    benchmark_policy=str(result.get("benchmark_policy", "") or "").strip(),
                    llm_backend=str(result.get("llm_backend", "") or "").strip(),
                    status=str(result.get("status", "") or "").strip(),
                    validator_passed=_validator_passed(label_dir / "validator.log"),
                    auto_repair_history_count=(
                        int(result.get("auto_repair_history_count"))
                        if result.get("auto_repair_history_count") is not None
                        else None
                    ),
                    generic_template_fallback_used=bool(result.get("generic_template_fallback_used")),
                    protocol_template_fallback_used=bool(result.get("protocol_template_fallback_used")),
                    planner_failopen_used=bool(result.get("planner_failopen_used")),
                    forbidden_benchmark_sources_visible=bool(result.get("forbidden_benchmark_sources_visible")),
                    planning_model=planning_model,
                    planning_model_group=normalize_model_group(planning_model),
                    fast_model=fast_model,
                    planner_attempts_started=int(planner_summary["planner_attempts_started"]),
                    planner_attempts_succeeded=int(planner_summary["planner_attempts_succeeded"]),
                    planner_attempts_failed=int(planner_summary["planner_attempts_failed"]),
                    planner_attempts_timed_out=int(planner_summary["planner_attempts_timed_out"]),
                    first_attempt_succeeded=planner_summary["first_attempt_succeeded"],
                    total_planning_elapsed_seconds=planner_summary["total_planning_elapsed_seconds"],
                    max_planning_attempt_elapsed_seconds=planner_summary["max_planning_attempt_elapsed_seconds"],
                    failure_category=classify_failure_category(error_text, failure_signatures),
                    failure_excerpt=error_text[:300],
                )
            )
    frame = pd.DataFrame(asdict(row) for row in rows)
    if frame.empty:
        return frame
    validator_passed = frame["validator_passed"].astype("boolean").fillna(False)
    frame["is_clean_pass"] = (
        (frame["status"] == "completed")
        & validator_passed
        & (frame["auto_repair_history_count"].fillna(0) == 0)
        & (~frame["generic_template_fallback_used"])
        & (~frame["protocol_template_fallback_used"])
        & (~frame["planner_failopen_used"])
        & (~frame["forbidden_benchmark_sources_visible"])
    )
    return frame.sort_values(["task", "label"]).reset_index(drop=True)


def build_failure_summary(history: pd.DataFrame) -> pd.DataFrame:
    """Summarize historical failures by category."""

    failures = history.loc[history["status"] == "failed"].copy()
    if failures.empty:
        return pd.DataFrame(columns=["failure_category", "count", "tasks"])
    summary = (
        failures.groupby("failure_category")
        .agg(
            count=("failure_category", "size"),
            tasks=("task", lambda values: ", ".join(sorted(set(map(str, values))))),
        )
        .reset_index()
        .sort_values(["count", "failure_category"], ascending=[False, True])
        .reset_index(drop=True)
    )
    return summary


def build_model_comparison(history: pd.DataFrame) -> pd.DataFrame:
    """Summarize planner behavior by planning model group."""

    model_rows = history.loc[history["planning_model_group"] != "unknown"].copy()
    if model_rows.empty:
        return pd.DataFrame()
    return (
        model_rows.groupby("planning_model_group")
        .agg(
            runs=("planning_model_group", "size"),
            clean_passes=("is_clean_pass", "sum"),
            median_planning_seconds=("total_planning_elapsed_seconds", "median"),
            mean_planning_seconds=("total_planning_elapsed_seconds", "mean"),
            median_attempts=("planner_attempts_started", "median"),
            mean_attempts=("planner_attempts_started", "mean"),
            first_attempt_success_rate=("first_attempt_succeeded", "mean"),
        )
        .reset_index()
        .sort_values("runs", ascending=False)
        .reset_index(drop=True)
    )


def build_replicate_matrix(history: pd.DataFrame, labels: list[str]) -> pd.DataFrame:
    """Build the clean-pass replicate matrix for selected labels."""

    subset = history.loc[history["label"].isin(labels)].copy()
    if subset.empty:
        return pd.DataFrame()
    subset["value"] = subset["is_clean_pass"].map({True: "pass", False: "fail"})
    matrix = subset.pivot(index="task", columns="label", values="value").reset_index()
    return matrix
