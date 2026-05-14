#!/usr/bin/env python3
"""Create synthetic DGE + pathway enrichment benchmark data.

Generates:
  - A count matrix (100 genes x 6 samples)
  - Sample metadata (condition: control/treatment)
  - GMT gene set file (4 pathways)
  - Truth data: DE genes, enriched pathways, expected directions

Architecture:
  - 4 pathways: APOPTOSIS_SIGNALING (15 genes, 12 DE up),
    CELL_CYCLE_REGULATION (15 genes, 10 DE down),
    INFLAMMATION_RESPONSE (15 genes, 0 DE),
    HOUSEKEEPING (55 genes, 0 DE)
  - 6 samples: 3 control + 3 treatment
  - Counts from Poisson(lambda) with shifted lambda for DE genes in treatment
"""

import json
import random
from pathlib import Path

import numpy as np

SEED = 42
OUT_DIR = Path(__file__).resolve().parent.parent / "benchmark_data" / "dge_pathway"

# Gene counts per pathway
PATHWAYS = {
    "APOPTOSIS_SIGNALING": {"prefix": "APOP", "total": 15, "n_de": 12, "direction": "up"},
    "CELL_CYCLE_REGULATION": {"prefix": "CCYC", "total": 15, "n_de": 10, "direction": "down"},
    "INFLAMMATION_RESPONSE": {"prefix": "INFL", "total": 15, "n_de": 0, "direction": None},
    "HOUSEKEEPING": {"prefix": "HK", "total": 55, "n_de": 0, "direction": None},
}

SAMPLES_CONTROL = ["control_1", "control_2", "control_3"]
SAMPLES_TREATMENT = ["treatment_1", "treatment_2", "treatment_3"]
ALL_SAMPLES = SAMPLES_CONTROL + SAMPLES_TREATMENT

# Count generation parameters
BASE_LAMBDA = 50  # base expression level
DE_UP_FOLD = 3.0  # fold change for upregulated genes
DE_DOWN_FOLD = 0.3  # fold change for downregulated genes


def main():
    rng = np.random.default_rng(SEED)
    random.seed(SEED)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    genes = []
    pathway_members = {}
    de_genes = []
    de_directions = {}

    for pw_name, pw_info in PATHWAYS.items():
        prefix = pw_info["prefix"]
        pw_genes = [f"{prefix}_{i+1:02d}" for i in range(pw_info["total"])]
        pathway_members[pw_name] = pw_genes
        genes.extend(pw_genes)

        # Mark DE genes (first n_de in each pathway)
        for i, g in enumerate(pw_genes):
            if i < pw_info["n_de"]:
                de_genes.append(g)
                de_directions[g] = pw_info["direction"]

    n_genes = len(genes)
    n_samples = len(ALL_SAMPLES)
    assert n_genes == 100

    # Generate count matrix
    counts = np.zeros((n_genes, n_samples), dtype=int)
    for i, gene in enumerate(genes):
        # Vary base expression per gene to be realistic
        gene_base = rng.integers(20, 200)
        for j, sample in enumerate(ALL_SAMPLES):
            lam = gene_base
            if gene in de_directions and sample.startswith("treatment"):
                if de_directions[gene] == "up":
                    lam = int(gene_base * DE_UP_FOLD)
                else:
                    lam = max(1, int(gene_base * DE_DOWN_FOLD))
            # Add per-sample noise
            lam = max(1, int(lam * rng.uniform(0.85, 1.15)))
            counts[i, j] = rng.poisson(lam)

    # Write counts.csv
    with open(OUT_DIR / "counts.csv", "w") as f:
        f.write("gene_id," + ",".join(ALL_SAMPLES) + "\n")
        for i, gene in enumerate(genes):
            f.write(gene + "," + ",".join(str(c) for c in counts[i]) + "\n")

    # Write metadata.tsv
    with open(OUT_DIR / "metadata.tsv", "w") as f:
        f.write("sample_id\tcondition\n")
        for s in SAMPLES_CONTROL:
            f.write(f"{s}\tcontrol\n")
        for s in SAMPLES_TREATMENT:
            f.write(f"{s}\ttreatment\n")

    # Write pathways.gmt
    with open(OUT_DIR / "pathways.gmt", "w") as f:
        for pw_name, pw_genes in pathway_members.items():
            f.write(pw_name + "\t" + pw_name + "_description\t" + "\t".join(pw_genes) + "\n")

    # Write truth.json
    enriched_pathways = [pw for pw, info in PATHWAYS.items() if info["n_de"] > 0]
    non_enriched_pathways = [pw for pw, info in PATHWAYS.items() if info["n_de"] == 0]

    truth = {
        "de_genes": sorted(de_genes),
        "de_gene_count": len(de_genes),
        "total_genes": n_genes,
        "enriched_pathways": enriched_pathways,
        "non_enriched_pathways": non_enriched_pathways,
        "expected_de_direction": {
            "APOP_*": "up",
            "CCYC_*": "down",
        },
    }
    with open(OUT_DIR / "truth.json", "w") as f:
        json.dump(truth, f, indent=2)

    print(f"Created benchmark data in {OUT_DIR}")
    print(f"  Genes: {n_genes}")
    print(f"  Samples: {n_samples}")
    print(f"  DE genes: {len(de_genes)}")
    print(f"  Pathways: {len(pathway_members)}")
    print(f"  Enriched: {enriched_pathways}")
    print("  Files: counts.csv, metadata.tsv, pathways.gmt, truth.json")


if __name__ == "__main__":
    main()
