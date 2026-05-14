#!/usr/bin/env python3
"""Summarize viral minimap2 PAF alignments into benchmark deliverables.

The helper reads a PAF file plus the corresponding ``samtools faidx`` index
for the concatenated viral panel and produces:

- ``classification_report.tsv``
- ``detected_viruses.txt``
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Sequence


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Optional CLI arguments.

    Returns:
        Parsed argument namespace.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paf", required=True, help="Input minimap2 PAF path.")
    parser.add_argument("--panel-fai", required=True, help="samtools faidx .fai path.")
    parser.add_argument("--output-report", required=True, help="Classification report TSV.")
    parser.add_argument("--output-detected", required=True, help="Detected-viruses text file.")
    parser.add_argument("--coverage-threshold", type=float, default=10.0, help="Coverage percent threshold.")
    return parser.parse_args(argv)


def _load_reference_lengths(panel_fai: Path) -> dict[str, int]:
    """Load reference lengths from a FASTA index.

    Args:
        panel_fai: Path to ``.fai`` file.

    Returns:
        Mapping from reference name to length.
    """

    ref_lengths: dict[str, int] = {}
    with panel_fai.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            parts = raw_line.strip().split("\t")
            if len(parts) < 2:
                continue
            ref_lengths[parts[0]] = int(parts[1])
    return ref_lengths


def main(argv: Sequence[str] | None = None) -> int:
    """Run viral PAF summarization.

    Args:
        argv: Optional CLI arguments.

    Returns:
        Process exit code.
    """

    args = _parse_args(argv)
    paf_path = Path(args.paf).expanduser().resolve(strict=False)
    panel_fai = Path(args.panel_fai).expanduser().resolve(strict=False)
    output_report = Path(args.output_report).expanduser().resolve(strict=False)
    output_detected = Path(args.output_detected).expanduser().resolve(strict=False)

    ref_lengths = _load_reference_lengths(panel_fai)
    reads_per_ref: defaultdict[str, int] = defaultdict(int)
    covered_positions: defaultdict[str, set[int]] = defaultdict(set)
    total_mapped = 0

    with paf_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            cols = raw_line.strip().split("\t")
            if len(cols) < 11:
                continue
            ref_name = cols[5]
            start = int(cols[7])
            end = int(cols[8])
            reads_per_ref[ref_name] += 1
            total_mapped += 1
            covered_positions[ref_name].update(range(start, end))

    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_detected.parent.mkdir(parents=True, exist_ok=True)
    detected: list[str] = []
    with output_report.open("w", encoding="utf-8") as handle:
        handle.write("virus_name\tref_length\tmapped_reads\tcovered_bases\tcoverage_pct\trelative_abundance\n")
        for ref_name in sorted(ref_lengths):
            ref_length = int(ref_lengths[ref_name])
            mapped_reads = int(reads_per_ref.get(ref_name, 0))
            covered_bases = min(ref_length, len(covered_positions.get(ref_name, set())))
            coverage_pct = (100.0 * covered_bases / ref_length) if ref_length > 0 else 0.0
            relative_abundance = (mapped_reads / total_mapped) if total_mapped > 0 else 0.0
            handle.write(
                f"{ref_name}\t{ref_length}\t{mapped_reads}\t{covered_bases}\t{coverage_pct:.2f}\t{relative_abundance:.4f}\n"
            )
            if coverage_pct >= float(args.coverage_threshold):
                detected.append(ref_name)

    with output_detected.open("w", encoding="utf-8") as handle:
        for ref_name in detected:
            handle.write(f"{ref_name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
