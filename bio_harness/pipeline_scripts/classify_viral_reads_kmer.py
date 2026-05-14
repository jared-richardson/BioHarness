#!/usr/bin/env python3
"""Classify viral read pairs against a staged reference panel using exact k-mers."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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
    parser.add_argument("--output-report", required=True)
    parser.add_argument("--output-detected", required=True)
    parser.add_argument("--coverage-threshold", type=float, default=50.0)
    parser.add_argument("--kmer-size", type=int, default=21)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run viral reference classification."""

    args = _parse_args(argv)
    reads_1 = Path(args.reads_1).expanduser().resolve(strict=False)
    reads_2 = Path(args.reads_2).expanduser().resolve(strict=False)
    reference_dir = Path(args.reference_dir).expanduser().resolve(strict=False)
    output_report = Path(args.output_report).expanduser().resolve(strict=False)
    output_detected = Path(args.output_detected).expanduser().resolve(strict=False)

    reference_paths = discover_reference_fastas(reference_dir)
    if not reference_paths:
        raise FileNotFoundError(f"No reference FASTA files found under {reference_dir}")

    records = load_reference_records(reference_paths)
    index = build_reference_index(records, kmer_size=max(5, int(args.kmer_size)))
    ref_lengths = {record.accession: len(record.sequence) for record in records}
    mapped_reads: Counter[str] = Counter()
    covered_positions: defaultdict[str, set[int]] = defaultdict(set)

    for read_pair in iter_fastq_pairs(reads_1, reads_2):
        votes, pair_positions = score_read_pair(read_pair, index=index, kmer_size=max(5, int(args.kmer_size)))
        accession = choose_best_accession(votes)
        if not accession:
            continue
        mapped_reads[accession] += 1
        covered_positions[accession].update(pair_positions.get(accession, set()))

    total_mapped = sum(mapped_reads.values())
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_detected.parent.mkdir(parents=True, exist_ok=True)
    detected: list[str] = []
    with output_report.open("w", encoding="utf-8") as handle:
        handle.write("virus_name\tref_length\tmapped_reads\tcovered_bases\tcoverage_pct\trelative_abundance\n")
        for accession in sorted(ref_lengths):
            ref_length = int(ref_lengths[accession])
            mapped = int(mapped_reads.get(accession, 0))
            covered = min(ref_length, len(covered_positions.get(accession, set())))
            coverage_pct = (100.0 * covered / ref_length) if ref_length > 0 else 0.0
            relative_abundance = (mapped / total_mapped) if total_mapped > 0 else 0.0
            handle.write(
                f"{accession}\t{ref_length}\t{mapped}\t{covered}\t{coverage_pct:.2f}\t{relative_abundance:.4f}\n"
            )
            if coverage_pct >= float(args.coverage_threshold):
                detected.append(accession)

    with output_detected.open("w", encoding="utf-8") as handle:
        for accession in detected:
            handle.write(f"{accession}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
