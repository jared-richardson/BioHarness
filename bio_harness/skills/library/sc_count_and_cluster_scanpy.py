"""Scanpy analysis helpers for the single-cell counting skill."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run_scanpy_pipeline(
    count_mat: dict[str, dict[str, int]],
    gene_names: set[str],
    output_dir: str,
    min_genes: int = 5,
    min_cells: int = 1,
    leiden_resolution: float = 0.5,
    n_hvgs: int = 200,
) -> dict:
    """Run the Scanpy clustering pipeline on one count matrix."""

    import anndata as ad
    import numpy as np
    import scanpy as sc

    all_genes = sorted(gene_names)
    all_barcodes = sorted(count_mat.keys())
    gene_to_idx = {gene: idx for idx, gene in enumerate(all_genes)}

    mat = np.zeros((len(all_barcodes), len(all_genes)), dtype=np.float32)
    for row_idx, barcode in enumerate(all_barcodes):
        for gene, count in count_mat[barcode].items():
            if gene in gene_to_idx:
                mat[row_idx, gene_to_idx[gene]] = count

    adata = ad.AnnData(X=mat)
    adata.obs_names = all_barcodes
    adata.var_names = all_genes
    print(f"  AnnData: {adata.n_obs} cells × {adata.n_vars} genes", file=sys.stderr)

    sc.pp.filter_cells(adata, min_genes=min_genes)
    sc.pp.filter_genes(adata, min_cells=min_cells)
    print(f"  After QC: {adata.n_obs} cells × {adata.n_vars} genes", file=sys.stderr)

    if adata.n_obs == 0:
        print("  ERROR: No cells passed QC", file=sys.stderr)
        return {"clusters": {}, "markers": {}}

    adata.raw = adata.copy()
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    n_hvg = min(n_hvgs, adata.n_vars)
    if n_hvg > 10:
        sc.pp.highly_variable_genes(adata, n_top_genes=n_hvg)
        adata_hvg = adata[:, adata.var.highly_variable].copy()
    else:
        adata_hvg = adata.copy()

    n_pcs = min(50, adata_hvg.n_vars - 1, adata_hvg.n_obs - 1)
    if n_pcs < 2:
        print("  WARNING: Too few features for PCA", file=sys.stderr)
        return {"clusters": {}, "markers": {}}
    sc.tl.pca(adata_hvg, n_comps=n_pcs)

    n_neighbors = min(15, adata_hvg.n_obs - 1)
    sc.pp.neighbors(adata_hvg, n_neighbors=n_neighbors, n_pcs=min(n_pcs, 20))
    sc.tl.leiden(adata_hvg, resolution=leiden_resolution, flavor="igraph", n_iterations=2)
    adata.obs["leiden"] = adata_hvg.obs["leiden"]

    adata_for_markers = adata.copy()
    sc.tl.rank_genes_groups(adata_for_markers, groupby="leiden", method="wilcoxon")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    clusters: dict[str, str] = {}
    for barcode, cluster in zip(adata.obs_names, adata.obs["leiden"]):
        clusters[barcode] = str(cluster)

    markers: dict[str, list[str]] = {}
    result = adata_for_markers.uns.get("rank_genes_groups")
    if result is not None:
        cluster_ids = [str(cluster) for cluster in sorted(adata.obs["leiden"].unique())]
        for cluster_id in cluster_ids:
            try:
                gene_list = (
                    result["names"][cluster_id]
                    if isinstance(result["names"], dict)
                    else [row[int(cluster_id)] for row in result["names"]]
                )
                markers[cluster_id] = [str(gene) for gene in gene_list[:20]]
            except (KeyError, IndexError):
                pass
        if not markers and hasattr(result["names"], "dtype"):
            for cluster_id in result["names"].dtype.names or []:
                markers[str(cluster_id)] = [str(gene) for gene in result["names"][cluster_id][:20]]

    adata.write_h5ad(str(out_dir / "adata.h5ad"))
    assignments_path = out_dir / "cluster_assignments.json"
    assignments_path.write_text(json.dumps(clusters, indent=2), encoding="utf-8")
    markers_path = out_dir / "marker_genes.json"
    markers_path.write_text(json.dumps(markers, indent=2), encoding="utf-8")

    print(f"  Clusters found: {len(set(clusters.values()))}", file=sys.stderr)
    for cluster_id in sorted(set(clusters.values())):
        size = sum(1 for value in clusters.values() if value == cluster_id)
        top = markers.get(cluster_id, [])[:5]
        print(f"    Cluster {cluster_id}: {size} cells, top markers: {top}", file=sys.stderr)

    print(f"  Saved: {out_dir / 'adata.h5ad'}", file=sys.stderr)
    print(f"  Saved: {assignments_path}", file=sys.stderr)
    print(f"  Saved: {markers_path}", file=sys.stderr)
    return {"clusters": clusters, "markers": markers}
