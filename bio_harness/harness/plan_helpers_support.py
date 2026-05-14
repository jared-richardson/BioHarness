"""Plan analysis and manipulation utilities."""
from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path
from typing import Any

from bio_harness.core.hierarchical_planning import workflow_spec_from_plan
from bio_harness.core.shell_output_hints import extract_shell_output_hints
from bio_harness.core.shell_parse import split_shell_segments

_PROBE_ONLY_PREFIXES = ("which ", "echo ", "pwd", "ls ", "test ", "command -v ")
_READ_ONLY_SEGMENT_COMMANDS = frozenset({"awk", "cat", "cut", "grep", "head", "sed", "sort", "uniq", "wc"})
_READ_ONLY_BCFTOOLS_SUBCOMMANDS = frozenset({"head", "index", "query", "stats", "view"})
_READ_ONLY_TABIX_SUBCOMMANDS = frozenset({"--list-chroms", "-l"})
_NEUTRAL_SHELL_SEGMENT_COMMANDS = frozenset({"cd", "popd", "pushd"})


def _is_probe_only_bash(command: str) -> bool:
    c = command.strip().lower()
    return c.startswith(_PROBE_ONLY_PREFIXES)


def _is_read_only_bash_segment(tokens: list[str]) -> bool:
    """Return whether one shell segment only inspects existing state."""

    if not tokens:
        return True
    command_name = Path(str(tokens[0] or "")).name.lower()
    if command_name in _NEUTRAL_SHELL_SEGMENT_COMMANDS:
        return True
    if command_name in _READ_ONLY_SEGMENT_COMMANDS:
        return True
    if command_name == "bcftools":
        subcommand = Path(str(tokens[1] or "")).name.lower() if len(tokens) > 1 else ""
        return subcommand in _READ_ONLY_BCFTOOLS_SUBCOMMANDS
    if command_name == "tabix":
        return any(token in _READ_ONLY_TABIX_SUBCOMMANDS for token in tokens[1:])
    return False


def _is_output_free_read_only_bash(command: str) -> bool:
    """Return whether one shell command only probes/inspects without outputs."""

    normalized = str(command or "").strip()
    if not normalized:
        return True
    if _is_probe_only_bash(normalized):
        return True
    hints = extract_shell_output_hints(normalized)
    if hints.output_paths or hints.output_roots:
        return False
    for segment in split_shell_segments(normalized):
        seg = str(segment or "").strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg, posix=True)
        except Exception:
            tokens = seg.split()
        if not _is_read_only_bash_segment(tokens):
            return False
    return True


def _is_actionable_executable_plan(plan: dict[str, Any]) -> bool:
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not steps:
        return False

    actionable = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = step.get("tool_name", "")
        args = step.get("arguments", {}) if isinstance(step.get("arguments"), dict) else {}
        if tool_name == "fastqc_run":
            actionable += 1
            continue
        if tool_name != "bash_run":
            continue
        cmd = str(args.get("command", "")).strip()
        if not cmd:
            continue
        if _is_output_free_read_only_bash(cmd):
            continue
        actionable += 1
    return actionable > 0


def _plan_summary_for_repair_prompt(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {"thought_process": "", "workflow": [], "global_constraints": [], "final_deliverables": []}
    summary = workflow_spec_from_plan(plan)
    if isinstance(plan.get("thought_process", ""), str) and str(plan.get("thought_process", "")).strip():
        summary["thought_process"] = str(plan.get("thought_process", "")).strip()
    return summary


def _missing_local_scripts_for_plan(plan: dict[str, Any], selected_dir: Path) -> list[str]:
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return []

    script_suffixes = {".sh", ".py", ".r"}
    missing: list[str] = []
    seen: set[str] = set()

    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments"), dict) else {}
        command = str(args.get("command", "")).strip()
        if not command:
            continue

        for seg in split_shell_segments(command):
            segment = str(seg or "").strip()
            if not segment:
                continue
            try:
                tokens = shlex.split(segment, posix=True)
            except Exception:
                tokens = segment.split()
            if not tokens:
                continue

            candidates: list[str] = []
            first = str(tokens[0]).strip()
            first_name = Path(first).name.lower()
            if Path(first).suffix.lower() in script_suffixes:
                candidates.append(first)
            if first_name in {"bash", "sh", "zsh", "python", "python3", "rscript"} and len(tokens) >= 2:
                nxt = str(tokens[1]).strip()
                if nxt and nxt not in {"-c", "-m", "-"} and not nxt.startswith("-"):
                    if Path(nxt).suffix.lower() in script_suffixes:
                        candidates.append(nxt)

            for raw in candidates:
                token = str(raw).strip()
                if not token:
                    continue
                p = Path(token).expanduser()
                explicit_path = p.is_absolute() or "/" in token or token.startswith(".") or token.startswith("~")
                if explicit_path:
                    resolved = p if p.is_absolute() else (selected_dir / p)
                    resolved = resolved.resolve(strict=False)
                    if not resolved.exists():
                        # Also check if the basename is on PATH (e.g. hap.py
                        # is a bioinformatics tool, not a local script).
                        if shutil.which(p.name):
                            continue
                        key = str(resolved)
                        if key not in seen:
                            seen.add(key)
                            missing.append(key)
                    continue

                if shutil.which(token):
                    continue
                # Bare tool names (no path separator) that aren't on PATH
                # are missing system tools, not local scripts. Only flag
                # them if the file actually exists in selected_dir (meaning
                # the user intended to reference a local file).
                resolved = (selected_dir / p).resolve(strict=False)
                if resolved.exists():
                    continue
                # Don't flag bare names as missing local scripts — they
                # are just missing system tools (handled elsewhere).
                continue

    return missing


def _normalize_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw_steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(raw_steps, list):
        return []
    return [s for s in raw_steps if isinstance(s, dict)]


def _step_fingerprint(step: dict[str, Any]) -> str:
    tool = str(step.get("tool_name", "")).strip().lower()
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
    payload = {"tool_name": tool, "arguments": args}
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _renumber_plan_steps(plan: dict[str, Any]) -> dict[str, Any]:
    steps = _normalize_steps(plan)
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    plan["plan"] = steps
    return plan


def _apply_repaired_plan_with_resume(run: dict[str, Any], patched_plan: dict[str, Any]) -> dict[str, Any]:
    old_plan = run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {}
    old_steps = _normalize_steps(old_plan)
    new_steps = _normalize_steps(patched_plan)
    old_statuses = list(run.get("step_statuses", [])) if isinstance(run.get("step_statuses", []), list) else []

    preserved_prefix = 0
    for idx, new_step in enumerate(new_steps):
        if idx >= len(old_steps) or idx >= len(old_statuses):
            break
        if str(old_statuses[idx]).strip().lower() != "completed":
            break
        if _step_fingerprint(old_steps[idx]) != _step_fingerprint(new_step):
            break
        preserved_prefix += 1

    patched_statuses = ["pending"] * len(new_steps)
    for idx in range(preserved_prefix):
        patched_statuses[idx] = "completed"

    run["plan"] = patched_plan
    run["step_statuses"] = patched_statuses
    run["next_step_idx"] = preserved_prefix
    run["status"] = "planned"
    run["error"] = ""

    return {
        "resume_idx": preserved_prefix,
        "preserved_completed_steps": preserved_prefix,
        "before_step_count": len(old_steps),
        "after_step_count": len(new_steps),
    }


def _plan_completed_prefix_len(run: dict[str, Any]) -> int:
    statuses = list(run.get("step_statuses", [])) if isinstance(run.get("step_statuses", []), list) else []
    prefix = 0
    for status in statuses:
        if str(status).strip().lower() != "completed":
            break
        prefix += 1
    return prefix


__all__ = [name for name in globals() if not name.startswith("__")]
