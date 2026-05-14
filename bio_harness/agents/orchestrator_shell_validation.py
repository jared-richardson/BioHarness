"""Deterministic shell-validation helpers for the orchestrator."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.shell_parse import (
    extract_segment_command,
    is_shell_assignment,
    should_ignore_command_token,
    split_shell_chain_segments,
    split_shell_pipeline_segments,
    split_shell_segments,
)

_INLINE_INTERPRETER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w.-])python3?\s+-c\b", flags=re.IGNORECASE), "python -c"),
    (re.compile(r"(?<![\w.-])python3?\s+-\s*<<", flags=re.IGNORECASE), "python - <<"),
    (re.compile(r"(?<![\w.-])rscript\s+-e\b", flags=re.IGNORECASE), "Rscript -e"),
    (re.compile(r"(?<![\w.-])perl\s+-e\b", flags=re.IGNORECASE), "perl -e"),
    (re.compile(r"(?<![\w.-])ruby\s+-e\b", flags=re.IGNORECASE), "ruby -e"),
    (re.compile(r"(?<![\w.-])node\s+-e\b", flags=re.IGNORECASE), "node -e"),
)


def split_shell_segments_for_validation(command: str) -> list[str]:
    return split_shell_segments(command)


def is_shell_assignment_token(token: str) -> bool:
    return is_shell_assignment(token)


def should_ignore_command_token_for_validation(token: str) -> bool:
    return should_ignore_command_token(token)


def extract_segment_command_for_validation(tokens: list[str]) -> str | None:
    return extract_segment_command(tokens)


def extract_step_requirements(command: str, cwd: str | None) -> dict[str, Any]:
    base = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    tools: list[str] = []
    input_paths: list[Path] = []
    maybe_nonempty: list[Path] = []
    gtf_paths: list[Path] = []
    fasta_paths: list[Path] = []

    segments = split_shell_segments(command)
    for seg in segments:
        try:
            tokens = shlex.split(seg)
        except Exception:
            continue
        if not tokens:
            continue

        cmd = extract_segment_command(tokens)
        if cmd:
            tools.append(cmd)

        def _to_path(raw: str) -> Path:
            path = Path(raw).expanduser()
            return path if path.is_absolute() else (base / path).resolve()

        for idx, token in enumerate(tokens):
            if token in {"--gtf", "--sjdbGTFfile"} and idx + 1 < len(tokens):
                path = _to_path(tokens[idx + 1])
                input_paths.append(path)
                gtf_paths.append(path)
            elif token in {"--genomeFastaFiles", "--fa", "--fasta"} and idx + 1 < len(tokens):
                path = _to_path(tokens[idx + 1])
                input_paths.append(path)
                fasta_paths.append(path)
            elif token in {"--b1", "--b2"} and idx + 1 < len(tokens):
                path = _to_path(tokens[idx + 1])
                input_paths.append(path)
                maybe_nonempty.append(path)

        seg_for_redir = seg
        heredoc_start = re.search(r"<<-?\s*['\"]?\w+['\"]?", seg_for_redir)
        if heredoc_start:
            seg_for_redir = seg_for_redir[: heredoc_start.start()]
        seg_for_redir = re.sub(r"'[^']*'", " ", seg_for_redir)
        seg_for_redir = re.sub(r'"[^"]*"', " ", seg_for_redir)
        in_redir = re.search(r"<\s*([^\s]+)", seg_for_redir)
        if in_redir:
            raw_redir = in_redir.group(1).strip("'\"")
            if raw_redir not in ("-",) and not raw_redir.startswith("-"):
                path = _to_path(raw_redir)
                input_paths.append(path)
                maybe_nonempty.append(path)

    return {
        "tools": sorted(set([tool for tool in tools if tool])),
        "input_paths": sorted(set(input_paths)),
        "must_be_nonempty": sorted(set(maybe_nonempty)),
        "gtf_paths": sorted(set(gtf_paths)),
        "fasta_paths": sorted(set(fasta_paths)),
    }


def find_stdin_blocking_commands(command: str) -> list[str]:
    offenders: list[str] = []
    for segment in split_shell_chain_segments(command):
        seg = segment.strip()
        if not seg:
            continue
        pipeline_parts = split_shell_pipeline_segments(seg)
        for idx, part in enumerate(pipeline_parts):
            try:
                tokens = shlex.split(part)
            except Exception:
                continue
            if not tokens:
                continue
            cmd = extract_segment_command(tokens)
            if cmd not in {"head", "tail"}:
                continue

            has_stdin_input = idx > 0 or bool(re.search(r"<\s*[^\s]+", part))
            expects_value = False
            has_file_arg = False
            for token in tokens[1:]:
                if expects_value:
                    expects_value = False
                    continue
                if token in {"-n", "-c", "--lines", "--bytes"}:
                    expects_value = True
                    continue
                if token.startswith("--lines=") or token.startswith("--bytes="):
                    continue
                if token.startswith("-"):
                    continue
                has_file_arg = True
                break

            if (not has_stdin_input) and (not has_file_arg):
                offenders.append(cmd)
    return sorted(set(offenders))


def find_disallowed_git_commands(command: str) -> list[str]:
    offenders: list[str] = []
    for match in re.finditer(r"(?<![\w.-])git\s+([a-zA-Z][\w-]*)", command):
        offenders.append(match.group(1).lower())
    for segment in split_shell_segments(command):
        seg = segment.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg)
        except Exception:
            continue
        if not tokens:
            continue
        cmd = extract_segment_command(tokens)
        if cmd != "git":
            continue
        subcmd = ""
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            subcmd = token
            break
        offenders.append((subcmd or "git").lower())
    return sorted(set(offenders))


def find_inline_interpreter_commands(command: str) -> list[str]:
    """Return inline interpreter forms that should not run via ``bash_run``."""

    offenders: list[str] = []
    for pattern, label in _INLINE_INTERPRETER_PATTERNS:
        if pattern.search(command):
            offenders.append(label)
    return sorted(set(offenders))
