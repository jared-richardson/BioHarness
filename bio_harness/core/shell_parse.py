from __future__ import annotations

import re
import shlex
from typing import Optional

_HEREDOC_MARKERS = ("<<", "<<-")


def _split_shell_top_level(
    command: str,
    *,
    split_pipe: bool,
    split_chain: bool,
) -> list[str]:
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

        if ch == "\\" and not in_single:
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

        if split_chain and text.startswith("&&", i):
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
            i += 2
            continue

        if split_chain and text.startswith("||", i):
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
            i += 2
            continue

        if split_chain and ch == ";":
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
            i += 1
            continue

        if split_pipe and ch == "|":
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def split_shell_chain_segments(command: str) -> list[str]:
    return _split_shell_top_level(command, split_pipe=False, split_chain=True)


def split_shell_pipeline_segments(command: str) -> list[str]:
    return _split_shell_top_level(command, split_pipe=True, split_chain=False)


def split_shell_segments(command: str) -> list[str]:
    return _split_shell_top_level(command, split_pipe=True, split_chain=True)


def strip_shell_heredoc_body(command: str) -> str:
    """Remove heredoc bodies so shell analysis stays on executable headers.

    Args:
        command: Raw shell command text.

    Returns:
        Command text truncated before the first heredoc marker when present.
    """

    cleaned = str(command or "").strip()
    for marker in _HEREDOC_MARKERS:
        if marker in cleaned:
            return cleaned.split(marker, 1)[0].strip()
    return cleaned


def strip_shell_comments(command: str) -> str:
    """Remove unquoted shell comments while preserving executable text."""

    text = str(command or "")
    if not text:
        return ""

    cleaned: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    in_comment = False
    line_start = True
    previous_was_space = True

    for ch in text:
        if in_comment:
            if ch == "\n":
                cleaned.append(ch)
                in_comment = False
                line_start = True
                previous_was_space = True
            continue

        if escaped:
            cleaned.append(ch)
            escaped = False
            line_start = False
            previous_was_space = ch.isspace()
            continue

        if ch == "\\" and not in_single:
            cleaned.append(ch)
            escaped = True
            line_start = False
            previous_was_space = False
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            cleaned.append(ch)
            line_start = False
            previous_was_space = False
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            cleaned.append(ch)
            line_start = False
            previous_was_space = False
            continue

        if ch == "#" and not in_single and not in_double and (line_start or previous_was_space):
            in_comment = True
            continue

        cleaned.append(ch)
        if ch == "\n":
            line_start = True
            previous_was_space = True
        else:
            line_start = False
            previous_was_space = ch.isspace()

    return "".join(cleaned)


def is_shell_assignment(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=(?:.*)$", token or ""))


def normalize_shell_command_token(token: str) -> str:
    """Normalize one shell token for command-dispatch inspection.

    Args:
        token: Raw shell token text.

    Returns:
        Token text with surrounding whitespace and leading line-continuation
        artifacts removed.
    """

    text = str(token or "")
    if not text:
        return ""
    normalized = text.strip()
    while normalized.startswith("\\"):
        candidate = normalized[1:].lstrip()
        if candidate == normalized:
            break
        normalized = candidate
    return normalized


def should_ignore_command_token(token: str) -> bool:
    if not token:
        return True
    shell_keywords = {
        "if", "then", "elif", "else", "fi", "do", "done", "while", "until", "for", "in",
        "case", "esac", "select", "{", "}", "(", ")", "[", "]", "[[", "]]", "((", "))",
        ":", "continue", "break", "read", "return", "export", "unset", "local", "shift",
        "eval", "set", "source", ".", "function", "exit", "command", "builtin",
        "test", "true", "false", "time", "coproc", "wait", "trap", "jobs", "fg", "bg",
        "alias", "unalias", "hash", "type", "declare", "typeset", "readonly",
        "let", "printf", "echo",
    }
    t = token.strip().strip("()")
    if not t:
        return True
    if t in shell_keywords:
        return True
    if t.startswith(("$(", "${", "`")):
        return True
    if t in {"|", "||", "&", "&&", ";"}:
        return True
    if is_shell_assignment(t):
        return True
    return False


def extract_segment_command(tokens: list[str]) -> Optional[str]:
    if not tokens:
        return None

    control_tokens = {"if", "then", "elif", "else", "fi", "do", "done", "for", "while", "until", "case", "esac"}
    if any(tok in control_tokens for tok in tokens):
        return None

    idx = 0
    while idx < len(tokens):
        tok = normalize_shell_command_token(tokens[idx]).strip("()")
        if not tok:
            idx += 1
            continue
        if is_shell_assignment(tok):
            idx += 1
            continue
        break
    if idx >= len(tokens):
        return None

    candidate = normalize_shell_command_token(tokens[idx]).strip("()")
    if candidate == "command":
        probe_flags = {"-v", "-V", "-p", "-P"}
        if any(t in probe_flags for t in tokens[idx + 1 :]):
            return None
        if idx + 1 < len(tokens):
            candidate = normalize_shell_command_token(tokens[idx + 1]).strip("()")
    elif candidate == "env":
        j = idx + 1
        while j < len(tokens):
            part = normalize_shell_command_token(tokens[j]).strip("()")
            if not part or part.startswith("-") or is_shell_assignment(part):
                j += 1
                continue
            candidate = part
            break
        else:
            return None

    if should_ignore_command_token(candidate):
        return None
    if not re.match(r"^[A-Za-z_][A-Za-z0-9._+-]*$", candidate):
        return None
    if candidate.isdigit():
        return None
    return candidate


def extract_tools(command: str) -> list[str]:
    tools: list[str] = []
    for seg in split_shell_segments(command):
        try:
            tokens = shlex.split(seg)
        except Exception:
            continue
        cmd = extract_segment_command(tokens)
        if cmd:
            tools.append(cmd)
    return sorted(set(tools))
