"""Artifact-path, missing-input, cache-cleanup, and output-verification helpers."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from bio_harness.core.artifact_inspectors import scan_existing_step_outputs
from bio_harness.core.shell_output_hints import extract_shell_output_hints

from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.harness.path_utils import (
    _normalize_plan_path_text,
    _resolve_existing_input_path,
)


def _iter_pathlike_values(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    text = str(raw_value).strip()
    return [text] if text else []


def _collect_planned_output_paths(plan: dict[str, Any], selected_dir: Path) -> set[str]:
    registry = default_tool_registry()
    planned: set[str] = set()
    for step in plan.get("plan", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        output_keys = {
            str(key).strip().lower()
            for key in (
                list(registry.output_argument_keys_for(tool_name))
                + list(registry.execution_output_parameters_for(tool_name))
            )
            if str(key).strip()
        }
        for key, raw_value in args.items():
            key_l = str(key).strip().lower()
            if not key_l.startswith("output") and key_l not in output_keys:
                continue
            for item in _iter_pathlike_values(raw_value):
                normalized = _normalize_plan_path_text(item, selected_dir)
                if normalized:
                    planned.add(normalized)
        if tool_name == "spades_assemble":
            out_dir = str(args.get("output_dir", "")).strip()
            if out_dir:
                for name in ("contigs.fasta", "scaffolds.fasta"):
                    normalized = _normalize_plan_path_text(str(Path(out_dir) / name), selected_dir)
                    if normalized:
                        planned.add(normalized)
        if tool_name == "bash_run":
            command = str(args.get("command", "")).strip()
            if not command:
                continue
            hints = extract_shell_output_hints(
                command,
                extra_output_flags=(
                    "-O",
                    "-h",
                    "-j",
                    "--out1",
                    "--out2",
                    "--report",
                    "--detected",
                    "--bam",
                    "--ref",
                ),
            )
            for candidate in hints.output_paths + hints.output_roots:
                normalized = _normalize_plan_path_text(candidate, selected_dir)
                if normalized:
                    planned.add(normalized)
    return planned


def _missing_input_paths_for_plan(plan: dict[str, Any], selected_dir: Path, data_root: Path) -> list[str]:
    registry = default_tool_registry()
    missing: list[str] = []
    planned_outputs = _collect_planned_output_paths(plan, selected_dir)
    for step in plan.get("plan", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip()
        keys = registry.input_keys_for(tool_name)
        if not keys:
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        for key in keys:
            raw_value = args.get(key, "")
            for raw in _iter_pathlike_values(raw_value):
                if not raw:
                    continue
                if _resolve_existing_input_path(raw, selected_dir, data_root):
                    continue
                normalized = _normalize_plan_path_text(raw, selected_dir)
                if normalized and normalized in planned_outputs:
                    continue
                missing.append(f"{tool_name}.{key}:{raw}")
    dedup: list[str] = []
    seen: set[str] = set()
    for item in missing:
        if item in seen:
            continue
        seen.add(item)
        dedup.append(item)
    return dedup


def _clean_stale_tmp_cache_paths(plan: dict[str, Any], selected_dir: Path, workspace_root: Path) -> dict[str, Any]:
    candidates: set[Path] = set()
    for step in (plan.get("plan", []) if isinstance(plan, dict) else []):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")) != "bash_run":
            continue
        cmd = str((step.get("arguments") or {}).get("command", "")).strip()
        if not cmd:
            continue
        for token in re.findall(r"(/[^\s\"']+)", cmd):
            path = Path(token)
            name_l = path.name.lower()
            if any(key in name_l for key in ("rmats_tmp", "__startmp")):
                candidates.add(path)
        for rel in re.findall(r"(outputs/[^\s\"']+)", cmd):
            path = selected_dir / rel
            name_l = path.name.lower()
            if any(key in name_l for key in ("rmats_tmp", "__startmp")):
                candidates.add(path)
    candidates.add(selected_dir / "outputs" / "splicing_auto" / "rmats_tmp")

    removed: list[str] = []
    for path in sorted(candidates):
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            continue
        try:
            resolved.relative_to(workspace_root)
        except Exception:
            continue
        if resolved.exists() and resolved.is_dir():
            shutil.rmtree(resolved)
            removed.append(str(resolved))
    return {
        "changed": bool(removed),
        "removed_paths": removed,
        "diff_summary": {"removed_path_count": len(removed)},
    }


def _plan_contains_splicing_steps(plan: dict[str, Any]) -> bool:
    for step in (plan or {}).get("plan", []):
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool_name", "")).lower()
        cmd = str(step.get("arguments", {}).get("command", "")).lower()
        if "rmats" in tool or "rmats" in cmd:
            return True
        if re.search(r"\bstar\b", cmd):
            return True
    return False


def _rmats_output_dirs(selected_dir: Path, plan: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    for step in (plan or {}).get("plan", []):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "rmats_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        output_dir = str(args.get("output_dir", "")).strip()
        if not output_dir:
            continue
        path = Path(output_dir).expanduser()
        if not path.is_absolute():
            path = selected_dir / path
        candidates.append(path)
    candidates.append(selected_dir / "outputs" / "splicing_auto" / "rmats")
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        rendered = str(path)
        if rendered in seen:
            continue
        seen.add(rendered)
        deduped.append(path)
    return deduped


def _verify_run_outputs(selected_dir: Path, plan: dict[str, Any]) -> tuple[bool, str]:
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    missing_outputs: list[str] = []
    if isinstance(steps, list):
        for idx, _step in enumerate(steps):
            outputs = scan_existing_step_outputs(selected_dir, plan, idx)
            if not outputs:
                continue
            missing_outputs.extend(
                path
                for path, info in outputs.items()
                if not bool(info.get("valid", False))
            )
    if missing_outputs:
        preview = ", ".join(sorted(dict.fromkeys(missing_outputs))[:3])
        return False, f"Planned outputs were not produced: {preview}"
    if not _plan_contains_splicing_steps(plan):
        return True, ""
    for rmats_out in _rmats_output_dirs(selected_dir, plan):
        if (rmats_out / "SE.MATS.JCEC.txt").exists() or (rmats_out / "SE.MATS.JC.txt").exists():
            return True, ""
    return False, "rMATS output tables were not produced (missing SE.MATS.JC/JCEC)."
