"""Argument and path helpers for direct-wrapper plan binding.

This module keeps generic argument-preservation and output-binding helpers out
of the higher-level direct-wrapper orchestration logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.tool_output_bindings import requested_output_bindings_for_tool
from bio_harness.core.tool_registry import ToolRegistry


def _locked_argument_values_for_tool(
    analysis_spec: Mapping[str, Any] | None,
    tool_name: str,
) -> dict[str, Any]:
    """Return locked explicit argument values for one tool."""

    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    intent = (
        spec.get("explicit_execution_intent", {})
        if isinstance(spec.get("explicit_execution_intent", {}), Mapping)
        else {}
    )
    locked = intent.get("locked_argument_values", {})
    if not isinstance(locked, Mapping):
        return {}
    raw = locked.get(tool_name, {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def _explicit_path_preservation_flags(
    analysis_spec: Mapping[str, Any] | None,
) -> tuple[bool, bool]:
    """Return whether explicit input and output paths should be preserved."""

    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    intent = (
        spec.get("explicit_execution_intent", {})
        if isinstance(spec.get("explicit_execution_intent", {}), Mapping)
        else {}
    )
    return (
        bool(intent.get("preserve_input_paths", False)),
        bool(intent.get("preserve_output_paths", False)),
    )


def _required_output_paths(contract: Mapping[str, Any] | None) -> list[str]:
    """Return normalized required output paths from the request contract."""

    raw = contract.get("required_output_paths", []) if isinstance(contract, Mapping) else []
    return [str(item).strip() for item in raw if str(item).strip()]


def _bind_execution_output_argument(
    *,
    tool_name: str,
    param_name: str,
    output_paths: list[str],
    selected_dir: str,
    current_arguments: Mapping[str, Any],
    registry: ToolRegistry,
) -> str:
    """Return a deterministic execution-output binding for one missing argument."""

    requested_bindings = requested_output_bindings_for_tool(
        tool_name,
        output_paths,
        registry=registry,
    )
    requested_value = str(requested_bindings.get(param_name, "") or "").strip()
    if requested_value:
        return requested_value

    canonical_map = registry.canonical_output_filenames_for(tool_name)
    primary_output = str(registry.primary_output_parameter_for(tool_name) or "").strip()
    canonical_name = canonical_map.get(param_name)
    directory_outputs = [path for path in output_paths if _looks_like_directory_output(path)]
    file_outputs = [path for path in output_paths if not _looks_like_directory_output(path)]
    matched_file_output = _matching_file_output(
        file_outputs,
        canonical_name if isinstance(canonical_name, str) else None,
    )

    if param_name == "output_dir":
        if directory_outputs:
            return directory_outputs[0]
        shared_parent = _shared_parent_directory(file_outputs)
        if shared_parent:
            return shared_parent
        return str(Path(selected_dir).expanduser() / tool_name)

    if isinstance(canonical_name, str) and canonical_name.strip():
        if matched_file_output:
            return matched_file_output
        if param_name == primary_output:
            if directory_outputs:
                return str(Path(directory_outputs[0]).expanduser() / canonical_name)
            if len(file_outputs) == 1:
                return file_outputs[0]
            return str(Path(selected_dir).expanduser() / tool_name / canonical_name)

        if directory_outputs:
            return str(Path(directory_outputs[0]).expanduser() / canonical_name)
        primary_value = str(current_arguments.get(primary_output, "") or "").strip()
        if primary_value:
            return str(Path(primary_value).expanduser().with_name(canonical_name))
        return str(Path(selected_dir).expanduser() / tool_name / canonical_name)

    if param_name == primary_output and directory_outputs:
        return directory_outputs[0]
    if param_name == primary_output and len(file_outputs) == 1:
        return file_outputs[0]
    if param_name == primary_output:
        return str(Path(selected_dir).expanduser() / tool_name)
    return ""


def _matching_file_output(file_outputs: list[str], canonical_name: str | None) -> str:
    """Return a requested file output matching one canonical filename."""

    target_name = str(canonical_name or "").strip()
    if not target_name:
        return ""
    for output_path in file_outputs:
        if Path(output_path).name == target_name:
            return output_path
    return ""


def _shared_parent_directory(file_outputs: list[str]) -> str:
    """Return the shared parent directory for requested file outputs."""

    if not file_outputs:
        return ""
    parents = [
        str(Path(path).expanduser().parent)
        for path in file_outputs
        if str(path).strip()
    ]
    if not parents:
        return ""
    first = parents[0]
    if all(parent == first for parent in parents[1:]):
        return first
    return ""


def _looks_like_directory_output(path_text: str) -> bool:
    """Return whether an output path is directory-like rather than file-like."""

    text = str(path_text or "").strip()
    if not text:
        return False
    if text.endswith(("/", "\\")):
        return True
    return Path(text).suffix == ""


def _missing_required_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    registry: ToolRegistry,
) -> list[str]:
    """Return required non-harness-managed arguments still missing for a tool."""

    harness_managed = set(registry.harness_managed_parameters_for(tool_name))
    missing: list[str] = []
    for param_name in registry.required_parameters_for(tool_name):
        if param_name in harness_managed:
            continue
        if _argument_missing(arguments.get(param_name)):
            missing.append(param_name)
    return missing


def _argument_missing(value: Any) -> bool:
    """Return whether an argument value should count as absent."""

    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _path_exists(value: Any) -> bool:
    """Return whether *value* names an existing filesystem path."""

    text = str(value or "").strip()
    if not text:
        return False
    try:
        return Path(text).expanduser().exists()
    except OSError:
        return False


def _same_path_value(current: Any, expected: Any) -> bool:
    """Return whether two path-like values resolve to the same path text."""

    left = str(current or "").strip()
    right = str(expected or "").strip()
    if not left or not right:
        return False
    try:
        return (
            Path(left).expanduser().resolve(strict=False)
            == Path(right).expanduser().resolve(strict=False)
        )
    except OSError:
        return left == right


def _should_preserve_path_value(
    current: Any,
    candidate: str,
    *,
    preserve_paths: bool,
) -> bool:
    """Return whether a deterministic preserved path should replace *current*."""

    if not candidate:
        return False
    if _argument_missing(current):
        return True
    if _same_path_value(current, candidate):
        return False
    if preserve_paths:
        return True
    return not _path_exists(current)


def _selected_dir_output_should_win(
    current: Any,
    candidate: str,
    *,
    selected_dir: str,
    tool_name: str,
    param_name: str,
    current_arguments: Mapping[str, Any],
    registry: ToolRegistry,
) -> bool:
    """Return whether a selected-dir-local current output should be preserved."""

    current_path = str(current or "").strip()
    candidate_path = str(candidate or "").strip()
    if not current_path or not candidate_path or not selected_dir:
        return False
    if _same_path_value(current_path, candidate_path):
        return False
    if not _path_is_within_dir(current_path, selected_dir) or _path_is_within_dir(
        candidate_path,
        selected_dir,
    ):
        return False
    try:
        current_resolved = Path(current_path).expanduser().resolve(strict=False)
        candidate_resolved = Path(candidate_path).expanduser().resolve(strict=False)
        selected_resolved = Path(selected_dir).expanduser().resolve(strict=False)
    except OSError:
        return False
    should_preserve = (
        current_resolved.parent == selected_resolved
        and current_resolved.name == candidate_resolved.name
    )
    if not should_preserve:
        return False
    primary_output = str(registry.primary_output_parameter_for(tool_name) or "").strip()
    if not primary_output or param_name == primary_output:
        return True
    primary_value = str(current_arguments.get(primary_output, "") or "").strip()
    if not primary_value:
        return False
    try:
        primary_resolved = Path(primary_value).expanduser().resolve(strict=False)
    except OSError:
        return False
    return primary_resolved.parent == selected_resolved


def _path_is_within_dir(path_text: str, root_dir: str) -> bool:
    """Return whether one candidate path stays inside one root directory."""

    try:
        return Path(path_text).expanduser().resolve(strict=False).is_relative_to(
            Path(root_dir).expanduser().resolve(strict=False)
        )
    except OSError:
        return False
