"""Safe placeholder extraction and resolution for ``bash_run`` commands.

This module resolves a narrow set of template-style placeholder syntaxes used
by planner-authored shell commands. Resolution is intentionally conservative:
it never executes shell, never rewrites free-form shell variables like
``$name``, and never touches placeholders that appear inside single-quoted
literals, comments, or heredoc bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


_ANGLE_PLACEHOLDER_RE = re.compile(r"<([A-Za-z_][A-Za-z0-9_]*)>")
_BRACED_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DOUBLE_BRACE_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
_PLACEHOLDER_TOKEN_RES = (
    _ANGLE_PLACEHOLDER_RE,
    _BRACED_PLACEHOLDER_RE,
    _DOUBLE_BRACE_PLACEHOLDER_RE,
)
_COMMENT_START_RE = re.compile(r"(^|\s)#")
_HEREDOC_START_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


@dataclass(frozen=True)
class PlaceholderToken:
    """One placeholder occurrence discovered in a shell command.

    Attributes:
        raw: Verbatim placeholder text such as ``<reference_fasta>``.
        name: Placeholder name without delimiters.
        start: Inclusive byte offset within the original command string.
        end: Exclusive byte offset within the original command string.
    """

    raw: str
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class PlaceholderResolutionResult:
    """Resolved placeholder state for one shell command.

    Attributes:
        resolved_command: Command after safe placeholder substitutions.
        resolutions: Ordered records describing successful substitutions.
        unresolved: Ordered unique placeholder names that could not be
            resolved deterministically.
    """

    resolved_command: str
    resolutions: list[dict[str, str]]
    unresolved: list[str]


def _iter_non_resolvable_ranges(command: str) -> list[tuple[int, int]]:
    """Return command spans where placeholders must not be resolved."""

    text = str(command or "")
    ranges: list[tuple[int, int]] = []
    in_single = False
    in_double = False
    escaped = False
    single_start = -1
    comment_start = -1
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if comment_start >= 0:
            if ch == "\n":
                ranges.append((comment_start, i))
                comment_start = -1
            i += 1
            continue

        if escaped:
            escaped = False
            i += 1
            continue

        if ch == "\\" and not in_single:
            escaped = True
            i += 1
            continue

        if in_single:
            if ch == "'":
                if single_start >= 0:
                    ranges.append((single_start, i))
                in_single = False
                single_start = -1
            i += 1
            continue

        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            single_start = i + 1
            i += 1
            continue

        if ch == '"':
            in_double = True
            i += 1
            continue

        if ch == "#" and (i == 0 or text[i - 1].isspace()):
            comment_start = i
            i += 1
            continue

        i += 1

    if comment_start >= 0:
        ranges.append((comment_start, n))
    if in_single and single_start >= 0:
        ranges.append((single_start, n))

    heredoc_offset = 0
    while heredoc_offset < n:
        match = _HEREDOC_START_RE.search(text, heredoc_offset)
        if match is None:
            break
        if _in_ranges(match.start(), ranges):
            heredoc_offset = match.end()
            continue
        delimiter = str(match.group(2) or "").strip()
        line_end = text.find("\n", match.end())
        if line_end < 0:
            break
        allow_tabs = text[match.start() : match.end()].startswith("<<-")
        body_start = line_end + 1
        body_end = body_start
        while body_end < n:
            next_line_end = text.find("\n", body_end)
            if next_line_end < 0:
                next_line_end = n
            body_line = text[body_end:next_line_end]
            compare = body_line.lstrip("\t") if allow_tabs else body_line
            if compare == delimiter:
                break
            body_end = next_line_end + 1 if next_line_end < n else n
        if body_start < body_end:
            ranges.append((body_start, body_end))
        heredoc_offset = max(body_end, match.end())

    return ranges


def _in_ranges(offset: int, ranges: Sequence[tuple[int, int]]) -> bool:
    """Return whether one offset falls inside any blocked range."""

    return any(start <= offset < end for start, end in ranges)


def extract_placeholder_tokens(command: str) -> list[PlaceholderToken]:
    """Extract supported placeholder tokens from one shell command.

    Args:
        command: Raw shell command text.

    Returns:
        Ordered placeholder tokens that are safe candidates for resolution.
    """

    text = str(command or "")
    if not text:
        return []

    blocked_ranges = _iter_non_resolvable_ranges(text)
    matches: list[PlaceholderToken] = []
    for token_re in _PLACEHOLDER_TOKEN_RES:
        for match in token_re.finditer(text):
            start = match.start()
            if _in_ranges(start, blocked_ranges):
                continue
            matches.append(
                PlaceholderToken(
                    raw=str(match.group(0) or ""),
                    name=str(match.group(1) or ""),
                    start=start,
                    end=match.end(),
                )
            )
    return sorted(matches, key=lambda item: (item.start, item.end))


def _stringify_resolution_value(value: Any) -> str | None:
    """Return one deterministic string replacement value when possible."""

    if value is None:
        return None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return None


def _resolve_from_prior_arguments(
    name: str,
    *,
    prior_step_arguments: Sequence[Mapping[str, Any]],
) -> tuple[str | None, str | None]:
    """Resolve one placeholder from prior step arguments."""

    candidates: list[str] = []
    for arguments in prior_step_arguments:
        if not isinstance(arguments, Mapping) or name not in arguments:
            continue
        rendered = _stringify_resolution_value(arguments.get(name))
        if rendered is None or not str(rendered).strip():
            continue
        candidates.append(str(rendered).strip())
    unique = list(dict.fromkeys(candidates))
    if not unique:
        return None, None
    if len(unique) > 1:
        return None, "ambiguous_prior_step_arguments"
    return unique[0], "prior_step_arguments"


def _resolve_from_path_graph(name: str, *, path_graph: Any = None) -> tuple[str | None, str | None]:
    """Resolve one placeholder from a mapping-like path graph object."""

    if path_graph is None:
        return None, None

    raw: Any = None
    if isinstance(path_graph, Mapping):
        raw = path_graph.get(name)
    elif hasattr(path_graph, "get"):
        try:
            raw = path_graph.get(name)
        except Exception:
            raw = None
    if raw is None:
        return None, None
    if isinstance(raw, (list, tuple, set)):
        unique = [str(item).strip() for item in raw if str(item).strip()]
        unique = list(dict.fromkeys(unique))
        if len(unique) != 1:
            return None, "ambiguous_path_graph"
        return unique[0], "path_graph"
    rendered = _stringify_resolution_value(raw)
    if rendered is None or not str(rendered).strip():
        return None, None
    return str(rendered).strip(), "path_graph"


def _resolve_from_defaults(
    name: str,
    *,
    wrapper_parameter_defaults: Mapping[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve one placeholder from wrapper-style defaults."""

    defaults = wrapper_parameter_defaults if isinstance(wrapper_parameter_defaults, Mapping) else {}
    if name not in defaults:
        return None, None
    rendered = _stringify_resolution_value(defaults.get(name))
    if rendered is None or not str(rendered).strip():
        return None, None
    return str(rendered).strip(), "wrapper_parameter_defaults"


def resolve_bash_placeholders(
    command: str,
    *,
    prior_step_arguments: Sequence[Mapping[str, Any]],
    path_graph: Any = None,
    wrapper_parameter_defaults: Mapping[str, Any] | None = None,
    selected_dir: str | None = None,
) -> PlaceholderResolutionResult:
    """Resolve supported placeholders in one shell command.

    Args:
        command: Raw shell command text.
        prior_step_arguments: Ordered argument mappings from prior plan steps.
        path_graph: Optional mapping-like lookup source for placeholder names.
        wrapper_parameter_defaults: Optional fallback mapping for default values.
        selected_dir: Optional selected-dir path. Present for future callers and
            parity with other path-normalization helpers.

    Returns:
        Structured resolution result containing the rewritten command, the
        successful substitutions, and unresolved placeholder names.
    """

    del selected_dir  # The current resolver does not rewrite relative values.

    text = str(command or "")
    tokens = extract_placeholder_tokens(text)
    if not tokens:
        return PlaceholderResolutionResult(
            resolved_command=text,
            resolutions=[],
            unresolved=[],
        )

    rendered_parts: list[str] = []
    resolutions: list[dict[str, str]] = []
    unresolved: list[str] = []
    cursor = 0
    for token in tokens:
        value, source = _resolve_from_prior_arguments(
            token.name,
            prior_step_arguments=prior_step_arguments,
        )
        if value is None and source is None:
            value, source = _resolve_from_path_graph(token.name, path_graph=path_graph)
        if value is None and source is None:
            value, source = _resolve_from_defaults(
                token.name,
                wrapper_parameter_defaults=wrapper_parameter_defaults,
            )
        rendered_parts.append(text[cursor:token.start])
        if value is None:
            rendered_parts.append(token.raw)
            if token.name not in unresolved:
                unresolved.append(token.name)
        else:
            rendered_parts.append(value)
            resolutions.append(
                {
                    "token": token.raw,
                    "value": value,
                    "source": str(source or ""),
                }
            )
        cursor = token.end
    rendered_parts.append(text[cursor:])
    return PlaceholderResolutionResult(
        resolved_command="".join(rendered_parts),
        resolutions=resolutions,
        unresolved=unresolved,
    )


__all__ = [
    "PlaceholderResolutionResult",
    "PlaceholderToken",
    "extract_placeholder_tokens",
    "resolve_bash_placeholders",
]
