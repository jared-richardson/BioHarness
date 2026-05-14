"""Shared shell-command output hint extraction helpers.

This module centralizes the deterministic path-extraction rules used by the
artifact validator, path repair, and workflow template compiler. Keeping the
shell parsing policy in one place reduces drift between benchmark-critical
subsystems that all need to understand the same helper-backed bash commands.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from bio_harness.core.shell_parse import (
    split_shell_segments,
    strip_shell_comments,
    strip_shell_heredoc_body,
)

_OUTPUT_FLAG_RE = re.compile(r"^--(?:outdir|output(?:[_-][a-z0-9][a-z0-9_-]*)?)$")
_REDIRECT_TOKENS = {">", ">>", "1>", "1>>", "2>", "2>>"}
_OUTPUT_SHORT_FLAGS = frozenset({"-o"})
_COMMAND_SPLIT_TOKENS = {"&&", "||", ";", "|"}
_COMMAND_OUTPUT_ROOT_FLAGS: dict[tuple[str, ...], frozenset[str]] = {
    ("bcftools", "isec"): frozenset({"-p"}),
}
_BCFTOOLS_ISEC_PREFIX_FLAGS = frozenset({"-p", "--prefix"})
_BCFTOOLS_ISEC_WRITE_FLAGS = frozenset({"-w", "--write"})
_BCFTOOLS_ISEC_OUTPUT_TYPE_FLAGS = frozenset({"-O", "--output-type"})
_POSITIONAL_OUTPUT_LAST_ARG_SCRIPTS = frozenset(
    {
        "normalize_gff_for_featurecounts.py",
        "gff3_to_gtf.py",
    }
)
_POSITIONAL_OUTPUT_INDEX_BY_SCRIPT = {
    "build_star_gene_counts_matrix.py": 2,
}
_DEFAULT_OUTPUT_FLAGS = frozenset(
    {
        "--out",
        "--output",
        "--output-file",
        "--output-csv",
        "--output-report",
        "--output-detected",
        "--json",
    }
)
_DEFAULT_OUTPUT_ROOT_FLAGS = frozenset({"--outdir", "--output-dir"})
_FILE_REWRITE_COMMANDS = frozenset({"cp", "mv"})
_LINK_COMMANDS = frozenset({"ln"})
_INPLACE_COMPRESSION_COMMANDS = frozenset({"bgzip", "gzip"})
_REMOVE_COMMANDS = frozenset({"rm", "rmdir"})


@dataclass(frozen=True)
class ShellOutputHints:
    """Deterministic output-path hints extracted from one shell command.

    Attributes:
        output_paths: Concrete file-like output paths.
        output_roots: Directory or output-root paths.
    """

    output_paths: tuple[str, ...]
    output_roots: tuple[str, ...]


def _normalize_shell_output_candidate(candidate: str) -> str:
    """Normalize one shell-derived output candidate.

    Args:
        candidate: Raw token extracted from a shell command.

    Returns:
        Normalized path text, or an empty string when the token is not a
        material output path.
    """

    cleaned = str(candidate or "").strip().strip("'\"").rstrip(";")
    if not cleaned or cleaned.startswith("/dev/") or cleaned.startswith("&"):
        return ""
    if cleaned.isdigit():
        return ""
    return cleaned


def _command_tokens_before(tokens: list[str], idx: int) -> tuple[str, ...]:
    """Return normalized command tokens for the current shell segment."""

    start = idx
    while start > 0 and tokens[start - 1] not in _COMMAND_SPLIT_TOKENS:
        start -= 1
    return tuple(
        str(token).strip().lower()
        for token in tokens[start:idx]
        if str(token).strip()
    )


def _bash_script_name(tokens: list[str]) -> str:
    """Return the invoked helper-script name for one shell token list."""

    if not tokens:
        return ""
    if tokens[0].startswith("python") and len(tokens) > 1:
        return Path(tokens[1]).name
    return Path(tokens[0]).name


def _extract_positional_script_outputs(tokens: list[str]) -> list[str]:
    """Return output paths produced by helper scripts with positional outputs."""

    script_name = _bash_script_name(tokens)
    if not script_name:
        return []

    outputs: list[str] = []
    if script_name in _POSITIONAL_OUTPUT_LAST_ARG_SCRIPTS and len(tokens) >= 3:
        candidate = _normalize_shell_output_candidate(tokens[-1])
        if candidate:
            outputs.append(candidate)

    output_index = _POSITIONAL_OUTPUT_INDEX_BY_SCRIPT.get(script_name)
    if output_index is not None and len(tokens) > output_index:
        candidate = _normalize_shell_output_candidate(tokens[output_index])
        if candidate:
            outputs.append(candidate)
    return outputs


def _positional_shell_operands(tokens: list[str]) -> list[str]:
    """Return non-flag positional shell operands for one token list."""

    operands: list[str] = []
    skip_next = False
    for idx, token in enumerate(tokens[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        probe = str(token).strip()
        if not probe:
            continue
        if probe in {"-t", "--target-directory"}:
            skip_next = True
            continue
        if probe.startswith("-"):
            continue
        previous = str(tokens[idx - 1]).strip().lower() if idx > 0 else ""
        if previous in {"-t", "--target-directory"}:
            continue
        operands.append(probe)
    return operands


def _extract_transform_command_outputs(tokens: list[str]) -> list[str]:
    """Return output paths produced by filesystem-transform shell commands."""

    if not tokens:
        return []
    command_name = Path(str(tokens[0] or "")).name.lower()
    operands = _positional_shell_operands(tokens)
    outputs: list[str] = []
    if command_name in _FILE_REWRITE_COMMANDS and len(operands) >= 2:
        candidate = _normalize_shell_output_candidate(operands[-1])
        if candidate:
            outputs.append(candidate)
    if command_name in _LINK_COMMANDS and len(operands) >= 2:
        candidate = _normalize_shell_output_candidate(operands[-1])
        if candidate:
            outputs.append(candidate)
    if command_name in _INPLACE_COMPRESSION_COMMANDS and "-c" not in tokens and operands:
        source = _normalize_shell_output_candidate(operands[-1])
        if source and not source.endswith(".gz"):
            outputs.append(f"{source}.gz")
    if command_name == "tabix" and operands:
        source = _normalize_shell_output_candidate(operands[-1])
        if source:
            outputs.append(f"{source}.tbi")
    return outputs


def _extract_transient_command_paths(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Return same-command transient paths removed or replaced later.

    These hints model paths that are produced within one shell step but then
    moved away or deleted before the step completes. They should not be treated
    as persisted outputs of the overall bash step.
    """

    if not tokens:
        return [], []
    command_name = Path(str(tokens[0] or "")).name.lower()
    operands = _positional_shell_operands(tokens)
    transient_paths: list[str] = []
    transient_roots: list[str] = []
    if command_name == "mv" and len(operands) >= 2:
        for operand in operands[:-1]:
            candidate = _normalize_shell_output_candidate(operand)
            if candidate:
                transient_paths.append(candidate)
    if command_name in _REMOVE_COMMANDS and operands:
        recursive = any(_is_recursive_remove_flag(token) for token in tokens[1:])
        for operand in operands:
            candidate = _normalize_shell_output_candidate(operand)
            if not candidate:
                continue
            if recursive:
                transient_roots.append(candidate)
            else:
                transient_paths.append(candidate)
    return transient_paths, transient_roots


def _is_recursive_remove_flag(token: str) -> bool:
    """Return whether one ``rm`` token requests recursive deletion."""

    probe = str(token or "").strip().lower()
    if probe in {"-r", "-rf", "-fr", "--recursive"}:
        return True
    if probe.startswith("-") and probe not in {"-", "--"}:
        chars = probe[1:]
        return "r" in chars and all(char in {"r", "f", "v", "d"} for char in chars)
    return False


def _matches_transient_path(candidate: str, transient: str) -> bool:
    """Return whether one output candidate matches an exact transient path."""

    try:
        return Path(candidate) == Path(transient)
    except Exception:
        return str(candidate).strip() == str(transient).strip()


def _is_under_transient_root(candidate: str, transient_root: str) -> bool:
    """Return whether one output candidate sits under a transient root."""

    try:
        candidate_path = Path(candidate)
        transient_path = Path(transient_root)
        return candidate_path == transient_path or transient_path in candidate_path.parents
    except Exception:
        candidate_text = str(candidate).strip().rstrip("/")
        transient_text = str(transient_root).strip().rstrip("/")
        return candidate_text == transient_text or candidate_text.startswith(f"{transient_text}/")


def _filter_transient_outputs(
    output_paths: list[str],
    output_roots: list[str],
    *,
    transient_paths: list[str],
    transient_roots: list[str],
) -> tuple[list[str], list[str]]:
    """Remove paths that are transient within the same shell command."""

    stable_paths = [
        path
        for path in output_paths
        if not any(_matches_transient_path(path, transient) for transient in transient_paths)
        and not any(_is_under_transient_root(path, transient_root) for transient_root in transient_roots)
    ]
    stable_roots = [
        path
        for path in output_roots
        if not any(_matches_transient_path(path, transient) for transient in transient_paths)
        and not any(_is_under_transient_root(path, transient_root) for transient_root in transient_roots)
    ]
    return stable_paths, stable_roots


def _dedupe_preserve_order(values: list[str]) -> tuple[str, ...]:
    """Deduplicate path hints while preserving their original order."""

    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _extract_bcftools_isec_outputs(tokens: list[str]) -> tuple[list[str], set[str]]:
    """Return deterministic concrete outputs for one ``bcftools isec`` command.

    When ``bcftools isec`` uses ``-p/--prefix`` together with a single
    ``-w/--write`` index, the emitted VCF/BCF filename is deterministic. In
    that case downstream validation should track the concrete emitted file
    rather than treating the whole prefix directory as an unconstrained output
    root.
    """

    if len(tokens) < 2:
        return [], set()
    if Path(str(tokens[0] or "")).name.lower() != "bcftools":
        return [], set()
    if Path(str(tokens[1] or "")).name.lower() != "isec":
        return [], set()

    prefix = ""
    write_value = ""
    output_type = ""
    idx = 2
    while idx < len(tokens):
        token = str(tokens[idx] or "").strip()
        if not token:
            idx += 1
            continue
        if token == "--":
            break
        if token in _BCFTOOLS_ISEC_PREFIX_FLAGS and idx + 1 < len(tokens):
            prefix = _normalize_shell_output_candidate(tokens[idx + 1])
            idx += 2
            continue
        if token.startswith("--prefix="):
            prefix = _normalize_shell_output_candidate(token.partition("=")[2])
            idx += 1
            continue
        if token in _BCFTOOLS_ISEC_WRITE_FLAGS and idx + 1 < len(tokens):
            write_value = str(tokens[idx + 1] or "").strip()
            idx += 2
            continue
        if token.startswith("--write="):
            write_value = str(token.partition("=")[2] or "").strip()
            idx += 1
            continue
        if token.startswith("-w") and token != "-w":
            write_value = str(token[2:] or "").lstrip("=").strip()
            idx += 1
            continue
        if token in _BCFTOOLS_ISEC_OUTPUT_TYPE_FLAGS and idx + 1 < len(tokens):
            output_type = str(tokens[idx + 1] or "").strip()
            idx += 2
            continue
        if token.startswith("--output-type="):
            output_type = str(token.partition("=")[2] or "").strip()
            idx += 1
            continue
        if token.startswith("-O") and token != "-O":
            output_type = str(token[2:] or "").lstrip("=").strip()
            idx += 1
            continue
        idx += 1

    emitted_name = _bcftools_isec_emitted_name(write_value, output_type)
    if not prefix or not emitted_name:
        return [], set()
    return [str(Path(prefix) / emitted_name)], {prefix}


def _bcftools_isec_emitted_name(write_value: str, output_type: str) -> str:
    """Return the deterministic filename emitted by ``bcftools isec -p``."""

    text = str(write_value or "").strip()
    if not text or "," in text or not text.isdigit():
        return ""
    write_index = int(text)
    if write_index <= 0:
        return ""

    normalized_type = str(output_type or "").strip().lower()[:1]
    suffix = ".vcf"
    if normalized_type == "z":
        suffix = ".vcf.gz"
    elif normalized_type in {"b", "u"}:
        suffix = ".bcf"
    return f"{write_index - 1:04d}{suffix}"


def extract_shell_output_hints(
    command: str,
    *,
    extra_output_flags: frozenset[str] | set[str] | tuple[str, ...] = (),
    extra_output_root_flags: frozenset[str] | set[str] | tuple[str, ...] = (),
    command_output_root_flags: Mapping[tuple[str, ...], frozenset[str] | set[str] | tuple[str, ...]] | None = None,
) -> ShellOutputHints:
    """Extract deterministic output hints from a shell command.

    Args:
        command: Shell command text.
        extra_output_flags: Additional literal flags whose next value should be
            treated as an output path for the current caller.
        extra_output_root_flags: Additional literal flags whose next value
            should be treated as an output root.
        command_output_root_flags: Optional command-signature-specific root
            flags. Defaults to the built-in map.

    Returns:
        A ``ShellOutputHints`` object with ordered file outputs and output
        roots.
    """

    cleaned = strip_shell_heredoc_body(strip_shell_comments(command))
    output_flags = _DEFAULT_OUTPUT_FLAGS | {
        str(flag).strip().lower()
        for flag in extra_output_flags
        if str(flag).strip()
    }
    output_root_flags = _DEFAULT_OUTPUT_ROOT_FLAGS | {
        str(flag).strip().lower()
        for flag in extra_output_root_flags
        if str(flag).strip()
    }
    root_flag_map: dict[tuple[str, ...], frozenset[str]] = {
        signature: frozenset(str(flag).strip() for flag in flags if str(flag).strip())
        for signature, flags in (command_output_root_flags or _COMMAND_OUTPUT_ROOT_FLAGS).items()
    }

    output_paths: list[str] = []
    output_roots: list[str] = []
    transient_paths: list[str] = []
    transient_roots: list[str] = []
    for segment in split_shell_segments(cleaned):
        seg = str(segment or "").strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            tokens = []
        if not tokens:
            continue
        deterministic_isec_outputs, suppressed_output_roots = _extract_bcftools_isec_outputs(tokens)
        output_paths.extend(_extract_positional_script_outputs(tokens))
        output_paths.extend(_extract_transform_command_outputs(tokens))
        output_paths.extend(deterministic_isec_outputs)
        segment_transient_paths, segment_transient_roots = _extract_transient_command_paths(tokens)
        transient_paths.extend(segment_transient_paths)
        transient_roots.extend(segment_transient_roots)
        for idx, token in enumerate(tokens):
            probe = str(token).strip()
            if not probe:
                continue
            probe_l = probe.lower()
            if probe in _REDIRECT_TOKENS and idx + 1 < len(tokens):
                candidate = _normalize_shell_output_candidate(tokens[idx + 1])
                if candidate:
                    output_paths.append(candidate)
                continue
            for redirect in _REDIRECT_TOKENS:
                if not probe.startswith(redirect) or probe == redirect:
                    continue
                candidate = _normalize_shell_output_candidate(probe[len(redirect) :])
                if candidate:
                    output_paths.append(candidate)
                break
            if probe in _OUTPUT_SHORT_FLAGS and idx + 1 < len(tokens):
                candidate = _normalize_shell_output_candidate(tokens[idx + 1])
                if candidate and not candidate.startswith("-"):
                    output_paths.append(candidate)
                continue
            if probe.startswith("-o") and probe != "-o":
                candidate = _normalize_shell_output_candidate(probe[2:])
                if candidate:
                    output_paths.append(candidate)
                continue
            command_tokens = _command_tokens_before(tokens, idx)
            for signature, flags in root_flag_map.items():
                if probe not in flags:
                    continue
                if command_tokens[: len(signature)] != signature:
                    continue
                if idx + 1 >= len(tokens):
                    continue
                candidate = _normalize_shell_output_candidate(tokens[idx + 1])
                if candidate and not candidate.startswith("-"):
                    if candidate in suppressed_output_roots:
                        break
                    output_roots.append(candidate)
                break
            flag, sep, value = probe.partition("=")
            flag_l = flag.lower()
            if sep and flag_l in output_root_flags and value:
                candidate = _normalize_shell_output_candidate(value)
                if candidate:
                    output_roots.append(candidate)
                continue
            if sep and (_OUTPUT_FLAG_RE.match(flag_l) or flag_l in output_flags) and value:
                candidate = _normalize_shell_output_candidate(value)
                if candidate:
                    output_paths.append(candidate)
                continue
            if probe_l in output_root_flags and idx + 1 < len(tokens):
                candidate = _normalize_shell_output_candidate(tokens[idx + 1])
                if candidate and not candidate.startswith("-"):
                    output_roots.append(candidate)
                continue
            if not (_OUTPUT_FLAG_RE.match(probe_l) or probe_l in output_flags):
                continue
            if idx + 1 >= len(tokens):
                continue
            candidate = _normalize_shell_output_candidate(tokens[idx + 1])
            if candidate and not candidate.startswith("-"):
                output_paths.append(candidate)

    output_paths, output_roots = _filter_transient_outputs(
        output_paths,
        output_roots,
        transient_paths=transient_paths,
        transient_roots=transient_roots,
    )

    return ShellOutputHints(
        output_paths=_dedupe_preserve_order(output_paths),
        output_roots=_dedupe_preserve_order(output_roots),
    )


__all__ = ["ShellOutputHints", "extract_shell_output_hints"]
