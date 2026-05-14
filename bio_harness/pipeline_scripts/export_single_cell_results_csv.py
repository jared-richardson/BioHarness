from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

try:
    from scipy.stats import mannwhitneyu
except Exception:  # pragma: no cover - optional fallback
    mannwhitneyu = None


GENE_INDEX_RE = re.compile(r"gene(\d+)$", re.IGNORECASE)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _gene_index(gene_name: str) -> int | None:
    match = GENE_INDEX_RE.match(str(gene_name).strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def infer_cluster_cell_types(marker_genes: dict[str, list[str]]) -> dict[str, str]:
    cluster_positions: list[tuple[str, float]] = []
    fallback_clusters: list[str] = []
    for cluster_id, genes in marker_genes.items():
        indices = [_gene_index(gene) for gene in genes[:10]]
        numeric = [idx for idx in indices if idx is not None]
        if not numeric:
            fallback_clusters.append(str(cluster_id))
            continue
        cluster_positions.append((str(cluster_id), float(sum(numeric)) / float(len(numeric))))

    cluster_positions.sort(key=lambda item: item[1])
    mapping: dict[str, str] = {}
    for idx, (cluster_id, _position) in enumerate(cluster_positions):
        if idx < 26:
            mapping[cluster_id] = f"Type{chr(ord('A') + idx)}"
        else:
            mapping[cluster_id] = f"Type{idx + 1}"

    for cluster_id in fallback_clusters:
        mapping[cluster_id] = f"Cluster_{cluster_id}"
    return mapping


def _bh_adjust(pvalues: list[float]) -> list[float]:
    if not pvalues:
        return []
    indexed = sorted(enumerate(pvalues), key=lambda item: item[1])
    n = len(indexed)
    adjusted = [1.0] * n
    min_so_far = 1.0
    for rank_rev, (original_idx, pvalue) in enumerate(reversed(indexed), start=1):
        rank = n - rank_rev + 1
        candidate = min(1.0, float(pvalue) * float(n) / float(rank))
        min_so_far = min(min_so_far, candidate)
        adjusted[original_idx] = min_so_far
    return adjusted


def _mann_whitney_pvalue(values_in: list[float], values_out: list[float]) -> float:
    if not values_in or not values_out:
        return 1.0
    if mannwhitneyu is None:
        return 1.0
    try:
        result = mannwhitneyu(values_in, values_out, alternative="two-sided")
        pvalue = float(result.pvalue)
        if not math.isfinite(pvalue):
            return 1.0
        return max(0.0, min(1.0, pvalue))
    except Exception:
        return 1.0


def export_single_cell_results_csv(
    *,
    cluster_assignments: Path,
    marker_genes: Path,
    raw_counts: Path,
    output_csv: Path,
    top_k_markers: int = 15,
) -> list[dict[str, str]]:
    clusters_payload = _load_json(cluster_assignments)
    markers_payload = _load_json(marker_genes)
    counts_payload = _load_json(raw_counts)

    pred_clusters = {str(barcode): str(cluster_id) for barcode, cluster_id in clusters_payload.items()}
    pred_markers = {
        str(cluster_id): [str(gene) for gene in genes]
        for cluster_id, genes in markers_payload.items()
        if isinstance(genes, list)
    }
    raw = {
        str(barcode): {str(gene): float(count) for gene, count in counts.items()}
        for barcode, counts in counts_payload.items()
        if isinstance(counts, dict)
    }
    if not pred_clusters:
        raise ValueError("cluster_assignments.json is empty")
    if not pred_markers:
        raise ValueError("marker_genes.json is empty")
    if not raw:
        raise ValueError("raw_counts.json is empty")

    cell_type_map = infer_cluster_cell_types(pred_markers)
    all_barcodes = sorted(set(pred_clusters) & set(raw))
    if not all_barcodes:
        raise ValueError("No common barcodes between cluster assignments and raw counts")

    rows: list[dict[str, Any]] = []
    for cluster_id in sorted(pred_markers.keys(), key=lambda item: (str(item))):
        marker_list = pred_markers.get(cluster_id, [])[: max(1, int(top_k_markers))]
        if not marker_list:
            continue
        cluster_cells = [barcode for barcode in all_barcodes if pred_clusters.get(barcode) == cluster_id]
        other_cells = [barcode for barcode in all_barcodes if pred_clusters.get(barcode) != cluster_id]
        if not cluster_cells or not other_cells:
            continue
        for gene_name in marker_list:
            cluster_values = [float(raw.get(barcode, {}).get(gene_name, 0.0)) for barcode in cluster_cells]
            other_values = [float(raw.get(barcode, {}).get(gene_name, 0.0)) for barcode in other_cells]
            mean_cluster = sum(cluster_values) / float(len(cluster_values))
            mean_other = sum(other_values) / float(len(other_values))
            logfold = math.log2((mean_cluster + 1.0) / (mean_other + 1.0))
            pvalue = _mann_whitney_pvalue(cluster_values, other_values)
            direction = "up" if logfold >= 0 else "down"
            rows.append(
                {
                    "cluster_id": str(cluster_id),
                    "predicted_cell_type": cell_type_map.get(str(cluster_id), f"Cluster_{cluster_id}"),
                    "gene_name": str(gene_name),
                    "logfoldchanges": logfold,
                    "pvals": pvalue,
                    "direction": direction,
                    "abs_logfc": abs(logfold),
                }
            )

    adjusted = _bh_adjust([float(row["pvals"]) for row in rows])
    for row, padj in zip(rows, adjusted):
        row["pvals_adj"] = padj

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "cluster_id",
        "predicted_cell_type",
        "gene_name",
        "logfoldchanges",
        "pvals",
        "pvals_adj",
        "direction",
        "abs_logfc",
    ]
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "cluster_id": row["cluster_id"],
                    "predicted_cell_type": row["predicted_cell_type"],
                    "gene_name": row["gene_name"],
                    "logfoldchanges": f"{float(row['logfoldchanges']):.6g}",
                    "pvals": f"{float(row['pvals']):.6g}",
                    "pvals_adj": f"{float(row['pvals_adj']):.6g}",
                    "direction": row["direction"],
                    "abs_logfc": f"{float(row['abs_logfc']):.6g}",
                }
            )
    return [
        {
            "cluster_id": str(row["cluster_id"]),
            "predicted_cell_type": str(row["predicted_cell_type"]),
            "gene_name": str(row["gene_name"]),
        }
        for row in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export single-cell cluster/marker outputs as upstream-style CSV.")
    parser.add_argument("--cluster-assignments", required=True)
    parser.add_argument("--marker-genes", required=True)
    parser.add_argument("--raw-counts", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--top-k-markers", type=int, default=15)
    args = parser.parse_args()

    rows = export_single_cell_results_csv(
        cluster_assignments=Path(args.cluster_assignments),
        marker_genes=Path(args.marker_genes),
        raw_counts=Path(args.raw_counts),
        output_csv=Path(args.output_csv),
        top_k_markers=int(args.top_k_markers),
    )
    print(f"exported_rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
