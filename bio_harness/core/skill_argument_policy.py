"""Deterministic allowlist checks for skill arguments.

This module keeps the planner-to-wrapper boundary honest by treating skill
frontmatter as the source of truth for model-supplied arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.parameter_ownership import is_harness_managed_parameter
from bio_harness.core.wrapper_contracts import normalize_wrapper_arguments


DISALLOWED_NON_BASH_ARGUMENTS = frozenset({"command"})
_BASH_RUN_DIRECTORY_ALIASES = ("working_directory", "working_dir", "cwd")


def documented_parameter_keys(skill_metadata: Mapping[str, Any] | None) -> set[str]:
    """Return documented parameter names for a skill.

    Args:
        skill_metadata: Frontmatter metadata for a skill definition.

    Returns:
        The set of documented parameter keys. Missing or malformed metadata
        yields an empty set.
    """

    if not isinstance(skill_metadata, Mapping):
        return set()
    parameters = skill_metadata.get("parameters", {})
    if not isinstance(parameters, Mapping):
        return set()
    return {
        str(key).strip()
        for key in parameters.keys()
        if str(key).strip()
    }


def harness_managed_parameter_keys(skill_metadata: Mapping[str, Any] | None) -> set[str]:
    """Return planner-sanitized harness-managed keys for one skill.

    Args:
        skill_metadata: Frontmatter metadata for a skill definition.

    Returns:
        The set of parameters that should be supplied by runtime glue instead
        of the planner.
    """

    if not isinstance(skill_metadata, Mapping):
        return set()
    parameters = skill_metadata.get("parameters", {})
    if not isinstance(parameters, Mapping):
        return set()
    managed: set[str] = set()
    for raw_name, raw_spec in parameters.items():
        name = str(raw_name).strip()
        if not name:
            continue
        if is_harness_managed_parameter(raw_spec if isinstance(raw_spec, Mapping) else None):
            managed.add(name)
    return managed


def sanitize_harness_managed_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    skill_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Drop planner-supplied harness-managed parameters from one step.

    Args:
        tool_name: Name of the requested skill.
        arguments: Planner-produced argument payload for the skill.
        skill_metadata: Frontmatter metadata for the skill.

    Returns:
        A copy of ``arguments`` with harness-managed keys removed. Invalid
        payloads yield an empty mapping.
    """

    del tool_name  # The signature stays tool-centric even though metadata drives the policy.
    if not isinstance(arguments, Mapping):
        return {}
    managed = harness_managed_parameter_keys(skill_metadata)
    return {
        str(key): value
        for key, value in arguments.items()
        if str(key).strip() and str(key).strip() not in managed
    }


def normalize_non_bash_run_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Normalize wrapper arguments before validation and command rendering.

    Args:
        tool_name: Name of the requested skill.
        arguments: Planner-produced argument payload for the skill.
        cwd: Optional working directory used to canonicalize path-typed
            wrapper arguments.

    Returns:
        A normalized wrapper argument mapping. Multi-input wrapper arguments are
        canonicalized into string lists so validation and runtime rendering use
        the same structure. Path-typed arguments are canonicalized against
        ``cwd`` when one is available.
    """

    return normalize_wrapper_arguments(tool_name, arguments, cwd=cwd)


def _normalize_bash_run_working_directory(
    raw_value: Any,
    *,
    cwd: str | None = None,
) -> str:
    """Return a canonical bash-run working directory.

    Args:
        raw_value: Planner-supplied working-directory payload.
        cwd: Optional base working directory for resolving relative paths.

    Returns:
        An absolute working-directory path, or an empty string when no working
        directory was supplied.
    """

    text = str(raw_value or "").strip()
    if not text:
        return ""
    candidate = Path(text).expanduser()
    if not candidate.is_absolute() and cwd:
        candidate = Path(cwd).expanduser().resolve(strict=False) / candidate
    return str(candidate.resolve(strict=False))


def normalize_bash_run_arguments(
    arguments: Mapping[str, Any],
    *,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Normalize ``bash_run`` arguments before validation and execution.

    Args:
        arguments: Planner-produced argument payload for ``bash_run``.
        cwd: Optional working directory used to canonicalize relative
            ``working_directory`` values.

    Returns:
        A normalized argument mapping containing ``command`` plus an optional
        canonical ``working_directory``. Common planner aliases like
        ``working_dir`` and ``cwd`` are folded into ``working_directory``.
    """

    if not isinstance(arguments, Mapping):
        return {}
    command = str(arguments.get("command", "") or arguments.get("script", "") or "").strip()
    normalized: dict[str, Any] = {}
    if command:
        normalized["command"] = command
    raw_working_directory = next(
        (
            arguments.get(alias)
            for alias in _BASH_RUN_DIRECTORY_ALIASES
            if str(arguments.get(alias, "") or "").strip()
        ),
        "",
    )
    working_directory = _normalize_bash_run_working_directory(
        raw_working_directory,
        cwd=cwd,
    )
    if working_directory:
        normalized["working_directory"] = working_directory
    return normalized


def normalize_execution_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Normalize planner arguments for execution-facing tool calls.

    Args:
        tool_name: Name of the requested skill.
        arguments: Planner-produced argument payload for the skill.
        cwd: Optional working directory used to canonicalize path-like
            arguments.

    Returns:
        A normalized argument mapping suitable for validation, command
        rendering, and runtime execution.
    """

    if str(tool_name or "").strip() == "bash_run":
        return normalize_bash_run_arguments(arguments, cwd=cwd)
    return normalize_non_bash_run_arguments(tool_name, arguments, cwd=cwd)


def resolve_execution_working_directory(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    cwd: str | None = None,
) -> str | None:
    """Return the effective working directory for one execution step.

    Args:
        tool_name: Name of the requested skill.
        arguments: Normalized execution argument payload.
        cwd: Default working directory for the step.

    Returns:
        The requested working directory for tools that support one, otherwise
        the provided default ``cwd``.
    """

    if str(tool_name or "").strip() == "bash_run":
        working_directory = str(arguments.get("working_directory", "") or "").strip()
        if working_directory:
            return working_directory
    return cwd


def validate_non_bash_run_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    skill_metadata: Mapping[str, Any] | None,
) -> list[str]:
    """Validate model-supplied arguments for a non-``bash_run`` skill.

    Args:
        tool_name: Name of the requested skill.
        arguments: Planner-produced argument payload for the skill.
        skill_metadata: Frontmatter metadata for the skill.

    Returns:
        A list of validation issue codes. An empty list means the payload is
        compatible with the documented wrapper interface.
    """

    if not isinstance(arguments, Mapping):
        return ["invalid_arguments_payload"]
    if not isinstance(skill_metadata, Mapping):
        return []

    allowed = documented_parameter_keys(skill_metadata)
    issues: list[str] = []

    for key in sorted(arguments.keys()):
        normalized = str(key).strip()
        if not normalized:
            issues.append("undocumented_argument:<empty>")
            continue
        if normalized in DISALLOWED_NON_BASH_ARGUMENTS:
            issues.append(f"disallowed_argument:{normalized}")
            continue
        if normalized not in allowed:
            issues.append(f"undocumented_argument:{normalized}")

    return issues
