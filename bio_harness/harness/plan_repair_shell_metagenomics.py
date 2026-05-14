"""Shell-segment and metagenomics repair helpers extracted from plan_repair."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.protocol_grounding import _looks_like_kraken2_db_dir, _resolve_metagenomics_kraken2_db
from bio_harness.harness.plan_helpers import _normalize_steps, _renumber_plan_steps


def _split_shell_command_segments(command: str) -> tuple[list[list[str]], str | None]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return [], None
    if not tokens:
        return [], None
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token == "&&":
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    cwd: str | None = None
    if segments and len(segments[0]) >= 2 and segments[0][0] == "cd":
        cwd = str(Path(segments[0][1]).expanduser().resolve(strict=False))
    return segments, cwd


def _quote_shell_segments(segments: list[list[str]]) -> str:
    rendered: list[str] = []
    for segment in segments:
        if not segment:
            continue
        rendered.append(" ".join(shlex.quote(str(token)) for token in segment))
    return " && ".join(rendered)


def _resolve_shell_path(token: str, *, cwd: str | None, selected_dir: Path) -> str:
    raw = str(token).strip().strip("'\"")
    if not raw:
        return raw
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path.resolve(strict=False))
    base = Path(cwd).expanduser() if cwd else selected_dir
    return str((base / path).resolve(strict=False))


def _repair_fastp_cli_flags(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
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
        segments, _cwd = _split_shell_command_segments(command)
        if not segments:
            continue
        changed = False
        for segment in segments:
            if not segment or Path(segment[0]).name.lower() != "fastp":
                continue
            for pos, token in enumerate(list(segment)):
                if token == "--threads":
                    segment[pos] = "--thread"
                    changed = True
        if not changed:
            continue
        updated_command = _quote_shell_segments(segments)
        step["arguments"] = {**args, "command": updated_command}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bash_run",
                "argument": "fastp_threads_flag",
                "from": "--threads",
                "to": "--thread",
            }
        )

    if not replacements:
        return plan, {"changed": False, "why": "no_fastp_cli_flag_repairs"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }


def _repair_metagenomics_trimmed_read_usage(
    plan: dict[str, Any],
    *,
    selected_dir: Path,
    analysis_spec: dict[str, Any] | None,
    request_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    request_l = str(request_text or "").lower()
    is_metagenomics = analysis_type == "metagenomics_classification" or "metagenom" in request_l
    if not is_metagenomics:
        return plan, {"changed": False, "why": "not_metagenomics_request"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    trimmed_r1 = ""
    trimmed_r2 = ""
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        if not command:
            continue
        segments, cwd = _split_shell_command_segments(command)
        if not segments:
            continue
        fastp_segment = next((segment for segment in segments if segment and Path(segment[0]).name.lower() == "fastp"), None)
        if not fastp_segment:
            continue
        for pos, token in enumerate(fastp_segment[:-1]):
            if token in {"-o", "--out1"} and not trimmed_r1:
                trimmed_r1 = _resolve_shell_path(fastp_segment[pos + 1], cwd=cwd, selected_dir=selected_dir)
            elif token in {"-O", "--out2"} and not trimmed_r2:
                trimmed_r2 = _resolve_shell_path(fastp_segment[pos + 1], cwd=cwd, selected_dir=selected_dir)
        if trimmed_r1 and trimmed_r2:
            break

    if not trimmed_r1 or not trimmed_r2:
        return plan, {"changed": False, "why": "no_fastp_trimmed_outputs_discovered"}

    replacements: list[dict[str, Any]] = []

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if tool_name == "spades_assemble":
            updated_args = dict(args)
            step_changed = False
            current_r1 = str(updated_args.get("reads_1", "")).strip()
            current_r2 = str(updated_args.get("reads_2", "")).strip()
            if current_r1 and current_r1 != trimmed_r1:
                updated_args["reads_1"] = trimmed_r1
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "spades_assemble",
                        "argument": "reads_1",
                        "from": current_r1,
                        "to": trimmed_r1,
                    }
                )
                step_changed = True
            if current_r2 and current_r2 != trimmed_r2:
                updated_args["reads_2"] = trimmed_r2
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "spades_assemble",
                        "argument": "reads_2",
                        "from": current_r2,
                        "to": trimmed_r2,
                    }
                )
                step_changed = True
            if not bool(updated_args.get("meta_mode", False)):
                updated_args["meta_mode"] = True
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "spades_assemble",
                        "argument": "meta_mode",
                        "from": bool(args.get("meta_mode", False)),
                        "to": True,
                    }
                )
                step_changed = True
            if bool(updated_args.get("careful", False)):
                updated_args["careful"] = False
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "spades_assemble",
                        "argument": "careful",
                        "from": True,
                        "to": False,
                    }
                )
                step_changed = True
            if step_changed:
                step["arguments"] = updated_args
            continue

        if tool_name != "bash_run":
            continue
        command = str(args.get("command", "")).strip()
        if not command or "kraken2" not in command.lower():
            continue
        segments, cwd = _split_shell_command_segments(command)
        if not segments:
            continue
        kraken_segment = next((segment for segment in segments if segment and Path(segment[0]).name.lower() == "kraken2"), None)
        if not kraken_segment:
            continue
        updated_tokens = list(kraken_segment)
        step_changed = False
        if "--paired" not in updated_tokens:
            updated_tokens.insert(1, "--paired")
            step_changed = True
        option_args = {
            "--db",
            "--report",
            "--output",
            "--classified-out",
            "--unclassified-out",
            "--threads",
            "--confidence",
            "--minimum-hit-groups",
            "--memory-mapping",
            "--report-minimizer-data",
            "--use-names",
        }
        positional_indices: list[int] = []
        skip_next = False
        for pos, token in enumerate(updated_tokens):
            if pos == 0:
                continue
            if skip_next:
                skip_next = False
                continue
            if token in option_args:
                skip_next = True
                continue
            if token.startswith("-"):
                continue
            positional_indices.append(pos)
        if len(positional_indices) >= 2:
            last_two = positional_indices[-2:]
            current_inputs = [
                _resolve_shell_path(updated_tokens[last_two[0]], cwd=cwd, selected_dir=selected_dir),
                _resolve_shell_path(updated_tokens[last_two[1]], cwd=cwd, selected_dir=selected_dir),
            ]
            desired_inputs = [trimmed_r1, trimmed_r2]
            if current_inputs != desired_inputs:
                updated_tokens[last_two[0]] = trimmed_r1
                updated_tokens[last_two[1]] = trimmed_r2
                step_changed = True
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "bash_run",
                        "argument": "kraken2_inputs",
                        "from": current_inputs,
                        "to": desired_inputs,
                    }
                )
        if "--paired" not in kraken_segment:
            replacements.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "argument": "kraken2_paired_flag",
                    "from": False,
                    "to": True,
                }
            )
        if step_changed:
            rebuilt_segments = [updated_tokens if segment is kraken_segment else segment for segment in segments]
            step["arguments"] = {**args, "command": _quote_shell_segments(rebuilt_segments)}

    if not replacements:
        return plan, {"changed": False, "why": "metagenomics_trimmed_bindings_already_clean"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "metagenomics_trimmed_bindings_repaired",
        "trimmed_reads": [trimmed_r1, trimmed_r2],
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }


def _repair_metagenomics_prebuilt_db_bindings(
    plan: dict[str, Any],
    *,
    selected_dir: Path,
    data_root: Path,
    analysis_spec: dict[str, Any] | None,
    request_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    if analysis_type == "direct_skill_smoke":
        return plan, {"changed": False, "why": "direct_skill_smoke_preserves_explicit_database_paths"}
    request_l = str(request_text or "").lower()
    is_metagenomics = analysis_type == "metagenomics_classification" or "metagenom" in request_l
    if not is_metagenomics:
        return plan, {"changed": False, "why": "not_metagenomics_request"}

    kraken_db, db_meta = _resolve_metagenomics_kraken2_db(
        selected_dir=selected_dir,
        data_root=data_root,
        analysis_spec=analysis_spec,
    )
    if not kraken_db:
        return plan, {"changed": False, "why": "no_prebuilt_kraken2_db_resolved", "kraken_db_meta": db_meta}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing", "kraken_db_meta": db_meta}

    quoted_db = shlex.quote(kraken_db)
    replacements: list[dict[str, Any]] = []

    def _replace_cli_db_arg(command: str, *, flag: str, tool_name: str) -> tuple[str, bool]:
        pattern = re.compile(rf"({re.escape(flag)}\s+)(\S+)")
        match = pattern.search(command)
        if match:
            current = match.group(2).strip().strip("'\"")
            if current and _looks_like_kraken2_db_dir(Path(current)):
                return command, False
            return pattern.sub(rf"\1{quoted_db}", command, count=1), True
        token = f"{tool_name} "
        idx = command.find(token)
        if idx < 0:
            return command, False
        return command.replace(token, f"{tool_name} {flag} {quoted_db} ", 1), True

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if not args:
            continue
        updated_args = dict(args)
        step_changed = False

        if tool_name == "metagenomics_kraken2_bracken_style":
            current_db = str(updated_args.get("database", "")).strip()
            if not current_db or not _looks_like_kraken2_db_dir(Path(current_db)):
                updated_args["database"] = kraken_db
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": tool_name,
                        "argument": "database",
                        "from": current_db,
                        "to": kraken_db,
                    }
                )
                step_changed = True
        elif tool_name == "bash_run":
            command = str(updated_args.get("command", "") or "").strip()
            command_l = command.lower()
            new_command = command
            command_changed = False
            if "kraken2" in command_l:
                new_command, changed = _replace_cli_db_arg(new_command, flag="--db", tool_name="kraken2")
                command_changed = command_changed or changed
            if "bracken" in command_l:
                new_command, changed = _replace_cli_db_arg(new_command, flag="-d", tool_name="bracken")
                command_changed = command_changed or changed
            if command_changed:
                updated_args["command"] = new_command
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": tool_name,
                        "argument": "command",
                        "from": command,
                        "to": new_command,
                    }
                )
                step_changed = True

        if step_changed:
            step["arguments"] = updated_args

    if not replacements:
        return plan, {"changed": False, "why": "metagenomics_db_already_bound", "kraken_db_meta": db_meta}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "metagenomics_prebuilt_db_repaired",
        "replacements": replacements,
        "kraken_db": kraken_db,
        "kraken_db_meta": db_meta,
    }
