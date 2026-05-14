#!/usr/bin/env python3
"""Create synthetic Visium spatial transcriptomics benchmark data — 6 cases.

Cases:
  clean_visium         — 200-spot, 3 spatial domains, 5 markers each
  noisy_prompt         — same data, vague prompt
  coordinate_ambiguity — same data, prompt hints at pixel-space coords
  mild_fragmentation   — 20% of spots zeroed out
  nested_output        — same data, prompt requests nested path
  malformed_coords     — 10% of spatial coords set to NaN
"""
import json
import shutil
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import scipy.sparse as sp

BASE = Path("workspace/benchmark_data/spatial")
SEED = 42
N_SPOTS = 200
N_GENES = 100
N_DOMAINS = 3
MARKERS_PER_DOMAIN = 5


def _create_adata(rng: np.random.RandomState):
    coords = np.array([(i, j) for i in range(15) for j in range(15)])[:N_SPOTS]

    domain_labels = np.array(
        [
            "DomainA" if x < 5 else "DomainB" if x < 10 else "DomainC"
            for x, y in coords
        ]
    )

    gene_names = [f"Gene_{i}" for i in range(N_GENES)]
    X = rng.poisson(5, (N_SPOTS, N_GENES)).astype(float)

    for d_idx, domain in enumerate(["DomainA", "DomainB", "DomainC"]):
        mask = domain_labels == domain
        ms = d_idx * MARKERS_PER_DOMAIN
        me = ms + MARKERS_PER_DOMAIN
        X[mask, ms:me] += rng.poisson(30, (mask.sum(), MARKERS_PER_DOMAIN))

    adata = ad.AnnData(
        X=sp.csr_matrix(X),
        obs=pd.DataFrame(
            {
                "domain_truth": domain_labels,
                "in_tissue": 1,
                "array_row": coords[:, 0],
                "array_col": coords[:, 1],
            },
            index=[f"spot_{i}" for i in range(N_SPOTS)],
        ),
        var=pd.DataFrame(index=gene_names),
    )
    adata.obsm["spatial"] = coords.astype(float)
    return adata, gene_names


def write_truth(out_dir, gene_names):
    with open(out_dir / "truth.json", "w") as f:
        json.dump(
            {
                "n_spots": N_SPOTS,
                "n_domains": N_DOMAINS,
                "domain_names": ["DomainA", "DomainB", "DomainC"],
                "markers": {
                    "DomainA": gene_names[:5],
                    "DomainB": gene_names[5:10],
                    "DomainC": gene_names[10:15],
                },
            },
            f,
            indent=2,
        )


def create_clean():
    rng = np.random.RandomState(SEED)
    adata, gene_names = _create_adata(rng)
    out = BASE / "clean_visium" / "data"
    out.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(str(out / "visium_data.h5ad"))
    write_truth(out, gene_names)
    return adata, gene_names


def create_mild_fragmentation():
    rng = np.random.RandomState(SEED)
    adata, gene_names = _create_adata(rng)
    # Zero out 20% of spots
    zero_mask = rng.random(N_SPOTS) < 0.20
    X_dense = adata.X.toarray()
    X_dense[zero_mask] = 0
    adata.X = sp.csr_matrix(X_dense)
    out = BASE / "mild_fragmentation" / "data"
    out.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(str(out / "visium_data.h5ad"))
    write_truth(out, gene_names)


def create_malformed_coords():
    rng = np.random.RandomState(SEED)
    adata, gene_names = _create_adata(rng)
    # NaN 10% of spatial coords
    coords = adata.obsm["spatial"].copy()
    nan_mask = rng.random(N_SPOTS) < 0.10
    coords[nan_mask] = np.nan
    adata.obsm["spatial"] = coords
    out = BASE / "malformed_coords" / "data"
    out.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(str(out / "visium_data.h5ad"))
    write_truth(out, gene_names)


def copy_clean_to_variant(variant_name):
    src = BASE / "clean_visium" / "data"
    dst = BASE / variant_name / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        shutil.copy2(f, dst / f.name)


def write_prompts():
    prompts = {
        "clean_visium": (
            "Analyze this Visium spatial transcriptomics dataset. Identify spatial "
            "domains and marker genes for each domain."
        ),
        "noisy_prompt": (
            "I have some spatial gene expression data from a tissue section. Can "
            "you figure out what regions are different and which genes define them?"
        ),
        "coordinate_ambiguity": (
            "Run spatial analysis on this h5ad file. The coordinates might be in "
            "pixel space rather than array indices."
        ),
        "mild_fragmentation": (
            "Analyze this spatial transcriptomics dataset. Identify spatial domains "
            "and marker genes. Note: some spots may have low or zero expression."
        ),
        "nested_output": (
            "Analyze this Visium spatial transcriptomics data. Save all results to "
            "spatial_results/analysis/"
        ),
        "malformed_coords": (
            "Run spatial domain identification on this Visium dataset. Some spot "
            "coordinates may be missing or malformed."
        ),
    }
    for name, text in prompts.items():
        p = BASE / name / "prompt.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")


def main():
    print("Creating spatial benchmark data...")
    adata, gene_names = create_clean()
    print(f"  clean_visium: {N_SPOTS} spots, {N_GENES} genes, {N_DOMAINS} domains")

    # Copy clean data to variants that only differ in prompt
    for v in ["noisy_prompt", "coordinate_ambiguity", "nested_output"]:
        copy_clean_to_variant(v)
        print(f"  {v}: copied from clean_visium")

    create_mild_fragmentation()
    print("  mild_fragmentation: 20% zeroed spots")

    create_malformed_coords()
    print("  malformed_coords: 10% NaN coordinates")

    write_prompts()
    print("Done: 6 spatial cases")


if __name__ == "__main__":
    main()
