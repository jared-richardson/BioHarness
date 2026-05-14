#!/usr/bin/env python3
"""Infer a phylogenetic tree from a multi-sequence FASTA using Biopython.

This helper is intended for benchmark-safe strict runs when native phylogeny
tooling is unavailable. It computes a pairwise distance matrix from global
sequence alignments and writes a Neighbor-Joining tree in Newick format.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from Bio import Phylo, SeqIO
from Bio.Align import PairwiseAligner
from Bio.Phylo.TreeConstruction import DistanceMatrix, DistanceTreeConstructor


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for phylogeny inference."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-fasta", required=True, help="Input multi-sequence FASTA file.")
    parser.add_argument("--output-tree", required=True, help="Output Newick tree path.")
    return parser.parse_args(argv)


def _pairwise_distance(seq_a: str, seq_b: str) -> float:
    """Compute a normalized distance from a global alignment score."""

    aligner = PairwiseAligner(mode="global")
    aligner.match_score = 2.0
    aligner.mismatch_score = -1.0
    aligner.open_gap_score = -5.0
    aligner.extend_gap_score = -0.5
    score = aligner.score(seq_a, seq_b)
    max_score = 2.0 * max(len(seq_a), len(seq_b), 1)
    similarity = max(0.0, min(1.0, score / max_score))
    return 1.0 - similarity


def _build_distance_matrix(input_fasta: Path) -> DistanceMatrix:
    """Build a Biopython distance matrix from a FASTA file."""

    records = list(SeqIO.parse(str(input_fasta), "fasta"))
    if len(records) < 3:
        raise ValueError(f"Expected at least 3 sequences in {input_fasta}, found {len(records)}")

    names = [str(record.id) for record in records]
    matrix: list[list[float]] = []
    for row_idx, record_a in enumerate(records):
        row: list[float] = []
        for col_idx in range(row_idx + 1):
            if row_idx == col_idx:
                row.append(0.0)
                continue
            record_b = records[col_idx]
            row.append(_pairwise_distance(str(record_a.seq), str(record_b.seq)))
        matrix.append(row)
    return DistanceMatrix(names=names, matrix=matrix)


def _sanitize_branch_lengths(tree) -> None:
    """Clamp negative branch lengths to zero for validator compatibility."""

    for clade in tree.find_clades():
        branch_length = getattr(clade, "branch_length", None)
        if branch_length is None:
            continue
        if branch_length < 0:
            clade.branch_length = 0.0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the helper script."""

    args = _parse_args(argv)
    input_fasta = Path(args.input_fasta).expanduser().resolve(strict=False)
    output_tree = Path(args.output_tree).expanduser().resolve(strict=False)
    if not input_fasta.exists():
        raise FileNotFoundError(f"Input FASTA not found: {input_fasta}")

    distance_matrix = _build_distance_matrix(input_fasta)
    constructor = DistanceTreeConstructor()
    tree = constructor.nj(distance_matrix)
    _sanitize_branch_lengths(tree)

    output_tree.parent.mkdir(parents=True, exist_ok=True)
    Phylo.write(tree, str(output_tree), "newick")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
