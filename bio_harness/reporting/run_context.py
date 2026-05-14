"""Resolve completed Bio-Harness runs into a stable reporting context."""

from __future__ import annotations

import inspect
import importlib
import json
import pkgutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


@dataclass(frozen=True)
class RunContext:
    """Resolved run metadata for reporting/export features."""

    resolution_mode: str
    selected_dir: Path
    result_path: Path
    result: dict[str, Any]
    run_dir: Path
    manifest_path: Path | None
    manifest: dict[str, Any]
    state_path: Path | None
    state: dict[str, Any]
    final_plan_path: Path | None
    final_plan: dict[str, Any]
    validator_log_path: Path | None
    harness_log_path: Path | None
    events_path: Path | None
    execution_log_path: Path | None
    preexecution_stage_repairs: dict[str, Any]
    bash_placeholder_resolutions: list[dict[str, Any]]


def build_live_result_payload(
    *,
    run: Mapping[str, Any],
    selected_dir: Path,
    run_dir: Path,
    benchmark_policy: str = "",
    data_root: Path | None = None,
    analysis_type: str = "",
    result_path: Path | None = None,
) -> dict[str, Any]:
    """Build a stable result-style payload from live run state.

    Args:
        run: In-memory run-state mapping.
        selected_dir: Selected output directory for the run.
        run_dir: Run-directory path.
        benchmark_policy: Active benchmark policy label.
        data_root: Optional input data root.
        analysis_type: Optional assay family label.
        result_path: Optional canonical result JSON path.

    Returns:
        Dict shaped like the persisted ``result.json`` payload expected by
        reporting surfaces.
    """

    planning_attempts = run.get("planning_attempts", [])
    auto_repair_history = run.get("auto_repair_history", [])
    return {
        "selected_dir": str(selected_dir),
        "run_dir": str(run_dir),
        "result_path": str(result_path) if result_path is not None else "",
        "status": str(run.get("status", "") or "").strip(),
        "benchmark_policy": str(benchmark_policy or "").strip(),
        "data_root": str(data_root) if data_root is not None else "",
        "analysis_type": str(analysis_type or "").strip(),
        "planning_attempts": planning_attempts,
        "auto_repair_history_count": len(auto_repair_history) if isinstance(auto_repair_history, list) else 0,
        "input_quality": run.get("input_quality", {}) if isinstance(run.get("input_quality", {}), dict) else {},
        "in_run_quality_summary": (
            run.get("in_run_quality_summary", {})
            if isinstance(run.get("in_run_quality_summary", {}), dict)
            else {}
        ),
        "research_report": run.get("research_report", {}) if isinstance(run.get("research_report", {}), dict) else {},
    }


def build_completed_run_context_payload(
    *,
    selected_dir: Path,
    run_dir: Path,
    result: Mapping[str, Any],
    manifest: Mapping[str, Any],
    state: Mapping[str, Any],
    final_plan: Mapping[str, Any] | None = None,
    preexecution_stage_repairs: Mapping[str, Any] | None = None,
    bash_placeholder_resolutions: list[Mapping[str, Any]] | None = None,
    result_path: Path | None = None,
    manifest_path: Path | None = None,
    state_path: Path | None = None,
    final_plan_path: Path | None = None,
    validator_log_path: Path | None = None,
    harness_log_path: Path | None = None,
    events_path: Path | None = None,
    execution_log_path: Path | None = None,
    resolution_mode: str = "completed_run_context",
) -> dict[str, Any]:
    """Build a stable completed-run context payload for persistence.

    Args:
        selected_dir: Selected output directory for the run.
        run_dir: Run-directory path.
        result: Result-style payload for the run.
        manifest: Persisted manifest payload.
        state: Persisted state payload.
        final_plan: Optional final structured plan payload.
        result_path: Optional canonical result JSON path.
        manifest_path: Optional manifest path.
        state_path: Optional state path.
        final_plan_path: Optional persisted final-plan path.
        validator_log_path: Optional validator log path.
        harness_log_path: Optional harness log path.
        events_path: Optional events JSONL path.
        execution_log_path: Optional execution log path.
        resolution_mode: Resolution label to store with the payload.

    Returns:
        JSON-serializable completed-run context payload.
    """

    return {
        "resolution_mode": str(resolution_mode or "completed_run_context"),
        "selected_dir": str(selected_dir),
        "result_path": str(result_path) if result_path is not None else "",
        "run_dir": str(run_dir),
        "manifest_path": str(manifest_path) if manifest_path is not None else "",
        "state_path": str(state_path) if state_path is not None else "",
        "final_plan_path": str(final_plan_path) if final_plan_path is not None else "",
        "validator_log_path": str(validator_log_path) if validator_log_path is not None else "",
        "harness_log_path": str(harness_log_path) if harness_log_path is not None else "",
        "events_path": str(events_path) if events_path is not None else "",
        "execution_log_path": str(execution_log_path) if execution_log_path is not None else "",
        "result": dict(result) if isinstance(result, Mapping) else {},
        "manifest": dict(manifest) if isinstance(manifest, Mapping) else {},
        "state": dict(state) if isinstance(state, Mapping) else {},
        "final_plan": dict(final_plan) if isinstance(final_plan, Mapping) else {},
        "preexecution_stage_repairs": (
            dict(preexecution_stage_repairs)
            if isinstance(preexecution_stage_repairs, Mapping)
            else {}
        ),
        "bash_placeholder_resolutions": [
            dict(item) for item in (bash_placeholder_resolutions or []) if isinstance(item, Mapping)
        ],
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional(path_value: Path) -> Path | None:
    return path_value if path_value.exists() else None


def _path_from_payload(payload: dict[str, Any], key: str) -> Path | None:
    raw = str(payload.get(key, "") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve(strict=False)


def _parse_plan_text(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("plan"), list):
        return {
            "thought_process": str(payload.get("thought_process", "") or "").strip(),
            "plan": payload.get("plan", []),
        }
    if str(payload.get("tool_name", "")).strip():
        return {"thought_process": "", "plan": [payload]}
    return {}


def _context_from_payload(payload: dict[str, Any]) -> RunContext | None:
    """Coerce one persisted completed-run context payload into ``RunContext``."""

    if not isinstance(payload, dict):
        return None
    selected_dir_raw = str(payload.get("selected_dir", "") or "").strip()
    run_dir_raw = str(payload.get("run_dir", "") or "").strip()
    if not selected_dir_raw or not run_dir_raw:
        return None

    selected_dir = Path(selected_dir_raw).expanduser().resolve(strict=False)
    run_dir = Path(run_dir_raw).expanduser().resolve(strict=False)
    result_path = _path_from_payload(payload, "result_path") or (selected_dir / "result.json")
    manifest_path = _path_from_payload(payload, "manifest_path")
    state_path = _path_from_payload(payload, "state_path")
    final_plan_path = _path_from_payload(payload, "final_plan_path")
    validator_log_path = _path_from_payload(payload, "validator_log_path")
    harness_log_path = _path_from_payload(payload, "harness_log_path")
    events_path = _path_from_payload(payload, "events_path")
    execution_log_path = _path_from_payload(payload, "execution_log_path")
    result = payload.get("result", {})
    manifest = payload.get("manifest", {})
    state = payload.get("state", {})
    final_plan = payload.get("final_plan", {})
    preexecution_stage_repairs = payload.get("preexecution_stage_repairs", {})
    bash_placeholder_resolutions = payload.get("bash_placeholder_resolutions", [])
    return RunContext(
        resolution_mode=str(payload.get("resolution_mode", "") or "completed_run_context"),
        selected_dir=selected_dir,
        result_path=result_path,
        result=result if isinstance(result, dict) else {},
        run_dir=run_dir,
        manifest_path=manifest_path if manifest_path and manifest_path.exists() else manifest_path,
        manifest=manifest if isinstance(manifest, dict) else {},
        state_path=state_path if state_path and state_path.exists() else state_path,
        state=state if isinstance(state, dict) else {},
        final_plan_path=final_plan_path if final_plan_path and final_plan_path.exists() else final_plan_path,
        final_plan=final_plan if isinstance(final_plan, dict) else {},
        validator_log_path=validator_log_path if validator_log_path and validator_log_path.exists() else validator_log_path,
        harness_log_path=harness_log_path if harness_log_path and harness_log_path.exists() else harness_log_path,
        events_path=events_path if events_path and events_path.exists() else events_path,
        execution_log_path=execution_log_path if execution_log_path and execution_log_path.exists() else execution_log_path,
        preexecution_stage_repairs=(
            preexecution_stage_repairs if isinstance(preexecution_stage_repairs, dict) else {}
        ),
        bash_placeholder_resolutions=[
            dict(item) for item in bash_placeholder_resolutions if isinstance(item, dict)
        ],
    )


def _load_persisted_completed_run_context(run_dir: Path) -> RunContext | None:
    """Load one persisted completed-run context from ``run_dir`` when present."""

    context_path = run_dir / "completed_run_context.json"
    if not context_path.exists():
        return None
    payload = _read_json(context_path)
    return _context_from_payload(payload)


def _resolve_final_plan(run_dir: Path) -> tuple[Path | None, dict[str, Any]]:
    planner_dir = run_dir / "planner"
    if not planner_dir.is_dir():
        return None, {}

    candidates = sorted(planner_dir.glob("*hierarchical_plan_success.txt"))
    if not candidates:
        candidates = sorted(planner_dir.glob("*structured_success.txt"))
    if not candidates:
        return None, {}

    for candidate in reversed(candidates):
        parsed = _parse_plan_text(candidate)
        if parsed.get("plan"):
            return candidate, parsed
    return None, {}


def _infer_selected_dir_from_run_dir(run_dir: Path, manifest: dict[str, Any], state: dict[str, Any]) -> Path:
    """Infer the selected-dir path from one archived run directory."""

    candidates = [
        _path_from_payload(manifest, "selected_dir"),
        _path_from_payload(state, "selected_dir"),
    ]
    for candidate in candidates:
        if candidate is not None:
            if _prefer_run_dir_over_candidate(run_dir, candidate):
                return run_dir
            return candidate

    path_graph_raw = (
        str(manifest.get("path_graph_db", "") or "").strip()
        or str(state.get("path_graph_db", "") or "").strip()
    )
    if path_graph_raw:
        path_graph_path = Path(path_graph_raw).expanduser().resolve(strict=False)
        if path_graph_path.name.endswith(".sqlite") and path_graph_path.parent.name == "knowledge":
            return path_graph_path.parent.parent
    return run_dir


def _prefer_run_dir_over_candidate(run_dir: Path, candidate: Path) -> bool:
    """Return whether one archived candidate selected-dir is too broad to trust."""

    if not _looks_like_workspace_root_candidate(run_dir, candidate):
        return False
    return _run_dir_contains_user_outputs(run_dir)


def _looks_like_workspace_root_candidate(run_dir: Path, candidate: Path) -> bool:
    """Return whether one selected-dir candidate collapses to the workspace root."""

    if run_dir.parent.name != "runs":
        return False
    try:
        return candidate == run_dir.parents[1]
    except IndexError:
        return False


def _run_dir_contains_user_outputs(run_dir: Path) -> bool:
    """Return whether one run directory already contains researcher-facing outputs."""

    deliverable_names = {
        "assembled.gtf",
        "gene_abundances.tsv",
        "cluster_assignments.csv",
        "marker_genes.csv",
        "processed.h5ad",
        "summary.json",
    }
    deliverable_suffixes = {".csv", ".tsv", ".gtf", ".vcf", ".h5ad", ".pdf", ".png", ".svg"}
    for path in run_dir.iterdir():
        if not path.is_file():
            continue
        if path.name in deliverable_names:
            return True
        if path.suffix.lower() in deliverable_suffixes and path.name not in {
            "state.json",
            "manifest.json",
            "exit.json",
            "path_decisions.json",
            "assistance_manifest.json",
            "executor.json",
        }:
            return True
    return (run_dir / "final").is_dir()


def _build_inferred_result_payload(
    *,
    selected_dir: Path,
    run_dir: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Build one compatibility result payload from archived run metadata."""

    exit_payload = _read_json(run_dir / "exit.json")
    status = (
        str(exit_payload.get("status", "") or "").strip()
        or str(state.get("status", "") or "").strip()
        or "completed"
    )
    plan_attempts = state.get("planning_attempts", 0)
    if not plan_attempts:
        planning_history = state.get("planning_attempts", [])
        if isinstance(planning_history, list):
            plan_attempts = len(planning_history)
    return {
        "selected_dir": str(selected_dir),
        "run_dir": str(run_dir),
        "status": status,
        "benchmark_policy": str(
            manifest.get("benchmark_policy", "")
            or state.get("benchmark_policy", "")
            or ""
        ).strip(),
        "data_root": str(
            manifest.get("data_root", "")
            or state.get("requested_data_root", "")
            or ""
        ).strip(),
        "analysis_type": str(
            (state.get("analysis_spec", {}) if isinstance(state.get("analysis_spec", {}), dict) else {}).get(
                "analysis_type", ""
            )
            or ""
        ).strip(),
        "planning_attempts": plan_attempts,
        "auto_repair_history_count": len(state.get("auto_repair_history", []) or []),
        "input_quality": state.get("input_quality", {}) if isinstance(state.get("input_quality", {}), dict) else {},
        "in_run_quality_summary": (
            state.get("in_run_quality_summary", {})
            if isinstance(state.get("in_run_quality_summary", {}), dict)
            else {}
        ),
    }


def resolve_run_context(path: str | Path) -> RunContext:
    """Resolve a selected-dir or result.json path into a reportable run context."""
    raw = Path(path).expanduser().resolve()
    if raw.is_file():
        if raw.name == "result.json":
            selected_dir = raw.parent
            result_path = raw
            resolution_mode = "result_json"
        elif raw.name == "completed_run_context.json":
            context = _context_from_payload(_read_json(raw))
            if context is None:
                raise ValueError(f"Could not parse completed run context: {raw}")
            return context
        elif raw.name == "state.json":
            run_dir = raw.parent
            persisted_context = _load_persisted_completed_run_context(run_dir)
            if persisted_context is not None:
                return persisted_context
            manifest_path = run_dir / "manifest.json"
            manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
            state = _read_json(raw)
            selected_dir = _infer_selected_dir_from_run_dir(run_dir, manifest, state)
            result_path = selected_dir / "result.json"
            result = _read_json(result_path) if result_path.is_file() else {}
            if not result:
                result = _build_inferred_result_payload(
                    selected_dir=selected_dir,
                    run_dir=run_dir,
                    manifest=manifest,
                    state=state,
                )
            result.setdefault("selected_dir", str(selected_dir))
            result.setdefault("run_dir", str(run_dir))
            final_plan_path, final_plan = _resolve_final_plan(run_dir)
            return RunContext(
                resolution_mode="run_dir_inferred",
                selected_dir=selected_dir,
                result_path=result_path,
                result=result,
                run_dir=run_dir,
                manifest_path=manifest_path if manifest_path.is_file() else None,
                manifest=manifest,
                state_path=raw,
                state=state,
                final_plan_path=final_plan_path,
                final_plan=final_plan,
                validator_log_path=_optional(selected_dir / "validator.log"),
                harness_log_path=_optional(selected_dir / "harness.log"),
                events_path=_optional(run_dir / "events.jsonl"),
                execution_log_path=_optional(run_dir / "execution.log"),
                preexecution_stage_repairs=(
                    state.get("preexecution_stage_repairs", {})
                    if isinstance(state.get("preexecution_stage_repairs", {}), dict)
                    else {}
                ),
                bash_placeholder_resolutions=[
                    dict(item)
                    for item in state.get("bash_placeholder_resolutions", [])
                    if isinstance(item, dict)
                ],
            )
        else:
            raise ValueError("Run reporting expects a selected-dir, run-dir, result.json path, or state.json path.")
    else:
        if (raw / "result.json").is_file():
            selected_dir = raw
            result_path = selected_dir / "result.json"
            resolution_mode = "result_json"
        elif (raw / "state.json").is_file():
            run_dir = raw
            persisted_context = _load_persisted_completed_run_context(run_dir)
            if persisted_context is not None:
                return persisted_context
            manifest_path = run_dir / "manifest.json"
            manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
            state_path = run_dir / "state.json"
            state = _read_json(state_path)
            selected_dir = _infer_selected_dir_from_run_dir(run_dir, manifest, state)
            result_path = selected_dir / "result.json"
            result = _read_json(result_path) if result_path.is_file() else {}
            if not result:
                result = _build_inferred_result_payload(
                    selected_dir=selected_dir,
                    run_dir=run_dir,
                    manifest=manifest,
                    state=state,
                )
            result.setdefault("selected_dir", str(selected_dir))
            result.setdefault("run_dir", str(run_dir))
            final_plan_path, final_plan = _resolve_final_plan(run_dir)
            return RunContext(
                resolution_mode="run_dir_inferred",
                selected_dir=selected_dir,
                result_path=result_path,
                result=result,
                run_dir=run_dir,
                manifest_path=manifest_path if manifest_path.is_file() else None,
                manifest=manifest,
                state_path=state_path if state_path.is_file() else None,
                state=state,
                final_plan_path=final_plan_path,
                final_plan=final_plan,
                validator_log_path=_optional(selected_dir / "validator.log"),
                harness_log_path=_optional(selected_dir / "harness.log"),
                events_path=_optional(run_dir / "events.jsonl"),
                execution_log_path=_optional(run_dir / "execution.log"),
                preexecution_stage_repairs=(
                    state.get("preexecution_stage_repairs", {})
                    if isinstance(state.get("preexecution_stage_repairs", {}), dict)
                    else {}
                ),
                bash_placeholder_resolutions=[
                    dict(item)
                    for item in state.get("bash_placeholder_resolutions", [])
                    if isinstance(item, dict)
                ],
            )
        else:
            selected_dir = raw
            result_path = selected_dir / "result.json"
            resolution_mode = "result_json"

    if not result_path.is_file():
        raise FileNotFoundError(f"Could not find result.json at {result_path}")

    result = _read_json(result_path)
    run_dir_raw = str(result.get("run_dir", "") or "").strip()
    if not run_dir_raw:
        raise ValueError(f"result.json does not contain run_dir: {result_path}")
    run_dir = Path(run_dir_raw).expanduser().resolve()
    persisted_context = _load_persisted_completed_run_context(run_dir)
    if persisted_context is not None:
        return persisted_context

    manifest_path = run_dir / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
    state_path = run_dir / "state.json"
    state = _read_json(state_path) if state_path.is_file() else {}
    final_plan_path, final_plan = _resolve_final_plan(run_dir)

    return RunContext(
        resolution_mode=resolution_mode,
        selected_dir=selected_dir,
        result_path=result_path,
        result=result,
        run_dir=run_dir,
        manifest_path=manifest_path if manifest_path.is_file() else None,
        manifest=manifest,
        state_path=state_path if state_path.is_file() else None,
        state=state,
        final_plan_path=final_plan_path,
        final_plan=final_plan,
        validator_log_path=_optional(selected_dir / "validator.log"),
        harness_log_path=_optional(selected_dir / "harness.log"),
        events_path=_optional(run_dir / "events.jsonl"),
        execution_log_path=_optional(run_dir / "execution.log"),
        preexecution_stage_repairs=(
            state.get("preexecution_stage_repairs", {})
            if isinstance(state.get("preexecution_stage_repairs", {}), dict)
            else {}
        ),
        bash_placeholder_resolutions=[
            dict(item)
            for item in state.get("bash_placeholder_resolutions", [])
            if isinstance(item, dict)
        ],
    )


def final_plan_steps(context: RunContext) -> list[dict[str, Any]]:
    """Return the normalized final plan steps."""
    steps = context.final_plan.get("plan", [])
    if not isinstance(steps, list):
        return []
    return [dict(step) for step in steps if isinstance(step, dict)]


@lru_cache(maxsize=1)
def _skill_renderer_map() -> dict[str, Any]:
    renderers: dict[str, Any] = {}
    try:
        import bio_harness.skills.library as library_pkg
    except Exception:
        return renderers
    for module_info in pkgutil.iter_modules(library_pkg.__path__):
        if module_info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{library_pkg.__name__}.{module_info.name}")
        except Exception:
            continue
        for attr_name, attr_value in inspect.getmembers(module, inspect.isfunction):
            if attr_name.startswith("_"):
                continue
            renderers.setdefault(attr_name, attr_value)
    return renderers


class _RenderMutationBlocked(RuntimeError):
    pass


def _blocked_mutation(*_args, **_kwargs):
    raise _RenderMutationBlocked("report rendering must not mutate the filesystem")


def render_step_command(step: dict[str, Any]) -> str:
    """Render a plan step to a shell command for exchange/export features."""
    tool_name = str(step.get("tool_name", "") or "").strip()
    arguments = dict(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {})
    if not tool_name:
        return ""
    if tool_name == "bash_run":
        return str(arguments.get("command", "") or "").strip()
    func = _skill_renderer_map().get(tool_name)
    if func is None:
        return ""
    try:
        with patch.object(Path, "mkdir", _blocked_mutation), patch.object(Path, "write_text", _blocked_mutation), patch.object(Path, "write_bytes", _blocked_mutation), patch.object(Path, "touch", _blocked_mutation):
            return str(func(**arguments)).strip()
    except Exception:
        return ""


def build_artifact_inventory(context: RunContext) -> list[dict[str, Any]]:
    """Build a simple artifact inventory for final outputs and key logs."""
    rows: list[dict[str, Any]] = []
    important_paths = [
        context.result_path,
        context.manifest_path,
        context.validator_log_path,
        context.harness_log_path,
        context.state_path,
        context.events_path,
        context.execution_log_path,
        context.final_plan_path,
        context.run_dir / "in_run_quality_summary.json",
        context.run_dir / "in_run_quality_events.jsonl",
    ]
    for path in important_paths:
        if path is None or not path.exists():
            continue
        rows.append(
            {
                "category": "run_metadata",
                "path": str(path),
                "relative_to_selected_dir": str(path.relative_to(context.selected_dir)) if path.is_relative_to(context.selected_dir) else "",
                "size_bytes": int(path.stat().st_size),
            }
        )

    final_dir = context.selected_dir / "final"
    if final_dir.is_dir():
        for path in sorted(p for p in final_dir.rglob("*") if p.is_file()):
            rows.append(
                {
                    "category": "final_output",
                    "path": str(path),
                    "relative_to_selected_dir": str(path.relative_to(context.selected_dir)),
                    "size_bytes": int(path.stat().st_size),
                }
            )
    return rows


def run_context_to_json(context: RunContext) -> dict[str, Any]:
    """Serialize one resolved run context into a stable JSON payload."""

    return {
        "resolution_mode": context.resolution_mode,
        "selected_dir": str(context.selected_dir),
        "result_path": str(context.result_path),
        "run_dir": str(context.run_dir),
        "manifest_path": str(context.manifest_path) if context.manifest_path else "",
        "state_path": str(context.state_path) if context.state_path else "",
        "final_plan_path": str(context.final_plan_path) if context.final_plan_path else "",
        "validator_log_path": str(context.validator_log_path) if context.validator_log_path else "",
        "harness_log_path": str(context.harness_log_path) if context.harness_log_path else "",
        "events_path": str(context.events_path) if context.events_path else "",
        "execution_log_path": str(context.execution_log_path) if context.execution_log_path else "",
        "result": context.result,
        "manifest": context.manifest,
        "state": context.state,
        "final_plan": context.final_plan,
    }
