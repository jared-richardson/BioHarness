"""Helpers for enforcing repository coding standards on staged Python files.

The guard is intentionally diff-aware. It enforces docstrings and file-size
limits for new or newly introduced public surfaces without forcing unrelated
legacy violations in untouched regions to be fixed during every commit.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_HUNK_HEADER_RE = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@",
    re.MULTILINE,
)
_SCRIPT_LINE_LIMIT = 500
_MODULE_LINE_LIMIT = 350


@dataclass(frozen=True, slots=True)
class StandardsViolation:
    """One standards violation discovered by the staged-file guard."""

    path: str
    message: str
    line: int | None = None


def parse_added_line_ranges(diff_text: str) -> list[tuple[int, int]]:
    """Parse added-line ranges from a unified diff.

    Args:
        diff_text: Unified diff text, typically produced with `--unified=0`.

    Returns:
        A sorted list of inclusive `(start, end)` line ranges in the new file.
    """
    ranges: list[tuple[int, int]] = []
    for match in _HUNK_HEADER_RE.finditer(diff_text):
        start = int(match.group("start"))
        count_text = match.group("count")
        count = int(count_text) if count_text else 1
        if count <= 0:
            continue
        ranges.append((start, start + count - 1))
    return ranges


def line_is_in_ranges(line_number: int, ranges: Iterable[tuple[int, int]]) -> bool:
    """Return whether a line number falls within any inclusive added range."""
    return any(start <= line_number <= end for start, end in ranges)


def public_docstring_violations(
    path: Path,
    source_text: str,
    *,
    added_ranges: list[tuple[int, int]],
    is_new_file: bool,
) -> list[StandardsViolation]:
    """Check docstring requirements for newly introduced public surfaces.

    Args:
        path: Python source path being checked.
        source_text: Current working-tree contents of the file.
        added_ranges: Added-line ranges from the staged diff.
        is_new_file: Whether the staged file is newly added.

    Returns:
        Standards violations for missing module or public definition docstrings.
    """
    try:
        module = ast.parse(source_text)
    except SyntaxError:
        return []

    violations: list[StandardsViolation] = []
    if is_new_file and not ast.get_docstring(module):
        violations.append(
            StandardsViolation(
                path=str(path),
                line=1,
                message="New Python modules must include a module docstring.",
            )
        )

    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if str(getattr(node, "name", "") or "").startswith("_"):
            continue
        if not line_is_in_ranges(int(node.lineno), added_ranges):
            continue
        if ast.get_docstring(node):
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        violations.append(
            StandardsViolation(
                path=str(path),
                line=int(node.lineno),
                message=f"New public {kind} `{node.name}` must include a docstring.",
            )
        )
    return violations


def line_count_violation(
    path: Path,
    *,
    current_line_count: int,
    previous_line_count: int | None,
) -> StandardsViolation | None:
    """Check the repo's file-size guard for Python modules and scripts.

    Existing oversized files are allowed to shrink, but they may not grow
    further. New files must start under the limit for their location.

    Args:
        path: Python source path being checked.
        current_line_count: Current working-tree line count.
        previous_line_count: Line count from `HEAD`, or `None` for a new file.

    Returns:
        A standards violation when the file exceeds the allowed growth policy,
        otherwise `None`.
    """
    limit = _SCRIPT_LINE_LIMIT if path.parts and path.parts[0] == "scripts" else _MODULE_LINE_LIMIT
    if previous_line_count is None:
        if current_line_count > limit:
            return StandardsViolation(
                path=str(path),
                message=(
                    f"New Python file is too large ({current_line_count} lines). "
                    f"Keep new files at or below {limit} lines."
                ),
            )
        return None

    if current_line_count > previous_line_count and current_line_count > limit:
        return StandardsViolation(
            path=str(path),
            message=(
                f"Python file grew to {current_line_count} lines, above the {limit}-line guard. "
                "Split responsibilities before adding more logic."
            ),
        )
    return None


__all__ = [
    "StandardsViolation",
    "line_count_violation",
    "line_is_in_ranges",
    "parse_added_line_ranges",
    "public_docstring_violations",
]
