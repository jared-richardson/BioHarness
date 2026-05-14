"""Quantification export repair helpers extracted from plan_repair."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.shell_parse import split_shell_segments
from bio_harness.harness.plan_helpers import _normalize_steps, _renumber_plan_steps


def _repair_quantification_export_segment(segment: str) -> tuple[str, dict[str, Any] | None]:
    text = str(segment or "").strip()
    if not text:
        return segment, None
    try:
        tokens = shlex.split(text, posix=True)
    except Exception:
        return segment, None
    if len(tokens) < 3 or str(tokens[0]).strip().lower() != "awk":
        return segment, None

    input_idx = -1
    source_kind = ""
    for idx, token in enumerate(tokens[2:], start=2):
        leaf = Path(str(token)).name.lower()
        if leaf == "quant.sf":
            input_idx = idx
            source_kind = "salmon_quant_sf"
            break
        if leaf == "abundance.tsv":
            input_idx = idx
            source_kind = "kallisto_abundance_tsv"
            break
    if input_idx < 0:
        return segment, None

    program = str(tokens[1]).strip()
    if source_kind == "salmon_quant_sf":
        if "$5" in program or "numreads" in program.lower():
            return segment, None
        repaired_program = 'NR>1 {print $1 "\\t" int($5)}'
    else:
        if "$4" in program or "est_counts" in program.lower():
            return segment, None
        repaired_program = 'NR>1 {print $1 "\\t" int($4)}'

    rebuilt = f"awk {shlex.quote(repaired_program)} {shlex.quote(str(tokens[input_idx]))}"
    if input_idx + 1 < len(tokens):
        rebuilt += " " + " ".join(tokens[input_idx + 1 :])
    return rebuilt, {
        "source_kind": source_kind,
        "before_program": program,
        "after_program": repaired_program,
    }


def _repair_quantification_export_command(command: str) -> tuple[str, dict[str, Any] | None]:
    text = str(command or "").strip()
    if not text:
        return command, None
    lowered = text.lower()
    if "transcript_counts.tsv" not in lowered:
        return command, None
    if "quant.sf" not in lowered and "abundance.tsv" not in lowered:
        return command, None
    if "salmon quant" not in lowered and "kallisto quant" not in lowered:
        return command, None

    source_match = re.search(r"([^\s;]+(?:quant\.sf|abundance\.tsv))\b", text)
    output_match = re.search(r">\s*([^\s;]+transcript_counts\.tsv)\b", text)
    if not source_match or not output_match:
        return command, None

    source_path = str(source_match.group(1) or "").strip().strip("\"'")
    output_path = str(output_match.group(1) or "").strip().strip("\"'")
    if not source_path or not output_path:
        return command, None

    source_kind = "salmon_quant_sf" if source_path.endswith("quant.sf") else "kallisto_abundance_tsv"
    export_program = 'NR>1 {print $1 "\\t" int($5)}' if source_kind == "salmon_quant_sf" else 'NR>1 {print $1 "\\t" int($4)}'
    rebuilt = f"awk {shlex.quote(export_program)} {shlex.quote(source_path)} > {shlex.quote(output_path)}"
    return rebuilt, {
        "source_kind": source_kind,
        "rewrote_quant_rerun_shell": True,
        "source_path": source_path,
        "output_path": output_path,
    }


def _repair_quantification_count_exports(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    replacements: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        if not command:
            continue
        updated_command = command
        step_replacements: list[dict[str, Any]] = []
        rebuilt_command, command_meta = _repair_quantification_export_command(command)
        if command_meta and rebuilt_command != command:
            updated_command = rebuilt_command
            step_replacements.append(command_meta)
        for segment in split_shell_segments(command):
            repaired_segment, meta = _repair_quantification_export_segment(segment)
            if not meta or repaired_segment == segment:
                continue
            updated_command = updated_command.replace(segment, repaired_segment, 1)
            step_replacements.append(meta)
        if not step_replacements:
            continue
        step["arguments"] = {**args, "command": updated_command}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bash_run",
                "repairs": step_replacements,
            }
        )

    if not replacements:
        return plan, {"changed": False, "why": "no_quantification_export_repairs"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }
