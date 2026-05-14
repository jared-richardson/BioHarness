"""Semantic feature extraction for deterministic output review.

This module extracts assay-level semantic features from output artifacts so the
review layer can evaluate biologically meaningful invariants without relying on
fixture-specific logic.
"""

from __future__ import annotations

import gzip
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import IO, Iterable

from bio_harness.core.tabular_io import load_delimited_dict_rows

_VCF_CONTIG_RE = re.compile(r"##contig=<ID=([^,>]+)")


@dataclass(frozen=True)
class TranscriptQuantSemanticFeatures:
    """Normalized semantic features for transcript-quantification tables.

    Attributes:
        row_count: Number of parsed rows.
        abundance_columns: Canonical abundance columns discovered in the table.
        numeric_value_count: Number of numeric abundance values observed.
        zero_value_count: Number of zero-valued abundance measurements.
        all_primary_abundance_zero: Whether every observed abundance value is zero.
        abundance_dynamic_range: Maximum minus minimum numeric abundance value.
    """

    row_count: int
    abundance_columns: tuple[str, ...]
    numeric_value_count: int
    zero_value_count: int
    all_primary_abundance_zero: bool
    abundance_dynamic_range: float | None


@dataclass(frozen=True)
class VcfHeaderPayloadFeatures:
    """Normalized semantic features for VCF header and payload agreement.

    Attributes:
        header_contig_ids: Declared header contig identifiers.
        payload_contig_ids: Contig identifiers observed in payload records.
        payload_contigs_missing_from_header: Payload contigs not declared in the header.
        naming_scheme_mismatch: Whether header and payload use incompatible
            chromosome-prefix conventions when both sets are populated.
    """

    header_contig_ids: tuple[str, ...]
    payload_contig_ids: tuple[str, ...]
    payload_contigs_missing_from_header: tuple[str, ...]
    naming_scheme_mismatch: bool


@dataclass(frozen=True)
class SingleCellFragmentationFeatures:
    """Normalized semantic features for single-cell clustering outputs.

    Attributes:
        cell_count: Number of distinct cells in the clustering output.
        cluster_count: Number of distinct assigned clusters.
        cluster_to_cell_ratio: Cluster count divided by cell count.
        singleton_cluster_fraction: Fraction of clusters containing one cell.
        median_cluster_size: Median number of cells per cluster.
        marker_cluster_count: Number of distinct clusters represented in markers.
        marker_clusters_missing_from_assignments: Marker clusters not present in
            the cell-level cluster assignments.
    """

    cell_count: int
    cluster_count: int
    cluster_to_cell_ratio: float
    singleton_cluster_fraction: float
    median_cluster_size: float
    marker_cluster_count: int
    marker_clusters_missing_from_assignments: tuple[str, ...]


def extract_transcript_quant_features(
    columns: Iterable[str],
    rows: list[dict[str, str]],
) -> TranscriptQuantSemanticFeatures:
    """Extract semantic abundance features from a transcript-quant table.

    Args:
        columns: Parsed column names.
        rows: Parsed table rows.

    Returns:
        Normalized abundance features suitable for deterministic invariants.
    """

    abundance_columns = tuple(
        column
        for column in columns
        if column.strip().lower() in {"coverage", "fpkm", "tpm"}
    )
    numeric_values: list[float] = []
    for row in rows:
        for column in abundance_columns:
            value = _safe_float(row.get(column, ""))
            if value == value:
                numeric_values.append(value)
    zero_value_count = sum(1 for value in numeric_values if value == 0.0)
    abundance_dynamic_range = None
    if numeric_values:
        abundance_dynamic_range = max(numeric_values) - min(numeric_values)
    return TranscriptQuantSemanticFeatures(
        row_count=len(rows),
        abundance_columns=abundance_columns,
        numeric_value_count=len(numeric_values),
        zero_value_count=zero_value_count,
        all_primary_abundance_zero=bool(numeric_values) and zero_value_count == len(numeric_values),
        abundance_dynamic_range=abundance_dynamic_range,
    )


def extract_vcf_header_payload_features(path: Path) -> VcfHeaderPayloadFeatures:
    """Extract semantic agreement features from a VCF header and payload.

    Args:
        path: VCF or VCF.GZ path to inspect.

    Returns:
        Normalized header and payload contig features.
    """

    header_contigs: set[str] = set()
    payload_contigs: set[str] = set()
    try:
        with _open_text_auto(Path(path).expanduser()) as handle:
            for line in handle:
                if line.startswith("##contig="):
                    match = _VCF_CONTIG_RE.search(line)
                    if match:
                        header_contigs.add(match.group(1).strip())
                    continue
                if line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                if fields and fields[0].strip():
                    payload_contigs.add(fields[0].strip())
    except OSError:
        return VcfHeaderPayloadFeatures(
            header_contig_ids=(),
            payload_contig_ids=(),
            payload_contigs_missing_from_header=(),
            naming_scheme_mismatch=False,
        )

    missing = tuple(sorted(payload_contigs - header_contigs)) if header_contigs else ()
    return VcfHeaderPayloadFeatures(
        header_contig_ids=tuple(sorted(header_contigs)),
        payload_contig_ids=tuple(sorted(payload_contigs)),
        payload_contigs_missing_from_header=missing,
        naming_scheme_mismatch=_naming_scheme_mismatch(header_contigs, payload_contigs),
    )


def extract_single_cell_fragmentation_features(
    clusters_path: Path,
    markers_path: Path,
) -> SingleCellFragmentationFeatures | None:
    """Extract semantic clustering features from single-cell outputs.

    Args:
        clusters_path: Cell-to-cluster assignment table.
        markers_path: Marker table keyed by cluster.

    Returns:
        Normalized clustering features, or ``None`` when the expected fields are
        not available.
    """

    try:
        cluster_columns, cluster_rows, _ = load_delimited_dict_rows(Path(clusters_path).expanduser())
        marker_columns, marker_rows, _ = load_delimited_dict_rows(Path(markers_path).expanduser())
    except Exception:
        return None

    cell_key = _first_matching_key(cluster_columns, ("cell_id", "cell", "barcode", "barcodes", "obs_name"))
    cluster_key = _first_matching_key(cluster_columns, ("cluster", "cluster_id", "leiden", "seurat_cluster"))
    marker_cluster_key = _first_matching_key(marker_columns, ("cluster", "cluster_id", "leiden", "seurat_cluster"))
    if not cell_key or not cluster_key or not marker_cluster_key:
        return None

    cell_ids = {
        str(row.get(cell_key, "")).strip()
        for row in cluster_rows
        if str(row.get(cell_key, "")).strip()
    }
    assigned_clusters = [
        str(row.get(cluster_key, "")).strip()
        for row in cluster_rows
        if str(row.get(cluster_key, "")).strip()
    ]
    if not cell_ids or not assigned_clusters:
        return None

    cluster_sizes = Counter(assigned_clusters)
    cluster_ids = tuple(sorted(cluster_sizes))
    singleton_cluster_count = sum(1 for size in cluster_sizes.values() if size == 1)
    marker_clusters = {
        str(row.get(marker_cluster_key, "")).strip()
        for row in marker_rows
        if str(row.get(marker_cluster_key, "")).strip()
    }
    return SingleCellFragmentationFeatures(
        cell_count=len(cell_ids),
        cluster_count=len(cluster_ids),
        cluster_to_cell_ratio=float(len(cluster_ids)) / float(len(cell_ids) or 1),
        singleton_cluster_fraction=float(singleton_cluster_count) / float(len(cluster_ids) or 1),
        median_cluster_size=float(median(cluster_sizes.values())),
        marker_cluster_count=len(marker_clusters),
        marker_clusters_missing_from_assignments=tuple(sorted(marker_clusters - set(cluster_ids))),
    )


def _first_matching_key(columns: Iterable[str], candidates: tuple[str, ...]) -> str:
    """Return the first case-insensitive matching column name."""

    lowered = {str(column).lower(): str(column) for column in columns}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return ""


def _naming_scheme_mismatch(header_contigs: set[str], payload_contigs: set[str]) -> bool:
    """Return whether header and payload disagree on ``chr`` prefix style."""

    if not header_contigs or not payload_contigs:
        return False
    header_has_chr = all(contig.lower().startswith("chr") for contig in header_contigs)
    payload_has_chr = all(contig.lower().startswith("chr") for contig in payload_contigs)
    return header_has_chr != payload_has_chr


def _open_text_auto(path: Path) -> IO[str]:
    """Open plain or gzipped text for reading."""

    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _safe_float(value: str) -> float:
    """Return ``float('nan')`` when a token is not numeric."""

    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float("nan")


__all__ = [
    "SingleCellFragmentationFeatures",
    "TranscriptQuantSemanticFeatures",
    "VcfHeaderPayloadFeatures",
    "extract_single_cell_fragmentation_features",
    "extract_transcript_quant_features",
    "extract_vcf_header_payload_features",
]
