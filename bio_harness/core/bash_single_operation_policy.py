"""Atomic shell-step validation for planner-authored ``bash_run`` steps.

This module enforces the harness invariant that one ``bash_run`` step should
contain exactly one visible shell operation. The goal is to keep planner output
transparent, easy to validate, and easy to repair without hidden compound
execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import shlex
import shutil
import subprocess

from bio_harness.core.shell_parse import (
    split_shell_chain_segments,
    split_shell_pipeline_segments,
    split_shell_segments,
    strip_shell_comments,
    strip_shell_heredoc_body,
)

_CONTROL_FLOW_KEYWORDS = frozenset({"if", "for", "while", "until", "case", "select"})
_NESTED_SHELL_COMMANDS = frozenset({"bash", "sh", "zsh"})


@dataclass(frozen=True)
class OperationCheck:
    """Result of validating one planner-authored shell command.

    Attributes:
        passed: Whether the command satisfies the atomic-step policy.
        operation_count: Best-effort count of top-level operations seen in the
            command.
        violations: Stable violation tokens describing why the command failed.
        normalized_command: Whitespace-collapsed command text for logging and
            comparison.
    """

    passed: bool
    operation_count: int
    violations: list[str]
    normalized_command: str


@dataclass(frozen=True)
class _StructureScan:
    """Top-level shell structure markers for one command string."""

    has_and: bool
    has_or: bool
    has_pipe: bool
    has_semicolon: bool
    logical_line_count: int


def _collapse_whitespace(text: str) -> str:
    """Return one command with internal whitespace collapsed."""

    return " ".join(str(text or "").split())


def _scan_top_level_structure(command: str) -> _StructureScan:
    """Inspect top-level shell control markers without descending into quotes."""

    text = str(command or "")
    in_single = False
    in_double = False
    escaped = False
    subshell_depth = 0
    has_and = False
    has_or = False
    has_pipe = False
    has_semicolon = False
    logical_line_count = 0
    current_line_has_text = False
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if escaped:
            if not ch.isspace():
                current_line_has_text = True
            escaped = False
            i += 1
            continue

        if ch == "\\" and not in_single:
            if i + 1 < n and text[i + 1] == "\n":
                i += 2
                continue
            if i + 2 < n and text[i + 1] == "\r" and text[i + 2] == "\n":
                i += 3
                continue
            escaped = True
            current_line_has_text = True
            i += 1
            continue

        if in_single:
            if ch == "'":
                in_single = False
            if not ch.isspace():
                current_line_has_text = True
            i += 1
            continue

        if in_double:
            if ch == '"':
                in_double = False
            if not ch.isspace():
                current_line_has_text = True
            i += 1
            continue

        if text.startswith("$(", i):
            subshell_depth += 1
            current_line_has_text = True
            i += 2
            continue

        if subshell_depth > 0:
            if ch == "(":
                subshell_depth += 1
            elif ch == ")":
                subshell_depth = max(0, subshell_depth - 1)
            if not ch.isspace():
                current_line_has_text = True
            i += 1
            continue

        if ch == "'":
            in_single = True
            current_line_has_text = True
            i += 1
            continue

        if ch == '"':
            in_double = True
            current_line_has_text = True
            i += 1
            continue

        if text.startswith("&&", i):
            has_and = True
            i += 2
            continue

        if text.startswith("||", i):
            has_or = True
            i += 2
            continue

        if ch == "|":
            has_pipe = True
            i += 1
            continue

        if ch == ";":
            has_semicolon = True
            i += 1
            continue

        if ch == "\n":
            if current_line_has_text:
                logical_line_count += 1
            current_line_has_text = False
            i += 1
            continue

        if ch == "\r":
            i += 1
            continue

        if not ch.isspace():
            current_line_has_text = True
        i += 1

    if current_line_has_text:
        logical_line_count += 1

    return _StructureScan(
        has_and=has_and,
        has_or=has_or,
        has_pipe=has_pipe,
        has_semicolon=has_semicolon,
        logical_line_count=logical_line_count,
    )


def _bash_parse_error(command: str) -> str:
    """Return one shell parse error string, or an empty string when valid."""

    if not shutil.which("bash"):
        return ""
    try:
        proc = subprocess.run(
            ["bash", "-n", "-c", command],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except OSError as exc:
        return str(exc)
    except subprocess.TimeoutExpired:
        return ""
    if proc.returncode == 0:
        return ""
    return (proc.stderr or proc.stdout or "").strip()


def _operation_count(command: str, scan: _StructureScan) -> int:
    """Return a best-effort count of top-level operations."""

    if scan.has_and or scan.has_or or scan.has_semicolon:
        return max(len(split_shell_chain_segments(command)), scan.logical_line_count)
    if scan.has_pipe:
        return max(len(split_shell_pipeline_segments(command)), scan.logical_line_count)
    return scan.logical_line_count


def _structural_violations(command: str, scan: _StructureScan) -> list[str]:
    """Return stable structural violation tokens for one command."""

    violations: list[str] = []
    if "<<" in command:
        violations.append("heredoc_not_allowed")
    if scan.has_and:
        violations.append("compound_and")
    if scan.has_or:
        violations.append("compound_or")
    if scan.has_pipe:
        violations.append("compound_pipe")
    if scan.has_semicolon:
        violations.append("compound_semicolon")
    if scan.logical_line_count > 1:
        violations.append("missing_command_separator")
    return violations


def _semantic_shell_violations(command: str) -> list[str]:
    """Return semantic shell violations that survive structural checks."""

    violations: list[str] = []
    for segment in split_shell_segments(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError as exc:
            return [f"parse_error:{exc}"]
        if not tokens:
            continue
        first = str(tokens[0] or "").strip().lower()
        if first in _CONTROL_FLOW_KEYWORDS:
            violations.append(f"control_flow_block:{first}")
        if first in _NESTED_SHELL_COMMANDS and "-c" in tokens[1:]:
            violations.append("nested_shell_command")
        if first == "set" and any(token.startswith("-") for token in tokens[1:]):
            violations.append("shell_pragma_without_operation")
    return violations


def check_single_operation(command: str) -> OperationCheck:
    """Return whether one planner-authored shell command is atomic.

    Args:
        command: Raw ``bash_run.command`` text.

    Returns:
        An :class:`OperationCheck` describing whether the command satisfies the
        atomic-step policy.
    """

    stripped_comments = strip_shell_comments(str(command or ""))
    normalized_command = _collapse_whitespace(stripped_comments)
    command_without_heredoc = strip_shell_heredoc_body(stripped_comments).strip()
    if not command_without_heredoc:
        return OperationCheck(
            passed=False,
            operation_count=0,
            violations=["empty_command"],
            normalized_command=normalized_command,
        )

    try:
        shlex.split(command_without_heredoc, posix=True)
    except ValueError as exc:
        return OperationCheck(
            passed=False,
            operation_count=0,
            violations=[f"parse_error:{exc}"],
            normalized_command=normalized_command,
        )

    parse_error = _bash_parse_error(command_without_heredoc)
    if parse_error:
        return OperationCheck(
            passed=False,
            operation_count=0,
            violations=[f"parse_error:{parse_error}"],
            normalized_command=normalized_command,
        )

    scan = _scan_top_level_structure(command_without_heredoc)
    operation_count = _operation_count(command_without_heredoc, scan)
    violations = _structural_violations(command_without_heredoc, scan)
    violations.extend(_semantic_shell_violations(command_without_heredoc))
    deduped = list(dict.fromkeys(violations))
    return OperationCheck(
        passed=operation_count == 1 and not deduped,
        operation_count=operation_count,
        violations=deduped,
        normalized_command=normalized_command,
    )

