"""Deterministic output quality assessment helpers.

This module provides lightweight, format-specific quality checks for common
bioinformatics outputs. The checks are intended for post-run inspection and
reporting, so the initial implementation is standalone and does not mutate
execution outcomes inside the runner.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import IO

from bio_harness.core.output_semantic_features import (
    extract_transcript_quant_features,
    extract_vcf_header_payload_features,
)
from bio_harness.core.tabular_io import load_delimited_dict_rows
from bio_harness.core.tool_env import pixi_env_bin_dirs, which_with_pixi


class QualityLevel(str, Enum):
    """Severity levels for output quality checks."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class QualityMetric:
    """One output quality measurement.

    Attributes:
        name: Stable metric identifier.
        value: Measured value.
        level: Severity for the metric.
        message: Human-readable explanation of the measurement.
        threshold: Threshold rule used to classify the measurement.
    """

    name: str
    value: float
    level: QualityLevel
    message: str
    threshold: str


@dataclass(frozen=True)
class QualityReport:
    """Quality assessment for one output file.

    Attributes:
        path: Absolute or user-facing path that was assessed.
        file_type: Detected file type.
        metrics: Individual measurements captured for the file.
        overall_level: Worst severity among the metrics.
        summary: One-line summary suitable for reports.
    """

    path: str
    file_type: str
    metrics: tuple[QualityMetric, ...]
    overall_level: QualityLevel
    summary: str


BAM_MAPPING_RATE_FAIL = 0.05
BAM_MAPPING_RATE_WARN = 0.50
BAM_DUPLICATE_RATE_WARN = 0.20
BAM_MIN_READS_FAIL = 0.0
BAM_LOW_COVERAGE_WARN_READS = 5.0

VCF_MIN_VARIANTS_FAIL = 0.0
VCF_TSTV_RATIO_WARN_LOW = 0.1
VCF_TSTV_RATIO_WARN_HIGH = 10.0
VCF_PASS_FRACTION_WARN = 0.20
VCF_MIN_GQ_WARN = 10.0
VCF_CLUSTER_DISTANCE_WARN = 5

FASTQ_MIN_READS_FAIL = 0.0
FASTQ_MEAN_QUALITY_WARN = 20.0
FASTQ_MEAN_QUALITY_FAIL = 10.0
FASTQ_GC_WARN_LOW = 0.20
FASTQ_GC_WARN_HIGH = 0.80
FASTQ_SHORT_READ_WARN = 20.0

TABULAR_MIN_ROWS_FAIL = 0.0
TABULAR_MIN_COLUMNS_FAIL = 0.0
DE_SIGNIFICANT_ROWS_WARN = 0.0
DE_HIGH_NA_WARN = 0.50
DE_ALL_SIGNIFICANT_WARN_FRACTION = 0.98

_FLAGSTAT_TOTAL_RE = re.compile(r"^\s*(\d+)\s+\+\s+\d+\s+in total")
_FLAGSTAT_MAPPED_RE = re.compile(r"^\s*(\d+)\s+\+\s+\d+\s+mapped\s+\(([\d.]+)%")
_FLAGSTAT_DUP_RE = re.compile(r"^\s*(\d+)\s+\+\s+\d+\s+duplicates")


@dataclass(frozen=True)
class _AlignmentStats:
    """Counts extracted from one alignment artifact."""

    total_reads: float
    mapped_reads: float
    duplicate_reads: float


@dataclass(frozen=True)
class _VcfStats:
    """Lightweight VCF metrics used by deterministic quality checks."""

    variant_count: float
    pass_count: float
    pass_fraction: float
    ts_tv_ratio: float | None
    mean_gq: float | None
    min_distance: int | None


@dataclass(frozen=True)
class _FastqStats:
    """Summarized FASTQ sampling measurements."""

    read_count: int
    total_bases: int
    quality_sum: int
    quality_count: int
    min_read_length: int
    max_read_length: int
    truncated: bool
    format_error: bool


def assess_output_quality(
    path: Path,
    tool_name: str = "",
    analysis_type: str = "",
) -> QualityReport:
    """Assess one output file by detected file type.

    Args:
        path: Output path to inspect.
        tool_name: Optional originating tool name for type-specific rules.
        analysis_type: Optional analysis type for context-specific checks.

    Returns:
        A structured quality report. Unknown file types return `SKIP`.
    """

    file_type = _detect_file_type(path)
    if file_type == "bam":
        return assess_bam_quality(path)
    if file_type == "vcf":
        return assess_vcf_quality(path)
    if file_type == "fastq":
        return assess_fastq_quality(path)
    if file_type == "gtf":
        return assess_gtf_quality(path)
    if file_type in {"csv", "tsv"}:
        return assess_tabular_quality(path, tool_name=tool_name, analysis_type=analysis_type)
    return _build_report(
        path=path,
        file_type=file_type,
        metrics=(
            QualityMetric(
                name="unsupported_format",
                value=0.0,
                level=QualityLevel.SKIP,
                message=f"Unsupported file type for quality assessment: {file_type}",
                threshold="unsupported -> skip",
            ),
        ),
        summary=f"Skipped quality assessment for unsupported file type '{file_type}'.",
    )


def assess_bam_quality(path: Path) -> QualityReport:
    """Assess BAM quality using samtools summaries.

    Args:
        path: BAM path to inspect.

    Returns:
        BAM-focused quality report with read count, mapping rate, and duplicate
        rate metrics.
    """

    resolved = Path(path).expanduser()
    if not resolved.exists() or resolved.stat().st_size == 0:
        return _missing_or_empty_report(resolved, "bam")

    is_sam_input = resolved.suffix.lower() == ".sam"
    samtools_bin = None if is_sam_input else _resolve_tool("samtools")
    if not is_sam_input and not samtools_bin:
        return _skip_report(resolved, "bam", "samtools unavailable")

    stats = _scan_alignment_stats(resolved, samtools_bin=samtools_bin)
    total_reads = stats.total_reads
    mapped_reads = stats.mapped_reads
    duplicate_reads = stats.duplicate_reads
    metrics: list[QualityMetric] = []

    if total_reads <= BAM_MIN_READS_FAIL:
        return _build_report(
            path=resolved,
            file_type="bam",
            metrics=(
                QualityMetric(
                    name="empty_file",
                    value=0.0,
                    level=QualityLevel.FAIL,
                    message="Alignment artifact contains zero reads.",
                    threshold="no reads -> fail",
                ),
            ),
            summary="BAM quality fail: alignment artifact contains zero reads.",
        )

    metrics.append(
        QualityMetric(
            name="total_reads",
            value=total_reads,
            level=QualityLevel.PASS,
            message=f"Alignment artifact contains {int(total_reads)} reads.",
            threshold=f"total_reads <= {BAM_MIN_READS_FAIL} -> fail",
        )
    )

    mapping_rate = (mapped_reads / total_reads) if total_reads > 0 else 0.0
    metrics.append(
        QualityMetric(
            name="mapping_rate",
            value=mapping_rate,
            level=_mapping_rate_level(mapping_rate),
            message=f"Mapping rate is {mapping_rate:.1%}.",
            threshold=(
                f"< {BAM_MAPPING_RATE_FAIL:.0%} -> fail, "
                f"< {BAM_MAPPING_RATE_WARN:.0%} -> warning"
            ),
        )
    )
    metrics.append(
        QualityMetric(
            name="low_mapping_rate",
            value=mapping_rate,
            level=_mapping_rate_level(mapping_rate),
            message=f"Low-mapping-rate check observed {mapping_rate:.1%} mapped reads.",
            threshold=(
                f"< {BAM_MAPPING_RATE_FAIL:.0%} -> fail, "
                f"< {BAM_MAPPING_RATE_WARN:.0%} -> warning"
            ),
        )
    )

    duplicate_rate = (duplicate_reads / total_reads) if total_reads > 0 else 0.0
    metrics.append(
        QualityMetric(
            name="duplicate_rate",
            value=duplicate_rate,
            level=QualityLevel.WARNING if duplicate_rate >= BAM_DUPLICATE_RATE_WARN else QualityLevel.PASS,
            message=f"Duplicate rate is {duplicate_rate:.1%}.",
            threshold=f">= {BAM_DUPLICATE_RATE_WARN:.0%} -> warning",
        )
    )
    metrics.append(
        QualityMetric(
            name="high_duplicate_rate",
            value=duplicate_rate,
            level=QualityLevel.WARNING if duplicate_rate >= BAM_DUPLICATE_RATE_WARN else QualityLevel.PASS,
            message=f"High-duplicate-rate check observed {duplicate_rate:.1%} duplicates.",
            threshold=f">= {BAM_DUPLICATE_RATE_WARN:.0%} -> warning",
        )
    )
    metrics.append(
        QualityMetric(
            name="low_coverage",
            value=total_reads,
            level=QualityLevel.WARNING if total_reads < BAM_LOW_COVERAGE_WARN_READS else QualityLevel.PASS,
            message=f"Coverage proxy uses {int(total_reads)} observed reads.",
            threshold=f"total_reads < {BAM_LOW_COVERAGE_WARN_READS:.0f} -> warning",
        )
    )
    summary = (
        f"BAM quality { _worst_level(tuple(metrics)).value }: "
        f"{int(total_reads)} reads, {mapping_rate:.1%} mapped, "
        f"{duplicate_rate:.1%} duplicates."
    )
    return _build_report(path=resolved, file_type="bam", metrics=tuple(metrics), summary=summary)


def assess_vcf_quality(path: Path) -> QualityReport:
    """Assess VCF quality with bcftools when available and a Python fallback.

    Args:
        path: VCF or VCF.GZ path to inspect.

    Returns:
        VCF-focused quality report with variant count, PASS fraction, and
        ts/tv ratio where available.
    """

    resolved = Path(path).expanduser()
    if not resolved.exists() or resolved.stat().st_size == 0:
        return _missing_or_empty_report(resolved, "vcf")

    vcf_stats = _scan_vcf_records(resolved)
    variant_count = vcf_stats.variant_count
    pass_count = vcf_stats.pass_count
    pass_fraction = vcf_stats.pass_fraction
    ts_tv_ratio = vcf_stats.ts_tv_ratio

    metrics: list[QualityMetric] = [
        QualityMetric(
            name="variant_count",
            value=variant_count,
            level=QualityLevel.FAIL if variant_count <= VCF_MIN_VARIANTS_FAIL else QualityLevel.PASS,
            message=f"VCF contains {int(variant_count)} variant records.",
            threshold=f"variant_count <= {VCF_MIN_VARIANTS_FAIL} -> fail",
        ),
        QualityMetric(
            name="pass_fraction",
            value=pass_fraction,
            level=QualityLevel.FAIL if pass_count <= 0 else QualityLevel.WARNING if pass_fraction < VCF_PASS_FRACTION_WARN else QualityLevel.PASS,
            message=f"{pass_fraction:.1%} of variants are marked PASS.",
            threshold=f"0 PASS -> fail, < {VCF_PASS_FRACTION_WARN:.0%} -> warning",
        ),
        QualityMetric(
            name="pass_variant_count",
            value=pass_count,
            level=QualityLevel.FAIL if pass_count <= 0 else QualityLevel.PASS,
            message=f"VCF contains {int(pass_count)} PASS variants.",
            threshold="pass_variant_count == 0 -> fail",
        ),
        QualityMetric(
            name="pass_rate",
            value=pass_fraction,
            level=QualityLevel.FAIL if pass_count <= 0 else QualityLevel.PASS,
            message=f"PASS rate is {pass_fraction:.1%}.",
            threshold="pass_variant_count == 0 -> fail",
        ),
    ]
    if pass_count <= 0:
        metrics.append(
            QualityMetric(
                name="no_pass_variants",
                value=0.0,
                level=QualityLevel.FAIL,
                message="VCF contains zero PASS variants.",
                threshold="0 PASS variants -> fail",
            )
        )
    if vcf_stats.mean_gq is not None:
        mean_gq = vcf_stats.mean_gq
        level = QualityLevel.WARNING if mean_gq < VCF_MIN_GQ_WARN else QualityLevel.PASS
        metrics.append(
            QualityMetric(
                name="mean_gq",
                value=mean_gq,
                level=level,
                message=f"Mean genotype quality is {mean_gq:.2f}.",
                threshold=f"mean_gq < {VCF_MIN_GQ_WARN:.1f} -> warning",
            )
        )
        metrics.append(
            QualityMetric(
                name="low_genotype_quality",
                value=mean_gq,
                level=level,
                message=f"Low-genotype-quality check observed mean GQ {mean_gq:.2f}.",
                threshold=f"mean_gq < {VCF_MIN_GQ_WARN:.1f} -> warning",
            )
        )
    if vcf_stats.min_distance is not None:
        cluster_level = (
            QualityLevel.WARNING
            if vcf_stats.min_distance < VCF_CLUSTER_DISTANCE_WARN
            else QualityLevel.PASS
        )
        metrics.append(
            QualityMetric(
                name="variant_clustering",
                value=float(vcf_stats.min_distance),
                level=cluster_level,
                message=f"Minimum adjacent variant distance is {vcf_stats.min_distance} bp.",
                threshold=f"min_distance < {VCF_CLUSTER_DISTANCE_WARN} -> warning",
            )
        )
    if ts_tv_ratio is not None:
        metrics.append(
            QualityMetric(
                name="ts_tv_ratio",
                value=ts_tv_ratio,
                level=_ts_tv_level(ts_tv_ratio),
                message=f"Transition/transversion ratio is {ts_tv_ratio:.2f}.",
                threshold=(
                    f"< {VCF_TSTV_RATIO_WARN_LOW:.2f} or "
                    f"> {VCF_TSTV_RATIO_WARN_HIGH:.2f} -> warning"
                ),
            )
        )
    header_payload = extract_vcf_header_payload_features(resolved)
    if header_payload.payload_contigs_missing_from_header:
        metrics.append(
            QualityMetric(
                name="header_payload_contig_mismatch",
                value=float(len(header_payload.payload_contigs_missing_from_header)),
                level=QualityLevel.FAIL,
                message=(
                    "VCF payload contains contigs not declared in the header: "
                    + ", ".join(header_payload.payload_contigs_missing_from_header)
                    + "."
                ),
                threshold="payload contigs missing from header -> fail",
            )
        )

    summary = (
        f"VCF quality { _worst_level(tuple(metrics)).value }: "
        f"{int(variant_count)} variants, {pass_fraction:.1%} PASS"
    )
    if ts_tv_ratio is not None:
        summary += f", ts/tv={ts_tv_ratio:.2f}"
    summary += "."
    return _build_report(path=resolved, file_type="vcf", metrics=tuple(metrics), summary=summary)


def assess_fastq_quality(path: Path) -> QualityReport:
    """Assess FASTQ quality using pure Python sampling.

    Args:
        path: FASTQ or FASTQ.GZ path to inspect.

    Returns:
        FASTQ quality report with read count, mean quality, and GC fraction.
    """

    resolved = Path(path).expanduser()
    if not resolved.exists() or resolved.stat().st_size == 0:
        return _missing_or_empty_report(resolved, "fastq")

    stats = _scan_fastq_records(resolved)
    if stats.truncated or stats.format_error:
        return _build_report(
            path=resolved,
            file_type="fastq",
            metrics=(
                QualityMetric(
                    name="truncated_file",
                    value=float(stats.read_count),
                    level=QualityLevel.FAIL,
                    message="FASTQ terminated mid-record or contains malformed entries.",
                    threshold="truncated or malformed FASTQ -> fail",
                ),
            ),
            summary="FASTQ quality fail: truncated or malformed FASTQ.",
        )
    if stats.read_count <= FASTQ_MIN_READS_FAIL:
        return _missing_or_empty_report(resolved, "fastq")

    mean_quality = float(stats.quality_sum) / float(stats.quality_count) if stats.quality_count else 0.0
    gc_bases = 0
    try:
        with _open_text_auto(resolved) as handle:
            while True:
                header = handle.readline()
                if not header:
                    break
                sequence = handle.readline().strip().upper()
                plus = handle.readline()
                quality = handle.readline()
                if not sequence or not plus or not quality:
                    break
                gc_bases += sum(1 for base in sequence if base in {"G", "C"})
    except OSError:
        return _missing_or_empty_report(resolved, "fastq")
    gc_fraction = float(gc_bases) / float(stats.total_bases) if stats.total_bases else 0.0
    mean_read_length = float(stats.total_bases) / float(stats.read_count) if stats.read_count else 0.0
    low_quality_level = (
        QualityLevel.FAIL
        if mean_quality < FASTQ_MEAN_QUALITY_FAIL
        else QualityLevel.WARNING if mean_quality < FASTQ_MEAN_QUALITY_WARN else QualityLevel.PASS
    )
    short_read_level = (
        QualityLevel.WARNING if mean_read_length < FASTQ_SHORT_READ_WARN else QualityLevel.PASS
    )
    metrics = (
        QualityMetric(
            name="read_count",
            value=float(stats.read_count),
            level=QualityLevel.PASS,
            message=f"FASTQ contains {stats.read_count} reads.",
            threshold=f"read_count <= {FASTQ_MIN_READS_FAIL} -> fail",
        ),
        QualityMetric(
            name="mean_read_length",
            value=mean_read_length,
            level=short_read_level,
            message=f"Mean read length is {mean_read_length:.1f} bases.",
            threshold=f"mean_read_length < {FASTQ_SHORT_READ_WARN:.1f} -> warning",
        ),
        QualityMetric(
            name="mean_quality",
            value=mean_quality,
            level=low_quality_level,
            message=f"Mean Phred quality is {mean_quality:.1f}.",
            threshold=(
                f"mean_quality < {FASTQ_MEAN_QUALITY_FAIL:.1f} -> fail, "
                f"< {FASTQ_MEAN_QUALITY_WARN:.1f} -> warning"
            ),
        ),
        QualityMetric(
            name="low_base_quality",
            value=mean_quality,
            level=low_quality_level,
            message=f"Low-base-quality check observed mean Phred quality {mean_quality:.1f}.",
            threshold=(
                f"mean_quality < {FASTQ_MEAN_QUALITY_FAIL:.1f} -> fail, "
                f"< {FASTQ_MEAN_QUALITY_WARN:.1f} -> warning"
            ),
        ),
        QualityMetric(
            name="short_reads",
            value=mean_read_length,
            level=short_read_level,
            message=f"Short-read check observed mean read length {mean_read_length:.1f}.",
            threshold=f"mean_read_length < {FASTQ_SHORT_READ_WARN:.1f} -> warning",
        ),
        QualityMetric(
            name="gc_fraction",
            value=gc_fraction,
            level=_gc_fraction_level(gc_fraction),
            message=f"GC fraction is {gc_fraction:.1%}.",
            threshold=(
                f"< {FASTQ_GC_WARN_LOW:.0%} or "
                f"> {FASTQ_GC_WARN_HIGH:.0%} -> warning"
            ),
        ),
    )
    summary = (
        f"FASTQ quality { _worst_level(metrics).value }: "
        f"{stats.read_count} reads, mean length {mean_read_length:.1f}, "
        f"mean Q {mean_quality:.1f}, GC {gc_fraction:.1%}."
    )
    return _build_report(path=resolved, file_type="fastq", metrics=metrics, summary=summary)


def assess_gtf_quality(path: Path) -> QualityReport:
    """Assess whether a GTF contains at least one structurally valid feature row.

    Args:
        path: GTF path to inspect.

    Returns:
        GTF-focused quality report with basic feature and attribute checks.
    """

    resolved = Path(path).expanduser()
    if not resolved.exists() or resolved.stat().st_size == 0:
        return _missing_or_empty_report(resolved, "gtf")

    feature_count = 0
    attribute_hits = 0
    try:
        with _open_text_auto(resolved) as handle:
            for line in handle:
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                fields = text.split("\t")
                if len(fields) < 9:
                    continue
                feature_count += 1
                attributes = fields[8]
                if "gene_id" in attributes or "transcript_id" in attributes:
                    attribute_hits += 1
    except OSError:
        return _missing_or_empty_report(resolved, "gtf")

    metrics = (
        QualityMetric(
            name="feature_count",
            value=float(feature_count),
            level=QualityLevel.FAIL if feature_count <= 0 else QualityLevel.PASS,
            message=f"GTF contains {feature_count} feature rows.",
            threshold="feature_count <= 0 -> fail",
        ),
        QualityMetric(
            name="annotation_attributes",
            value=float(attribute_hits),
            level=QualityLevel.FAIL if feature_count > 0 and attribute_hits <= 0 else QualityLevel.PASS,
            message=(
                f"GTF contains {attribute_hits} feature rows with gene_id or transcript_id attributes."
            ),
            threshold="annotation_attributes <= 0 -> fail when feature_count > 0",
        ),
    )
    summary = (
        f"GTF quality {_worst_level(metrics).value}: "
        f"{feature_count} features, {attribute_hits} annotated rows."
    )
    return _build_report(path=resolved, file_type="gtf", metrics=metrics, summary=summary)


def assess_tabular_quality(
    path: Path,
    tool_name: str = "",
    analysis_type: str = "",
) -> QualityReport:
    """Assess small CSV/TSV outputs with generic and DE-specific checks.

    Args:
        path: Tabular output path to inspect.
        tool_name: Optional tool context for DE-specific rules.
        analysis_type: Optional analysis type for DE-specific rules.

    Returns:
        Tabular quality report with row and column checks plus specialized DE
        metrics when applicable.
    """

    resolved = Path(path).expanduser()
    if not resolved.exists() or resolved.stat().st_size == 0:
        return _missing_or_empty_report(resolved, "tabular")

    try:
        columns, rows, delimiter = load_delimited_dict_rows(resolved)
    except Exception as exc:
        return _build_report(
            path=resolved,
            file_type="tabular",
            metrics=(
                QualityMetric(
                    name="parse_failure",
                    value=0.0,
                    level=QualityLevel.FAIL,
                    message=f"Unable to parse tabular output: {exc}",
                    threshold="parse failure -> fail",
                ),
            ),
            summary="Tabular quality fail: unable to parse file.",
        )

    metrics: list[QualityMetric] = [
        QualityMetric(
            name="row_count",
            value=float(len(rows)),
            level=QualityLevel.FAIL if len(rows) <= TABULAR_MIN_ROWS_FAIL else QualityLevel.PASS,
            message=f"Table contains {len(rows)} data rows.",
            threshold=f"row_count <= {TABULAR_MIN_ROWS_FAIL} -> fail",
        ),
        QualityMetric(
            name="column_count",
            value=float(len(columns)),
            level=QualityLevel.FAIL if len(columns) <= TABULAR_MIN_COLUMNS_FAIL else QualityLevel.PASS,
            message=f"Table contains {len(columns)} columns.",
            threshold=f"column_count <= {TABULAR_MIN_COLUMNS_FAIL} -> fail",
        ),
    ]

    single_cell_markers = _looks_like_single_cell_marker_output(
        tool_name=tool_name,
        analysis_type=analysis_type,
        path=resolved,
        columns=columns,
    )
    de_like = _looks_like_de_output(
        tool_name=tool_name,
        analysis_type=analysis_type,
        path=resolved,
        columns=columns,
    )
    transcript_quant_like = _looks_like_transcript_quant_output(
        tool_name=tool_name,
        analysis_type=analysis_type,
        path=resolved,
        columns=columns,
    )
    if single_cell_markers:
        cluster_count = _count_distinct_values(rows, "cluster")
        marker_gene_count = _count_distinct_values(rows, "gene")
        missing_columns = _missing_required_single_cell_marker_columns(columns)
        if missing_columns:
            metrics.append(
                QualityMetric(
                    name="missing_marker_column",
                    value=float(len(missing_columns)),
                    level=QualityLevel.FAIL,
                    message=(
                        "Single-cell marker table is missing required columns: "
                        + ", ".join(sorted(missing_columns))
                        + "."
                    ),
                    threshold="missing single-cell marker columns -> fail",
                )
            )
        metrics.extend(
            [
                QualityMetric(
                    name="marker_gene_count",
                    value=float(marker_gene_count),
                    level=QualityLevel.FAIL if marker_gene_count <= 0 else QualityLevel.PASS,
                    message=f"Single-cell marker table contains {marker_gene_count} marker genes.",
                    threshold="marker_gene_count <= 0 -> fail",
                ),
                QualityMetric(
                    name="cluster_count",
                    value=float(cluster_count),
                    level=QualityLevel.FAIL if cluster_count <= 0 else QualityLevel.PASS,
                    message=f"Single-cell marker table contains markers for {cluster_count} clusters.",
                    threshold="cluster_count <= 0 -> fail",
                ),
            ]
        )
    elif transcript_quant_like:
        features = extract_transcript_quant_features(columns, rows)
        if features.abundance_columns:
            metrics.append(
                QualityMetric(
                    name="transcript_quant_rows",
                    value=float(features.row_count),
                    level=QualityLevel.FAIL if features.row_count <= 0 else QualityLevel.PASS,
                    message=(
                        "Transcript abundance table contains "
                        f"{features.row_count} rows across {len(features.abundance_columns)} abundance columns."
                    ),
                    threshold="transcript_quant_rows <= 0 -> fail",
                )
            )
            if features.abundance_dynamic_range is not None:
                metrics.append(
                    QualityMetric(
                        name="abundance_dynamic_range",
                        value=features.abundance_dynamic_range,
                        level=QualityLevel.PASS,
                        message=(
                            "Transcript abundance dynamic range spans "
                            f"{features.abundance_dynamic_range:.3f}."
                        ),
                        threshold="informational",
                    )
                )
            metrics.append(
                QualityMetric(
                    name="all_primary_abundance_zero",
                    value=float(features.zero_value_count),
                    level=QualityLevel.FAIL if features.all_primary_abundance_zero else QualityLevel.PASS,
                    message=(
                        "All primary transcript abundance values are zero."
                        if features.all_primary_abundance_zero
                        else "Transcript abundance table contains non-zero primary abundance values."
                    ),
                    threshold="all primary abundance values zero -> fail",
                )
            )
    elif de_like:
        total_genes = len(rows)
        metrics.append(
            QualityMetric(
                name="total_genes",
                value=float(total_genes),
                level=QualityLevel.FAIL if total_genes <= 0 else QualityLevel.PASS,
                message=f"Differential result table contains {total_genes} genes.",
                threshold="total_genes == 0 -> fail",
            )
        )
        missing_columns = _missing_required_de_columns(columns)
        if missing_columns:
            metrics.append(
                QualityMetric(
                    name="missing_required_column",
                    value=float(len(missing_columns)),
                    level=QualityLevel.FAIL,
                    message=(
                        "Differential result table is missing required columns: "
                        + ", ".join(sorted(missing_columns))
                        + "."
                    ),
                    threshold="missing DE columns -> fail",
                )
            )
        significant_count = _count_significant_rows(rows)
        metrics.append(
            QualityMetric(
                name="significant_row_count",
                value=float(significant_count),
                level=QualityLevel.WARNING if significant_count <= DE_SIGNIFICANT_ROWS_WARN else QualityLevel.PASS,
                message=f"Differential result table has {significant_count} significant rows.",
                threshold=f"significant_rows <= {DE_SIGNIFICANT_ROWS_WARN} -> warning",
            )
        )
        metrics.append(
            QualityMetric(
                name="significant_genes",
                value=float(significant_count),
                level=QualityLevel.PASS,
                message=f"Differential result table has {significant_count} significant genes.",
                threshold="informational",
            )
        )
        if total_genes > 0 and significant_count <= 0:
            metrics.append(
                QualityMetric(
                    name="no_significant_genes",
                    value=0.0,
                    level=QualityLevel.WARNING,
                    message="Differential result table contains zero significant genes.",
                    threshold="significant_genes == 0 -> warning",
                )
            )
        if total_genes > 0 and significant_count / float(total_genes) >= DE_ALL_SIGNIFICANT_WARN_FRACTION:
            metrics.append(
                QualityMetric(
                    name="suspiciously_all_significant",
                    value=float(significant_count),
                    level=QualityLevel.WARNING,
                    message="Nearly all genes are significant, which is suspicious for typical DE output.",
                    threshold=f"significant_fraction >= {DE_ALL_SIGNIFICANT_WARN_FRACTION:.2f} -> warning",
                )
            )
        na_fraction = _de_na_fraction(rows)
        if na_fraction is not None:
            metrics.append(
                QualityMetric(
                    name="na_fraction",
                    value=na_fraction,
                    level=QualityLevel.WARNING if na_fraction >= DE_HIGH_NA_WARN else QualityLevel.PASS,
                    message=f"Missing-value fraction in the significance column is {na_fraction:.1%}.",
                    threshold=f"na_fraction >= {DE_HIGH_NA_WARN:.0%} -> warning",
                )
            )
            metrics.append(
                QualityMetric(
                    name="high_na_fraction",
                    value=na_fraction,
                    level=QualityLevel.WARNING if na_fraction >= DE_HIGH_NA_WARN else QualityLevel.PASS,
                    message=f"High-NA-fraction check observed {na_fraction:.1%} missing significance values.",
                    threshold=f"na_fraction >= {DE_HIGH_NA_WARN:.0%} -> warning",
                )
            )
        fold_change_range = _fold_change_range(rows)
        if fold_change_range is not None:
            metrics.append(
                QualityMetric(
                    name="fold_change_range",
                    value=fold_change_range[1] - fold_change_range[0],
                    level=QualityLevel.PASS,
                    message=(
                        "Observed fold-change range spans "
                        f"{fold_change_range[0]:.2f} to {fold_change_range[1]:.2f}."
                    ),
                    threshold="informational",
                )
            )

    file_type = "tsv" if delimiter == "\t" else "csv"
    summary = (
        f"Tabular quality { _worst_level(tuple(metrics)).value }: "
        f"{len(rows)} rows, {len(columns)} columns."
    )
    return _build_report(path=resolved, file_type=file_type, metrics=tuple(metrics), summary=summary)


def _detect_file_type(path: Path) -> str:
    """Detect a supported file type from path and file content.

    Args:
        path: Path to inspect.

    Returns:
        One of `bam`, `vcf`, `fastq`, `csv`, `tsv`, or `unknown`.
    """

    resolved = Path(path).expanduser()
    suffixes = [suffix.lower() for suffix in resolved.suffixes]
    name_lower = resolved.name.lower()
    if suffixes[-1:] in ([ ".bam"], [".sam"]):
        return "bam"
    if name_lower.endswith(".vcf") or name_lower.endswith(".vcf.gz"):
        return "vcf"
    if any(name_lower.endswith(token) for token in (".fastq", ".fastq.gz", ".fq", ".fq.gz")):
        return "fastq"
    if suffixes[-1:] == [".csv"]:
        return "csv"
    if suffixes[-1:] == [".gtf"]:
        return "gtf"
    if suffixes[-1:] == [".tsv"]:
        return "tsv"

    sample = _read_text_sample(resolved)
    if "##fileformat=VCF" in sample or sample.lstrip().startswith("#CHROM\t"):
        return "vcf"
    sample_lines = sample.splitlines()
    if len(sample_lines) >= 4 and sample_lines[0].startswith("@") and sample_lines[2].startswith("+"):
        return "fastq"
    if "\t" in sample:
        return "tsv"
    if "," in sample:
        return "csv"
    return "unknown"


def _worst_level(metrics: tuple[QualityMetric, ...]) -> QualityLevel:
    """Return the worst severity represented in a metric set.

    Args:
        metrics: Quality measurements to evaluate.

    Returns:
        The most severe `QualityLevel`.
    """

    if not metrics:
        return QualityLevel.SKIP
    order = {
        QualityLevel.FAIL: 3,
        QualityLevel.WARNING: 2,
        QualityLevel.PASS: 1,
        QualityLevel.SKIP: 0,
    }
    return max(metrics, key=lambda metric: order[metric.level]).level


def _build_report(
    *,
    path: Path,
    file_type: str,
    metrics: tuple[QualityMetric, ...],
    summary: str,
) -> QualityReport:
    """Construct a consistent `QualityReport` value object."""

    return QualityReport(
        path=str(path),
        file_type=file_type,
        metrics=metrics,
        overall_level=_worst_level(metrics),
        summary=summary,
    )


def _missing_or_empty_report(path: Path, file_type: str) -> QualityReport:
    """Return a failure report for a missing or empty artifact."""

    exists = path.exists()
    message = "File is empty." if exists else "File does not exist."
    return _build_report(
        path=path,
        file_type=file_type,
        metrics=(
            QualityMetric(
                name="empty_file" if exists else "missing_file",
                value=0.0,
                level=QualityLevel.FAIL,
                message=message,
                threshold="missing or empty -> fail",
            ),
        ),
        summary=f"{file_type.upper()} quality fail: {message}",
    )


def _skip_report(path: Path, file_type: str, reason: str) -> QualityReport:
    """Return a skip report when an external dependency is unavailable."""

    return _build_report(
        path=path,
        file_type=file_type,
        metrics=(
            QualityMetric(
                name="tool_unavailable",
                value=0.0,
                level=QualityLevel.SKIP,
                message=reason,
                threshold="tool unavailable -> skip",
            ),
        ),
        summary=f"{file_type.upper()} quality skipped: {reason}.",
    )


def _resolve_tool(name: str) -> str | None:
    """Resolve a tool from the Pixi environment or system PATH."""

    return which_with_pixi(name) or shutil.which(name)


def _tool_env() -> dict[str, str]:
    """Build a subprocess environment with Pixi tool bins on PATH."""

    env = dict(os.environ)
    additions = [str(path) for path in pixi_env_bin_dirs()]
    if not additions:
        return env
    existing = env.get("PATH", "")
    existing_parts = existing.split(os.pathsep) if existing else []
    ordered = additions + [part for part in existing_parts if part and part not in additions]
    env["PATH"] = os.pathsep.join(ordered)
    return env


def _run_tool(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str] | None:
    """Run one external command and capture text output."""

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
    """Open plain or gzipped text for reading."""

    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _read_text_sample(path: Path, *, chars: int = 4096) -> str:
    """Read a small text sample from a file, handling gzip when needed."""

    try:
        with _open_text_auto(path) as handle:
            return handle.read(chars)
    except OSError:
        return ""


def _mapping_rate_level(mapping_rate: float) -> QualityLevel:
    """Classify BAM mapping rate."""

    if mapping_rate < BAM_MAPPING_RATE_FAIL:
        return QualityLevel.FAIL
    if mapping_rate < BAM_MAPPING_RATE_WARN:
        return QualityLevel.WARNING
    return QualityLevel.PASS


def _gc_fraction_level(gc_fraction: float) -> QualityLevel:
    """Classify GC balance for FASTQ sequences."""

    if gc_fraction < FASTQ_GC_WARN_LOW or gc_fraction > FASTQ_GC_WARN_HIGH:
        return QualityLevel.WARNING
    return QualityLevel.PASS


def _ts_tv_level(ts_tv_ratio: float) -> QualityLevel:
    """Classify ts/tv ratio for VCFs."""

    if ts_tv_ratio < VCF_TSTV_RATIO_WARN_LOW or ts_tv_ratio > VCF_TSTV_RATIO_WARN_HIGH:
        return QualityLevel.WARNING
    return QualityLevel.PASS


def _scan_vcf_records(path: Path) -> _VcfStats:
    """Scan VCF records directly for lightweight metrics."""

    variant_count = 0
    pass_count = 0
    transitions = 0
    transversions = 0
    gq_sum = 0.0
    gq_count = 0
    last_position_by_chrom: dict[str, int] = {}
    min_distance: int | None = None
    try:
        with _open_text_auto(path) as handle:
            for line in handle:
                if not line or line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 8:
                    continue
                variant_count += 1
                if fields[6] == "PASS":
                    pass_count += 1
                chrom = fields[0]
                pos = int(_safe_float(fields[1])) if fields[1].strip() else 0
                previous = last_position_by_chrom.get(chrom)
                if previous and pos > 0:
                    distance = pos - previous
                    if min_distance is None or distance < min_distance:
                        min_distance = distance
                if pos > 0:
                    last_position_by_chrom[chrom] = pos
                ref = fields[3].strip().upper()
                for alt in fields[4].strip().upper().split(","):
                    if len(ref) != 1 or len(alt) != 1:
                        continue
                    if ref not in {"A", "C", "G", "T"} or alt not in {"A", "C", "G", "T"}:
                        continue
                    if {ref, alt} in ({"A", "G"}, {"C", "T"}):
                        transitions += 1
                    elif ref != alt:
                        transversions += 1
                if len(fields) >= 10:
                    format_tokens = fields[8].split(":")
                    sample_tokens = fields[9].split(":")
                    if "GQ" in format_tokens:
                        gq_index = format_tokens.index("GQ")
                        if gq_index < len(sample_tokens):
                            gq_value = _safe_float(sample_tokens[gq_index])
                            if gq_value == gq_value:
                                gq_sum += gq_value
                                gq_count += 1
    except OSError:
        return _VcfStats(
            variant_count=0.0,
            pass_count=0.0,
            pass_fraction=0.0,
            ts_tv_ratio=None,
            mean_gq=None,
            min_distance=None,
        )

    pass_fraction = (float(pass_count) / float(variant_count)) if variant_count else 0.0
    ts_tv_ratio = None
    if transversions > 0:
        ts_tv_ratio = float(transitions) / float(transversions)
    mean_gq = (gq_sum / float(gq_count)) if gq_count else None
    return _VcfStats(
        variant_count=float(variant_count),
        pass_count=float(pass_count),
        pass_fraction=pass_fraction,
        ts_tv_ratio=ts_tv_ratio,
        mean_gq=mean_gq,
        min_distance=min_distance,
    )


def _looks_like_de_output(
    *,
    tool_name: str,
    analysis_type: str,
    path: Path,
    columns: list[str],
) -> bool:
    """Return whether tabular metrics should use DE-specific checks."""

    tool_token = str(tool_name or "").lower()
    analysis_token = str(analysis_type or "").lower()
    if tool_token in {"deseq2_run", "edger_run", "limma_voom_run"}:
        return True
    if "differential_expression" in analysis_token or "deseq" in analysis_token:
        return True
    column_tokens = {column.lower() for column in columns}
    if "gene" in column_tokens and column_tokens.intersection({"log2foldchange", "logfc", "log2fc"}):
        return True
    name_lower = path.name.lower()
    return name_lower.endswith("_de.csv") or name_lower.endswith("_de.tsv") or "deseq" in name_lower


def _looks_like_single_cell_marker_output(
    *,
    tool_name: str,
    analysis_type: str,
    path: Path,
    columns: list[str],
) -> bool:
    """Return whether tabular metrics should use single-cell marker checks."""

    analysis_token = str(analysis_type or "").lower()
    tool_token = str(tool_name or "").lower()
    name_lower = path.name.lower()
    if not (
        "single_cell" in analysis_token
        or "scanpy" in tool_token
        or "seurat" in tool_token
        or "marker" in name_lower
    ):
        return False
    lower_columns = {column.lower() for column in columns}
    if not {"gene", "cluster"}.issubset(lower_columns):
        return False
    return bool(lower_columns.intersection({"pval_adj", "padj", "score", "log2fc", "logfc"}))


def _looks_like_transcript_quant_output(
    *,
    tool_name: str,
    analysis_type: str,
    path: Path,
    columns: list[str],
) -> bool:
    """Return whether tabular metrics should use transcript-quant rules."""

    tool_token = str(tool_name or "").lower()
    analysis_token = str(analysis_type or "").lower()
    if tool_token in {"stringtie_quant", "salmon_quant", "kallisto_quant"}:
        return True
    if "transcript_quant" in analysis_token or "quantification" in analysis_token:
        return True
    lower_columns = {column.lower() for column in columns}
    if lower_columns.intersection({"coverage", "fpkm", "tpm"}) and lower_columns.intersection(
        {"gene id", "gene_name", "gene name", "transcript id", "transcript_id"}
    ):
        return True
    name_lower = path.name.lower()
    return "abundance" in name_lower or "quant" in name_lower


def _count_significant_rows(rows: list[dict[str, str]]) -> int:
    """Count significant DE rows using common adjusted-p-value columns."""

    candidates = ("padj", "fdr", "adj_pval", "qvalue", "pvalue")
    for candidate in candidates:
        matching_key = _find_case_insensitive_key(rows, candidate)
        if not matching_key:
            continue
        return sum(1 for row in rows if _safe_float(row.get(matching_key, "")) < 0.05)
    return 0


def _significance_key(rows: list[dict[str, str]]) -> str:
    """Return the preferred significance column when one exists."""

    candidates = ("padj", "fdr", "adj_pval", "qvalue", "pvalue")
    for candidate in candidates:
        matching_key = _find_case_insensitive_key(rows, candidate)
        if matching_key:
            return matching_key
    return ""


def _de_na_fraction(rows: list[dict[str, str]]) -> float | None:
    """Return the missing-value fraction for the DE significance column."""

    significance_key = _significance_key(rows)
    if not significance_key or not rows:
        return None
    missing = 0
    for row in rows:
        token = str(row.get(significance_key, "")).strip().lower()
        if token in {"", "na", "nan", "none"}:
            missing += 1
    return float(missing) / float(len(rows))


def _missing_required_de_columns(columns: list[str]) -> tuple[str, ...]:
    """Return missing required DE result columns."""

    lower_columns = {column.lower() for column in columns}
    missing: list[str] = []
    if "gene" not in lower_columns:
        missing.append("gene")
    if not lower_columns.intersection({"log2foldchange", "logfc", "log2fc"}):
        missing.append("log2FoldChange")
    if not lower_columns.intersection({"padj", "fdr", "adj_pval", "qvalue", "pvalue"}):
        missing.append("padj_or_pvalue")
    return tuple(missing)


def _missing_required_single_cell_marker_columns(columns: list[str]) -> tuple[str, ...]:
    """Return missing required columns for single-cell marker tables."""

    lower_columns = {column.lower() for column in columns}
    missing: list[str] = []
    if "gene" not in lower_columns:
        missing.append("gene")
    if "cluster" not in lower_columns:
        missing.append("cluster")
    if not lower_columns.intersection({"pval_adj", "padj", "score", "log2fc", "logfc"}):
        missing.append("marker_score_or_significance")
    return tuple(missing)


def _count_distinct_values(rows: list[dict[str, str]], key_name: str) -> int:
    """Count distinct non-empty values for one case-insensitive tabular key."""

    matching_key = _find_case_insensitive_key(rows, key_name)
    if not matching_key:
        return 0
    values = {
        str(row.get(matching_key, "")).strip()
        for row in rows
        if str(row.get(matching_key, "")).strip()
    }
    return len(values)


def _fold_change_range(rows: list[dict[str, str]]) -> tuple[float, float] | None:
    """Return the observed fold-change range when a standard column exists."""

    candidates = ("log2foldchange", "logfc", "log2fc")
    for candidate in candidates:
        matching_key = _find_case_insensitive_key(rows, candidate)
        if not matching_key:
            continue
        values = [_safe_float(row.get(matching_key, "")) for row in rows]
        numeric = [value for value in values if value == value]
        if numeric:
            return min(numeric), max(numeric)
    return None


def _scan_alignment_stats(path: Path, *, samtools_bin: str | None) -> _AlignmentStats:
    """Return read counts for SAM/BAM inputs."""

    if path.suffix.lower() == ".sam":
        return _scan_sam_records(path)

    total_reads = 0.0
    mapped_reads = 0.0
    duplicate_reads = 0.0
    if not samtools_bin:
        return _AlignmentStats(total_reads=0.0, mapped_reads=0.0, duplicate_reads=0.0)

    flagstat = _run_tool([samtools_bin, "flagstat", str(path)], timeout=30)
    if flagstat and flagstat.returncode == 0:
        for line in flagstat.stdout.splitlines():
            total_match = _FLAGSTAT_TOTAL_RE.match(line)
            if total_match:
                total_reads = float(total_match.group(1))
                continue
            mapped_match = _FLAGSTAT_MAPPED_RE.match(line)
            if mapped_match:
                mapped_reads = float(mapped_match.group(1))
                continue
            duplicate_match = _FLAGSTAT_DUP_RE.match(line)
            if duplicate_match:
                duplicate_reads = float(duplicate_match.group(1))

    idxstats = _run_tool([samtools_bin, "idxstats", str(path)], timeout=30)
    if idxstats and idxstats.returncode == 0:
        idx_mapped = 0.0
        idx_unmapped = 0.0
        for line in idxstats.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) < 4:
                continue
            if fields[0] == "*":
                idx_unmapped += _safe_float(fields[3])
                continue
            idx_mapped += _safe_float(fields[2])
        if idx_mapped > 0:
            mapped_reads = idx_mapped
        if total_reads <= 0 and (idx_mapped + idx_unmapped) > 0:
            total_reads = idx_mapped + idx_unmapped
    return _AlignmentStats(total_reads=total_reads, mapped_reads=mapped_reads, duplicate_reads=duplicate_reads)


def _scan_sam_records(path: Path) -> _AlignmentStats:
    """Return read counts for plain-text SAM files."""

    total_reads = 0
    mapped_reads = 0
    duplicate_reads = 0
    try:
        with _open_text_auto(path) as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("@"):
                    continue
                fields = raw_line.rstrip("\n").split("\t")
                if len(fields) < 11:
                    continue
                try:
                    flag = int(fields[1])
                except ValueError:
                    continue
                total_reads += 1
                if not (flag & 0x4):
                    mapped_reads += 1
                if flag & 0x400:
                    duplicate_reads += 1
    except OSError:
        return _AlignmentStats(total_reads=0.0, mapped_reads=0.0, duplicate_reads=0.0)
    return _AlignmentStats(
        total_reads=float(total_reads),
        mapped_reads=float(mapped_reads),
        duplicate_reads=float(duplicate_reads),
    )


def _scan_fastq_records(path: Path) -> _FastqStats:
    """Return basic FASTQ statistics and structural validity."""

    read_count = 0
    total_bases = 0
    quality_sum = 0
    quality_count = 0
    min_read_length = 0
    max_read_length = 0
    truncated = False
    format_error = False
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
                sequence_text = sequence.strip().upper()
                quality_text = quality.strip()
                if not header.startswith("@") or not plus.startswith("+"):
                    format_error = True
                    break
                if len(sequence_text) != len(quality_text):
                    format_error = True
                    break
                read_length = len(sequence_text)
                if read_count == 0:
                    min_read_length = read_length
                    max_read_length = read_length
                else:
                    min_read_length = min(min_read_length, read_length)
                    max_read_length = max(max_read_length, read_length)
                read_count += 1
                total_bases += read_length
                quality_sum += sum(ord(char) - 33 for char in quality_text)
                quality_count += len(quality_text)
    except OSError:
        return _FastqStats(
            read_count=0,
            total_bases=0,
            quality_sum=0,
            quality_count=0,
            min_read_length=0,
            max_read_length=0,
            truncated=True,
            format_error=True,
        )
    return _FastqStats(
        read_count=read_count,
        total_bases=total_bases,
        quality_sum=quality_sum,
        quality_count=quality_count,
        min_read_length=min_read_length,
        max_read_length=max_read_length,
        truncated=truncated,
        format_error=format_error,
    )


def _find_case_insensitive_key(rows: list[dict[str, str]], expected: str) -> str:
    """Find a case-insensitive key from a row set."""

    if not rows:
        return ""
    expected_lower = expected.lower()
    for key in rows[0]:
        if key.lower() == expected_lower:
            return key
    return ""


def _safe_float(value: str | float | int) -> float:
    """Convert a loose numeric value to float."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


__all__ = [
    "BAM_DUPLICATE_RATE_WARN",
    "BAM_MAPPING_RATE_FAIL",
    "BAM_MAPPING_RATE_WARN",
    "BAM_MIN_READS_FAIL",
    "DE_SIGNIFICANT_ROWS_WARN",
    "FASTQ_GC_WARN_HIGH",
    "FASTQ_GC_WARN_LOW",
    "FASTQ_MEAN_QUALITY_WARN",
    "FASTQ_MIN_READS_FAIL",
    "QualityLevel",
    "QualityMetric",
    "QualityReport",
    "TABULAR_MIN_COLUMNS_FAIL",
    "TABULAR_MIN_ROWS_FAIL",
    "VCF_MIN_VARIANTS_FAIL",
    "VCF_PASS_FRACTION_WARN",
    "VCF_TSTV_RATIO_WARN_HIGH",
    "VCF_TSTV_RATIO_WARN_LOW",
    "assess_bam_quality",
    "assess_fastq_quality",
    "assess_output_quality",
    "assess_tabular_quality",
    "assess_vcf_quality",
]
