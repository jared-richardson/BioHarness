"""Inspect and repair ``bcftools`` shell semantics.

This module handles shell-level ``bcftools`` commands that need deterministic,
benchmark-safe validation before execution. It currently covers three classes
of issues:

1. Ambiguous expression namespaces in ``bcftools filter/view/query`` commands.
2. Explicitly misqualified expression namespaces such as ``FORMAT/AF`` when the
   live VCF header only exposes ``INFO/AF`` and the swap is safe.
3. Mis-specified ``bcftools isec`` export patterns where ``-p/--prefix`` is
   combined with a concrete output target that ``bcftools`` will not actually
   materialize.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.artifact_inspectors import inspect_vcf_field_namespaces
from bio_harness.core.shell_parse import (
    is_shell_assignment,
    normalize_shell_command_token,
    split_shell_chain_segments,
    split_shell_pipeline_segments,
    split_shell_segments,
)

_BCFTOOLS_EXPRESSION_SUBCOMMANDS = frozenset({"filter", "view", "query"})
_BCFTOOLS_EXPRESSION_OPTIONS = frozenset({"-i", "--include", "-e", "--exclude"})
_BCFTOOLS_OUTPUT_PATH_OPTIONS = frozenset({"-o", "--output"})
_SAFE_SINGLE_SAMPLE_INFO_TAGS = frozenset({
    "AC",
    "AF",
    "AN",
    "AO",
    "DP",
    "MIN_DP",
    "NS",
    "QA",
    "QR",
    "RO",
})
_SHELL_REDIRECT_TOKENS = frozenset({">", ">>", "1>", "1>>"})
_BCFTOOLS_ISEC_PREFIX_OPTIONS = frozenset({"-p", "--prefix"})
_BCFTOOLS_ISEC_OUTPUT_OPTIONS = frozenset({"-o", "--output"})
_BCFTOOLS_ISEC_WRITE_OPTIONS = frozenset({"-w", "--write"})
_BCFTOOLS_ISEC_OUTPUT_TYPE_OPTIONS = frozenset({"-O", "--output-type"})
_BCFTOOLS_ISEC_NFILES_OPTIONS = frozenset({"-n", "--nfiles"})
_BCFTOOLS_ISEC_COLLAPSE_OPTIONS = frozenset({"-c", "--collapse"})
_BCFTOOLS_ISEC_INCLUDE_OPTIONS = frozenset({"-i", "--include"})
_BCFTOOLS_ISEC_EXCLUDE_OPTIONS = frozenset({"-e", "--exclude"})
_BCFTOOLS_ISEC_REGION_OPTIONS = frozenset({"-r", "--regions"})
_BCFTOOLS_ISEC_REGION_FILE_OPTIONS = frozenset({"-R", "--regions-file"})
_BCFTOOLS_ISEC_TARGET_OPTIONS = frozenset({"-t", "--targets"})
_BCFTOOLS_ISEC_TARGET_FILE_OPTIONS = frozenset({"-T", "--targets-file"})
_BCFTOOLS_ISEC_FILTER_OPTIONS = frozenset({"-f", "--apply-filters"})
_BCFTOOLS_ISEC_UNIQUE_VALUE_OPTION_KEYS = {
    **{token: "collapse" for token in _BCFTOOLS_ISEC_COLLAPSE_OPTIONS},
    **{token: "exclude" for token in _BCFTOOLS_ISEC_EXCLUDE_OPTIONS},
    **{token: "apply-filters" for token in _BCFTOOLS_ISEC_FILTER_OPTIONS},
    **{token: "include" for token in _BCFTOOLS_ISEC_INCLUDE_OPTIONS},
    **{token: "nfiles" for token in _BCFTOOLS_ISEC_NFILES_OPTIONS},
    **{token: "output" for token in _BCFTOOLS_ISEC_OUTPUT_OPTIONS},
    **{token: "output-type" for token in _BCFTOOLS_ISEC_OUTPUT_TYPE_OPTIONS},
    **{token: "prefix" for token in _BCFTOOLS_ISEC_PREFIX_OPTIONS},
    **{token: "regions" for token in _BCFTOOLS_ISEC_REGION_OPTIONS},
    **{token: "regions-file" for token in _BCFTOOLS_ISEC_REGION_FILE_OPTIONS},
    **{token: "targets" for token in _BCFTOOLS_ISEC_TARGET_OPTIONS},
    **{token: "targets-file" for token in _BCFTOOLS_ISEC_TARGET_FILE_OPTIONS},
    **{token: "write" for token in _BCFTOOLS_ISEC_WRITE_OPTIONS},
}
_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_/])([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_])")
_NAMESPACED_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<namespace>INFO|FORMAT|FMT)/(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_])"
)
_VCF_SUFFIXES = (".vcf", ".vcf.gz", ".bcf")


def inspect_bcftools_expression_command(
    command: str,
    *,
    cwd: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return bcftools expression namespace issues for one shell command.

    Args:
        command: Full shell command, potentially containing multiple segments.
        cwd: Working directory used to resolve relative VCF paths.

    Returns:
        A list of issue dictionaries describing ambiguous or misqualified
        namespace usage.
    """

    issues: list[dict[str, Any]] = []
    for segment in split_shell_segments(str(command or "")):
        segment_issues, _ = _inspect_bcftools_expression_segment(
            str(segment or ""),
            cwd=cwd,
            auto_repair=False,
        )
        issues.extend(segment_issues)
    return issues


def repair_bcftools_expression_command(
    command: str,
    *,
    cwd: str | Path | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Repair low-ambiguity bcftools expression namespace issues in one shell command.

    Args:
        command: Full shell command, potentially containing multiple segments.
        cwd: Working directory used to resolve relative VCF paths.

    Returns:
        Tuple of ``(repaired_command, repairs)``. ``repairs`` contains the
        issue dictionaries that were actually repaired.
    """

    original = str(command or "").strip()
    if not original:
        return original, []

    updated_command = original
    repairs: list[dict[str, Any]] = []
    for segment in split_shell_segments(original):
        segment_text = str(segment or "")
        segment_issues, repaired_segment = _inspect_bcftools_expression_segment(
            segment_text,
            cwd=cwd,
            auto_repair=True,
        )
        if repaired_segment == segment_text:
            continue
        updated_command = updated_command.replace(segment_text, repaired_segment, 1)
        repairs.extend(issue for issue in segment_issues if issue.get("repairable"))
    if updated_command != original:
        updated_command = _collapse_shell_line_continuations(updated_command)
    return updated_command, repairs


def inspect_bcftools_isec_command(command: str) -> list[dict[str, Any]]:
    """Return deterministic ``bcftools isec`` export issues for one command.

    Args:
        command: Full shell command, potentially containing chained segments.

    Returns:
        A list of issue dictionaries describing output-mode mismatches.
    """

    issues: list[dict[str, Any]] = []
    for segment in split_shell_chain_segments(str(command or "")):
        segment_issues, _ = _inspect_bcftools_isec_chain_segment(
            str(segment or ""),
            auto_repair=False,
        )
        issues.extend(segment_issues)
    return issues


def repair_bcftools_isec_command(command: str) -> tuple[str, list[dict[str, Any]]]:
    """Repair low-ambiguity ``bcftools isec`` output-mode misuse.

    Args:
        command: Full shell command, potentially containing chained segments.

    Returns:
        Tuple of ``(repaired_command, repairs)`` for any applied local fixes.
    """

    original = str(command or "").strip()
    if not original:
        return original, []

    chain_segments = [str(segment or "").strip() for segment in split_shell_chain_segments(original)]
    updated_segments = list(chain_segments)
    repairs: list[dict[str, Any]] = []
    for idx, segment_text in enumerate(chain_segments):
        segment_issues, repaired_segment = _inspect_bcftools_isec_chain_segment(
            segment_text,
            auto_repair=True,
        )
        if repaired_segment == segment_text:
            chain_repair = _repair_bcftools_isec_followup_chain_segments(
                updated_segments,
                segment_index=idx,
            )
            if chain_repair is None:
                continue
            updated_segments, chain_repairs = chain_repair
            repairs.extend(chain_repairs)
            continue
        updated_segments[idx] = repaired_segment
        repairs.extend(issue for issue in segment_issues if issue.get("repairable"))
    grouped_repair = _repair_bcftools_isec_reused_prefix_export_groups(updated_segments)
    if grouped_repair is not None:
        updated_segments, grouped_repairs = grouped_repair
        repairs.extend(grouped_repairs)
    if updated_segments == chain_segments:
        return original, []
    return " && ".join(segment for segment in updated_segments if segment), repairs


def _repair_bcftools_isec_reused_prefix_export_groups(
    chain_segments: list[str],
) -> tuple[list[str], list[dict[str, Any]]] | None:
    """Repair repeated ``bcftools isec -p`` exports that reuse one prefix root.

    Some planner outputs chain multiple ``bcftools isec -p <dir>`` invocations
    into the same prefix directory and then try to export numbered members such
    as ``0000.vcf`` and ``0001.vcf`` via later ``mv`` commands. ``bcftools``
    only materializes one deterministic emitted file per invocation, so the
    robust repair is to rewrite each ``isec`` call to export directly to its
    intended downstream target using a unique transient prefix.
    """

    if not chain_segments:
        return None

    isec_entries: dict[str, list[dict[str, Any]]] = {}
    for idx, segment_text in enumerate(chain_segments):
        normalized_segment = _normalize_shell_segment_text(segment_text)
        pipeline_segments = [
            _normalize_shell_segment_text(part)
            for part in split_shell_pipeline_segments(normalized_segment)
            if _normalize_shell_segment_text(part)
        ]
        if len(pipeline_segments) != 1:
            continue
        tokens = _tokenize_shell_segment(pipeline_segments[0])
        if not tokens:
            continue
        command_start = _bcftools_command_start(tokens)
        if command_start is None or _token_name(tokens[command_start + 1]) != "isec":
            continue
        _duplicate_issues, canonical_tokens = _inspect_bcftools_isec_unique_options(
            tokens,
            segment_text=segment_text,
        )
        metadata = _parse_bcftools_isec_metadata(canonical_tokens, pipeline_segments[0])
        if metadata["output_target"]:
            continue
        prefix_key = _normalize_bcftools_isec_prefix(metadata["prefix"])
        if not prefix_key:
            continue
        isec_entries.setdefault(prefix_key, []).append(
            {"segment_index": idx, "segment_text": segment_text, "metadata": metadata}
        )

    updated_segments = list(chain_segments)
    repairs: list[dict[str, Any]] = []
    changed = False
    for prefix_key, entries in sorted(isec_entries.items()):
        export_groups = _collect_bcftools_isec_prefix_export_groups(
            chain_segments,
            prefix_key=prefix_key,
        )
        if len(entries) < 1 or len(export_groups) < len(entries):
            continue
        for entry, export_group in zip(entries, export_groups):
            updated_segments[int(entry["segment_index"])] = _repair_bcftools_isec_followup_export_segment(
                entry["metadata"],
                output_target=str(export_group["output_target"]),
                force_unique_prefix=True,
            )
            for consumer_index in export_group["consumer_indexes"]:
                updated_segments[int(consumer_index)] = ""
            repairs.append(
                {
                    "issue": "invalid_bcftools_isec_output_mode",
                    "reason": "reused_prefix_export_collision",
                    "repairable": True,
                    "segment": str(entry["segment_text"]),
                    "prefix": str(entry["metadata"]["prefix"]),
                    "subcommand": "isec",
                    "output_target": str(export_group["output_target"]),
                }
            )
            changed = True
    if not changed:
        return None
    return updated_segments, repairs


def _collect_bcftools_isec_prefix_export_groups(
    chain_segments: list[str],
    *,
    prefix_key: str,
) -> list[dict[str, Any]]:
    """Collect later move/copy exports sourced from one ``isec`` prefix root."""

    groups: list[dict[str, Any]] = []
    index_lookup: dict[str, list[int]] = {}
    for idx, segment in enumerate(chain_segments):
        tokens = _tokenize_shell_segment(segment)
        if len(tokens) < 3 or _token_name(tokens[0]) not in {"mv", "cp"}:
            continue
        source = str(tokens[1]).strip()
        target = str(tokens[2]).strip()
        source_path = Path(source)
        source_prefix = _normalize_bcftools_isec_prefix(str(source_path.parent))
        if source_prefix != prefix_key:
            continue
        source_name = source_path.name
        if source_name.endswith((".tbi", ".csi")):
            index_lookup.setdefault(Path(target).name, []).append(idx)
            continue
        if not _looks_like_bcftools_isec_prefix_artifact(source_name):
            continue
        consumer_indexes = [idx]
        consumer_indexes.extend(index_lookup.pop(f"{Path(target).name}.tbi", []))
        consumer_indexes.extend(index_lookup.pop(f"{Path(target).name}.csi", []))
        groups.append(
            {
                "consumer_indexes": tuple(sorted(set(consumer_indexes))),
                "output_target": target,
            }
        )
    return groups


def _normalize_bcftools_isec_prefix(prefix: str) -> str:
    """Return a stable comparison key for one ``bcftools isec`` prefix value."""

    raw = str(prefix or "").strip()
    if not raw:
        return ""
    normalized = raw.rstrip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _inspect_bcftools_expression_segment(
    segment: str,
    *,
    cwd: str | Path | None,
    auto_repair: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Inspect one shell segment for ambiguous ``bcftools`` expression tags."""

    original_text = str(segment or "").strip()
    text = _normalize_shell_segment_text(original_text)
    if not text:
        return [], original_text

    tokens = _tokenize_shell_segment(text)
    if not tokens:
        return [], original_text

    command_start = _bcftools_command_start(tokens)
    if command_start is None:
        return [], original_text

    subcommand = _token_name(tokens[command_start + 1])
    if subcommand not in _BCFTOOLS_EXPRESSION_SUBCOMMANDS:
        return [], original_text

    input_vcf = _resolve_existing_vcf_input(tokens, cwd=cwd)
    if input_vcf is None:
        return [], original_text

    inspection = inspect_vcf_field_namespaces(input_vcf)
    if not inspection.exists:
        return [], original_text

    repaired_tokens = list(tokens)
    issues: list[dict[str, Any]] = []
    changed = False

    idx = command_start + 2
    while idx < len(repaired_tokens):
        token = str(repaired_tokens[idx]).strip()
        if token == "--":
            break
        if token not in _BCFTOOLS_EXPRESSION_OPTIONS:
            idx += 1
            continue
        if idx + 1 >= len(repaired_tokens):
            break

        expression = str(repaired_tokens[idx + 1]).strip()
        expression_issues, repaired_expression = _inspect_expression_namespaces(
            expression,
            input_vcf=input_vcf,
            subcommand=subcommand,
            option=token,
            inspection=inspection,
            auto_repair=auto_repair,
        )
        issues.extend(
            {
                **issue,
                "segment": original_text,
            }
            for issue in expression_issues
        )
        if repaired_expression != expression:
            repaired_tokens[idx + 1] = repaired_expression
            changed = True
        idx += 2

    if not changed:
        return issues, original_text
    repaired_text = " ".join(shlex.quote(token) for token in repaired_tokens)
    return issues, repaired_text


def _inspect_bcftools_isec_chain_segment(
    segment: str,
    *,
    auto_repair: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Inspect one chain segment for deterministic ``bcftools isec`` issues."""

    original_text = str(segment or "").strip()
    text = _normalize_shell_segment_text(original_text)
    if not text:
        return [], original_text

    pipeline_segments = [
        _normalize_shell_segment_text(part)
        for part in split_shell_pipeline_segments(text)
        if _normalize_shell_segment_text(part)
    ]
    if not pipeline_segments:
        return [], original_text

    left_tokens = _tokenize_shell_segment(pipeline_segments[0])
    if not left_tokens:
        return [], original_text

    command_start = _bcftools_command_start(left_tokens)
    if command_start is None or _token_name(left_tokens[command_start + 1]) != "isec":
        return [], original_text

    duplicate_option_issues, canonical_left_tokens = _inspect_bcftools_isec_unique_options(
        left_tokens,
        segment_text=original_text,
    )
    metadata = _parse_bcftools_isec_metadata(canonical_left_tokens, pipeline_segments[0])
    issues: list[dict[str, Any]] = list(duplicate_option_issues)
    if metadata["plain_vcf_inputs"]:
        issues.append(
            {
                "issue": "invalid_bcftools_isec_output_mode",
                "reason": "plain_vcf_input_requires_bgzip",
                "repairable": True,
                "segment": original_text,
                "prefix": metadata["prefix"],
                "subcommand": "isec",
                "write_index": metadata["write_index"],
                "output_target": metadata["output_target"],
            }
        )

    if metadata["prefix"] and metadata["prefix_is_selected_root"]:
        issues.append(
            {
                "issue": "invalid_bcftools_isec_output_mode",
                "reason": "overbroad_prefix_root",
                "repairable": bool(metadata["repairable_export"]),
                "segment": original_text,
                "prefix": metadata["prefix"],
                "subcommand": "isec",
                "write_index": metadata["write_index"],
                "output_target": metadata["output_target"],
            }
        )

    repaired_segment = original_text
    canonical_segment = _render_bcftools_isec_pipeline_segment(
        canonical_left_tokens,
        pipeline_segments[1:],
    )
    if metadata["output_target"]:
        issues.append(
            {
                "issue": "invalid_bcftools_isec_output_mode",
                "reason": "prefix_output_target_ignored",
                "repairable": bool(metadata["repairable_export"]),
                "segment": original_text,
                "prefix": metadata["prefix"],
                "subcommand": "isec",
                "write_index": metadata["write_index"],
                "output_target": metadata["output_target"],
            }
        )
        if auto_repair and metadata["repairable_export"]:
            repaired_segment = _repair_bcftools_isec_output_segment(metadata)
        elif auto_repair and canonical_segment != original_text:
            repaired_segment = canonical_segment
        return issues, repaired_segment

    if len(pipeline_segments) == 2 and metadata["prefix"]:
        pipeline_export = _parse_bcftools_isec_pipeline_export(pipeline_segments[1])
        if pipeline_export:
            issues.append(
                {
                    "issue": "invalid_bcftools_isec_output_mode",
                    "reason": "prefix_pipeline_stdout_ignored",
                    "repairable": bool(metadata["repairable_export"] and pipeline_export["repairable"]),
                    "segment": original_text,
                    "prefix": metadata["prefix"],
                    "subcommand": "isec",
                    "write_index": metadata["write_index"],
                    "output_target": pipeline_export["output_target"],
                }
            )
            if auto_repair and metadata["repairable_export"] and pipeline_export["repairable"]:
                repaired_segment = _repair_bcftools_isec_pipeline_segment(
                    metadata,
                    pipeline_export=pipeline_export,
                )
            elif auto_repair and canonical_segment != original_text:
                repaired_segment = canonical_segment
            return issues, repaired_segment

    if auto_repair and metadata["plain_vcf_inputs"]:
        staged_segment = _render_bcftools_isec_plain_input_segment(
            canonical_left_tokens,
            pipeline_segments[1:],
        )
        if staged_segment != original_text:
            return issues, staged_segment

    if auto_repair and canonical_segment != original_text:
        return issues, canonical_segment
    return issues, original_text


def _repair_bcftools_isec_followup_chain_segments(
    chain_segments: list[str],
    *,
    segment_index: int,
) -> tuple[list[str], list[dict[str, Any]]] | None:
    """Repair an ``isec -p`` segment consumed by later chained commands.

    This handles the case where ``bcftools isec`` writes into an overbroad
    prefix such as ``.`` and a later ``&&``-chained segment consumes the
    deterministic emitted file like ``0000.vcf``. The repair moves the prefix
    into a transient deterministic directory and rewrites later consumers to
    the concrete emitted path.
    """

    if segment_index < 0 or segment_index >= len(chain_segments):
        return None

    segment_text = str(chain_segments[segment_index] or "").strip()
    if not segment_text:
        return None

    normalized_segment = _normalize_shell_segment_text(segment_text)
    pipeline_segments = [
        _normalize_shell_segment_text(part)
        for part in split_shell_pipeline_segments(normalized_segment)
        if _normalize_shell_segment_text(part)
    ]
    if len(pipeline_segments) != 1:
        return None

    left_tokens = _tokenize_shell_segment(pipeline_segments[0])
    if not left_tokens:
        return None
    command_start = _bcftools_command_start(left_tokens)
    if command_start is None or _token_name(left_tokens[command_start + 1]) != "isec":
        return None

    duplicate_option_issues, canonical_left_tokens = _inspect_bcftools_isec_unique_options(
        left_tokens,
        segment_text=segment_text,
    )
    metadata = _parse_bcftools_isec_metadata(canonical_left_tokens, pipeline_segments[0])
    if not metadata["prefix_is_selected_root"] or metadata["output_target"]:
        return None

    followup = _discover_bcftools_isec_followup_consumers(
        chain_segments,
        start_index=segment_index + 1,
    )
    if not followup["consumer_indexes"]:
        export_followup = _discover_bcftools_isec_followup_export_moves(
            chain_segments,
            start_index=segment_index + 1,
            metadata=metadata,
        )
        if not export_followup["consumer_indexes"]:
            return None
        output_target = str(export_followup["output_target"]).strip()
        if not output_target:
            return None
        updated_segments = list(chain_segments)
        updated_segments[segment_index] = _repair_bcftools_isec_followup_export_segment(
            metadata,
            output_target=output_target,
        )
        for consumer_index in export_followup["consumer_indexes"]:
            updated_segments[consumer_index] = ""
        repairs: list[dict[str, Any]] = list(duplicate_option_issues)
        repairs.append(
            {
                "issue": "invalid_bcftools_isec_output_mode",
                "reason": "overbroad_prefix_root",
                "repairable": True,
                "segment": segment_text,
                "prefix": metadata["prefix"],
                "subcommand": "isec",
                "write_index": metadata["write_index"],
                "output_target": output_target,
            }
        )
        return updated_segments, repairs

    repair_target = followup["output_target"] or next(iter(followup["artifact_names"]), "isec_output")
    repair_prefix = _choose_isec_repair_prefix(metadata["prefix"], repair_target)
    repaired_segment = _render_bcftools_isec_prefix_only_segment(
        metadata,
        repair_prefix=repair_prefix,
    )
    updated_segments = list(chain_segments)
    updated_segments[segment_index] = repaired_segment

    consumer_indexes = list(followup["consumer_indexes"])
    artifact_names = set(followup["artifact_names"])
    for consumer_index in consumer_indexes:
        rewritten = _rewrite_bcftools_isec_followup_consumer_segment(
            updated_segments[consumer_index],
            artifact_names=artifact_names,
            original_prefix=metadata["prefix"],
            repair_prefix=repair_prefix,
        )
        updated_segments[consumer_index] = rewritten

    cleanup_tail = _render_bcftools_isec_cleanup(
        original_prefix=metadata["prefix"],
        repair_prefix=repair_prefix,
    )
    if cleanup_tail and consumer_indexes:
        last_index = consumer_indexes[-1]
        updated_segments[last_index] = f"{updated_segments[last_index]} && {cleanup_tail}"

    repairs: list[dict[str, Any]] = list(duplicate_option_issues)
    repairs.append(
        {
            "issue": "invalid_bcftools_isec_output_mode",
            "reason": "overbroad_prefix_root",
            "repairable": True,
            "segment": segment_text,
            "prefix": metadata["prefix"],
            "subcommand": "isec",
            "write_index": metadata["write_index"],
            "output_target": followup["output_target"],
        }
    )
    return updated_segments, repairs


def _render_bcftools_isec_plain_input_segment(
    left_tokens: list[str],
    pipeline_segments: list[str],
) -> str:
    """Render one ``bcftools isec`` segment with staged ``.vcf`` inputs."""

    staged_tokens, staging_commands = _stage_bcftools_isec_plain_vcf_inputs(list(left_tokens))
    rendered_segment = _render_bcftools_isec_pipeline_segment(staged_tokens, pipeline_segments)
    parts = [*staging_commands, rendered_segment]
    return " && ".join(part for part in parts if part)


def _discover_bcftools_isec_followup_export_moves(
    chain_segments: list[str],
    *,
    start_index: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Return chained move/copy exports that misuse one ``isec -p`` prefix root."""

    input_basenames = _collect_bcftools_isec_input_basenames(list(metadata.get("tokens", [])))
    if not input_basenames:
        return {"consumer_indexes": tuple(), "output_target": ""}

    consumer_indexes: list[int] = []
    output_target = ""
    for idx in range(start_index, len(chain_segments)):
        segment = str(chain_segments[idx] or "").strip()
        if not segment:
            continue
        tokens = _tokenize_shell_segment(segment)
        if len(tokens) < 3 or _token_name(tokens[0]) not in {"cp", "mv"}:
            continue
        source = str(tokens[1]).strip()
        target = str(tokens[2]).strip()
        source_base = Path(source).name
        target_base = Path(target).name
        if not source_base or not target_base:
            continue
        if source_base == target_base:
            continue
        if source_base in input_basenames or _matches_isec_followup_index_move(
            source_base,
            input_basenames=input_basenames,
        ):
            consumer_indexes.append(idx)
            if not output_target and not target_base.endswith(".tbi"):
                output_target = target

    return {
        "consumer_indexes": tuple(consumer_indexes),
        "output_target": output_target,
    }


def _matches_isec_followup_index_move(
    source_basename: str,
    *,
    input_basenames: set[str],
) -> bool:
    """Return whether one basename looks like an index move for an isec input."""

    source_text = str(source_basename or "").strip()
    if not source_text.endswith(".tbi"):
        return False
    return source_text[: -len(".tbi")] in input_basenames


def _collect_bcftools_isec_input_basenames(tokens: list[str]) -> set[str]:
    """Return VCF/BCF input basenames referenced by one ``bcftools isec`` call."""

    basenames: set[str] = set()
    command_start = _bcftools_command_start(tokens)
    idx = (command_start + 2) if command_start is not None else 0
    while idx < len(tokens):
        token = str(tokens[idx]).strip()
        if token == "--":
            break
        previous = str(tokens[idx - 1]).strip() if idx > 0 else ""
        if (
            token.endswith(_VCF_SUFFIXES)
            and previous not in _BCFTOOLS_ISEC_OUTPUT_OPTIONS
            and previous not in _BCFTOOLS_ISEC_PREFIX_OPTIONS
        ):
            basenames.add(Path(token).name)
        idx += 1
    return basenames


def _repair_bcftools_isec_followup_export_segment(
    metadata: dict[str, Any],
    *,
    output_target: str,
    force_unique_prefix: bool = False,
) -> str:
    """Rewrite one ``isec -p`` segment to export directly to a downstream target."""

    tokens = list(metadata["tokens"])
    repair_prefix = (
        _forced_isec_repair_prefix(output_target)
        if force_unique_prefix
        else _choose_isec_repair_prefix(metadata["prefix"], output_target)
    )
    cleaned_tokens = _replace_bcftools_isec_prefix(tokens, repair_prefix)
    cleaned_tokens, staging_commands = _stage_bcftools_isec_plain_vcf_inputs(cleaned_tokens)
    emitted_path = f"{repair_prefix.rstrip('/')}/{metadata['emitted_name']}"
    export_tail = _render_bcftools_isec_export(
        emitted_path=emitted_path,
        output_target=output_target,
    )
    index_tail = _render_bcftools_isec_index(output_target)
    cleanup_tail = _render_bcftools_isec_cleanup(
        original_prefix=metadata["prefix"],
        repair_prefix=repair_prefix,
    )
    parts = [
        *staging_commands,
        " ".join(shlex.quote(token) for token in cleaned_tokens),
        export_tail,
    ]
    if index_tail:
        parts.append(index_tail)
    if cleanup_tail:
        parts.append(cleanup_tail)
    return " && ".join(part for part in parts if part)


def _normalize_shell_segment_text(segment: str) -> str:
    """Normalize one shell segment before token inspection."""

    text = str(segment or "").strip()
    while text.startswith("\\"):
        candidate = text[1:].lstrip()
        if candidate == text:
            break
        text = candidate
    return text


def _tokenize_shell_segment(segment: str) -> list[str]:
    """Return normalized shell tokens for one segment."""

    try:
        raw_tokens = shlex.split(segment, posix=True)
    except Exception:
        return []
    return [normalize_shell_command_token(token) for token in raw_tokens if normalize_shell_command_token(token)]


def _token_name(token: str) -> str:
    """Return a normalized command-like token basename."""

    return Path(normalize_shell_command_token(token)).name.lower()


def _bcftools_command_start(tokens: list[str]) -> int | None:
    """Return the command index for a ``bcftools`` invocation."""

    idx = 0
    while idx < len(tokens) and is_shell_assignment(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return None
    if _token_name(tokens[idx]) == "env":
        idx += 1
        while idx < len(tokens) and is_shell_assignment(tokens[idx]):
            idx += 1
    if idx >= len(tokens):
        return None
    if _token_name(tokens[idx]) == "command":
        idx += 1
    if idx + 1 >= len(tokens):
        return None
    if _token_name(tokens[idx]) != "bcftools":
        return None
    return idx


def _resolve_existing_vcf_input(
    tokens: list[str],
    *,
    cwd: str | Path | None,
) -> Path | None:
    """Resolve the existing VCF input path referenced by a command."""

    base_dir = Path(cwd).expanduser().resolve(strict=False) if cwd else None
    existing_paths: list[Path] = []
    for idx, token in enumerate(tokens):
        value = str(token or "").strip()
        if not value or not value.endswith(_VCF_SUFFIXES):
            continue
        prev = str(tokens[idx - 1]).strip() if idx > 0 else ""
        if prev in _BCFTOOLS_OUTPUT_PATH_OPTIONS:
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            if base_dir is None:
                continue
            candidate = base_dir / candidate
        resolved = candidate.resolve(strict=False)
        if resolved.exists():
            existing_paths.append(resolved)
    return existing_paths[-1] if existing_paths else None


def _inspect_expression_namespaces(
    expression: str,
    *,
    input_vcf: Path,
    subcommand: str,
    option: str,
    inspection: Any,
    auto_repair: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Inspect and optionally repair one ``bcftools`` expression string."""

    text = str(expression or "").strip()
    if not text:
        return [], text

    quoted_spans = _quoted_spans(text)
    info_tags = {str(tag).strip() for tag in getattr(inspection, "info_tags", ())}
    format_tags = {str(tag).strip() for tag in getattr(inspection, "format_tags", ())}
    sample_names = tuple(getattr(inspection, "sample_names", ()) or ())

    replacements: dict[str, str] = {}
    explicit_replacements: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    issues: list[dict[str, Any]] = []
    for match in _IDENTIFIER_RE.finditer(text):
        tag = str(match.group(1) or "").strip()
        if not tag or tag in seen:
            continue
        if _match_inside_quotes(match.start(), quoted_spans):
            continue
        if _looks_like_namespace_prefix(text, match) or _looks_like_function_name(text, match):
            continue
        if tag not in info_tags or tag not in format_tags:
            continue

        seen.add(tag)
        repairable = len(sample_names) == 1 and tag in _SAFE_SINGLE_SAMPLE_INFO_TAGS
        preferred_namespace = "INFO" if repairable else ""
        issues.append(
            {
                "issue": "ambiguous_bcftools_expression_namespace",
                "tag": tag,
                "input_vcf": str(input_vcf),
                "subcommand": subcommand,
                "option": option,
                "expression": text,
                "repairable": repairable,
                "preferred_namespace": preferred_namespace,
                "sample_count": len(sample_names),
                "namespaces": ["INFO", "FORMAT"],
                "reason": (
                    "single_sample_safe_info_namespace"
                    if repairable
                    else (
                        "multi_sample_vcf"
                        if len(sample_names) != 1
                        else "ambiguous_tag_not_in_safe_info_policy"
                    )
                ),
            }
        )
        if auto_repair and repairable:
            replacements[tag] = preferred_namespace

    explicit_seen: set[tuple[str, str]] = set()
    for match in _NAMESPACED_IDENTIFIER_RE.finditer(text):
        namespace = str(match.group("namespace") or "").strip().upper()
        normalized_namespace = "FORMAT" if namespace == "FMT" else namespace
        tag = str(match.group("tag") or "").strip()
        if not tag:
            continue
        if _match_inside_quotes(match.start(), quoted_spans):
            continue
        signature = (normalized_namespace, tag)
        if signature in explicit_seen:
            continue
        explicit_seen.add(signature)

        if normalized_namespace == "INFO":
            namespace_tags = info_tags
            alternate_tags = format_tags
        else:
            namespace_tags = format_tags
            alternate_tags = info_tags
        if tag in namespace_tags or tag not in alternate_tags:
            continue

        repairable = (
            normalized_namespace == "FORMAT"
            and len(sample_names) == 1
            and tag in _SAFE_SINGLE_SAMPLE_INFO_TAGS
        )
        preferred_namespace = "INFO" if repairable else ""
        available_namespace = "INFO" if normalized_namespace == "FORMAT" else "FORMAT"
        issues.append(
            {
                "issue": "missing_bcftools_expression_namespace_field",
                "tag": tag,
                "input_vcf": str(input_vcf),
                "subcommand": subcommand,
                "option": option,
                "expression": text,
                "missing_namespace": normalized_namespace,
                "available_namespace": available_namespace,
                "repairable": repairable,
                "preferred_namespace": preferred_namespace,
                "sample_count": len(sample_names),
                "reason": (
                    "single_sample_safe_info_namespace"
                    if repairable
                    else "alternate_namespace_exists_but_not_safe"
                ),
            }
        )
        if auto_repair and repairable:
            explicit_replacements.append((match.start(), match.end(), f"{preferred_namespace}/{tag}"))

    if not replacements and not explicit_replacements:
        return issues, text
    repaired = _apply_explicit_namespace_replacements(text, explicit_replacements)
    if replacements:
        repaired = _apply_namespace_replacements(repaired, replacements)
    return issues, repaired


def _parse_bcftools_isec_metadata(tokens: list[str], segment_text: str) -> dict[str, Any]:
    """Parse the deterministic output-affecting options of one isec segment."""

    command_start = _bcftools_command_start(tokens)
    prefix = ""
    output_target = ""
    output_type = ""
    write_index_value = ""
    bcftools_tokens = list(tokens)
    idx = command_start + 2
    while idx < len(tokens):
        token = str(tokens[idx]).strip()
        if token == "--":
            break
        if token in _BCFTOOLS_ISEC_PREFIX_OPTIONS and idx + 1 < len(tokens):
            prefix = str(tokens[idx + 1]).strip()
            idx += 2
            continue
        if token.startswith("--prefix="):
            prefix = token.partition("=")[2].strip()
            idx += 1
            continue
        if token in _BCFTOOLS_ISEC_OUTPUT_OPTIONS and idx + 1 < len(tokens):
            output_target = str(tokens[idx + 1]).strip()
            idx += 2
            continue
        if token.startswith("--output="):
            output_target = token.partition("=")[2].strip()
            idx += 1
            continue
        if token == "-o" and idx + 1 < len(tokens):
            output_target = str(tokens[idx + 1]).strip()
            idx += 2
            continue
        if token.startswith("-o") and token != "-o":
            output_target = token[2:].strip()
            idx += 1
            continue
        if token in _BCFTOOLS_ISEC_WRITE_OPTIONS and idx + 1 < len(tokens):
            write_index_value = str(tokens[idx + 1]).strip()
            idx += 2
            continue
        if token.startswith("--write="):
            write_index_value = token.partition("=")[2].strip()
            idx += 1
            continue
        if token.startswith("-w") and token != "-w":
            write_index_value = token[2:].strip()
            idx += 1
            continue
        if token in _BCFTOOLS_ISEC_OUTPUT_TYPE_OPTIONS and idx + 1 < len(tokens):
            output_type = str(tokens[idx + 1]).strip()
            idx += 2
            continue
        if token.startswith("--output-type="):
            output_type = token.partition("=")[2].strip()
            idx += 1
            continue
        if token.startswith("-O") and token != "-O":
            output_type = token[2:].strip()
            idx += 1
            continue
        idx += 1

    write_index = _parse_single_isec_write_index(write_index_value)
    emitted_name = _bcftools_isec_emitted_name(write_index, output_type)
    repair_prefix = _choose_isec_repair_prefix(prefix, output_target)
    return {
        "segment": segment_text,
        "tokens": bcftools_tokens,
        "prefix": prefix,
        "prefix_is_selected_root": prefix in {"", "."},
        "output_target": output_target,
        "output_type": output_type,
        "write_index": write_index,
        "emitted_name": emitted_name,
        "plain_vcf_inputs": _collect_bcftools_isec_plain_vcf_inputs(bcftools_tokens),
        "repair_prefix": repair_prefix,
        "repairable_export": bool(output_target and emitted_name and repair_prefix),
    }


def _inspect_bcftools_isec_unique_options(
    tokens: list[str],
    *,
    segment_text: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Detect and canonicalize duplicate non-repeatable ``bcftools isec`` options."""

    occurrences: list[dict[str, Any]] = []
    idx = 0
    while idx < len(tokens):
        occurrence = _parse_bcftools_isec_unique_option_occurrence(tokens, idx)
        if occurrence is None:
            idx += 1
            continue
        occurrences.append(occurrence)
        idx = int(occurrence["end"])

    if not occurrences:
        return [], list(tokens)

    counts: dict[str, int] = {}
    for occurrence in occurrences:
        key = str(occurrence["key"]).strip()
        counts[key] = counts.get(key, 0) + 1
    if all(count <= 1 for count in counts.values()):
        return [], list(tokens)

    last_occurrence_by_key: dict[str, int] = {}
    for occ_index, occurrence in enumerate(occurrences):
        last_occurrence_by_key[str(occurrence["key"]).strip()] = occ_index

    cleaned: list[str] = []
    issues: list[dict[str, Any]] = []
    idx = 0
    occ_index = 0
    while idx < len(tokens):
        occurrence = (
            occurrences[occ_index]
            if occ_index < len(occurrences) and int(occurrences[occ_index]["start"]) == idx
            else None
        )
        if occurrence is None:
            cleaned.append(tokens[idx])
            idx += 1
            continue

        key = str(occurrence["key"]).strip()
        keep_occurrence = occ_index == last_occurrence_by_key[key]
        if keep_occurrence:
            cleaned.extend(tokens[idx : int(occurrence["end"])])
        else:
            issues.append(
                {
                    "issue": "invalid_bcftools_isec_output_mode",
                    "reason": "duplicate_unique_option",
                    "repairable": True,
                    "segment": segment_text,
                    "subcommand": "isec",
                    "option_key": key,
                    "option_token": str(occurrence["token"]).strip(),
                    "option_value": str(occurrence["value"]).strip(),
                }
            )
        idx = int(occurrence["end"])
        occ_index += 1

    return issues, cleaned


def _parse_bcftools_isec_unique_option_occurrence(
    tokens: list[str],
    idx: int,
) -> dict[str, Any] | None:
    """Parse one non-repeatable ``bcftools isec`` option occurrence."""

    token = str(tokens[idx]).strip()
    if not token:
        return None

    if token in _BCFTOOLS_ISEC_UNIQUE_VALUE_OPTION_KEYS and idx + 1 < len(tokens):
        return {
            "key": _BCFTOOLS_ISEC_UNIQUE_VALUE_OPTION_KEYS[token],
            "token": token,
            "value": str(tokens[idx + 1]).strip(),
            "start": idx,
            "end": idx + 2,
        }

    if token.startswith("--") and "=" in token:
        option_name, _, value = token.partition("=")
        key = _BCFTOOLS_ISEC_UNIQUE_VALUE_OPTION_KEYS.get(option_name)
        if key:
            return {
                "key": key,
                "token": option_name,
                "value": str(value).strip(),
                "start": idx,
                "end": idx + 1,
            }
        return None

    if token.startswith("-") and not token.startswith("--") and len(token) > 2:
        option_name = token[:2]
        key = _BCFTOOLS_ISEC_UNIQUE_VALUE_OPTION_KEYS.get(option_name)
        if key:
            return {
                "key": key,
                "token": option_name,
                "value": str(token[2:]).lstrip("=").strip(),
                "start": idx,
                "end": idx + 1,
            }
    return None


def _render_bcftools_isec_pipeline_segment(left_tokens: list[str], right_segments: list[str]) -> str:
    """Render one canonicalized ``bcftools isec`` segment plus any pipeline tail."""

    parts = [" ".join(shlex.quote(token) for token in left_tokens)]
    parts.extend(str(segment or "").strip() for segment in right_segments if str(segment or "").strip())
    return " | ".join(part for part in parts if part)


def _parse_single_isec_write_index(raw_value: str) -> int:
    """Return a single write index when ``-w`` unambiguously selects one file."""

    text = str(raw_value or "").strip()
    if not text or "," in text:
        return 0
    if not text.isdigit():
        return 0
    value = int(text)
    return value if value > 0 else 0


def _bcftools_isec_emitted_name(write_index: int, output_type: str) -> str:
    """Return the concrete filename emitted by ``bcftools isec -p``."""

    if write_index <= 0:
        return ""
    normalized_type = str(output_type or "").strip().lower()[:1]
    suffix = ".vcf"
    if normalized_type == "z":
        suffix = ".vcf.gz"
    elif normalized_type in {"b", "u"}:
        suffix = ".bcf"
    return f"{write_index - 1:04d}{suffix}"


def _choose_isec_repair_prefix(prefix: str, output_target: str) -> str:
    """Return a deterministic prefix directory for repaired ``isec`` exports."""

    normalized_prefix = str(prefix or "").strip()
    if normalized_prefix and normalized_prefix not in {".", "./"}:
        return normalized_prefix
    target_name = Path(str(output_target or "").strip() or "isec_output").name
    stem = target_name
    for suffix in (".vcf.gz", ".vcf", ".bcf", ".csv", ".tsv", ".txt", ".gz"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem) or "isec_output"
    return f".isec_export_{stem}"


def _forced_isec_repair_prefix(output_target: str) -> str:
    """Return a unique transient prefix regardless of the original prefix."""

    return _choose_isec_repair_prefix(".", output_target)


def _parse_bcftools_isec_pipeline_export(segment: str) -> dict[str, Any]:
    """Return a repairable pipeline export description for one trailing segment."""

    tokens = _tokenize_shell_segment(segment)
    if not tokens:
        return {}
    if _token_name(tokens[0]) != "bgzip":
        return {}
    for idx, token in enumerate(tokens):
        if token not in _SHELL_REDIRECT_TOKENS:
            continue
        if idx + 1 >= len(tokens):
            return {}
        output_target = str(tokens[idx + 1]).strip()
        if not output_target:
            return {}
        if not _bgzip_segment_reads_from_stdin(tokens[:idx]):
            return {}
        return {
            "repairable": True,
            "kind": "bgzip_redirect",
            "output_target": output_target,
        }
    return {}


_BGZIP_VALUE_OPTIONS = frozenset({
    "-@",
    "--threads",
    "-b",
    "--offset",
    "-I",
    "--index-name",
    "-l",
    "--compress-level",
    "-s",
    "--size",
})


def _bgzip_segment_reads_from_stdin(tokens: list[str]) -> bool:
    """Return whether a ``bgzip`` segment consumes stdin and writes to stdout.

    The semantic repair for ``bcftools isec -p`` only needs the redirected
    output target. We accept both ``bgzip -c > out.vcf.gz`` and
    ``bgzip > out.vcf.gz`` shapes as long as the segment does not also pass a
    positional input file that would make the stdin pipeline irrelevant.
    """

    if not tokens or _token_name(tokens[0]) != "bgzip":
        return False

    idx = 1
    while idx < len(tokens):
        token = str(tokens[idx]).strip()
        if not token:
            idx += 1
            continue
        if token in _BGZIP_VALUE_OPTIONS:
            if idx + 1 >= len(tokens):
                return False
            idx += 2
            continue
        if token.startswith("--") and "=" in token:
            option_name, _, _value = token.partition("=")
            if option_name in _BGZIP_VALUE_OPTIONS:
                idx += 1
                continue
        if token.startswith("-@") and token != "-@":
            idx += 1
            continue
        if token.startswith("-") and len(token) > 1:
            idx += 1
            continue
        return False
    return True


def _repair_bcftools_isec_output_segment(metadata: dict[str, Any]) -> str:
    """Rewrite one ``isec -p`` segment to materialize its concrete output file."""

    tokens = list(metadata["tokens"])
    cleaned_tokens = _remove_bcftools_isec_output_target(tokens)
    cleaned_tokens = _replace_bcftools_isec_prefix(cleaned_tokens, metadata["repair_prefix"])
    cleaned_tokens, staging_commands = _stage_bcftools_isec_plain_vcf_inputs(cleaned_tokens)
    emitted_path = f"{metadata['repair_prefix'].rstrip('/')}/{metadata['emitted_name']}"
    output_target = str(metadata["output_target"]).strip()

    export_tail = _render_bcftools_isec_export(
        emitted_path=emitted_path,
        output_target=output_target,
    )
    cleanup_tail = _render_bcftools_isec_cleanup(
        original_prefix=metadata["prefix"],
        repair_prefix=metadata["repair_prefix"],
    )
    parts = [
        *staging_commands,
        " ".join(shlex.quote(token) for token in cleaned_tokens),
        export_tail,
    ]
    if cleanup_tail:
        parts.append(cleanup_tail)
    return " && ".join(part for part in parts if part)


def _repair_bcftools_isec_pipeline_segment(
    metadata: dict[str, Any],
    *,
    pipeline_export: dict[str, Any],
) -> str:
    """Rewrite a piped ``isec -p`` segment to export from the prefix artifact."""

    tokens = list(metadata["tokens"])
    output_target = str(pipeline_export["output_target"]).strip()
    repair_prefix = _choose_isec_repair_prefix(metadata["prefix"], output_target)
    cleaned_tokens = _replace_bcftools_isec_prefix(tokens, repair_prefix)
    cleaned_tokens, staging_commands = _stage_bcftools_isec_plain_vcf_inputs(cleaned_tokens)
    emitted_path = f"{repair_prefix.rstrip('/')}/{metadata['emitted_name']}"
    if emitted_path.endswith(".vcf.gz") or emitted_path.endswith(".bcf"):
        export_tail = f"cat {shlex.quote(emitted_path)} > {shlex.quote(output_target)}"
    else:
        export_tail = f"bgzip -c {shlex.quote(emitted_path)} > {shlex.quote(output_target)}"
    cleanup_tail = _render_bcftools_isec_cleanup(
        original_prefix=metadata["prefix"],
        repair_prefix=repair_prefix,
    )
    parts = [
        *staging_commands,
        " ".join(shlex.quote(token) for token in cleaned_tokens),
        export_tail,
    ]
    if cleanup_tail:
        parts.append(cleanup_tail)
    return " && ".join(part for part in parts if part)


def _render_bcftools_isec_prefix_only_segment(
    metadata: dict[str, Any],
    *,
    repair_prefix: str,
) -> str:
    """Rewrite one ``isec -p`` segment to a safe transient prefix only."""

    tokens = list(metadata["tokens"])
    cleaned_tokens = _replace_bcftools_isec_prefix(tokens, repair_prefix)
    cleaned_tokens, staging_commands = _stage_bcftools_isec_plain_vcf_inputs(cleaned_tokens)
    parts = [
        *staging_commands,
        " ".join(shlex.quote(token) for token in cleaned_tokens),
    ]
    return " && ".join(part for part in parts if part)


def _discover_bcftools_isec_followup_consumers(
    chain_segments: list[str],
    *,
    start_index: int,
) -> dict[str, Any]:
    """Return chained consumers of deterministic ``isec`` prefix artifacts."""

    consumer_indexes: list[int] = []
    artifact_names: set[str] = set()
    output_target = ""

    for idx in range(start_index, len(chain_segments)):
        segment = str(chain_segments[idx] or "").strip()
        if not segment:
            continue
        tokens = _tokenize_shell_segment(segment)
        if not tokens:
            continue
        referenced_names = {
            Path(str(token).strip()).name
            for token in tokens
            if _looks_like_bcftools_isec_prefix_artifact(Path(str(token).strip()).name)
        }
        if not referenced_names:
            continue
        consumer_indexes.append(idx)
        artifact_names.update(referenced_names)
        if not output_target:
            output_target = _infer_shell_segment_output_target(tokens)

    return {
        "consumer_indexes": tuple(consumer_indexes),
        "artifact_names": tuple(sorted(artifact_names)),
        "output_target": output_target,
    }


def _looks_like_bcftools_isec_prefix_artifact(basename: str) -> bool:
    """Return whether a basename looks like a deterministic ``isec`` artifact."""

    name = str(basename or "").strip()
    if not name:
        return False
    return bool(re.fullmatch(r"\d{4}\.(?:vcf(?:\.gz)?|bcf)", name))


def _infer_shell_segment_output_target(tokens: list[str]) -> str:
    """Return a concrete output target for one shell segment when obvious."""

    for idx in range(len(tokens) - 1, -1, -1):
        token = str(tokens[idx]).strip()
        if token in _SHELL_REDIRECT_TOKENS and idx + 1 < len(tokens):
            return str(tokens[idx + 1]).strip()
        for redirect in _SHELL_REDIRECT_TOKENS:
            if token.startswith(redirect) and token != redirect:
                return str(token[len(redirect) :]).strip()

    if len(tokens) >= 3 and _token_name(tokens[0]) in {"mv", "cp"}:
        return str(tokens[2]).strip()
    return ""


def _rewrite_bcftools_isec_followup_consumer_segment(
    segment: str,
    *,
    artifact_names: set[str],
    original_prefix: str,
    repair_prefix: str,
) -> str:
    """Rewrite one chained consumer to read from the repaired ``isec`` prefix."""

    tokens = _tokenize_shell_segment(segment)
    if not tokens:
        return str(segment or "").strip()

    normalized_original_prefix = str(original_prefix or "").strip().rstrip("/")
    rewritten: list[str] = []
    changed = False
    for token in tokens:
        normalized = normalize_shell_command_token(token)
        candidate = str(normalized).strip()
        basename = Path(candidate).name
        parent = str(Path(candidate).parent).strip()
        if basename in artifact_names and parent in {"", "."}:
            rewritten.append(f"{repair_prefix.rstrip('/')}/{basename}")
            changed = True
            continue
        if (
            basename in artifact_names
            and normalized_original_prefix
            and candidate.startswith(f"{normalized_original_prefix}/")
        ):
            rewritten.append(f"{repair_prefix.rstrip('/')}/{basename}")
            changed = True
            continue
        rewritten.append(candidate)
    if not changed:
        return str(segment or "").strip()
    return " ".join(shlex.quote(token) for token in rewritten)


def _collect_bcftools_isec_plain_vcf_inputs(tokens: list[str]) -> list[str]:
    """Return uncompressed VCF inputs referenced by one ``bcftools isec`` call."""

    inputs: list[str] = []
    command_start = _bcftools_command_start(tokens)
    idx = (command_start + 2) if command_start is not None else 0
    while idx < len(tokens):
        token = str(tokens[idx]).strip()
        if token == "--":
            break
        previous = str(tokens[idx - 1]).strip() if idx > 0 else ""
        if (
            token.endswith(".vcf")
            and not token.endswith(".vcf.gz")
            and previous not in _BCFTOOLS_ISEC_OUTPUT_OPTIONS
        ):
            inputs.append(token)
        idx += 1
    deduped: list[str] = []
    seen: set[str] = set()
    for item in inputs:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _stage_bcftools_isec_plain_vcf_inputs(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Rewrite plain ``.vcf`` isec inputs to staged ``.vcf.gz`` plus indexes."""

    plain_inputs = _collect_bcftools_isec_plain_vcf_inputs(tokens)
    if not plain_inputs:
        return tokens, []

    replacements = {path: f"{path}.gz" for path in plain_inputs}
    rewritten = [replacements.get(str(token).strip(), str(token).strip()) for token in tokens]
    staging_commands = [
        (
            f"if [ ! -f {shlex.quote(path + '.gz')} ] || "
            f"[ {shlex.quote(path)} -nt {shlex.quote(path + '.gz')} ]; then "
            f"bgzip -c {shlex.quote(path)} > {shlex.quote(path + '.gz')}; fi"
        )
        for path in plain_inputs
    ]
    staging_commands.extend(
        f"tabix -f {shlex.quote(path + '.gz')}"
        for path in plain_inputs
    )
    return rewritten, staging_commands


def _remove_bcftools_isec_output_target(tokens: list[str]) -> list[str]:
    """Remove ``-o/--output`` from an ``isec`` token list."""

    cleaned: list[str] = []
    idx = 0
    while idx < len(tokens):
        token = str(tokens[idx]).strip()
        if token in _BCFTOOLS_ISEC_OUTPUT_OPTIONS:
            idx += 2
            continue
        if token.startswith("--output="):
            idx += 1
            continue
        if token.startswith("-o") and token != "-o":
            idx += 1
            continue
        cleaned.append(token)
        idx += 1
    return cleaned


def _replace_bcftools_isec_prefix(tokens: list[str], replacement_prefix: str) -> list[str]:
    """Replace the ``-p/--prefix`` value in one ``isec`` token list."""

    cleaned: list[str] = []
    idx = 0
    replaced = False
    while idx < len(tokens):
        token = str(tokens[idx]).strip()
        if token in _BCFTOOLS_ISEC_PREFIX_OPTIONS and idx + 1 < len(tokens):
            cleaned.extend([token, replacement_prefix])
            idx += 2
            replaced = True
            continue
        if token.startswith("--prefix="):
            cleaned.append(f"--prefix={replacement_prefix}")
            idx += 1
            replaced = True
            continue
        cleaned.append(token)
        idx += 1
    if not replaced:
        cleaned.extend(["-p", replacement_prefix])
    return cleaned


def _render_bcftools_isec_export(*, emitted_path: str, output_target: str) -> str:
    """Render the concrete export command for one repaired ``isec`` output."""

    if emitted_path.endswith(".vcf") and output_target.endswith(".vcf.gz"):
        return f"bgzip -c {shlex.quote(emitted_path)} > {shlex.quote(output_target)}"
    return f"mv -f {shlex.quote(emitted_path)} {shlex.quote(output_target)}"


def _render_bcftools_isec_cleanup(*, original_prefix: str, repair_prefix: str) -> str:
    """Render cleanup for synthetic prefix directories only."""

    normalized_original = str(original_prefix or "").strip()
    normalized_repair = str(repair_prefix or "").strip()
    if not normalized_repair or normalized_repair == normalized_original:
        return ""
    return f"rm -rf {shlex.quote(normalized_repair)}"


def _render_bcftools_isec_index(output_target: str) -> str:
    """Render index creation for deterministic exported ``isec`` outputs."""

    target = str(output_target or "").strip()
    if not target:
        return ""
    if target.endswith(".vcf.gz"):
        return f"tabix -f -p vcf {shlex.quote(target)}"
    if target.endswith(".bcf"):
        return f"bcftools index -f {shlex.quote(target)}"
    return ""


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Return inclusive-exclusive spans that fall inside shell-style quotes."""

    spans: list[tuple[int, int]] = []
    quote_char = ""
    start = -1
    escape = False
    for idx, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if quote_char:
            if char == quote_char:
                spans.append((start, idx + 1))
                quote_char = ""
                start = -1
            continue
        if char in {"'", '"'}:
            quote_char = char
            start = idx
    if quote_char and start >= 0:
        spans.append((start, len(text)))
    return spans


def _match_inside_quotes(position: int, spans: list[tuple[int, int]]) -> bool:
    """Return whether a character offset is inside a quoted span."""

    return any(start <= position < end for start, end in spans)


def _looks_like_namespace_prefix(text: str, match: re.Match[str]) -> bool:
    """Return whether a token is already part of a namespace prefix."""

    start = match.start()
    end = match.end()
    if start > 0 and text[start - 1] == "/":
        return True
    if end < len(text) and text[end] == "/":
        return True
    return str(match.group(1) or "") in {"INFO", "FORMAT", "FMT"}


def _looks_like_function_name(text: str, match: re.Match[str]) -> bool:
    """Return whether a token is used as a function name."""

    idx = match.end()
    while idx < len(text) and text[idx].isspace():
        idx += 1
    return idx < len(text) and text[idx] == "("


def _apply_namespace_replacements(expression: str, replacements: dict[str, str]) -> str:
    """Apply namespace prefixes to bare tag tokens in one expression."""

    quoted_spans = _quoted_spans(expression)
    rebuilt: list[str] = []
    last = 0
    for match in _IDENTIFIER_RE.finditer(expression):
        tag = str(match.group(1) or "").strip()
        if tag not in replacements:
            continue
        if _match_inside_quotes(match.start(), quoted_spans):
            continue
        if _looks_like_namespace_prefix(expression, match) or _looks_like_function_name(expression, match):
            continue
        rebuilt.append(expression[last:match.start()])
        rebuilt.append(f"{replacements[tag]}/{tag}")
        last = match.end()
    if last == 0:
        return expression
    rebuilt.append(expression[last:])
    return "".join(rebuilt)


def _collapse_shell_line_continuations(command: str) -> str:
    """Collapse shell line-continuation markers after command repair."""

    return re.sub(r"\\\s*\n\s*", " ", str(command or "")).strip()


def _apply_explicit_namespace_replacements(
    expression: str,
    replacements: list[tuple[int, int, str]],
) -> str:
    """Apply direct span replacements for explicit namespace corrections."""

    if not replacements:
        return expression
    rebuilt: list[str] = []
    last = 0
    for start, end, replacement in sorted(replacements, key=lambda item: item[0]):
        if start < last:
            continue
        rebuilt.append(expression[last:start])
        rebuilt.append(replacement)
        last = end
    rebuilt.append(expression[last:])
    return "".join(rebuilt)


__all__ = [
    "inspect_bcftools_expression_command",
    "inspect_bcftools_isec_command",
    "repair_bcftools_expression_command",
    "repair_bcftools_isec_command",
]
