#!/usr/bin/env python3
"""Deterministic Scanpy preprocessing/clustering workflow."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import anndata as ad
import scanpy as sc


def _load_input_adata(input_path: Path) -> tuple[ad.AnnData, str]:
    if input_path.is_dir():
        matrix_candidates = ("matrix.mtx.gz", "matrix.mtx")
        if any((input_path / candidate).exists() for candidate in matrix_candidates):
            return sc.read_10x_mtx(str(input_path), var_names="gene_symbols", cache=False), "10x_mtx_dir"
        raise ValueError(f"Unsupported directory input for scanpy_workflow: {input_path}")

    lower_name = input_path.name.lower()
    if lower_name.endswith(".h5ad"):
        return sc.read_h5ad(str(input_path)), "h5ad"
    if lower_name.endswith(".loom"):
        return sc.read_loom(str(input_path)), "loom"
    if lower_name.endswith(".h5"):
        return sc.read_10x_h5(str(input_path)), "10x_h5"
    if lower_name.endswith(".mtx") or lower_name.endswith(".mtx.gz"):
        matrix_dir = input_path.parent
        if any((matrix_dir / candidate).exists() for candidate in ("barcodes.tsv.gz", "barcodes.tsv")):
            return sc.read_10x_mtx(str(matrix_dir), var_names="gene_symbols", cache=False), "10x_mtx_file"
    raise ValueError(f"Unsupported input for scanpy_workflow: {input_path}")


def _base_adata(input_adata: ad.AnnData) -> ad.AnnData:
    if input_adata.raw is not None:
        try:
            raw_adata = input_adata.raw.to_adata()
            if raw_adata.n_obs > 0 and raw_adata.n_vars > 0:
                return raw_adata
        except Exception:
            pass
    return input_adata.copy()


def _annotate_mito_fraction(adata: ad.AnnData) -> None:
    mito_mask = [str(name).upper().startswith("MT-") for name in adata.var_names]
    adata.var["mt"] = mito_mask
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None, log1p=False, inplace=True)
    if "pct_counts_mt" not in adata.obs:
        adata.obs["pct_counts_mt"] = 0.0


def _extract_marker_rows(adata: ad.AnnData) -> list[dict[str, Any]]:
    marker_rows: list[dict[str, Any]] = []
    cluster_ids = sorted({str(cluster_id) for cluster_id in adata.obs.get("leiden", [])})
    if len(cluster_ids) < 2:
        return marker_rows
    try:
        sc.tl.rank_genes_groups(adata, groupby="leiden", method="wilcoxon")
        markers_df = sc.get.rank_genes_groups_df(adata, group=None)
    except Exception:
        return marker_rows
    if "group" not in markers_df or "names" not in markers_df:
        return marker_rows
    for cluster_id, cluster_rows in markers_df.groupby("group", sort=True):
        top_rows = cluster_rows.head(20)
        for rank_index, (_, row) in enumerate(top_rows.iterrows(), start=1):
            marker_rows.append(
                {
                    "cluster_id": str(cluster_id),
                    "rank": rank_index,
                    "gene_name": str(row.get("names", "")),
                    "score": float(row.get("scores", 0.0) or 0.0),
                    "logfoldchanges": float(row.get("logfoldchanges", 0.0) or 0.0),
                    "pvals_adj": float(row.get("pvals_adj", 1.0) or 1.0),
                }
            )
    return marker_rows


def run_scanpy_workflow(
    *,
    input_path: Path,
    output_dir: Path,
    min_genes: int = 300,
    min_cells: int = 20,
    max_mito_pct: float = 15.0,
    n_hvgs: int = 2000,
    leiden_resolution: float = 0.3,
) -> dict[str, Any]:
    input_adata, source_kind = _load_input_adata(input_path)
    adata = _base_adata(input_adata)
    adata.obs_names_make_unique()
    adata.var_names_make_unique()
    if adata.n_obs == 0 or adata.n_vars == 0:
        raise ValueError("Input AnnData is empty")

    _annotate_mito_fraction(adata)
    sc.pp.filter_cells(adata, min_genes=max(0, int(min_genes)))
    sc.pp.filter_genes(adata, min_cells=max(0, int(min_cells)))
    if adata.n_obs == 0 or adata.n_vars == 0:
        raise ValueError("No cells or genes remain after QC filtering")
    if "pct_counts_mt" in adata.obs:
        adata = adata[adata.obs["pct_counts_mt"] <= float(max_mito_pct)].copy()
    if adata.n_obs == 0 or adata.n_vars == 0:
        raise ValueError("No cells remain after mitochondrial filtering")

    adata.layers["counts"] = adata.X.copy()
    adata.raw = adata.copy()
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    n_top_genes = min(max(1, int(n_hvgs)), adata.n_vars)
    if n_top_genes >= 2:
        sc.pp.highly_variable_genes(adata, n_top_genes=n_top_genes, flavor="seurat")
        if "highly_variable" in adata.var and int(adata.var["highly_variable"].sum()) >= 2:
            adata_hvg = adata[:, adata.var["highly_variable"]].copy()
        else:
            adata_hvg = adata.copy()
    else:
        adata_hvg = adata.copy()

    if adata_hvg.n_obs >= 3 and adata_hvg.n_vars >= 2:
        n_pcs = min(50, adata_hvg.n_obs - 1, adata_hvg.n_vars - 1)
        if n_pcs >= 2:
            sc.tl.pca(adata_hvg, n_comps=n_pcs)
            n_neighbors = min(15, adata_hvg.n_obs - 1)
            if n_neighbors >= 2:
                sc.pp.neighbors(adata_hvg, n_neighbors=n_neighbors, n_pcs=min(n_pcs, 20))
                sc.tl.leiden(adata_hvg, resolution=float(leiden_resolution), flavor="igraph", n_iterations=2)
                try:
                    sc.tl.umap(adata_hvg)
                except Exception:
                    pass
                adata.obs["leiden"] = adata_hvg.obs["leiden"].astype(str)
    if "leiden" not in adata.obs:
        adata.obs["leiden"] = ["0"] * adata.n_obs

    marker_rows = _extract_marker_rows(adata)
    output_dir.mkdir(parents=True, exist_ok=True)

    processed_path = output_dir / "processed.h5ad"
    adata.write_h5ad(str(processed_path))

    assignments_path = output_dir / "cluster_assignments.csv"
    with assignments_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("cell_id", "cluster_id"))
        writer.writeheader()
        for cell_id, cluster_id in zip(adata.obs_names, adata.obs["leiden"]):
            writer.writerow({"cell_id": str(cell_id), "cluster_id": str(cluster_id)})

    markers_path = output_dir / "marker_genes.csv"
    with markers_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("cluster_id", "rank", "gene_name", "score", "logfoldchanges", "pvals_adj"),
        )
        writer.writeheader()
        for row in marker_rows:
            writer.writerow(row)

    summary = {
        "input_path": str(input_path),
        "source_kind": source_kind,
        "cells": int(adata.n_obs),
        "genes": int(adata.n_vars),
        "cluster_count": len({str(cluster_id) for cluster_id in adata.obs["leiden"]}),
        "marker_row_count": len(marker_rows),
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-path", required=True, help="Input h5ad/loom/10x path.")
    parser.add_argument("--output-dir", required=True, help="Output directory.")
    parser.add_argument("--min-genes", type=int, default=300)
    parser.add_argument("--min-cells", type=int, default=20)
    parser.add_argument("--max-mito-pct", type=float, default=15.0)
    parser.add_argument("--n-hvgs", type=int, default=2000)
    parser.add_argument("--leiden-resolution", type=float, default=0.3)
    args = parser.parse_args()

    summary = run_scanpy_workflow(
        input_path=Path(args.input_path).expanduser().resolve(),
        output_dir=Path(args.output_dir).expanduser().resolve(),
        min_genes=int(args.min_genes),
        min_cells=int(args.min_cells),
        max_mito_pct=float(args.max_mito_pct),
        n_hvgs=int(args.n_hvgs),
        leiden_resolution=float(args.leiden_resolution),
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
