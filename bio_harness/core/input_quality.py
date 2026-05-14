"""Deterministic input-quality scanning helpers.

This module provides standalone preflight scanning for planned input files.
The initial implementation is intentionally side-effect free so it can be
validated independently before any runner integration.
"""

from __future__ import annotations

import gzip
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO

from bio_harness.core.shell_output_hints import extract_shell_output_hints
from bio_harness.core.shell_parse import is_shell_assignment, should_ignore_command_token, split_shell_segments
from bio_harness.core.tabular_io import load_delimited_dict_rows
from bio_harness.core.tool_env import pixi_env_bin_dirs, which_with_pixi


@dataclass(frozen=True)
class InputIssue:
    """One detected input problem or caution.

    Attributes:
        path: Input path associated with the issue.
        severity: Either `error` or `warning`.
        category: Stable machine-readable issue identifier.
        message: Human-readable description of the issue.
        suggestion: Actionable remediation guidance.
    """

    path: str
    severity: str
    category: str
    message: str
    suggestion: str


@dataclass(frozen=True)
class InputScanResult:
    """Aggregated result of scanning planned inputs.

    Attributes:
        issues: All issues detected across scanned inputs.
        has_blocking: Whether any issue has severity `error`.
        summary: One-line summary for reports and logs.
    """

    issues: tuple[InputIssue, ...]
    has_blocking: bool
    summary: str


@dataclass(frozen=True)
class _FastqSummary:
    """Internal FASTQ summary used by multiple preflight checks."""

    read_count: int
    mean_quality: float
    min_read_length: int
    max_read_length: int
    adapter_fraction: float
    truncated: bool
    format_error: bool
    unusual_quality_encoding: bool


@dataclass(frozen=True)
class _ReferenceSummary:
    """Internal FASTA summary used by preflight checks."""

    contigs: tuple[str, ...]
    total_bases: int
    n_fraction: float
    multiline: bool
    empty: bool


@dataclass(frozen=True)
class _AlignmentSummary:
    """Internal SAM/BAM summary used by preflight checks."""

    sort_order: str
    contigs: tuple[str, ...]
    total_reads: int
    truncated: bool


_FASTQ_SUFFIXES = (".fastq", ".fastq.gz", ".fq", ".fq.gz")
_FASTA_SUFFIXES = (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")
_BAM_SUFFIXES = (".bam", ".sam")
_VCF_SUFFIXES = (".vcf", ".vcf.gz")
_ANNOTATION_SUFFIXES = (".gff", ".gff3", ".gtf")
_METADATA_SUFFIXES = (".csv", ".tsv", ".txt")
_ASSAY_AUXILIARY_TEXT_ARGUMENT_HINTS = frozenset(
    {
        "whitelist",
        "barcode_whitelist",
        "cell_whitelist",
        "feature_whitelist",
        "chemistry_whitelist",
    }
)
_ASSAY_AUXILIARY_TEXT_FILENAMES = frozenset(
    {
        "barcodes_whitelist.txt",
        "whitelist.txt",
    }
)
_COUNT_MATRIX_ARGUMENT_HINTS = frozenset(
    {
        "counts",
        "counts_matrix",
        "count_matrix",
        "feature_matrix",
        "feature_table",
        "peak_table",
        "abundance_matrix",
        "intensity_matrix",
        "metabolite_matrix",
        "expression_matrix",
        "gene_counts",
    }
)
_SAMPLE_ALIASES = {"sample", "sample_id", "samplename", "sample_name"}
_CONDITION_ALIASES = {"condition", "group", "phenotype", "treatment"}
_ADAPTER_PREFIXES = ("AGATCGGAAGAG",)
_SHELL_REDIRECT_TOKENS = frozenset({">", ">>", "1>", "1>>", "2>", "2>>", "<", "0<"})


def scan_plan_inputs(
    plan: dict[str, Any],
    data_root: Path,
    selected_dir: Path | None = None,
    analysis_type: str = "",
) -> InputScanResult:
    """Scan input files referenced by a plan.

    Args:
        plan: Structured plan dictionary.
        data_root: Task data root for plan-relative path resolution.
        selected_dir: Optional run output directory used to exclude outputs.
        analysis_type: Analysis type for metadata-specific validation rules.

    Returns:
        Aggregate scan result for all detected inputs.
    """

    extracted = _extract_input_paths_from_plan(
        plan,
        data_root,
        selected_dir=selected_dir,
    )
    selected_root = Path(selected_dir).expanduser().resolve(strict=False) if selected_dir else None
    seen: set[str] = set()
    issues: list[InputIssue] = []
    fastq_samples: set[str] = set()
    fastq_by_sample: dict[str, list[tuple[Path, _FastqSummary]]] = {}
    reference_summaries: dict[Path, _ReferenceSummary] = {}
    alignment_summaries: dict[Path, _AlignmentSummary] = {}

    for arg_name, path in extracted:
        resolved = path.expanduser().resolve(strict=False)
        if selected_root and _is_within(resolved, selected_root):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        kind = _classify_input_kind(arg_name, resolved)
        if kind == "fastq":
            sample_name = _sample_name_from_fastq(resolved)
            summary = _scan_fastq_summary(resolved)
            fastq_samples.add(sample_name)
            fastq_by_sample.setdefault(sample_name, []).append((resolved, summary))
            issues.extend(_fastq_issues_from_summary(resolved, summary))
        elif kind == "reference_fasta":
            summary = _scan_reference_summary(resolved)
            reference_summaries[resolved] = summary
            issues.extend(_reference_issues_from_summary(resolved, summary))
        elif kind == "alignment":
            summary = _scan_alignment_summary(resolved)
            alignment_summaries[resolved] = summary
            issues.extend(_alignment_issues_from_summary(resolved, summary))
        elif kind == "count_matrix":
            issues.extend(_scan_count_matrix_input(resolved))
        elif kind == "assay_auxiliary_text":
            issues.extend(_scan_assay_auxiliary_text_input(resolved))
        elif kind == "metadata_table":
            issues.extend(scan_metadata_table(resolved, analysis_type=analysis_type))
        elif kind == "annotation":
            issues.extend(scan_annotation_input(resolved))
        elif kind == "vcf":
            issues.extend(scan_vcf_input(resolved))

    issues.extend(_scan_paired_fastq_consistency(fastq_by_sample))
    metadata_paths = [
        path.expanduser().resolve(strict=False)
        for arg_name, path in extracted
        if _classify_input_kind(arg_name, path) == "metadata_table"
    ]
    expected_sample_names: list[str] = []
    for arg_name, path in extracted:
        if _classify_input_kind(arg_name, path) != "count_matrix":
            continue
        expected_sample_names.extend(_count_matrix_sample_names(path))
    seen_metadata_paths = {str(path) for path in metadata_paths}
    issues = [
        issue
        for issue in issues
        if not (
            issue.category in {"missing_required_column", "insufficient_samples", "unknown_condition_value", "duplicate_sample_ids"}
            and issue.path in seen_metadata_paths
        )
    ]
    for metadata_path in metadata_paths:
        issues.extend(
            scan_metadata_table(
                metadata_path,
                analysis_type=analysis_type,
                expected_sample_names=expected_sample_names or None,
            )
        )
    for metadata_path in metadata_paths:
        issues.extend(_scan_metadata_sample_alignment(metadata_path, fastq_samples))
    issues.extend(_scan_alignment_reference_consistency(alignment_summaries, reference_summaries))

    blocking = any(issue.severity == "error" for issue in issues)
    if not issues:
        summary = "No input quality issues detected."
    else:
        summary = f"Detected {len(issues)} input issue(s); blocking={str(blocking).lower()}."
    return InputScanResult(issues=tuple(issues), has_blocking=blocking, summary=summary)


def scan_fastq_input(path: Path) -> list[InputIssue]:
    """Scan a FASTQ input for obvious quality problems.

    Args:
        path: FASTQ path to inspect.

    Returns:
        Zero or more input issues for the file.
    """

    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.exists():
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="missing_file",
                message="FASTQ file does not exist.",
                suggestion="Check the input path and ensure the FASTQ is present.",
            )
        ]
    return _fastq_issues_from_summary(resolved, _scan_fastq_summary(resolved))


def scan_reference_fasta(path: Path) -> list[InputIssue]:
    """Scan a reference FASTA for obvious structural problems.

    Args:
        path: FASTA path to inspect.

    Returns:
        Zero or more input issues for the file.
    """

    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.exists():
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="missing_file",
                message="Reference FASTA does not exist.",
                suggestion="Check the reference path and ensure the FASTA is staged.",
            )
        ]
    return _reference_issues_from_summary(resolved, _scan_reference_summary(resolved))


def scan_bam_input(path: Path) -> list[InputIssue]:
    """Scan a BAM input for missing prerequisites and obvious corruption.

    Args:
        path: BAM path to inspect.

    Returns:
        Zero or more input issues for the file.
    """

    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.exists():
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="missing_file",
                message="BAM file does not exist.",
                suggestion="Check the alignment input path.",
            )
        ]
    return _alignment_issues_from_summary(resolved, _scan_alignment_summary(resolved))


def scan_metadata_table(
    path: Path,
    analysis_type: str = "",
    expected_sample_names: list[str] | None = None,
) -> list[InputIssue]:
    """Scan a metadata table for required columns and contrast readiness.

    Args:
        path: Metadata table path.
        analysis_type: Optional analysis type for DE-specific checks.
        expected_sample_names: Optional paired matrix sample names.

    Returns:
        Zero or more input issues for the metadata table.
    """

    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        columns, rows, delimiter = load_delimited_dict_rows(resolved)
    except Exception as exc:
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="parse_failure",
                message=f"Cannot parse metadata table: {exc}",
                suggestion="Check the delimiter and header row of the metadata file.",
            )
        ]

    issues: list[InputIssue] = []
    suffix = resolved.suffix.lower()
    if (suffix == ".csv" and delimiter == "\t") or (suffix == ".tsv" and delimiter == ","):
        issues.append(
            InputIssue(
                path=str(resolved),
                severity="warning",
                category="delimiter_mismatch",
                message=f"Metadata extension {suffix} does not match detected delimiter.",
                suggestion="Rename the file or rewrite it with a matching delimiter.",
            )
        )
    sample_col = _find_alias(columns, _SAMPLE_ALIASES)
    if not sample_col:
        sample_col = _infer_sample_column_from_expected_names(columns, rows, expected_sample_names)
    if not sample_col:
        issues.append(
            InputIssue(
                path=str(resolved),
                severity="error",
                category="missing_required_column",
                message=f"No sample column found. Columns: {columns}",
                suggestion="Add a column named sample, sample_id, or sample_name.",
            )
        )
    if not rows:
        issues.append(
            InputIssue(
                path=str(resolved),
                severity="error",
                category="empty_file",
                message="Metadata table has zero data rows.",
                suggestion="Add sample rows to the metadata file.",
            )
        )
        return issues

    if _requires_condition_column(analysis_type):
        condition_col = _find_alias(columns, _CONDITION_ALIASES)
        if not condition_col:
            condition_col = _infer_condition_column(columns)
        if not condition_col:
            issues.append(
                InputIssue(
                    path=str(resolved),
                    severity="error",
                    category="missing_required_column",
                    message=f"DE analysis requires a condition column. Columns: {columns}",
                    suggestion="Add a column named condition, group, phenotype, or treatment.",
                )
            )
        else:
            values = {
                str(row.get(condition_col, "")).strip()
                for row in rows
                if str(row.get(condition_col, "")).strip()
            }
            if len(rows) < 2 or len(values) < 2:
                issues.append(
                    InputIssue(
                        path=str(resolved),
                        severity="error",
                        category="insufficient_samples",
                        message=(
                            f"Condition column '{condition_col}' has only {len(values)} unique value(s): "
                            f"{sorted(values)}"
                        ),
                        suggestion="Provide at least two samples spanning two groups so a contrast can be formed.",
                    )
                )
            if "unknown" in {value.lower() for value in values}:
                issues.append(
                    InputIssue(
                        path=str(resolved),
                        severity="warning",
                        category="unknown_condition_value",
                        message="Condition column contains 'unknown' values.",
                        suggestion="Replace 'unknown' with real biological group labels if possible.",
                    )
                )
    if sample_col:
        normalized_samples = [
            _normalize_sample_name(str(row.get(sample_col, "")).strip())
            for row in rows
            if str(row.get(sample_col, "")).strip()
        ]
        if len(normalized_samples) != len(set(normalized_samples)):
            issues.append(
                InputIssue(
                    path=str(resolved),
                    severity="error",
                    category="duplicate_sample_ids",
                    message="Metadata contains duplicate sample identifiers.",
                    suggestion="Ensure every metadata row has a unique sample identifier.",
                )
            )
    return issues


def scan_annotation_input(path: Path) -> list[InputIssue]:
    """Scan a GFF/GTF annotation file for gross formatting errors.

    Args:
        path: Annotation path to inspect.

    Returns:
        Zero or more formatting issues.
    """

    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        with _open_text_auto(resolved) as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if len(raw_line.rstrip("\n").split("\t")) < 9:
                    return [
                        InputIssue(
                            path=str(resolved),
                            severity="error",
                            category="format_mismatch",
                            message="Annotation file does not contain the expected 9-column GFF/GTF layout.",
                            suggestion="Use a valid GFF3/GTF annotation file.",
                        )
                    ]
                return []
    except OSError as exc:
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="read_failure",
                message=f"Could not read annotation file: {exc}",
                suggestion="Verify the annotation file is readable.",
            )
        ]
    return []


def scan_vcf_input(path: Path) -> list[InputIssue]:
    """Scan a VCF input for basic header sanity.

    Args:
        path: VCF path to inspect.

    Returns:
        Zero or more structural issues.
    """

    resolved = Path(path).expanduser().resolve(strict=False)
    saw_fileformat = False
    saw_header = False
    try:
        with _open_text_auto(resolved) as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("##fileformat=VCF"):
                    saw_fileformat = True
                    continue
                if line.startswith("#CHROM\t"):
                    saw_header = True
                    break
                if line.startswith("#"):
                    continue
                break
    except OSError as exc:
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="read_failure",
                message=f"Could not read VCF input: {exc}",
                suggestion="Verify the VCF is readable and not corrupt.",
            )
        ]
    if not saw_fileformat or not saw_header:
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="malformed_header",
                message="VCF is missing the required fileformat line or column header.",
                suggestion="Ensure the VCF begins with a valid ##fileformat line and #CHROM header.",
            )
        ]
    return []


def _extract_input_paths_from_plan(
    plan: dict[str, Any],
    data_root: Path,
    *,
    selected_dir: Path | None = None,
) -> list[tuple[str, Path]]:
    """Collect existing path-like arguments from a plan.

    Args:
        plan: Structured plan dictionary.
        data_root: Base directory used to resolve plan-relative paths.

    Returns:
        Ordered list of `(argument_name, path)` tuples for existing paths.
    """

    discovered: list[tuple[str, Path]] = []
    data_root_resolved = Path(data_root).expanduser().resolve(strict=False)
    selected_root = Path(selected_dir).expanduser().resolve(strict=False) if selected_dir else None
    for step in (plan or {}).get("plan", []):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        arguments = step.get("arguments", {})
        if not isinstance(arguments, dict):
            continue
        for arg_name, value in arguments.items():
            arg_name_text = str(arg_name or "").strip()
            if tool_name == "bash_run" and arg_name_text.lower() == "command":
                discovered.extend(
                    _collect_command_path_values(
                        arg_name_text,
                        value,
                        data_root_resolved,
                        selected_dir=selected_root,
                    )
                )
                continue
            discovered.extend(_collect_path_values(str(arg_name), value, data_root_resolved))
    return discovered


def _collect_path_values(arg_name: str, value: Any, data_root: Path) -> list[tuple[str, Path]]:
    """Collect existing paths from one argument value."""

    if isinstance(value, dict):
        found: list[tuple[str, Path]] = []
        for nested_key, nested_value in value.items():
            found.extend(_collect_path_values(str(nested_key), nested_value, data_root))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_collect_path_values(arg_name, item, data_root))
        return found
    if not isinstance(value, (str, os.PathLike)):
        return []

    token = str(value).strip()
    if not _looks_like_path_token(token):
        return []
    try:
        candidate = Path(token).expanduser()
    except (OSError, RuntimeError, ValueError):
        return []
    if not candidate.is_absolute():
        candidate = data_root / candidate
    try:
        exists = candidate.exists()
    except OSError:
        return []
    if exists:
        return [(arg_name, candidate)]
    return []


def _looks_like_path_token(token: str) -> bool:
    """Return whether one raw string token looks like a filesystem path."""

    text = str(token or "").strip()
    if len(text) < 3:
        return False
    if any(char in text for char in ("\n", "\r", "\t")):
        return False
    if any(marker in text for marker in ("&&", "||", ";", "|", ">", "<", "$(")):
        return False
    if any(char.isspace() for char in text) and not text.startswith(("/", "./", "../", "~/")):
        return False
    if text.startswith("~") and not text.startswith(("~/", "~\\")):
        return False
    if text.startswith(("/", "./", "../", "~/")):
        return True
    suffix = Path(text).suffix.lower()
    if suffix in (_FASTQ_SUFFIXES + _FASTA_SUFFIXES + _BAM_SUFFIXES + _VCF_SUFFIXES + _ANNOTATION_SUFFIXES + _METADATA_SUFFIXES):
        return True
    return False


def _collect_command_path_values(
    arg_name: str,
    value: Any,
    data_root: Path,
    *,
    selected_dir: Path | None,
) -> list[tuple[str, Path]]:
    """Collect existing input paths embedded inside one shell command."""

    if not isinstance(value, (str, os.PathLike)):
        return []
    command = str(value or "").strip()
    if not command:
        return []

    output_hints = extract_shell_output_hints(command)
    declared_outputs = {
        _normalize_command_path_candidate(candidate)
        for candidate in (
            list(output_hints.output_paths)
            + list(output_hints.output_roots)
        )
        if _normalize_command_path_candidate(candidate)
    }

    discovered: list[tuple[str, Path]] = []
    for segment in split_shell_segments(command):
        shell_segment = str(segment or "").strip()
        if not shell_segment:
            continue
        try:
            tokens = shlex.split(shell_segment, posix=True)
        except ValueError:
            tokens = shell_segment.split()
        idx = 0
        while idx < len(tokens):
            token = str(tokens[idx] or "").strip()
            if not token:
                idx += 1
                continue
            if token in _SHELL_REDIRECT_TOKENS:
                idx += 2
                continue
            if any(token.startswith(prefix) and token != prefix for prefix in _SHELL_REDIRECT_TOKENS):
                idx += 1
                continue
            if is_shell_assignment(token) or should_ignore_command_token(token):
                idx += 1
                continue

            candidate_token = token
            if token.startswith("--") and "=" in token:
                _flag, candidate_token = token.split("=", 1)
            normalized_candidate = _normalize_command_path_candidate(candidate_token)
            if not normalized_candidate or normalized_candidate in declared_outputs:
                idx += 1
                continue
            if not _looks_like_path_token(normalized_candidate):
                idx += 1
                continue
            resolved = _resolve_existing_command_path(
                normalized_candidate,
                data_root,
                selected_dir=selected_dir,
            )
            if resolved is not None:
                discovered.append((arg_name, resolved))
            idx += 1
    return discovered


def _normalize_command_path_candidate(token: str) -> str:
    """Normalize one shell-token candidate before path detection."""

    return str(token or "").strip().strip("'\"").rstrip(";")


def _resolve_existing_command_path(
    token: str,
    data_root: Path,
    *,
    selected_dir: Path | None,
) -> Path | None:
    """Resolve one shell-derived token to an existing filesystem path."""

    try:
        candidate = Path(token).expanduser()
    except (OSError, RuntimeError, ValueError):
        return None

    probes: list[Path] = []
    if candidate.is_absolute():
        probes.append(candidate)
    else:
        probes.append(data_root / candidate)
        if selected_dir is not None:
            probes.append(selected_dir / candidate)

    for probe in probes:
        try:
            resolved = probe.resolve(strict=False)
        except OSError:
            continue
        try:
            if resolved.exists():
                return resolved
        except OSError:
            continue
    return None


def _scan_metadata_sample_alignment(path: Path, fastq_samples: set[str]) -> list[InputIssue]:
    """Cross-check metadata sample names against observed FASTQ stems."""

    if not fastq_samples:
        return []
    try:
        columns, rows, _ = load_delimited_dict_rows(path)
    except Exception:
        return []
    sample_col = _find_alias(columns, _SAMPLE_ALIASES)
    if not sample_col:
        return []
    metadata_samples = {
        _normalize_sample_name(str(row.get(sample_col, "")).strip())
        for row in rows
        if str(row.get(sample_col, "")).strip()
    }
    normalized_fastq = {_normalize_sample_name(name) for name in fastq_samples if name}
    if metadata_samples and len(normalized_fastq) >= 2 and metadata_samples.isdisjoint(normalized_fastq):
        return [
            InputIssue(
                path=str(path),
                severity="warning",
                category="sample_name_mismatch",
                message="Metadata sample names do not appear to match FASTQ filenames.",
                suggestion="Check that metadata sample identifiers match the sequencing file names.",
            )
        ]
    return []


def _looks_like_fastq(path: Path) -> bool:
    return any(path.name.lower().endswith(suffix) for suffix in _FASTQ_SUFFIXES)


def _looks_like_reference_fasta(path: Path) -> bool:
    return any(path.name.lower().endswith(suffix) for suffix in _FASTA_SUFFIXES)


def _looks_like_bam(path: Path) -> bool:
    return any(path.name.lower().endswith(suffix) for suffix in _BAM_SUFFIXES)


def _looks_like_vcf(path: Path) -> bool:
    return any(path.name.lower().endswith(suffix) for suffix in _VCF_SUFFIXES)


def _looks_like_annotation(path: Path) -> bool:
    return any(path.name.lower().endswith(suffix) for suffix in _ANNOTATION_SUFFIXES)


def _looks_like_metadata_table(path: Path) -> bool:
    return any(path.name.lower().endswith(suffix) for suffix in _METADATA_SUFFIXES)


def _looks_like_assay_auxiliary_text(arg_name: str, path: Path) -> bool:
    """Return whether one text file should be treated as an assay auxiliary."""

    arg_token = str(arg_name or "").strip().lower()
    path_name = path.name.lower()
    if arg_token in _ASSAY_AUXILIARY_TEXT_ARGUMENT_HINTS:
        return True
    if path_name in _ASSAY_AUXILIARY_TEXT_FILENAMES:
        return True
    return "whitelist" in path_name


def _classify_input_kind(arg_name: str, path: Path) -> str:
    """Classify one planned input by argument role and file suffix."""

    arg_token = str(arg_name or "").strip().lower()
    if _looks_like_fastq(path):
        return "fastq"
    if _looks_like_reference_fasta(path):
        return "reference_fasta"
    if _looks_like_bam(path):
        return "alignment"
    if _looks_like_vcf(path):
        return "vcf"
    if _looks_like_annotation(path):
        return "annotation"
    if arg_token in _COUNT_MATRIX_ARGUMENT_HINTS:
        return "count_matrix"
    if _looks_like_assay_auxiliary_text(arg_name, path):
        return "assay_auxiliary_text"
    if _looks_like_metadata_table(path):
        return "metadata_table"
    return "other"


def _scan_assay_auxiliary_text_input(path: Path) -> list[InputIssue]:
    """Scan a lightweight assay-support text file for gross problems."""

    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        with _open_text_auto(resolved) as handle:
            for raw_line in handle:
                if raw_line.strip():
                    return []
    except OSError as exc:
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="read_failure",
                message=f"Could not read assay auxiliary input: {exc}",
                suggestion="Verify the support file is present and readable.",
            )
        ]
    return [
        InputIssue(
            path=str(resolved),
            severity="error",
            category="empty_file",
            message="Assay auxiliary text input is empty.",
            suggestion="Provide a non-empty whitelist or support text file.",
        )
    ]


def _scan_count_matrix_input(path: Path) -> list[InputIssue]:
    """Scan a tabular count matrix without misclassifying it as metadata."""

    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        columns, rows, _delimiter = load_delimited_dict_rows(resolved)
    except Exception as exc:
        return [
            InputIssue(
                path=str(resolved),
                severity="error",
                category="parse_failure",
                message=f"Cannot parse count matrix: {exc}",
                suggestion="Check the delimiter and header row of the count matrix.",
            )
        ]

    issues: list[InputIssue] = []
    if not columns:
        issues.append(
            InputIssue(
                path=str(resolved),
                severity="error",
                category="empty_file",
                message="Count matrix is missing a header row.",
                suggestion="Provide a count matrix with a feature column and sample columns.",
            )
        )
        return issues
    if len(columns) < 2:
        issues.append(
            InputIssue(
                path=str(resolved),
                severity="error",
                category="insufficient_columns",
                message=f"Count matrix has only {len(columns)} column(s): {columns}",
                suggestion="Provide a feature identifier column plus at least one sample column.",
            )
        )
    if not rows:
        issues.append(
            InputIssue(
                path=str(resolved),
                severity="error",
                category="empty_file",
                message="Count matrix has zero data rows.",
                suggestion="Provide at least one quantified feature row.",
            )
        )
    return issues


def _count_matrix_sample_names(path: Path) -> list[str]:
    """Return normalized sample names from one wide count or abundance matrix."""

    resolved = Path(path).expanduser().resolve(strict=False)
    try:
        columns, _rows, _delimiter = load_delimited_dict_rows(resolved)
    except Exception:
        return []
    if len(columns) < 2:
        return []
    return [_normalize_sample_name(column) for column in columns[1:] if str(column).strip()]


def _requires_condition_column(analysis_type: str) -> bool:
    token = str(analysis_type or "").lower()
    return (
        "differential_expression" in token
        or "deseq" in token
        or "proteomics" in token
        or "metabolomics" in token
        or "differential" in token
    )


def _find_alias(columns: list[str], aliases: set[str]) -> str:
    for column in columns:
        if column.lower() in aliases:
            return column
    return ""


def _column_name_tokens(column: str) -> tuple[str, ...]:
    """Return normalized semantic tokens from one metadata header."""

    return tuple(
        token
        for token in re.split(r"[^a-z0-9]+", str(column or "").lower())
        if token
    )


def _infer_condition_column(columns: list[str]) -> str:
    """Infer one condition-like metadata column from semantic header tokens."""

    strong_markers = {"condition", "treatment", "phenotype"}
    soft_marker = "group"
    strong_candidates: list[str] = []
    soft_candidates: list[str] = []
    for column in columns:
        tokens = set(_column_name_tokens(column))
        if not tokens:
            continue
        if tokens & strong_markers:
            strong_candidates.append(column)
            continue
        if tokens == {soft_marker}:
            soft_candidates.append(column)
    if len(strong_candidates) == 1:
        return strong_candidates[0]
    if len(soft_candidates) == 1:
        return soft_candidates[0]
    return ""


def _infer_sample_column_from_expected_names(
    columns: list[str],
    rows: list[dict[str, Any]],
    expected_sample_names: list[str] | None,
) -> str:
    """Infer one metadata sample column from paired matrix sample names."""

    normalized_expected = {
        _normalize_sample_name(str(name).strip())
        for name in (expected_sample_names or [])
        if str(name).strip()
    }
    if not normalized_expected:
        return ""
    scored: list[tuple[int, int, str]] = []
    for column in columns:
        values = [
            _normalize_sample_name(str(row.get(column, "")).strip())
            for row in rows
            if str(row.get(column, "")).strip()
        ]
        if not values:
            continue
        overlap = len(normalized_expected.intersection(values))
        exact = int(overlap == len(normalized_expected) and len(set(values)) >= len(normalized_expected))
        if overlap:
            scored.append((exact, overlap, str(column)))
    if not scored:
        return ""
    scored.sort(reverse=True)
    return scored[0][2]


def _sample_name_from_fastq(path: Path) -> str:
    stem = path.name
    for suffix in _FASTQ_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return _normalize_sample_name(stem)


def _normalize_sample_name(value: str) -> str:
    token = str(value or "").strip()
    token = re.sub(r"([_.-]R[12]|/[12])$", "", token, flags=re.IGNORECASE)
    return token.lower()


def _fastq_issues_from_summary(path: Path, summary: _FastqSummary) -> list[InputIssue]:
    """Render FASTQ issues from one parsed summary."""

    issues: list[InputIssue] = []
    if summary.truncated:
        issues.append(
            InputIssue(
                path=str(path),
                severity="error",
                category="truncated_file",
                message="FASTQ ends mid-record.",
                suggestion="Replace the truncated FASTQ with a complete file.",
            )
        )
        return issues
    if summary.format_error:
        issues.append(
            InputIssue(
                path=str(path),
                severity="error",
                category="fastq_format_error",
                message="FASTQ contains malformed records or mismatched quality lengths.",
                suggestion="Validate the FASTQ structure and regenerate the file if needed.",
            )
        )
        return issues
    if summary.read_count == 0:
        issues.append(
            InputIssue(
                path=str(path),
                severity="error",
                category="empty_file",
                message="FASTQ file has zero reads.",
                suggestion="Provide a non-empty FASTQ file.",
            )
        )
        return issues
    if summary.mean_quality < 15.0:
        issues.append(
            InputIssue(
                path=str(path),
                severity="error",
                category="low_quality",
                message=f"Mean FASTQ quality is very low ({summary.mean_quality:.1f}).",
                suggestion="Check the sequencing data or trim/filter poor-quality reads.",
            )
        )
    if summary.min_read_length < 20:
        issues.append(
            InputIssue(
                path=str(path),
                severity="warning",
                category="short_reads",
                message=f"FASTQ contains very short reads (minimum length {summary.min_read_length}).",
                suggestion="Confirm the downstream tool supports such short reads.",
            )
        )
    if summary.min_read_length != summary.max_read_length:
        issues.append(
            InputIssue(
                path=str(path),
                severity="warning",
                category="mixed_read_lengths",
                message="FASTQ contains mixed read lengths.",
                suggestion="Verify the mixed lengths are expected for this assay.",
            )
        )
    if summary.adapter_fraction >= 0.20:
        issues.append(
            InputIssue(
                path=str(path),
                severity="warning",
                category="adapter_contamination",
                message=f"Adapter sequence detected in {summary.adapter_fraction:.1%} of sampled reads.",
                suggestion="Trim adapters before alignment or quantification.",
            )
        )
    if summary.unusual_quality_encoding:
        issues.append(
            InputIssue(
                path=str(path),
                severity="warning",
                category="unusual_quality_encoding",
                message="FASTQ quality scores appear to use an unusual encoding range.",
                suggestion="Confirm the FASTQ quality encoding is Phred+33.",
            )
        )
    return issues


def _reference_issues_from_summary(path: Path, summary: _ReferenceSummary) -> list[InputIssue]:
    """Render FASTA issues from one parsed summary."""

    issues: list[InputIssue] = []
    if summary.empty:
        issues.append(
            InputIssue(
                path=str(path),
                severity="error",
                category="empty_file",
                message="Reference FASTA contains zero contigs or only whitespace.",
                suggestion="Provide a valid FASTA with at least one sequence record.",
            )
        )
        return issues
    if summary.n_fraction >= 0.25:
        issues.append(
            InputIssue(
                path=str(path),
                severity="warning",
                category="high_n_fraction",
                message=f"Reference FASTA contains a high ambiguous-base fraction ({summary.n_fraction:.1%}).",
                suggestion="Verify the reference sequence quality and masking strategy.",
            )
        )
    if not path.with_suffix(path.suffix + ".fai").exists() and not path.name.endswith(".gz") and not summary.multiline:
        issues.append(
            InputIssue(
                path=str(path),
                severity="warning",
                category="missing_index",
                message="Reference FASTA index (.fai) is missing.",
                suggestion="Run samtools faidx on the reference before indexed lookup steps.",
            )
        )
    if summary.total_bases < 100:
        issues.append(
            InputIssue(
                path=str(path),
                severity="warning",
                category="tiny_reference",
                message=f"Reference FASTA is very small ({summary.total_bases} bases total).",
                suggestion="Confirm that this tiny reference is intentional.",
            )
        )
    return issues


def _alignment_issues_from_summary(path: Path, summary: _AlignmentSummary) -> list[InputIssue]:
    """Render SAM/BAM issues from one parsed summary."""

    issues: list[InputIssue] = []
    if summary.truncated:
        issues.append(
            InputIssue(
                path=str(path),
                severity="error",
                category="truncated_file",
                message="Alignment file is truncated or malformed.",
                suggestion="Regenerate the SAM/BAM file and ensure it was written completely.",
            )
        )
        return issues
    if summary.total_reads == 0:
        issues.append(
            InputIssue(
                path=str(path),
                severity="error",
                category="empty_file",
                message="Alignment file contains zero reads.",
                suggestion="Provide a non-empty alignment file.",
            )
        )
        return issues
    if path.suffix.lower() == ".bam":
        bai_candidates = (Path(str(path) + ".bai"), path.with_suffix(".bai"))
        if not any(candidate.exists() for candidate in bai_candidates):
            issues.append(
                InputIssue(
                    path=str(path),
                    severity="warning",
                    category="missing_index",
                    message="BAM index (.bai) is missing.",
                    suggestion="Run samtools index on the BAM before using indexed tools.",
                )
            )
    if summary.sort_order and summary.sort_order != "coordinate":
        issues.append(
            InputIssue(
                path=str(path),
                severity="warning",
                category="unsorted_bam",
                message=f"Alignment header declares sort order '{summary.sort_order}'.",
                suggestion="Run samtools sort if downstream tools require coordinate-sorted input.",
            )
        )
    return issues


def _scan_paired_fastq_consistency(
    fastq_by_sample: dict[str, list[tuple[Path, _FastqSummary]]]
) -> list[InputIssue]:
    """Check paired FASTQ inputs for read-count mismatches."""

    issues: list[InputIssue] = []
    for _sample_name, entries in fastq_by_sample.items():
        if len(entries) != 2:
            continue
        left, right = entries
        if left[1].read_count != right[1].read_count:
            issues.append(
                InputIssue(
                    path=str(left[0].parent),
                    severity="error",
                    category="read_count_mismatch",
                    message=(
                        f"Paired FASTQ files have different read counts "
                        f"({left[1].read_count} vs {right[1].read_count})."
                    ),
                    suggestion="Ensure R1 and R2 contain matching paired-end read counts.",
                )
            )
    return issues


def _scan_alignment_reference_consistency(
    alignment_summaries: dict[Path, _AlignmentSummary],
    reference_summaries: dict[Path, _ReferenceSummary],
) -> list[InputIssue]:
    """Check whether alignment contigs overlap the available reference."""

    if not alignment_summaries or not reference_summaries:
        return []
    reference_contigs = {
        contig
        for summary in reference_summaries.values()
        for contig in summary.contigs
    }
    issues: list[InputIssue] = []
    for path, summary in alignment_summaries.items():
        if summary.contigs and reference_contigs and not set(summary.contigs).intersection(reference_contigs):
            issues.append(
                InputIssue(
                    path=str(path),
                    severity="error",
                    category="reference_mismatch",
                    message="Alignment references do not match the provided FASTA contig names.",
                    suggestion="Use a matching reference FASTA for the alignment file.",
                )
            )
    return issues


def _scan_fastq_summary(path: Path) -> _FastqSummary:
    """Parse one FASTQ file into a reusable structural summary."""

    read_count = 0
    quality_sum = 0
    quality_count = 0
    min_read_length = 0
    max_read_length = 0
    adapter_hits = 0
    truncated = False
    format_error = False
    unusual_quality_encoding = False
    try:
        with _open_text_auto(path) as handle:
            while read_count < 10000:
                header = handle.readline()
                if not header:
                    break
                sequence = handle.readline()
                plus = handle.readline()
                quality = handle.readline()
                if not sequence or not plus or not quality:
                    truncated = True
                    break
                sequence_text = sequence.strip()
                quality_text = quality.strip()
                if not header.startswith("@") or not plus.startswith("+") or len(sequence_text) != len(quality_text):
                    format_error = True
                    break
                decoded = [ord(char) - 33 for char in quality_text]
                if decoded and max(decoded) > 45:
                    unusual_quality_encoding = True
                read_length = len(sequence_text)
                if read_count == 0:
                    min_read_length = read_length
                    max_read_length = read_length
                else:
                    min_read_length = min(min_read_length, read_length)
                    max_read_length = max(max_read_length, read_length)
                read_count += 1
                quality_sum += sum(decoded)
                quality_count += len(decoded)
                if sequence_text.upper().startswith(_ADAPTER_PREFIXES):
                    adapter_hits += 1
    except OSError:
        return _FastqSummary(
            read_count=0,
            mean_quality=0.0,
            min_read_length=0,
            max_read_length=0,
            adapter_fraction=0.0,
            truncated=True,
            format_error=True,
            unusual_quality_encoding=False,
        )
    mean_quality = float(quality_sum) / float(quality_count) if quality_count else 0.0
    adapter_fraction = float(adapter_hits) / float(read_count) if read_count else 0.0
    return _FastqSummary(
        read_count=read_count,
        mean_quality=mean_quality,
        min_read_length=min_read_length,
        max_read_length=max_read_length,
        adapter_fraction=adapter_fraction,
        truncated=truncated,
        format_error=format_error,
        unusual_quality_encoding=unusual_quality_encoding,
    )


def _scan_reference_summary(path: Path) -> _ReferenceSummary:
    """Parse one FASTA file into a reusable structural summary."""

    contigs: list[str] = []
    total_bases = 0
    n_count = 0
    multiline = False
    sequence_lines = 0
    try:
        with _open_text_auto(path) as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith(">"):
                    contigs.append(line[1:].split()[0])
                    continue
                sequence_lines += 1
                multiline = multiline or sequence_lines > len(contigs)
                total_bases += len(line)
                n_count += line.upper().count("N")
    except OSError:
        return _ReferenceSummary(contigs=(), total_bases=0, n_fraction=0.0, multiline=False, empty=True)
    if not contigs:
        return _ReferenceSummary(contigs=(), total_bases=0, n_fraction=0.0, multiline=multiline, empty=True)
    n_fraction = float(n_count) / float(total_bases) if total_bases else 0.0
    return _ReferenceSummary(
        contigs=tuple(contigs),
        total_bases=total_bases,
        n_fraction=n_fraction,
        multiline=multiline,
        empty=False,
    )


def _scan_alignment_summary(path: Path) -> _AlignmentSummary:
    """Parse one alignment file into a reusable structural summary."""

    if path.suffix.lower() == ".sam":
        return _scan_sam_summary(path)
    return _scan_bam_summary(path)


def _scan_sam_summary(path: Path) -> _AlignmentSummary:
    """Parse a text SAM file."""

    contigs: list[str] = []
    sort_order = ""
    total_reads = 0
    try:
        with _open_text_auto(path) as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("@HD"):
                    for field in raw_line.rstrip("\n").split("\t")[1:]:
                        if field.startswith("SO:"):
                            sort_order = field.split(":", 1)[1]
                    continue
                if line.startswith("@SQ"):
                    for field in raw_line.rstrip("\n").split("\t")[1:]:
                        if field.startswith("SN:"):
                            contigs.append(field.split(":", 1)[1])
                    continue
                fields = raw_line.rstrip("\n").split("\t")
                if len(fields) < 11:
                    return _AlignmentSummary(
                        sort_order=sort_order,
                        contigs=tuple(contigs),
                        total_reads=total_reads,
                        truncated=True,
                    )
                total_reads += 1
    except OSError:
        return _AlignmentSummary(sort_order="", contigs=(), total_reads=0, truncated=True)
    return _AlignmentSummary(sort_order=sort_order, contigs=tuple(contigs), total_reads=total_reads, truncated=False)


def _scan_bam_summary(path: Path) -> _AlignmentSummary:
    """Parse a BAM file with samtools when available."""

    samtools_bin = _resolve_tool("samtools")
    if not samtools_bin:
        return _AlignmentSummary(sort_order="", contigs=(), total_reads=1, truncated=False)
    header_run = _run_tool([samtools_bin, "view", "-H", str(path)], timeout=20)
    idxstats_run = _run_tool([samtools_bin, "idxstats", str(path)], timeout=20)
    if not header_run or header_run.returncode != 0:
        return _AlignmentSummary(sort_order="", contigs=(), total_reads=0, truncated=True)
    sort_order = ""
    contigs: list[str] = []
    for line in header_run.stdout.splitlines():
        if line.startswith("@HD"):
            for field in line.split("\t")[1:]:
                if field.startswith("SO:"):
                    sort_order = field.split(":", 1)[1]
        elif line.startswith("@SQ"):
            for field in line.split("\t")[1:]:
                if field.startswith("SN:"):
                    contigs.append(field.split(":", 1)[1])
    total_reads = 1
    if idxstats_run and idxstats_run.returncode == 0:
        total_reads = 0
        for line in idxstats_run.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) < 4:
                continue
            total_reads += int(_safe_float(fields[2])) + int(_safe_float(fields[3]))
    return _AlignmentSummary(sort_order=sort_order, contigs=tuple(contigs), total_reads=total_reads, truncated=False)


def _resolve_tool(name: str) -> str | None:
    return which_with_pixi(name) or shutil.which(name)


def _tool_env() -> dict[str, str]:
    env = dict(os.environ)
    additions = [str(path) for path in pixi_env_bin_dirs()]
    if not additions:
        return env
    existing = env.get("PATH", "")
    existing_parts = existing.split(os.pathsep) if existing else []
    env["PATH"] = os.pathsep.join(additions + [part for part in existing_parts if part and part not in additions])
    return env


def _run_tool(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=_tool_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _open_text_auto(path: Path) -> IO[str]:
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_float(value: str | float | int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "InputIssue",
    "InputScanResult",
    "scan_bam_input",
    "scan_fastq_input",
    "scan_metadata_table",
    "scan_plan_inputs",
    "scan_reference_fasta",
]
