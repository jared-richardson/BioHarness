"""Artifact-role invariants for executable plans.

This module validates and deterministically repairs path-role corruption that
can arise while the harness normalizes planner output. The central invariant is
that pre-existing inputs and references must remain distinct from execution
outputs, even when they share the same file extension.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.artifact_inspectors import _extract_expected_outputs
from bio_harness.core.artifact_roles import is_input_like_file_role
from bio_harness.core.bcftools_shell_semantics import repair_bcftools_isec_command
from bio_harness.core.shell_bindings import (
    analyze_shell_segments,
    default_shell_path_bindings,
    looks_like_pathlike_shell_text,
    resolve_shell_text,
)
from bio_harness.core.shell_output_hints import extract_shell_output_hints
from bio_harness.core.shell_parse import is_shell_assignment, split_shell_segments, strip_shell_comments
from bio_harness.core.tool_registry import ToolRegistry, default_tool_registry
from bio_harness.core.wrapper_contracts import normalize_wrapper_argument_value

_SHELL_OUTPUT_REDIRECT_TOKENS = frozenset({">", ">>", "1>", "1>>", "2>", "2>>"})
_SHELL_INPUT_REDIRECT_TOKENS = frozenset({"<", "0<"})
_SHELL_CWD_COMMANDS = frozenset({"cd", "pushd"})
_SHELL_DIRECTORY_CREATE_COMMANDS = frozenset({"mkdir"})
_SHELL_DIRECTORY_CREATE_FLAGS = frozenset({"-d", "--directory"})
_SHELL_SCRIPT_INTERPRETERS = frozenset({"bash", "python", "python3", "sh"})
_SHELL_INPUT_FLAG_KEYWORDS = (
    "annotation",
    "bam",
    "bed",
    "config",
    "cram",
    "csv",
    "db",
    "fasta",
    "fastq",
    "file",
    "files",
    "gff",
    "gff3",
    "gtf",
    "index",
    "input",
    "matrix",
    "metadata",
    "path",
    "paths",
    "reads",
    "reference",
    "table",
    "tsv",
    "vcf",
)
_EVOLUTION_BRANCH_ALIAS_RE = re.compile(r"(?<![a-z0-9])(anc|ancestor)(?![a-z0-9])")
_EVOLUTION_BRANCH_INDEX_RE = re.compile(r"(?:evol(?:ved)?|isolate|mutant)[^0-9]*(\d+)")
_EVOLUTION_SUBTRACTED_ALIAS_RE = re.compile(
    r"(?:(?<![a-z0-9])(?:minus|sub(?:tract(?:ed)?)?)(?![a-z0-9]).*?"
    r"(?<![a-z0-9])(?:anc|ancestor)(?![a-z0-9])|"
    r"(?<![a-z0-9])(?:anc|ancestor)(?![a-z0-9]).*?"
    r"(?<![a-z0-9])(?:minus|sub(?:tract(?:ed)?)?)(?![a-z0-9]))"
)
_EVOLUTION_NO_ANCESTOR_ALIAS_RE = re.compile(
    r"(?:no|without|minus)[^a-z0-9]*(?:anc|ancestor)|(?:anc|ancestor)[^a-z0-9]*(?:removed|excluded)"
)
_VCF_RAW_CALL_ALIAS_RE = re.compile(r"(?<![a-z0-9])call(?:er|ing|s?)?(?![a-z0-9])")
_VCF_CALLER_ALIAS_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"freebayes|haplotypecaller|mutect2?|deepvariant|strelka2?|lofreq|"
    r"varscan2?|platypus|octopus|clair3?|longshot|mpileup"
    r")(?![a-z0-9])"
)
_VCF_VARIANT_CALL_DIR_ALIAS_RE = re.compile(r"variant[^a-z0-9]*call(?:er|ing|s?)")
_VCF_SUFFIXES = (".vcf.gz", ".vcf")
_FASTA_SUFFIXES = (".fasta", ".fa", ".fna")
_GFF_SUFFIXES = (".gff3", ".gff", ".gtf")


def validate_artifact_role_invariants(
    plan: Mapping[str, Any],
    *,
    selected_dir: str | Path,
    allowed_input_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
    registry: ToolRegistry | None = None,
) -> list[dict[str, Any]]:
    """Return artifact-role violations for one executable plan.

    Args:
        plan: Candidate executable plan.
        selected_dir: Selected run output directory for the current task.
        allowed_input_roots: Optional readonly input roots that are allowed to
            live under ``selected_dir`` without being treated as generated
            outputs. This covers harness layouts such as
            ``selected_dir/inputs_readonly``.
        registry: Optional runtime tool registry override.

    Returns:
        Structured violation payloads. Each entry includes the step identity,
        offending parameter, and a stable violation type.
    """

    registry = registry or default_tool_registry()
    steps = plan.get("plan", []) if isinstance(plan, Mapping) else []
    if not isinstance(steps, list):
        return []

    selected_root = _normalize_root(selected_dir)
    allowed_roots = _normalize_allowed_roots(allowed_input_roots)
    upstream_outputs: set[str] = set()
    upstream_output_roots: set[str] = set()
    shell_bindings = default_shell_path_bindings(selected_root)
    violations: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, Mapping):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        if not tool_name:
            continue
        args = step.get("arguments", {})
        if not isinstance(args, Mapping):
            continue
        schema = registry.parameter_schema_for(tool_name)
        input_keys = set(registry.input_keys_for(tool_name))
        output_values = _produced_output_values(
            step,
            selected_root=selected_root,
            output_keys=_output_keys_for_tool(tool_name, registry),
            shell_bindings=shell_bindings,
        )
        output_roots = _produced_output_roots(
            step,
            selected_root=selected_root,
            output_keys=_output_keys_for_tool(tool_name, registry),
            shell_bindings=shell_bindings,
        )
        for param_name in sorted(input_keys):
            raw_value = args.get(param_name)
            for path_text in _iter_pathlike_values(
                raw_value,
                tool_name=tool_name,
                param_name=param_name,
                ):
                resolution = _resolve_contract_path_text(
                    path_text,
                    selected_root=selected_root,
                    shell_bindings=shell_bindings,
                )
                if resolution["unsupported"]:
                    violations.append(
                        _artifact_role_violation(
                            step=step,
                            default_index=index,
                            tool_name=tool_name,
                            param_name=param_name,
                            violation_type="unsupported_shell_variable_expansion",
                            path=str(path_text or "").strip(),
                            detail=(
                                f"{tool_name}.{param_name} uses shell expansion outside the "
                                "supported deterministic subset"
                            ),
                        )
                    )
                    continue
                if resolution["unresolved"]:
                    violations.append(
                        _artifact_role_violation(
                            step=step,
                            default_index=index,
                            tool_name=tool_name,
                            param_name=param_name,
                            violation_type="unresolved_shell_variable_path",
                            path=str(path_text or "").strip(),
                            detail=(
                                f"{tool_name}.{param_name} references unresolved shell "
                                "variables in a path-like argument"
                            ),
                        )
                    )
                    continue
                normalized = str(resolution["normalized"] or "").strip()
                if not normalized:
                    continue
                if normalized in output_values:
                    violations.append(
                        _artifact_role_violation(
                            step=step,
                            default_index=index,
                            tool_name=tool_name,
                            param_name=param_name,
                            violation_type="input_equals_output",
                            path=normalized,
                            detail=(
                                f"{tool_name}.{param_name} points to the same path as "
                                "a declared execution output"
                            ),
                        )
                    )
                    continue
                parameter = schema.get(param_name)
                if not parameter or not is_input_like_file_role(parameter.file_role):
                    continue
                if (
                    _path_within_root(normalized, selected_root)
                    and not _path_within_any_root(normalized, allowed_roots)
                    and normalized not in upstream_outputs
                    and not _path_within_any_root(normalized, upstream_output_roots)
                ):
                    violations.append(
                        _artifact_role_violation(
                            step=step,
                            default_index=index,
                            tool_name=tool_name,
                            param_name=param_name,
                            violation_type="input_in_selected_dir_without_producer",
                            path=normalized,
                            detail=(
                                f"{tool_name}.{param_name} points inside the selected "
                                "output directory without an upstream producer"
                            ),
                        )
                    )
        if tool_name == "bash_run":
            violations.extend(
                _validate_bash_run_selected_dir_inputs(
                    step,
                    default_index=index,
                    selected_root=selected_root,
                    allowed_roots=allowed_roots,
                    output_values=output_values,
                    output_roots=output_roots,
                    upstream_outputs=upstream_outputs,
                    upstream_output_roots=upstream_output_roots,
                    shell_bindings=shell_bindings,
                )
            )
            shell_bindings = _updated_shell_bindings_for_step(
                step,
                selected_root=selected_root,
                shell_bindings=shell_bindings,
            )
        upstream_outputs.update(output_values)
        upstream_output_roots.update(output_roots)
    return violations


def summarize_artifact_role_violations(
    violations: list[dict[str, Any]],
) -> list[str]:
    """Render artifact-role violations into stable one-line issue strings."""

    rendered: list[str] = []
    seen: set[str] = set()
    for row in violations:
        if not isinstance(row, Mapping):
            continue
        token = (
            f"{row.get('tool_name', '')}.{row.get('param_name', '')}:"
            f"{row.get('violation_type', '')}:{row.get('path', '')}"
        )
        if token in seen:
            continue
        seen.add(token)
        rendered.append(token)
    return rendered


def repair_artifact_role_violations(
    plan: Mapping[str, Any],
    *,
    source_plan: Mapping[str, Any] | None,
    selected_dir: str | Path,
    allowed_input_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
    registry: ToolRegistry | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Restore corrupted input/reference paths from an earlier source plan.

    Args:
        plan: Candidate executable plan after one normalization phase.
        source_plan: Earlier version of the plan whose bindings should be
            treated as the preferred source when they remain valid.
        selected_dir: Selected run output directory for the current task.
        allowed_input_roots: Optional readonly input roots that may legally
            live beneath ``selected_dir``.
        registry: Optional runtime tool registry override.

    Returns:
        Tuple of `(repaired_plan, meta)` describing any deterministic restores.
    """

    registry = registry or default_tool_registry()
    current_steps = _steps_from_plan(plan)
    source_steps = _steps_from_plan(source_plan)
    if not current_steps or not source_steps:
        return dict(plan or {}), {"changed": False, "why": "missing_plan_steps"}

    violations = validate_artifact_role_invariants(
        plan,
        selected_dir=selected_dir,
        allowed_input_roots=allowed_input_roots,
        registry=registry,
    )
    if not violations:
        return dict(plan or {}), {"changed": False, "why": "no_artifact_role_violations"}

    violations_by_step: dict[int, list[dict[str, Any]]] = {}
    for violation in violations:
        step_index = int(violation.get("step_index", -1))
        if step_index < 0:
            continue
        violations_by_step.setdefault(step_index, []).append(dict(violation))
    if not violations_by_step:
        return dict(plan or {}), {"changed": False, "why": "no_repairable_violations"}

    selected_root = _normalize_root(selected_dir)
    repaired_steps: list[Any] = []
    restored: list[str] = []
    inferred: list[str] = []
    upstream_outputs: set[str] = set()
    upstream_output_roots: set[str] = set()
    shell_bindings = default_shell_path_bindings(selected_root)
    for index, step in enumerate(current_steps):
        if not isinstance(step, Mapping):
            repaired_steps.append(step)
            continue
        repaired_step = dict(step)
        args = dict(step.get("arguments", {})) if isinstance(step.get("arguments", {}), Mapping) else {}
        source_step = source_steps[index] if index < len(source_steps) and isinstance(source_steps[index], Mapping) else {}
        source_args = (
            dict(source_step.get("arguments", {}))
            if isinstance(source_step, Mapping) and isinstance(source_step.get("arguments", {}), Mapping)
            else {}
        )
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        output_keys = _output_keys_for_tool(tool_name, registry)
        output_values = _produced_output_values(
            repaired_step,
            selected_root=selected_root,
            output_keys=output_keys,
            shell_bindings=shell_bindings,
        )
        output_roots = _produced_output_roots(
            repaired_step,
            selected_root=selected_root,
            output_keys=output_keys,
            shell_bindings=shell_bindings,
        )
        violations_for_step = violations_by_step.get(index, [])
        violations_by_param: dict[str, list[dict[str, Any]]] = {}
        for violation in violations_for_step:
            param_name = str(violation.get("param_name", "") or "").strip()
            if not param_name:
                continue
            violations_by_param.setdefault(param_name, []).append(violation)
        for param_name, param_violations in sorted(violations_by_param.items()):
            if param_name != "command":
                normalized_value, normalized_changed = _resolve_value_shell_bindings(
                    args.get(param_name),
                    selected_root=selected_root,
                    shell_bindings=shell_bindings,
                )
                if normalized_changed:
                    args[param_name] = normalized_value
                    inferred.append(f"{tool_name}.{param_name}:shell_binding")
            source_value = source_args.get(param_name)
            if param_name != "command" and _value_is_safe_input_binding(
                source_value,
                selected_root=selected_root,
                current_output_values=output_values,
                current_output_roots=output_roots,
                upstream_outputs=upstream_outputs,
                upstream_output_roots=upstream_output_roots,
            ):
                args[param_name] = source_value
                restored.append(f"{tool_name}.{param_name}")
                continue
            if param_name == "command":
                repaired_command, replacements = _repair_bash_command_input_aliases(
                    str(args.get("command", "") or ""),
                    violations=param_violations,
                    selected_root=selected_root,
                    upstream_outputs=upstream_outputs,
                )
                if replacements:
                    args["command"] = repaired_command
                    inferred.extend(replacements)
                continue
            updated_value = args.get(param_name)
            changed = False
            for violation in param_violations:
                missing_path = str(violation.get("path", "") or "").strip()
                if not missing_path:
                    continue
                candidate = _infer_upstream_input_binding(
                    missing_path,
                    upstream_outputs=upstream_outputs,
                    consumer_hint=f"{tool_name}.{param_name}",
                )
                if not candidate:
                    continue
                updated_value, replaced = _replace_pathlike_value(
                    updated_value,
                    missing_path=missing_path,
                    replacement_path=candidate,
                    selected_root=selected_root,
                )
                changed = changed or replaced
            if changed and _value_is_safe_input_binding(
                updated_value,
                selected_root=selected_root,
                current_output_values=output_values,
                current_output_roots=output_roots,
                upstream_outputs=upstream_outputs,
                upstream_output_roots=upstream_output_roots,
            ):
                args[param_name] = updated_value
                inferred.append(f"{tool_name}.{param_name}")
        repaired_step["arguments"] = args
        repaired_steps.append(repaired_step)
        upstream_outputs.update(output_values)
        upstream_output_roots.update(output_roots)
        shell_bindings = _updated_shell_bindings_for_step(
            repaired_step,
            selected_root=selected_root,
            shell_bindings=shell_bindings,
        )

    if not restored and not inferred:
        return dict(plan or {}), {"changed": False, "why": "no_safe_artifact_role_repairs"}

    repaired_plan = dict(plan or {})
    repaired_plan["plan"] = repaired_steps
    remaining = validate_artifact_role_invariants(
        repaired_plan,
        selected_dir=selected_dir,
        allowed_input_roots=allowed_input_roots,
        registry=registry,
    )
    return repaired_plan, {
        "changed": True,
        "restored": restored,
        "inferred": inferred,
        "remaining_issues": summarize_artifact_role_violations(remaining),
    }


def _steps_from_plan(plan: Mapping[str, Any] | None) -> list[Any]:
    """Return normalized step rows from one plan-like payload."""

    if not isinstance(plan, Mapping):
        return []
    steps = plan.get("plan", [])
    return list(steps) if isinstance(steps, list) else []


def _artifact_role_violation(
    *,
    step: Mapping[str, Any],
    default_index: int,
    tool_name: str,
    param_name: str,
    violation_type: str,
    path: str,
    detail: str,
) -> dict[str, Any]:
    """Build one stable artifact-role violation payload."""

    return {
        "step_index": default_index - 1,
        "step_id": int(step.get("step_id", default_index) or default_index),
        "tool_name": tool_name,
        "param_name": param_name,
        "violation_type": violation_type,
        "path": path,
        "detail": detail,
    }


def _validate_bash_run_selected_dir_inputs(
    step: Mapping[str, Any],
    *,
    default_index: int,
    selected_root: Path,
    allowed_roots: set[str],
    output_values: set[str],
    output_roots: set[str],
    upstream_outputs: set[str],
    upstream_output_roots: set[str],
    shell_bindings: Mapping[str, str] | None,
) -> list[dict[str, Any]]:
    """Return artifact-role violations for shell-command selected-dir inputs."""

    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    command = str(args.get("command", "") or "").strip()
    if not command:
        return []

    violations: list[dict[str, Any]] = []
    seen: set[str] = set()
    safe_upstream_output_roots = _filter_overbroad_output_roots(
        upstream_output_roots,
        selected_root=selected_root,
    )
    analyses = analyze_shell_segments(command, bindings=shell_bindings)
    for analysis in analyses:
        for unsupported_token in analysis.unsupported_tokens:
            violations.append(
                _artifact_role_violation(
                    step=step,
                    default_index=default_index,
                    tool_name="bash_run",
                    param_name="command",
                    violation_type="unsupported_shell_variable_expansion",
                    path=str(unsupported_token or "").strip(),
                    detail=(
                        "bash_run.command uses shell expansion outside the supported "
                        "deterministic subset"
                    ),
                )
            )
        for unresolved_name in analysis.unresolved_names:
            violations.append(
                _artifact_role_violation(
                    step=step,
                    default_index=default_index,
                    tool_name="bash_run",
                    param_name="command",
                    violation_type="unresolved_shell_variable_path",
                    path=str(unresolved_name or "").strip(),
                    detail=(
                        "bash_run.command references a shell variable that does not "
                        "have a deterministic binding"
                    ),
                )
            )
    for normalized in _iter_bash_run_input_paths(
        command,
        selected_root=selected_root,
        output_values=output_values,
        output_roots=output_roots,
        shell_bindings=shell_bindings,
    ):
        if (
            normalized == str(selected_root)
            or not _path_within_root(normalized, selected_root)
            or _path_within_any_root(normalized, allowed_roots)
            or normalized in upstream_outputs
            or _path_within_any_root(normalized, safe_upstream_output_roots)
        ):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        violations.append(
            _artifact_role_violation(
                step=step,
                default_index=default_index,
                tool_name="bash_run",
                param_name="command",
                violation_type="input_in_selected_dir_without_producer",
                path=normalized,
                detail=(
                    "bash_run.command references a path inside the selected "
                    "output directory without an upstream producer"
                ),
            )
        )
    return violations


def _iter_bash_run_input_paths(
    command: str,
    *,
    selected_root: Path,
    output_values: set[str],
    output_roots: set[str],
    shell_bindings: Mapping[str, str] | None,
) -> list[str]:
    """Return normalized selected-dir input-like paths referenced by one command."""

    discovered: list[str] = []
    seen: set[str] = set()
    for _raw_fragment, normalized in _iter_bash_run_input_fragments(
        command,
        selected_root=selected_root,
        output_values=output_values,
        output_roots=output_roots,
        shell_bindings=shell_bindings,
    ):
        if normalized in seen:
            continue
        seen.add(normalized)
        discovered.append(normalized)
    return discovered


def _iter_bash_run_input_fragments(
    command: str,
    *,
    selected_root: Path,
    output_values: set[str],
    output_roots: set[str],
    shell_bindings: Mapping[str, str] | None,
) -> list[tuple[str, str]]:
    """Return raw shell fragments plus their normalized selected-dir paths."""

    discovered: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    known_output_values = set(output_values)
    known_output_roots = set(output_roots)
    analyses = analyze_shell_segments(command, bindings=shell_bindings)
    for analysis in analyses:
        segment_text = str(analysis.original_text or "").strip()
        if not segment_text:
            continue
        segment_hints = extract_shell_output_hints(analysis.resolved_text)
        segment_output_values = {
            normalized
            for normalized in (
                _normalize_path_text(path_text, selected_root)
                for path_text in segment_hints.output_paths
            )
            if normalized
        }
        segment_output_roots = {
            normalized
            for normalized in (
                _normalize_path_text(root_text, selected_root)
                for root_text in segment_hints.output_roots
            )
            if normalized
        }
        safe_known_output_roots = _filter_overbroad_output_roots(
            known_output_roots,
            selected_root=selected_root,
        )
        try:
            tokens = shlex.split(segment_text, posix=True)
            resolved_tokens = shlex.split(analysis.resolved_text, posix=True)
        except ValueError:
            tokens = []
            resolved_tokens = []
        if not tokens:
            continue
        ignored_indexes = _ignored_shell_operand_indexes(tokens)
        for index, token in enumerate(tokens):
            probe = str(token or "").strip()
            if not probe or index in ignored_indexes:
                continue
            if is_shell_assignment(probe):
                continue
            if probe in _SHELL_OUTPUT_REDIRECT_TOKENS:
                continue
            if any(probe.startswith(redirect) and probe != redirect for redirect in _SHELL_OUTPUT_REDIRECT_TOKENS):
                continue
            if probe in _SHELL_INPUT_REDIRECT_TOKENS:
                continue
            previous = str(tokens[index - 1]).strip() if index > 0 else ""
            resolved_probe = str(resolved_tokens[index]).strip() if index < len(resolved_tokens) else probe
            raw_fragment = ""
            candidate = ""
            if probe.startswith("--") and "=" in probe:
                flag, _, value = probe.partition("=")
                resolved_value = ""
                if index < len(resolved_tokens):
                    _, _, resolved_value = str(resolved_tokens[index]).partition("=")
                if _looks_like_shell_input_flag(flag) and looks_like_pathlike_shell_text(resolved_value or value):
                    raw_fragment = value
                    candidate = _normalize_path_text(resolved_value or value, selected_root)
            elif "=" in probe and previous.startswith("-") and _looks_like_shell_input_flag(previous):
                _, _, value = probe.partition("=")
                _, _, resolved_value = resolved_probe.partition("=")
                if looks_like_pathlike_shell_text(resolved_value or value):
                    raw_fragment = value
                    candidate = _normalize_path_text(resolved_value or value, selected_root)
            elif looks_like_pathlike_shell_text(resolved_probe):
                if previous.startswith("-") and _looks_like_shell_output_flag(previous):
                    continue
                if (
                    previous.startswith("-")
                    and not _looks_like_shell_input_flag(previous)
                    and not _shell_flag_has_inline_value(previous)
                ):
                    continue
                if not previous.startswith("-") and _is_directory_like_output_path(
                    _normalize_path_text(resolved_probe, selected_root)
                ):
                    continue
                raw_fragment = probe
                candidate = _normalize_path_text(resolved_probe, selected_root)
            if (
                not candidate
                or candidate in known_output_values
                or candidate in segment_output_values
                or _path_within_any_root(candidate, safe_known_output_roots)
            ):
                continue
            pair = (raw_fragment, candidate)
            if pair in seen:
                continue
            seen.add(pair)
            discovered.append(pair)
        known_output_values.update(segment_output_values)
        known_output_roots.update(segment_output_roots)
    return discovered


def _filter_overbroad_output_roots(
    roots: set[str],
    *,
    selected_root: Path,
) -> set[str]:
    """Drop output roots that collapse to the entire selected directory."""

    selected_text = str(selected_root)
    return {
        normalized
        for normalized in (str(root or "").strip() for root in roots)
        if normalized and normalized != selected_text
    }


def _ignored_shell_operand_indexes(tokens: list[str]) -> set[int]:
    """Return operand indexes that belong to shell setup commands."""

    if not tokens:
        return set()
    command_name = Path(str(tokens[0] or "")).name.lower()
    ignored: set[int] = set()
    if (
        command_name in _SHELL_SCRIPT_INTERPRETERS
        and len(tokens) > 1
        and str(tokens[1]).strip()
        and not str(tokens[1]).startswith("-")
    ):
        ignored.add(1)
    if command_name == "env":
        for index, token in enumerate(tokens[1:], start=1):
            probe = str(token).strip()
            if not probe or probe.startswith("-") or "=" not in probe:
                break
            ignored.add(index)
    if command_name in _SHELL_CWD_COMMANDS:
        return ignored | ({1} if len(tokens) > 1 else set())
    if command_name in _SHELL_DIRECTORY_CREATE_COMMANDS:
        return ignored | {
            index
            for index, token in enumerate(tokens[1:], start=1)
            if str(token).strip() and not str(token).startswith("-")
        }
    if command_name == "install" and any(str(token).strip() in _SHELL_DIRECTORY_CREATE_FLAGS for token in tokens[1:]):
        return ignored | {
            index
            for index, token in enumerate(tokens[1:], start=1)
            if str(token).strip() and not str(token).startswith("-")
        }
    return ignored


def _looks_like_shell_path_token(token: str) -> bool:
    """Return whether one shell token materially looks like a filesystem path."""

    text = str(token or "").strip().strip("'\"").rstrip(";")
    if not text or text.startswith("-") or " " in text:
        return False
    if "/" in text or text.startswith(".") or text.startswith("~"):
        return True
    lowered = text.lower()
    suffixes = (
        ".txt",
        ".tsv",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".bam",
        ".cram",
        ".sam",
        ".fastq",
        ".fq",
        ".fastq.gz",
        ".fq.gz",
        ".fa",
        ".fasta",
        ".fna",
        ".gff",
        ".gff3",
        ".gtf",
        ".vcf",
        ".vcf.gz",
    )
    return lowered.endswith(suffixes)


def _looks_like_shell_input_flag(flag: str) -> bool:
    """Return whether one shell flag likely names an input-like path value."""

    normalized = str(flag or "").strip().lower()
    if not normalized.startswith("-") or _looks_like_shell_output_flag(normalized):
        return False
    probe = normalized.lstrip("-").replace("_", "-")
    return any(keyword in probe for keyword in _SHELL_INPUT_FLAG_KEYWORDS)


def _looks_like_shell_output_flag(flag: str) -> bool:
    """Return whether one shell flag likely names an output-like path value."""

    normalized = str(flag or "").strip().lower()
    if not normalized.startswith("-"):
        return False
    if normalized in {"-o", "--out", "--outdir", "--output", "--output-dir"}:
        return True
    probe = normalized.lstrip("-").replace("_", "-")
    return probe.startswith("out") or "output" in probe


def _shell_flag_has_inline_value(flag: str) -> bool:
    """Return whether one shell flag token already carries its own operand."""

    normalized = str(flag or "").strip()
    if not normalized.startswith("-"):
        return False
    if normalized.startswith("--"):
        return "=" in normalized
    if len(normalized) <= 2:
        return False
    suffix = normalized[2:]
    return any(not ch.isalpha() for ch in suffix)


def _normalize_allowed_roots(
    allowed_input_roots: list[str | Path] | tuple[str | Path, ...] | None,
) -> set[str]:
    """Normalize readonly input roots for artifact-role validation."""

    normalized: set[str] = set()
    for raw_root in allowed_input_roots or []:
        root_text = str(raw_root or "").strip()
        if not root_text:
            continue
        normalized_root = _normalize_root(root_text)
        if normalized_root:
            normalized.add(normalized_root)
    return normalized


def _output_keys_for_tool(tool_name: str, registry: ToolRegistry) -> set[str]:
    """Return output-like parameter names for one tool."""

    return {
        str(key).strip()
        for key in (
            list(registry.output_argument_keys_for(tool_name))
            + list(registry.execution_output_parameters_for(tool_name))
        )
        if str(key).strip()
    }


def _iter_pathlike_values(
    raw_value: Any,
    *,
    tool_name: str = "",
    param_name: str = "",
) -> list[str]:
    """Return stable path-like text values from one argument payload.

    Args:
        raw_value: Raw argument payload.
        tool_name: Optional step tool name used for wrapper-aware normalization.
        param_name: Optional argument name used for wrapper-aware normalization.

    Returns:
        A stable list of path-like strings.
    """

    if raw_value is None:
        return []
    normalized = normalize_wrapper_argument_value(tool_name, param_name, raw_value)
    if isinstance(normalized, (list, tuple, set)):
        return [str(item).strip() for item in normalized if str(item).strip()]
    text = str(normalized).strip()
    return [text] if text else []


def _normalize_root(root: str | Path) -> Path:
    """Return a stable selected-dir root path."""

    return Path(root).expanduser().resolve(strict=False)


def _normalize_path_text(path_text: str, selected_root: Path) -> str:
    """Return a normalized absolute path string for one plan value."""

    text = str(path_text or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = selected_root / path
    return str(path.resolve(strict=False))


def _resolve_contract_path_text(
    path_text: str,
    *,
    selected_root: Path,
    shell_bindings: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Return normalized contract-path resolution metadata for one value.

    Args:
        path_text: Raw planner-emitted path text.
        selected_root: Selected run output directory.
        shell_bindings: Current deterministic shell binding map.

    Returns:
        A mapping with normalized path text plus unresolved/unsupported flags.
    """

    raw = str(path_text or "").strip()
    if not raw:
        return {
            "normalized": "",
            "unresolved": False,
            "unsupported": False,
        }
    if "$" in raw or "`" in raw:
        resolution = resolve_shell_text(raw, bindings=shell_bindings)
        return {
            "normalized": (
                _normalize_path_text(resolution.resolved_text, selected_root)
                if not resolution.unresolved_names and not resolution.unsupported
                else ""
            ),
            "unresolved": bool(resolution.unresolved_names),
            "unsupported": resolution.unsupported,
        }
    return {
        "normalized": _normalize_path_text(raw, selected_root),
        "unresolved": False,
        "unsupported": False,
    }


def _updated_shell_bindings_for_step(
    step: Mapping[str, Any],
    *,
    selected_root: Path,
    shell_bindings: Mapping[str, str] | None,
) -> dict[str, str]:
    """Return updated plan-level shell bindings after one step.

    Args:
        step: One executable plan step.
        selected_root: Selected run output directory.
        shell_bindings: Current deterministic shell binding map.

    Returns:
        Updated shell binding map after processing the step.
    """

    bindings = dict(shell_bindings or {})
    tool_name = str(step.get("tool_name", "") or "").strip().lower()
    if tool_name != "bash_run":
        return bindings
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    command = str(args.get("command", "") or "").strip()
    if not command:
        return bindings
    analyses = analyze_shell_segments(command, bindings=bindings)
    if not analyses:
        return bindings
    return dict(analyses[-1].bindings_after)


def _normalized_output_values(
    args: Mapping[str, Any],
    *,
    tool_name: str,
    selected_root: Path,
    output_keys: set[str],
    shell_bindings: Mapping[str, str] | None = None,
) -> set[str]:
    """Return normalized output paths declared by one step."""

    outputs: set[str] = set()
    for key in output_keys:
        for path_text in _iter_pathlike_values(
            args.get(key),
            tool_name=tool_name,
            param_name=key,
        ):
            resolution = _resolve_contract_path_text(
                path_text,
                selected_root=selected_root,
                shell_bindings=shell_bindings,
            )
            normalized = str(resolution["normalized"] or "").strip()
            if normalized:
                outputs.add(normalized)
    return outputs


def _produced_output_values(
    step: Mapping[str, Any],
    *,
    selected_root: Path,
    output_keys: set[str],
    shell_bindings: Mapping[str, str] | None = None,
) -> set[str]:
    """Return normalized produced output paths for one step.

    This combines declarative output arguments with the same expected-output
    extraction used by runtime completion checks, so downstream steps can
    safely consume files produced by upstream bash helpers or wrappers that
    emit well-known artifacts beneath an output root.
    """

    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    outputs = _normalized_output_values(
        args,
        tool_name=str(step.get("tool_name", "") or ""),
        selected_root=selected_root,
        output_keys=output_keys,
        shell_bindings=shell_bindings,
    )
    tool_name = str(step.get("tool_name", "") or "").strip().lower()
    if tool_name == "bash_run":
        command = str(args.get("command", "") or "").strip()
        analyses = analyze_shell_segments(command, bindings=shell_bindings)
        for analysis in analyses:
            hints = extract_shell_output_hints(analysis.resolved_text)
            for path_text in hints.output_paths:
                normalized = _normalize_path_text(path_text, selected_root)
                if normalized:
                    outputs.add(normalized)
        return outputs
    for path_text in _extract_expected_outputs(dict(step)):
        if _is_directory_like_output_path(path_text):
            continue
        resolution = _resolve_contract_path_text(
            path_text,
            selected_root=selected_root,
            shell_bindings=shell_bindings,
        )
        normalized = str(resolution["normalized"] or "").strip()
        if normalized:
            outputs.add(normalized)
    return outputs


def _produced_output_roots(
    step: Mapping[str, Any],
    *,
    selected_root: Path,
    output_keys: set[str],
    shell_bindings: Mapping[str, str] | None = None,
) -> set[str]:
    """Return output directories or prefixes produced by one step."""

    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    roots: set[str] = set()
    for key in output_keys:
        if key != "output_dir" and not key.endswith("_dir"):
            continue
        for path_text in _iter_pathlike_values(
            args.get(key),
            tool_name=str(step.get("tool_name", "") or ""),
            param_name=key,
        ):
            resolution = _resolve_contract_path_text(
                path_text,
                selected_root=selected_root,
                shell_bindings=shell_bindings,
            )
            normalized = str(resolution["normalized"] or "").strip()
            if normalized:
                roots.add(normalized)
    tool_name = str(step.get("tool_name", "") or "").strip().lower()
    if tool_name == "bash_run":
        command = str(args.get("command", "") or "").strip()
        analyses = analyze_shell_segments(command, bindings=shell_bindings)
        for analysis in analyses:
            hints = extract_shell_output_hints(analysis.resolved_text)
            for root_text in hints.output_roots:
                normalized = _normalize_path_text(root_text, selected_root)
                if normalized:
                    roots.add(normalized)
        return roots
    for path_text in _extract_expected_outputs(dict(step)):
        if not _is_directory_like_output_path(path_text):
            continue
        resolution = _resolve_contract_path_text(
            path_text,
            selected_root=selected_root,
            shell_bindings=shell_bindings,
        )
        normalized = str(resolution["normalized"] or "").strip()
        if normalized:
            roots.add(normalized)
    return roots


def _path_within_root(path_text: str, root: Path) -> bool:
    """Return whether *path_text* resolves beneath *root*."""

    try:
        Path(path_text).resolve(strict=False).relative_to(root)
        return True
    except Exception:
        return False


def _path_within_any_root(path_text: str, roots: set[str]) -> bool:
    """Return whether *path_text* resolves beneath any known output root."""

    return any(_path_within_root(path_text, Path(root)) for root in roots)


def _is_directory_like_output_path(path_text: str) -> bool:
    """Return whether one produced path should be treated as an output root."""

    text = str(path_text or "").strip().rstrip("/")
    if not text:
        return False
    return Path(text).suffix == ""


def _value_is_safe_input_binding(
    value: Any,
    *,
    selected_root: Path,
    current_output_values: set[str],
    current_output_roots: set[str],
    upstream_outputs: set[str],
    upstream_output_roots: set[str],
) -> bool:
    """Return whether one source value can safely restore an input binding."""

    safe_current_output_roots = _filter_overbroad_output_roots(
        current_output_roots,
        selected_root=selected_root,
    )
    safe_upstream_output_roots = _filter_overbroad_output_roots(
        upstream_output_roots,
        selected_root=selected_root,
    )
    values = [
        normalized
        for normalized in (
            _normalize_path_text(path_text, selected_root)
            for path_text in _iter_pathlike_values(value)
        )
        if normalized
    ]
    if not values:
        return False
    for normalized in values:
        if normalized in current_output_values:
            return False
        if _path_within_any_root(normalized, safe_current_output_roots):
            return False
        if (
            _path_within_root(normalized, selected_root)
            and normalized not in upstream_outputs
            and not _path_within_any_root(normalized, safe_upstream_output_roots)
        ):
            return False
    return True


def _resolve_value_shell_bindings(
    value: Any,
    *,
    selected_root: Path,
    shell_bindings: Mapping[str, str] | None,
) -> tuple[Any, bool]:
    """Resolve safe shell-variable path aliases inside one argument value.

    Args:
        value: Raw argument payload.
        selected_root: Selected run output directory.
        shell_bindings: Current deterministic shell binding map.

    Returns:
        The normalized value plus a flag indicating whether any replacement was
        applied.
    """

    if value is None:
        return value, False
    if isinstance(value, list):
        changed = False
        updated: list[Any] = []
        for item in value:
            resolved_item, item_changed = _resolve_value_shell_bindings(
                item,
                selected_root=selected_root,
                shell_bindings=shell_bindings,
            )
            updated.append(resolved_item)
            changed = changed or item_changed
        return updated, changed
    if isinstance(value, tuple):
        updated, changed = _resolve_value_shell_bindings(
            list(value),
            selected_root=selected_root,
            shell_bindings=shell_bindings,
        )
        return tuple(updated), changed
    if isinstance(value, set):
        updated, changed = _resolve_value_shell_bindings(
            list(value),
            selected_root=selected_root,
            shell_bindings=shell_bindings,
        )
        return set(updated), changed
    raw = str(value or "").strip()
    if not raw or ("$" not in raw and "`" not in raw):
        return value, False
    resolution = resolve_shell_text(raw, bindings=shell_bindings)
    if resolution.unsupported or resolution.unresolved_names:
        return value, False
    if looks_like_pathlike_shell_text(resolution.resolved_text):
        return _normalize_path_text(resolution.resolved_text, selected_root), True
    return resolution.resolved_text, resolution.resolved_text != raw


def _infer_upstream_input_binding(
    missing_path: str,
    *,
    upstream_outputs: set[str],
    consumer_hint: str = "",
) -> str:
    """Return a unique upstream producer path for one missing selected-dir input."""

    if not missing_path or not upstream_outputs:
        return ""
    missing_name = Path(missing_path).name
    exact_matches = sorted(path for path in upstream_outputs if Path(path).name == missing_name)
    if len(exact_matches) == 1:
        return exact_matches[0]
    sibling_match = _infer_unique_sibling_output_binding(
        missing_path,
        upstream_outputs=upstream_outputs,
    )
    if sibling_match:
        return sibling_match
    alias_key = _selected_dir_artifact_alias_key(missing_path)
    if not alias_key:
        return _infer_generic_branch_vcf_binding(
            missing_path,
            upstream_outputs=upstream_outputs,
            consumer_hint=consumer_hint,
        )
    alias_matches = sorted(path for path in upstream_outputs if _selected_dir_artifact_alias_key(path) == alias_key)
    preferred_alias_match = _prefer_exact_suffix_alias_match(
        missing_path,
        candidates=alias_matches,
    )
    if preferred_alias_match:
        return preferred_alias_match
    if len(alias_matches) == 1:
        return alias_matches[0]
    return _infer_generic_branch_vcf_binding(
        missing_path,
        upstream_outputs=upstream_outputs,
        consumer_hint=consumer_hint,
    )


def _prefer_exact_suffix_alias_match(
    missing_path: str,
    *,
    candidates: list[str],
) -> str:
    """Return one alias candidate whose filename suffix exactly matches.

    When a shell helper emits both an uncompressed intermediate and the final
    compressed artifact (for example ``foo.vcf`` and ``foo.vcf.gz``), both
    paths can share the same branch-local alias key. Downstream consumers
    should bind to the candidate whose suffix exactly matches the missing
    selected-dir input when that choice is unique.
    """

    missing_suffix = "".join(Path(str(missing_path or "").strip()).suffixes).lower()
    if not missing_suffix:
        return ""
    suffix_matches = sorted(
        path
        for path in candidates
        if "".join(Path(str(path or "").strip()).suffixes).lower() == missing_suffix
    )
    return suffix_matches[0] if len(suffix_matches) == 1 else ""


def _infer_unique_sibling_output_binding(
    missing_path: str,
    *,
    upstream_outputs: set[str],
) -> str:
    """Return one unique upstream output under the same parent when safe.

    This covers deterministic prefix-directory producers such as
    ``bcftools isec -p`` where downstream steps may guess the wrong numbered
    member (for example ``0002.vcf`` instead of the actual ``0000.vcf``).
    """

    missing = Path(missing_path)
    parent = str(missing.parent.resolve(strict=False))
    suffix = "".join(missing.suffixes).lower()
    if not parent or not suffix:
        return ""
    candidates = sorted(
        path
        for path in upstream_outputs
        if str(Path(path).parent.resolve(strict=False)) == parent
        and "".join(Path(path).suffixes).lower() == suffix
    )
    return candidates[0] if len(candidates) == 1 else ""


def _infer_generic_branch_vcf_binding(
    missing_path: str,
    *,
    upstream_outputs: set[str],
    consumer_hint: str = "",
) -> str:
    """Return a unique branch-local VCF binding or stage predecessor when safe."""

    missing_family = _selected_dir_artifact_family(missing_path)
    missing_sample = _selected_dir_artifact_sample(missing_path)
    missing_stage = _selected_dir_artifact_stage(missing_path, missing_family)
    if missing_family != "vcf" or not missing_sample or not missing_stage:
        return ""
    candidate_stages = _selected_dir_vcf_predecessor_stages(
        missing_stage,
        consumer_hint=consumer_hint,
    )
    if not candidate_stages:
        return ""
    for stage in candidate_stages:
        candidates = sorted(
            path
            for path in upstream_outputs
            if _selected_dir_artifact_family(path) == "vcf"
            and _selected_dir_artifact_sample(path) == missing_sample
            and _selected_dir_artifact_stage(path, "vcf") == stage
        )
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return ""
    return ""


def _selected_dir_vcf_predecessor_stages(stage: str, *, consumer_hint: str = "") -> tuple[str, ...]:
    """Return safe predecessor stages for one missing selected-dir VCF stage."""

    normalized = str(stage or "").strip().lower()
    if not normalized:
        return ()
    hint = str(consumer_hint or "").strip().lower()
    if normalized == "vcf" and "bcftools isec" in hint:
        return ("filtered", "normalized", "subtracted", "raw", "vcf")
    predecessor_map: dict[str, tuple[str, ...]] = {
        "vcf": ("raw", "vcf"),
        # Filtering is a structural refinement over a branch-local raw call.
        "filtered": ("raw", "vcf"),
        # Normalization can safely fall back only to an earlier branch-local VCF.
        "normalized": ("filtered", "raw", "vcf"),
        # Minus-ancestor / no-ancestor aliases still consume branch-local VCFs.
        "subtracted": ("filtered", "normalized", "raw", "vcf"),
    }
    return predecessor_map.get(normalized, ())


def _replace_pathlike_value(
    value: Any,
    *,
    missing_path: str,
    replacement_path: str,
    selected_root: Path,
) -> tuple[Any, bool]:
    """Replace one path-like input value when it matches a missing selected-dir path."""

    if value is None:
        return value, False
    if isinstance(value, list):
        changed = False
        updated: list[Any] = []
        for item in value:
            repaired, repaired_changed = _replace_pathlike_value(
                item,
                missing_path=missing_path,
                replacement_path=replacement_path,
                selected_root=selected_root,
            )
            updated.append(repaired)
            changed = changed or repaired_changed
        return updated, changed
    if isinstance(value, tuple):
        updated, changed = _replace_pathlike_value(
            list(value),
            missing_path=missing_path,
            replacement_path=replacement_path,
            selected_root=selected_root,
        )
        return tuple(updated), changed
    if isinstance(value, set):
        updated, changed = _replace_pathlike_value(
            list(value),
            missing_path=missing_path,
            replacement_path=replacement_path,
            selected_root=selected_root,
        )
        return set(updated), changed
    raw = str(value or "").strip()
    if not raw or _normalize_path_text(raw, selected_root) != missing_path:
        return value, False
    return _render_replacement_path(
        raw,
        replacement_path,
        selected_root=selected_root,
    ), True


def _repair_bash_command_input_aliases(
    command: str,
    *,
    violations: list[dict[str, Any]],
    selected_root: Path,
    upstream_outputs: set[str],
) -> tuple[str, list[str]]:
    """Rewrite bash-run selected-dir inputs to unique upstream producer aliases."""

    updated = str(command or "")
    replacements: list[str] = []
    replacements_by_missing_path: dict[str, str] = {}
    raw_fragment_replacements: dict[str, str] = {}
    for violation in violations:
        missing_path = str(violation.get("path", "") or "").strip()
        if not missing_path:
            continue
        candidate = _infer_upstream_input_binding(
            missing_path,
            upstream_outputs=upstream_outputs,
            consumer_hint=command,
        )
        if not candidate:
            continue
        fragments = [
            raw_fragment
            for raw_fragment, normalized in _iter_bash_run_input_fragments(
                updated,
                selected_root=selected_root,
                output_values=set(),
                output_roots=set(),
                shell_bindings=default_shell_path_bindings(selected_root),
            )
            if normalized == missing_path
        ]
        if not fragments:
            continue
        replacements_by_missing_path[missing_path] = candidate
        for raw_fragment in fragments:
            replacement_fragment = _render_replacement_path(
                raw_fragment,
                candidate,
                selected_root=selected_root,
            )
            if replacement_fragment:
                raw_fragment_replacements[raw_fragment] = replacement_fragment
        replacements.append(f"bash_run.command:{Path(missing_path).name}")
    for raw_fragment, replacement in sorted(raw_fragment_replacements.items(), key=lambda item: len(item[0]), reverse=True):
        updated = updated.replace(raw_fragment, replacement)
    if replacements_by_missing_path:
        updated, changed = _rewrite_bash_command_input_tokens(
            updated,
            selected_root=selected_root,
            replacements_by_missing_path=replacements_by_missing_path,
        )
        if changed and not replacements:
            replacements.append("bash_run.command:input_aliases")
    repaired_isec_command, _ = repair_bcftools_isec_command(updated)
    if repaired_isec_command != updated:
        updated = repaired_isec_command
        replacements.append("bash_run.command:bcftools_isec_output_mode")
    return updated, replacements


def _rewrite_bash_command_input_tokens(
    command: str,
    *,
    selected_root: Path,
    replacements_by_missing_path: Mapping[str, str],
) -> tuple[str, bool]:
    """Rewrite exact bash input tokens without mutating output path substrings."""

    segments = split_shell_segments(command)
    if not segments:
        return str(command or ""), False

    rewritten_segments: list[str] = []
    changed = False
    for segment in segments:
        rewritten_segment, segment_changed = _rewrite_bash_segment_input_tokens(
            segment,
            selected_root=selected_root,
            replacements_by_missing_path=replacements_by_missing_path,
        )
        rewritten_segments.append(rewritten_segment)
        changed = changed or segment_changed
    if not changed:
        return str(command or ""), False
    return " && ".join(segment for segment in rewritten_segments if segment), True


def _rewrite_bash_segment_input_tokens(
    segment: str,
    *,
    selected_root: Path,
    replacements_by_missing_path: Mapping[str, str],
) -> tuple[str, bool]:
    """Rewrite one shell segment's exact input tokens when a mapping exists."""

    text = str(segment or "").strip()
    if not text:
        return text, False
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return text, False
    if not tokens:
        return text, False

    ignored_indexes = _ignored_shell_operand_indexes(tokens)
    updated_tokens: list[str] = []
    changed = False
    for index, token in enumerate(tokens):
        probe = str(token or "").strip()
        if not probe:
            updated_tokens.append(token)
            continue
        previous = str(tokens[index - 1]).strip() if index > 0 else ""
        replacement_token = ""
        if probe.startswith("--") and "=" in probe:
            flag, _, value = probe.partition("=")
            if _looks_like_shell_input_flag(flag) and _looks_like_shell_path_token(value):
                replacement_token = _rewrite_shell_token_value(
                    value,
                    selected_root=selected_root,
                    replacements_by_missing_path=replacements_by_missing_path,
                )
                if replacement_token:
                    updated_tokens.append(f"{flag}={replacement_token}")
                    changed = True
                    continue
        elif (
            "=" in probe
            and previous.startswith("-")
            and _looks_like_shell_input_flag(previous)
        ):
            key, _, value = probe.partition("=")
            if _looks_like_shell_path_token(value):
                replacement_token = _rewrite_shell_token_value(
                    value,
                    selected_root=selected_root,
                    replacements_by_missing_path=replacements_by_missing_path,
                )
                if replacement_token:
                    updated_tokens.append(f"{key}={replacement_token}")
                    changed = True
                    continue
        elif index not in ignored_indexes and _looks_like_shell_path_token(probe):
            if previous.startswith("-") and _looks_like_shell_output_flag(previous):
                updated_tokens.append(token)
                continue
            if (
                previous.startswith("-")
                and not _looks_like_shell_input_flag(previous)
                and not _shell_flag_has_inline_value(previous)
            ):
                updated_tokens.append(token)
                continue
            replacement_token = _rewrite_shell_token_value(
                probe,
                selected_root=selected_root,
                replacements_by_missing_path=replacements_by_missing_path,
            )
            if replacement_token:
                updated_tokens.append(replacement_token)
                changed = True
                continue
        updated_tokens.append(token)
    if not changed:
        return text, False
    return " ".join(shlex.quote(token) for token in updated_tokens), True


def _rewrite_shell_token_value(
    raw_fragment: str,
    *,
    selected_root: Path,
    replacements_by_missing_path: Mapping[str, str],
) -> str:
    """Return one rewritten shell-token value when an exact mapping exists."""

    missing_path = _normalize_path_text(raw_fragment, selected_root)
    replacement_path = str(replacements_by_missing_path.get(missing_path, "") or "").strip()
    if not replacement_path:
        return ""
    return _render_replacement_path(
        raw_fragment,
        replacement_path,
        selected_root=selected_root,
    )


def _render_replacement_path(
    template_path: str,
    replacement_path: str,
    *,
    selected_root: Path,
) -> str:
    """Render one repaired path while preserving absolute vs relative style."""

    raw = str(template_path or "").strip()
    candidate = str(replacement_path or "").strip()
    if not raw or not candidate:
        return candidate
    if "$" in raw:
        return candidate
    template = Path(raw).expanduser()
    if template.is_absolute():
        return candidate
    try:
        return str(Path(candidate).resolve(strict=False).relative_to(selected_root))
    except Exception:
        return candidate


def _selected_dir_artifact_alias_key(path_text: str) -> str:
    """Return a low-ambiguity alias key for benchmark-style selected-dir artifacts."""

    text = str(path_text or "").strip().lower()
    if not text:
        return ""
    family = _selected_dir_artifact_family(text)
    sample = _selected_dir_artifact_sample(text)
    stage = _selected_dir_artifact_stage(text, family)
    if not family or not sample or not stage:
        return ""
    return f"{sample}:{stage}:{family}"


def _selected_dir_artifact_family(path_text: str) -> str:
    """Return the coarse file family for one path."""

    text = str(path_text or "").strip().lower()
    if text.endswith(_VCF_SUFFIXES):
        return "vcf"
    if text.endswith(".bam"):
        return "bam"
    if text.endswith(_FASTA_SUFFIXES):
        return "fasta"
    if text.endswith(_GFF_SUFFIXES):
        return "annotation"
    if text.endswith(".faa"):
        return "protein"
    return ""


def _selected_dir_artifact_sample(path_text: str) -> str:
    """Return the evolution-branch label embedded in one selected-dir path."""

    raw = str(path_text or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    parts = [part for part in [path.name, *[parent.name for parent in path.parents]] if part]
    ancestor_seen = False
    for part in parts:
        text = str(part).strip().lower()
        if not text:
            continue
        match = _EVOLUTION_BRANCH_INDEX_RE.search(text)
        if match:
            return f"evol{match.group(1)}"
        if _EVOLUTION_BRANCH_ALIAS_RE.search(text):
            ancestor_seen = True
    return "ancestor" if ancestor_seen else ""


def _selected_dir_artifact_stage(path_text: str, family: str) -> str:
    """Return the artifact stage encoded in one selected-dir path."""

    text = str(path_text or "").strip().lower()
    if not text or not family:
        return ""
    if family == "bam":
        if "unmapped" in text:
            return "unmapped_bam"
        return "aligned_bam"
    if family == "fasta":
        if "contig" in text:
            return "contigs"
        if "scaffold" in text:
            return "scaffolds"
        return ""
    if family == "vcf":
        stage_tokens = tuple(
            stage
            for stage in (
                "annotated",
                "filtered",
                "normalized",
                "novel",
                "shared",
            )
            if stage in text
        )
        if stage_tokens:
            return "_".join(stage_tokens)
        if _EVOLUTION_SUBTRACTED_ALIAS_RE.search(text) or _EVOLUTION_NO_ANCESTOR_ALIAS_RE.search(text):
            return "subtracted"
        if "raw" in text:
            return "raw"
        if (
            _VCF_RAW_CALL_ALIAS_RE.search(text)
            or _VCF_CALLER_ALIAS_RE.search(text)
            or _VCF_VARIANT_CALL_DIR_ALIAS_RE.search(text)
        ):
            return "raw"
        return "vcf"
    if family == "annotation":
        return "annotation_gff"
    if family == "protein":
        return "annotation_faa"
    return ""
