#!/usr/bin/env python3
"""Single-cell RNA-seq counting and clustering pipeline.

Skill wrapper function: ``sc_count_and_cluster(**kwargs)`` returns a shell
command that runs this script with the appropriate arguments.

Pipeline handles 10x Chromium-format FASTQs:
  1. Demultiplex reads by cell barcode (from R1)
  2. Map cDNA reads (R2) to gene bodies
  3. Deduplicate by UMI
  4. Build count matrix
  5. Scanpy: QC → Normalize → HVG → PCA → Neighbors → Leiden → Markers

Inputs:
  - R1 FASTQ (barcode + UMI)
  - R2 FASTQ (cDNA)
  - Optional barcode whitelist
  - Reference FASTA + GTF annotation

Outputs:
  - AnnData h5ad with clusters
  - Cell type assignments CSV
  - Marker genes CSV
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path


def _ensure_repo_root_on_sys_path() -> None:
    """Add the repository root to ``sys.path`` for direct script execution."""

    repo_root = Path(__file__).resolve().parents[3]
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)


_ensure_repo_root_on_sys_path()

from bio_harness.skills.library.sc_count_and_cluster_fastq import (  # noqa: E402
    infer_barcode_whitelist,
    load_whitelist_barcodes,
    read_fastq_pairs,
    resolve_whitelist_path,
)
from bio_harness.skills.library.sc_count_and_cluster_mapping import (  # noqa: E402
    build_gene_kmer_index,
    count_matrix,
    count_matrix_from_kmers,
    load_genome,
    map_read_to_gene,
    parse_gtf_genes,
)
from bio_harness.skills.library.sc_count_and_cluster_scanpy import run_scanpy_pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# Skill wrapper function (called by the harness orchestrator)
# ---------------------------------------------------------------------------
_SCRIPT_PATH = str(Path(__file__).resolve())

__all__ = [
    "build_gene_kmer_index",
    "count_matrix",
    "count_matrix_from_kmers",
    "infer_barcode_whitelist",
    "load_genome",
    "load_whitelist_barcodes",
    "map_read_to_gene",
    "parse_gtf_genes",
    "read_fastq_pairs",
    "resolve_whitelist_path",
    "run_scanpy_pipeline",
    "sc_count_and_cluster",
]


def sc_count_and_cluster(**kwargs) -> str:
    """Return shell command to run the single-cell pipeline.

    Expected kwargs: ``r1``, ``r2``, ``reference``, ``gtf``, ``output_dir``,
    and optional ``whitelist``, ``barcode_len``, ``umi_len``, ``kmer_size``,
    ``min_genes``, ``min_cells``, ``leiden_resolution``, ``n_hvgs``.
    """
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    # Resolve Python interpreter (prefer pixi environment)
    python_bin = os.getenv("BIO_HARNESS_PYTHON", "")
    if not python_bin:
        pixi_python = Path(__file__).resolve().parents[3] / ".pixi" / "envs" / "default" / "bin" / "python3"
        if pixi_python.is_file():
            python_bin = str(pixi_python)
        else:
            python_bin = sys.executable or "python3"

    parts = [shlex.quote(python_bin), shlex.quote(_SCRIPT_PATH)]

    # Required arguments
    for arg in ("r1", "r2", "reference", "gtf", "output_dir"):
        val = str(kwargs.get(arg, "")).strip()
        if not val:
            # Also check underscore/dash variants
            alt = arg.replace("_", "-")
            val = str(kwargs.get(alt, "")).strip()
        if not val:
            raise ValueError(f"Missing required argument: {arg}")
        cli_flag = f"--{arg.replace('_', '-')}"
        parts.extend([cli_flag, shlex.quote(val)])

    whitelist = str(kwargs.get("whitelist", "") or kwargs.get("white-list", "") or "").strip()
    if whitelist:
        parts.extend(["--whitelist", shlex.quote(whitelist)])

    # Optional arguments
    for opt in ("barcode_len", "umi_len", "kmer_size", "min_genes", "min_cells", "leiden_resolution", "n_hvgs"):
        val = kwargs.get(opt, None)
        if val is not None:
            cli_flag = f"--{opt.replace('_', '-')}"
            parts.extend([cli_flag, shlex.quote(str(val))])

    return " ".join(parts)


def main() -> None:
    """Run the single-cell counting and clustering CLI entrypoint."""

    parser = argparse.ArgumentParser(description="Single-cell counting + clustering")
    parser.add_argument("--r1", required=True, help="R1 FASTQ (barcode+UMI)")
    parser.add_argument("--r2", required=True, help="R2 FASTQ (cDNA)")
    parser.add_argument(
        "--whitelist",
        required=False,
        default="",
        help="Optional barcode whitelist. When omitted, the wrapper infers one from R1.",
    )
    parser.add_argument("--reference", required=True, help="Reference genome FASTA")
    parser.add_argument("--gtf", required=True, help="Gene annotation GTF")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--barcode-len", type=int, default=16)
    parser.add_argument("--umi-len", type=int, default=12)
    parser.add_argument("--kmer-size", type=int, default=30)
    parser.add_argument("--min-genes", type=int, default=5)
    parser.add_argument("--min-cells", type=int, default=1)
    parser.add_argument("--leiden-resolution", type=float, default=0.5)
    parser.add_argument("--n-hvgs", type=int, default=200)
    args = parser.parse_args()

    print("=== Single-cell RNA-seq Pipeline ===", file=sys.stderr)
    resolved_whitelist = resolve_whitelist_path(
        args.whitelist,
        r1_path=args.r1,
        output_dir=args.output_dir,
        barcode_len=args.barcode_len,
    )

    # Step 1: Count
    print("\n--- Step 1: Counting ---", file=sys.stderr)
    count_mat, gene_names = count_matrix(
        r1_path=args.r1,
        r2_path=args.r2,
        whitelist_path=resolved_whitelist,
        reference_fasta=args.reference,
        gtf_path=args.gtf,
        barcode_len=args.barcode_len,
        umi_len=args.umi_len,
        kmer_size=args.kmer_size,
    )

    # Save raw counts
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts_path = out_dir / "raw_counts.json"
    with open(counts_path, "w") as f:
        json.dump(count_mat, f, indent=2)
    print(f"  Raw counts saved: {counts_path}", file=sys.stderr)

    # Step 2: Cluster
    print("\n--- Step 2: Clustering ---", file=sys.stderr)
    results = run_scanpy_pipeline(
        count_mat=count_mat,
        gene_names=gene_names,
        output_dir=args.output_dir,
        min_genes=args.min_genes,
        min_cells=args.min_cells,
        leiden_resolution=args.leiden_resolution,
        n_hvgs=args.n_hvgs,
    )

    print(f"\n=== Done: {len(results['clusters'])} cells in {len(set(results['clusters'].values()))} clusters ===", file=sys.stderr)


if __name__ == "__main__":
    main()
