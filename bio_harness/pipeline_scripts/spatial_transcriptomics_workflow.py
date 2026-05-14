#!/usr/bin/env python3
"""Deterministic processed-input spatial transcriptomics workflow.

This workflow is intentionally scoped to processed AnnData-style inputs with
precomputed spot-by-gene matrices and spatial coordinates. It avoids raw-image
registration or Space Ranger-style preprocessing so the harness can benchmark a
first-class spatial family on one workstation with deterministic outputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anndata as ad
import numpy as np
import scipy.sparse as sp
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class SpatialWorkflowSummary:
    """Compact summary for one deterministic spatial analysis run."""

    input_path: str
    spots_input: int
    genes_input: int
    spots_retained: int
    genes_retained: int
    domains_detected: int
    markers_written: int
    filtered_zero_count_spots: int


def _load_input_adata(input_path: Path) -> ad.AnnData:
    """Load one processed AnnData input.

    Args:
        input_path: Path to a processed spatial `.h5ad` file.

    Returns:
        Loaded AnnData object.

    Raises:
        ValueError: If the input format is unsupported.
    """

    lower_name = input_path.name.lower()
    if lower_name.endswith(".h5ad"):
        return ad.read_h5ad(str(input_path))
    raise ValueError(f"Unsupported input for spatial_transcriptomics_workflow: {input_path}")


def _to_dense_array(matrix: Any) -> np.ndarray:
    """Return a dense floating-point matrix."""

    if sp.issparse(matrix):
        return matrix.toarray().astype(float, copy=False)
    return np.asarray(matrix, dtype=float)


def _extract_spatial_coordinates(adata: ad.AnnData) -> np.ndarray:
    """Extract validated 2D spatial coordinates from an AnnData object.

    Args:
        adata: Loaded AnnData object.

    Returns:
        `n_spots x 2` floating-point coordinate matrix.

    Raises:
        ValueError: If the coordinates are missing, malformed, or non-finite.
    """

    if "spatial" not in adata.obsm:
        raise ValueError("Spatial input is missing `obsm['spatial']` coordinates.")
    coords = np.asarray(adata.obsm["spatial"], dtype=float)
    if coords.ndim != 2 or coords.shape[1] < 2:
        raise ValueError("Spatial coordinates must be a two-column matrix.")
    coords = coords[:, :2]
    if not np.isfinite(coords).all():
        raise ValueError("Spatial coordinates contain missing or non-finite values.")
    return coords


def _filter_low_signal_spots_and_genes(
    adata: ad.AnnData,
    *,
    min_genes: int,
    min_cells: int,
) -> tuple[ad.AnnData, int]:
    """Filter zero-signal spots and low-support genes deterministically.

    Args:
        adata: Input AnnData object.
        min_genes: Minimum detected genes per retained spot.
        min_cells: Minimum expressing spots per retained gene.

    Returns:
        Tuple of filtered AnnData object and count of removed spots.

    Raises:
        ValueError: If filtering removes every spot or every gene.
    """

    matrix = _to_dense_array(adata.X)
    genes_per_spot = np.count_nonzero(matrix > 0, axis=1)
    spot_totals = matrix.sum(axis=1)
    keep_spots = (genes_per_spot >= max(1, int(min_genes))) & (spot_totals > 0)
    removed_spots = int((~keep_spots).sum())
    if not keep_spots.any():
        raise ValueError("No spatial spots remain after QC filtering.")
    filtered = adata[keep_spots].copy()
    filtered_matrix = _to_dense_array(filtered.X)
    cells_per_gene = np.count_nonzero(filtered_matrix > 0, axis=0)
    keep_genes = cells_per_gene >= max(1, int(min_cells))
    if not keep_genes.any():
        raise ValueError("No genes remain after QC filtering.")
    filtered = filtered[:, keep_genes].copy()
    return filtered, removed_spots


def _normalize_log1p(matrix: np.ndarray) -> np.ndarray:
    """Library-normalize one count matrix and apply log1p."""

    totals = matrix.sum(axis=1, keepdims=True)
    totals[totals <= 0] = 1.0
    normalized = (matrix / totals) * 1e4
    return np.log1p(normalized)


def _select_high_variance_genes(matrix: np.ndarray, *, n_hvgs: int) -> np.ndarray:
    """Return high-variance gene columns from a normalized matrix."""

    if matrix.shape[1] <= max(1, int(n_hvgs)):
        return matrix
    variances = np.var(matrix, axis=0)
    order = np.argsort(variances)[::-1]
    keep = order[: max(2, int(n_hvgs))]
    return matrix[:, keep]


def _build_spatial_feature_matrix(
    expression_matrix: np.ndarray,
    coords: np.ndarray,
    *,
    n_pcs: int,
) -> np.ndarray:
    """Combine expression PCs with standardized spatial coordinates."""

    max_components = min(max(2, int(n_pcs)), expression_matrix.shape[0] - 1, expression_matrix.shape[1])
    if max_components < 2:
        expr_features = StandardScaler().fit_transform(expression_matrix)
    else:
        expr_features = PCA(n_components=max_components, random_state=0).fit_transform(expression_matrix)
        expr_features = StandardScaler().fit_transform(expr_features)
    coord_features = StandardScaler().fit_transform(coords)
    return np.concatenate([expr_features, 1.5 * coord_features], axis=1)


def _choose_domain_count(features: np.ndarray) -> int:
    """Choose a domain count by bounded silhouette search."""

    upper = min(6, features.shape[0] - 1)
    if upper < 2:
        return 1
    best_k = 2
    best_score = float("-inf")
    for k in range(2, upper + 1):
        labels = KMeans(n_clusters=k, n_init=20, random_state=0).fit_predict(features)
        if len(set(labels)) < 2:
            continue
        score = float(silhouette_score(features, labels))
        if score > best_score:
            best_k = k
            best_score = score
    return best_k


def _rank_marker_rows(
    expression_matrix: np.ndarray,
    gene_names: list[str],
    labels: np.ndarray,
    *,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """Compute simple domain marker rows by mean-expression contrast."""

    rows: list[dict[str, Any]] = []
    for label in sorted(np.unique(labels)):
        in_mask = labels == label
        out_mask = ~in_mask
        if in_mask.sum() == 0:
            continue
        mean_in = expression_matrix[in_mask].mean(axis=0)
        mean_out = expression_matrix[out_mask].mean(axis=0) if out_mask.any() else np.zeros_like(mean_in)
        scores = mean_in - mean_out
        order = np.argsort(scores)[::-1][:top_n]
        domain_name = f"Domain{int(label) + 1}"
        for gene_idx in order:
            rows.append(
                {
                    "domain": domain_name,
                    "gene": gene_names[int(gene_idx)],
                    "score": float(scores[int(gene_idx)]),
                }
            )
    return rows


def _write_domain_assignments(
    path: Path,
    *,
    spot_ids: list[str],
    labels: np.ndarray,
    coords: np.ndarray,
) -> None:
    """Persist domain assignments in canonical CSV form."""

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("spot_id", "domain", "x", "y"))
        writer.writeheader()
        for spot_id, label, coord in zip(spot_ids, labels, coords, strict=True):
            writer.writerow(
                {
                    "spot_id": spot_id,
                    "domain": f"Domain{int(label) + 1}",
                    "x": float(coord[0]),
                    "y": float(coord[1]),
                }
            )


def _write_marker_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    """Persist spatial marker genes in canonical CSV form."""

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("domain", "gene", "score"))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_spatial_transcriptomics_workflow(
    *,
    input_path: Path,
    output_dir: Path,
    domain_assignments_csv: Path | None = None,
    marker_genes_csv: Path | None = None,
    results_h5ad: Path | None = None,
    min_genes: int = 3,
    min_cells: int = 2,
    n_hvgs: int = 50,
    n_pcs: int = 10,
) -> dict[str, Any]:
    """Run one deterministic processed-input spatial transcriptomics analysis.

    Args:
        input_path: Input `.h5ad` path containing expression and coordinates.
        output_dir: Directory where canonical outputs should be written.
        domain_assignments_csv: Optional explicit assignments output path.
        marker_genes_csv: Optional explicit marker output path.
        results_h5ad: Optional explicit processed AnnData output path.
        min_genes: Minimum detected genes per retained spot.
        min_cells: Minimum expressing spots per retained gene.
        n_hvgs: Maximum number of high-variance genes used for clustering.
        n_pcs: Maximum number of expression PCs before spatial concatenation.

    Returns:
        JSON-serializable workflow summary.

    Raises:
        ValueError: If the input is empty or missing valid spatial coordinates.
    """

    input_adata = _load_input_adata(input_path)
    input_adata.obs_names_make_unique()
    input_adata.var_names_make_unique()
    if input_adata.n_obs == 0 or input_adata.n_vars == 0:
        raise ValueError("Input AnnData is empty.")
    coords = _extract_spatial_coordinates(input_adata)
    working = input_adata.copy()
    working.obsm["spatial"] = coords
    filtered, removed_spots = _filter_low_signal_spots_and_genes(
        working,
        min_genes=min_genes,
        min_cells=min_cells,
    )
    filtered_coords = _extract_spatial_coordinates(filtered)
    counts_matrix = _to_dense_array(filtered.X)
    normalized = _normalize_log1p(counts_matrix)
    selected = _select_high_variance_genes(normalized, n_hvgs=n_hvgs)
    features = _build_spatial_feature_matrix(selected, filtered_coords, n_pcs=n_pcs)
    domain_count = _choose_domain_count(features)
    labels = KMeans(n_clusters=domain_count, n_init=30, random_state=0).fit_predict(features)
    marker_rows = _rank_marker_rows(normalized, list(map(str, filtered.var_names)), labels)

    output_dir.mkdir(parents=True, exist_ok=True)
    assignments_path = domain_assignments_csv or (output_dir / "spatial_domain_assignments.csv")
    markers_path = marker_genes_csv or (output_dir / "spatial_marker_genes.csv")
    results_path = results_h5ad or (output_dir / "spatial_results.h5ad")
    qc_path = output_dir / "spatial_qc_summary.json"
    summary_md_path = output_dir / "spatial_summary.md"

    _write_domain_assignments(
        assignments_path,
        spot_ids=[str(item) for item in filtered.obs_names],
        labels=labels,
        coords=filtered_coords,
    )
    _write_marker_rows(markers_path, marker_rows)

    filtered.obs["spatial_domain"] = [f"Domain{int(label) + 1}" for label in labels]
    filtered.obsm["spatial"] = filtered_coords
    filtered.write_h5ad(str(results_path))

    summary = SpatialWorkflowSummary(
        input_path=str(input_path),
        spots_input=int(input_adata.n_obs),
        genes_input=int(input_adata.n_vars),
        spots_retained=int(filtered.n_obs),
        genes_retained=int(filtered.n_vars),
        domains_detected=int(domain_count),
        markers_written=len(marker_rows),
        filtered_zero_count_spots=int(removed_spots),
    )
    qc_path.write_text(json.dumps(summary.__dict__, indent=2) + "\n", encoding="utf-8")
    summary_md_path.write_text(
        (
            "# Spatial Transcriptomics Summary\n\n"
            f"- Spots input: `{summary.spots_input}`\n"
            f"- Spots retained: `{summary.spots_retained}`\n"
            f"- Genes input: `{summary.genes_input}`\n"
            f"- Genes retained: `{summary.genes_retained}`\n"
            f"- Domains detected: `{summary.domains_detected}`\n"
            f"- Marker rows written: `{summary.markers_written}`\n"
            f"- Zero-count spots removed: `{summary.filtered_zero_count_spots}`\n"
        ),
        encoding="utf-8",
    )
    return dict(summary.__dict__)


def main() -> int:
    """CLI entrypoint for the deterministic spatial workflow."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-path", required=True, help="Input spatial AnnData `.h5ad` file.")
    parser.add_argument("--output-dir", required=True, help="Output directory.")
    parser.add_argument("--domain-assignments-csv", default="", help="Optional explicit assignments CSV path.")
    parser.add_argument("--marker-genes-csv", default="", help="Optional explicit marker CSV path.")
    parser.add_argument("--results-h5ad", default="", help="Optional explicit processed AnnData output path.")
    parser.add_argument("--min-genes", type=int, default=3)
    parser.add_argument("--min-cells", type=int, default=2)
    parser.add_argument("--n-hvgs", type=int, default=50)
    parser.add_argument("--n-pcs", type=int, default=10)
    args = parser.parse_args()

    try:
        summary = run_spatial_transcriptomics_workflow(
            input_path=Path(args.input_path).expanduser().resolve(),
            output_dir=Path(args.output_dir).expanduser().resolve(),
            domain_assignments_csv=Path(args.domain_assignments_csv).expanduser().resolve() if args.domain_assignments_csv else None,
            marker_genes_csv=Path(args.marker_genes_csv).expanduser().resolve() if args.marker_genes_csv else None,
            results_h5ad=Path(args.results_h5ad).expanduser().resolve() if args.results_h5ad else None,
            min_genes=int(args.min_genes),
            min_cells=int(args.min_cells),
            n_hvgs=int(args.n_hvgs),
            n_pcs=int(args.n_pcs),
        )
    except ValueError as exc:
        print(f"__FORMAT_INPUT_ERROR__:{exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
