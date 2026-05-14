"""Helpers for mapping requested output paths onto wrapper arguments.

These helpers keep request-driven output preservation consistent between
analysis-spec normalization and direct-wrapper execution repair. The goal is
to preserve explicit user-owned artifact locations without inventing scientific
steps or wrapper parameters.
"""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.request_output_intent import is_file_like_output_path
from bio_harness.core.tool_registry import ToolRegistry, default_tool_registry


def requested_output_bindings_for_tool(
    tool_name: str,
    output_paths: list[str],
    *,
    registry: ToolRegistry | None = None,
) -> dict[str, str]:
    """Return deterministic output-argument bindings from requested paths.

    Args:
        tool_name: Wrapper tool name whose outputs should be bound.
        output_paths: Explicit output roots or artifact paths requested by the
            user or contract.
        registry: Optional registry override.

    Returns:
        Mapping of wrapper output-argument names to concrete requested paths.
        When the request only supplies an output directory/root, canonical
        file outputs are expanded under that directory for tools that declare
        canonical filenames.
    """

    registry = registry or default_tool_registry()
    normalized_paths = [str(path).strip() for path in output_paths if str(path).strip()]
    if not normalized_paths:
        return {}

    output_keys = set(registry.execution_output_parameters_for(tool_name))
    output_keys.update(registry.output_argument_keys_for(tool_name))
    if not output_keys:
        return {}

    directory_outputs = [
        path for path in normalized_paths if not is_file_like_output_path(path)
    ]
    file_outputs = [
        path for path in normalized_paths if is_file_like_output_path(path)
    ]
    primary_output = str(registry.primary_output_parameter_for(tool_name) or "").strip()
    canonical_map = registry.canonical_output_filenames_for(tool_name)

    bindings: dict[str, str] = {}
    if "output_dir" in output_keys:
        output_dir = _requested_output_dir(directory_outputs, file_outputs)
        if output_dir:
            bindings["output_dir"] = output_dir

    for param_name in sorted(output_keys):
        canonical_name = canonical_map.get(param_name)
        if not isinstance(canonical_name, str) or not canonical_name.strip():
            continue
        matched_output = _matching_file_output(file_outputs, canonical_name)
        if matched_output:
            bindings[param_name] = matched_output
            continue
        if directory_outputs:
            bindings[param_name] = str(
                Path(directory_outputs[0]).expanduser() / canonical_name
            )

    if primary_output and primary_output in output_keys and primary_output not in bindings:
        if len(file_outputs) == 1:
            bindings[primary_output] = file_outputs[0]

    return bindings


def _requested_output_dir(directory_outputs: list[str], file_outputs: list[str]) -> str:
    """Return one requested output directory from explicit paths."""

    if directory_outputs:
        return directory_outputs[0]
    return _shared_parent_directory(file_outputs)


def _matching_file_output(file_outputs: list[str], canonical_name: str) -> str:
    """Return the requested file path matching one canonical filename."""

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
