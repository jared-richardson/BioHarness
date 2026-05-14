#!/usr/bin/env python3
"""Classify metagenomics read pairs against a staged bacterial reference panel."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Sequence

from bio_harness.pipeline_scripts.reference_kmer_classifier import (
    build_reference_index,
    choose_best_accession,
    discover_reference_fastas,
    iter_fastq_pairs,
    load_reference_records,
    score_read_pair,
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reads-1", required=True)
    parser.add_argument("--reads-2", required=True)
    parser.add_argument("--reference-dir", required=True)
    parser.add_argument("--taxonomy-tsv", required=True)
    parser.add_argument("--output-report", required=True)
    parser.add_argument("--kmer-size", type=int, default=31)
    return parser.parse_args(argv)


def _load_taxonomy_lookup(path: Path) -> dict[str, tuple[int, str]]:
    """Load a lowercase taxon-name lookup from ktaxonomy.tsv."""

    lookup: dict[str, tuple[int, str]] = {}
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            parts = [token.strip() for token in raw_line.split("|")]
            if len(parts) < 5:
                continue
            try:
                taxid = int(parts[0])
            except ValueError:
                continue
            rank = parts[2]
            name = parts[4].lower()
            if name:
                lookup.setdefault(name, (taxid, rank))
    return lookup


def _species_name(description: str) -> str:
    """Extract a species label from a FASTA description."""

    tokens = description.split()
    if len(tokens) >= 3:
        return " ".join(tokens[1:3])
    return tokens[-1] if tokens else description


def _genus_name(species_name: str) -> str:
    """Extract the genus token from a species label."""

    return species_name.split()[0] if species_name else species_name


def main(argv: Sequence[str] | None = None) -> int:
    """Run metagenomics reference classification."""

    args = _parse_args(argv)
    reads_1 = Path(args.reads_1).expanduser().resolve(strict=False)
    reads_2 = Path(args.reads_2).expanduser().resolve(strict=False)
    reference_dir = Path(args.reference_dir).expanduser().resolve(strict=False)
    taxonomy_tsv = Path(args.taxonomy_tsv).expanduser().resolve(strict=False)
    output_report = Path(args.output_report).expanduser().resolve(strict=False)

    reference_paths = discover_reference_fastas(reference_dir)
    if not reference_paths:
        raise FileNotFoundError(f"No reference FASTA files found under {reference_dir}")
    if not taxonomy_tsv.exists():
        raise FileNotFoundError(f"Taxonomy TSV not found: {taxonomy_tsv}")

    records = load_reference_records(reference_paths)
    index = build_reference_index(records, kmer_size=max(5, int(args.kmer_size)))
    taxonomy = _load_taxonomy_lookup(taxonomy_tsv)

    species_counts: Counter[str] = Counter()
    genus_counts: Counter[str] = Counter()
    classified = 0
    unclassified = 0

    accession_to_species = {record.accession: _species_name(record.description) for record in records}
    for read_pair in iter_fastq_pairs(reads_1, reads_2):
        votes, _ = score_read_pair(read_pair, index=index, kmer_size=max(5, int(args.kmer_size)))
        accession = choose_best_accession(votes)
        if not accession:
            unclassified += 1
            continue
        species_name = accession_to_species[accession]
        genus_name = _genus_name(species_name)
        species_counts[species_name] += 1
        genus_counts[genus_name] += 1
        classified += 1

    total = classified + unclassified
    output_report.parent.mkdir(parents=True, exist_ok=True)
    with output_report.open("w", encoding="utf-8") as handle:
        if total == 0:
            raise ValueError("No read pairs were classified from the provided FASTQs.")
        handle.write(f"{(100.0 * unclassified / total):.2f}\t{unclassified}\t{unclassified}\tU\t0\tunclassified\n")
        handle.write(f"{(100.0 * classified / total):.2f}\t{classified}\t{classified}\tR\t1\troot\n")
        for genus_name, count in genus_counts.most_common():
            taxid = taxonomy.get(genus_name.lower(), (0, ""))[0]
            handle.write(f"{(100.0 * count / total):.2f}\t{count}\t0\tG\t{taxid}\t{genus_name}\n")
        for species_name, count in species_counts.most_common():
            taxid = taxonomy.get(species_name.lower(), (0, ""))[0]
            handle.write(f"{(100.0 * count / total):.2f}\t{count}\t{count}\tS\t{taxid}\t{species_name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
