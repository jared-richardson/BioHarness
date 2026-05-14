"""Safe shell binding resolution for deterministic plan validation.

This module resolves a narrow, benchmark-safe subset of shell variable usage so
the harness can normalize planner-emitted path aliases without executing shell.
It intentionally supports only plain ``NAME=value`` assignments and simple
``$NAME`` or ``${NAME}`` references.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from bio_harness.core.shell_parse import (
    is_shell_assignment,
    split_shell_segments,
    strip_shell_comments,
    strip_shell_heredoc_body,
)

_SIMPLE_BRACED_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SIMPLE_BARE_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_ANY_BRACED_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_ASSIGNMENT_PREFIX_COMMANDS = frozenset({"export", "local", "declare", "readonly", "typeset"})
_PATHLIKE_SUFFIXES = (
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


@dataclass(frozen=True)
class ShellTextResolution:
    """Represents deterministic shell-text resolution.

    Attributes:
        resolved_text: The resolved text when resolution succeeded, otherwise
            the original input.
        had_reference: Whether the input contained shell variable syntax.
        unresolved_names: Variable names that could not be resolved from the
            supplied binding map.
        unsupported: Whether the text contains unsupported shell expansion.
    """

    resolved_text: str
    had_reference: bool
    unresolved_names: tuple[str, ...]
    unsupported: bool


@dataclass(frozen=True)
class ShellSegmentAnalysis:
    """Represents one shell segment after safe alias analysis.

    Attributes:
        original_text: Raw segment text.
        resolved_text: Segment text with supported aliases expanded.
        bindings_after: Binding map after processing segment assignments.
        unresolved_names: Variable names referenced but not bound.
        unsupported_tokens: Raw tokens that used unsupported shell expansion.
    """

    original_text: str
    resolved_text: str
    bindings_after: Mapping[str, str]
    unresolved_names: tuple[str, ...]
    unsupported_tokens: tuple[str, ...]


def default_shell_path_bindings(selected_root: str | Path) -> dict[str, str]:
    """Return deterministic built-in shell path bindings for one run.

    Args:
        selected_root: Current selected output directory.

    Returns:
        A mutable mapping of stable shell aliases to concrete paths.
    """

    root = str(Path(selected_root).expanduser().resolve(strict=False))
    return {
        "OUTPUT_DIR": root,
        "SELECTED_DIR": root,
        "RESULTS_DIR": root,
    }


def resolve_shell_text(
    text: str,
    *,
    bindings: Mapping[str, str] | None,
) -> ShellTextResolution:
    """Resolve supported shell variables in one text token.

    Args:
        text: Raw token or argument text.
        bindings: Shell binding map visible at the current analysis point.

    Returns:
        Structured resolution status for the token.
    """

    raw = str(text or "")
    if not raw:
        return ShellTextResolution(
            resolved_text=raw,
            had_reference=False,
            unresolved_names=(),
            unsupported=False,
        )
    if _contains_unsupported_shell_expansion(raw):
        return ShellTextResolution(
            resolved_text=raw,
            had_reference=True,
            unresolved_names=(),
            unsupported=True,
        )

    current = raw
    had_reference = False
    unresolved_names: set[str] = set()
    available = dict(bindings or {})
    for _ in range(8):
        names = _referenced_shell_variable_names(current)
        if not names:
            break
        had_reference = True
        missing = [name for name in names if name not in available]
        if missing:
            unresolved_names.update(missing)
            return ShellTextResolution(
                resolved_text=raw,
                had_reference=True,
                unresolved_names=tuple(sorted(unresolved_names)),
                unsupported=False,
            )
        current = _replace_shell_variable_references(current, available)

    return ShellTextResolution(
        resolved_text=current,
        had_reference=had_reference,
        unresolved_names=(),
        unsupported=False,
    )


def analyze_shell_segments(
    command: str,
    *,
    bindings: Mapping[str, str] | None,
) -> list[ShellSegmentAnalysis]:
    """Analyze shell segments and propagate safe assignment bindings.

    Args:
        command: Raw shell command text.
        bindings: Initial binding map visible before the command runs.

    Returns:
        Ordered segment analyses with bindings updated in segment order.
    """

    current_bindings = dict(bindings or {})
    analyses: list[ShellSegmentAnalysis] = []
    cleaned = strip_shell_heredoc_body(strip_shell_comments(command))
    for segment in _split_analysis_segments(cleaned):
        text = str(segment or "").strip()
        if not text:
            continue
        try:
            tokens = shlex.split(text, posix=True)
        except ValueError:
            analyses.append(
                ShellSegmentAnalysis(
                    original_text=text,
                    resolved_text=text,
                    bindings_after=dict(current_bindings),
                    unresolved_names=(),
                    unsupported_tokens=(),
                )
            )
            continue

        updated_bindings = dict(current_bindings)
        resolved_tokens: list[str] = []
        unresolved_names: set[str] = set()
        unsupported_tokens: list[str] = []
        _apply_simple_for_loop_bindings(tokens, updated_bindings)
        allow_assignment = True
        for token in tokens:
            probe = str(token or "").strip()
            if not probe:
                resolved_tokens.append(token)
                continue
            if probe in _ASSIGNMENT_PREFIX_COMMANDS and allow_assignment:
                resolved_tokens.append(probe)
                continue
            if is_shell_assignment(probe) and allow_assignment:
                name, _, value = probe.partition("=")
                resolution = resolve_shell_text(value, bindings=updated_bindings)
                if resolution.unsupported:
                    unsupported_tokens.append(probe)
                    updated_bindings[name] = resolution.resolved_text
                    resolved_tokens.append(probe)
                    continue
                if resolution.unresolved_names:
                    unresolved_names.update(resolution.unresolved_names)
                    resolved_tokens.append(probe)
                    continue
                updated_bindings[name] = resolution.resolved_text
                resolved_tokens.append(f"{name}={resolution.resolved_text}")
                continue

            allow_assignment = False
            resolution = resolve_shell_text(probe, bindings=updated_bindings)
            if resolution.unsupported:
                unsupported_tokens.append(probe)
                resolved_tokens.append(probe)
                continue
            if resolution.unresolved_names:
                unresolved_names.update(resolution.unresolved_names)
                resolved_tokens.append(probe)
                continue
            resolved_tokens.append(resolution.resolved_text)

        analyses.append(
            ShellSegmentAnalysis(
                original_text=text,
                resolved_text=" ".join(shlex.quote(token) for token in resolved_tokens if str(token).strip()),
                bindings_after=dict(updated_bindings),
                unresolved_names=tuple(sorted(unresolved_names)),
                unsupported_tokens=tuple(unsupported_tokens),
            )
        )
        current_bindings = updated_bindings
    return analyses


def _split_analysis_segments(command: str) -> list[str]:
    """Split shell text into analysis segments, including top-level newlines."""

    segments: list[str] = []
    for segment in split_shell_segments(command):
        pieces = _split_top_level_newlines(segment)
        if pieces:
            segments.extend(pieces)
        elif str(segment or "").strip():
            segments.append(str(segment).strip())
    return segments


def _split_top_level_newlines(command: str) -> list[str]:
    """Split one shell segment on unquoted top-level newlines."""

    text = str(command or "")
    if not text.strip():
        return []
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    subshell_depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escaped:
            buf.append(ch)
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
            buf.append(ch)
            escaped = True
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue
        if text.startswith("$(", i):
            buf.append("$(")
            subshell_depth += 1
            i += 2
            continue
        if subshell_depth > 0:
            buf.append(ch)
            if ch == "(":
                subshell_depth += 1
            elif ch == ")":
                subshell_depth = max(0, subshell_depth - 1)
            i += 1
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue
        if ch in {"\n", "\r"}:
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
            if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 2
            else:
                i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _apply_simple_for_loop_bindings(tokens: list[str], bindings: dict[str, str]) -> None:
    """Populate deterministic bindings for simple ``for name in ...`` loops."""

    if len(tokens) < 4 or str(tokens[0] or "").strip() != "for":
        return
    loop_name = str(tokens[1] or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", loop_name):
        return
    if str(tokens[2] or "").strip() != "in":
        return
    values: list[str] = []
    for token in tokens[3:]:
        probe = str(token or "").strip()
        if probe in {"do", ";"}:
            break
        resolution = resolve_shell_text(probe, bindings=bindings)
        if resolution.unsupported or resolution.unresolved_names:
            continue
        values.append(resolution.resolved_text)
    if values:
        bindings[loop_name] = values[0]


def looks_like_pathlike_shell_text(text: str) -> bool:
    """Return whether one shell text value materially looks like a path.

    Args:
        text: Candidate shell token or resolved text.

    Returns:
        ``True`` when the text resembles a filesystem path.
    """

    raw = str(text or "").strip().strip("'\"")
    if not raw or raw.startswith("-") or " " in raw:
        return False
    if "/" in raw or raw.startswith(".") or raw.startswith("~"):
        return True
    return raw.lower().endswith(_PATHLIKE_SUFFIXES)


def has_shell_variable_reference(text: str) -> bool:
    """Return whether one text contains supported shell variable syntax.

    Args:
        text: Candidate token or argument text.

    Returns:
        ``True`` when the text references one shell variable.
    """

    raw = str(text or "")
    return bool(_SIMPLE_BRACED_VAR_RE.search(raw) or _SIMPLE_BARE_VAR_RE.search(raw))


def _contains_unsupported_shell_expansion(text: str) -> bool:
    """Return whether one text uses unsupported shell expansion.

    Args:
        text: Candidate shell token or argument text.

    Returns:
        ``True`` when the text uses shell features outside the supported
        deterministic subset.
    """

    raw = str(text or "")
    if "$(" in raw or "`" in raw:
        return True
    for body in _ANY_BRACED_VAR_RE.findall(raw):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", body):
            return True
    return False


def _referenced_shell_variable_names(text: str) -> list[str]:
    """Return unique supported variable names referenced by one text.

    Args:
        text: Candidate shell token or argument text.

    Returns:
        Stable ordered variable names referenced in the text.
    """

    raw = str(text or "")
    names: list[str] = []
    seen: set[str] = set()
    for match in _SIMPLE_BRACED_VAR_RE.finditer(raw):
        name = str(match.group(1) or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    for match in _SIMPLE_BARE_VAR_RE.finditer(raw):
        name = str(match.group(1) or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _replace_shell_variable_references(text: str, bindings: Mapping[str, str]) -> str:
    """Replace supported variable references in one text.

    Args:
        text: Candidate shell token or argument text.
        bindings: Binding map with already-resolved string values.

    Returns:
        Text with supported references replaced.
    """

    replaced = _SIMPLE_BRACED_VAR_RE.sub(
        lambda match: str(bindings.get(str(match.group(1) or "").strip(), match.group(0))),
        str(text or ""),
    )
    return _SIMPLE_BARE_VAR_RE.sub(
        lambda match: str(bindings.get(str(match.group(1) or "").strip(), match.group(0))),
        replaced,
    )


__all__ = [
    "ShellSegmentAnalysis",
    "ShellTextResolution",
    "analyze_shell_segments",
    "default_shell_path_bindings",
    "has_shell_variable_reference",
    "looks_like_pathlike_shell_text",
    "resolve_shell_text",
]
