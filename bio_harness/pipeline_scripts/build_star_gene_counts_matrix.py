#!/usr/bin/env python3
from __future__ import annotations

import csv
import sys
from pathlib import Path


def _read_star_counts(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for index, row in enumerate(reader):
            if index < 4 or not row:
                continue
            gene_id = str(row[0]).strip()
            count = str(row[3]).strip() if len(row) > 3 else ""
            if gene_id:
                rows.append((gene_id, count))
    return rows


def build(output_path: Path, sample_specs: list[str]) -> int:
    matrices: list[tuple[str, list[tuple[str, str]]]] = []
    for spec in sample_specs:
        sample_name, counts_path = spec.split("=", 1)
        matrices.append((sample_name.strip(), _read_star_counts(Path(counts_path.strip()))))
    if not matrices:
        return 1

    gene_ids = [gene_id for gene_id, _ in matrices[0][1]]
    rows_by_sample: list[list[str]] = []
    for sample_name, rows in matrices:
        sample_gene_ids = [gene_id for gene_id, _ in rows]
        if sample_gene_ids != gene_ids:
            raise ValueError(f"Gene ID order mismatch for sample {sample_name}")
        rows_by_sample.append([count for _, count in rows])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["gene_id"] + [sample_name for sample_name, _ in matrices])
        for index, gene_id in enumerate(gene_ids):
            writer.writerow([gene_id] + [sample_counts[index] for sample_counts in rows_by_sample])
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <output.tsv> sample=count_file [sample=count_file ...]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(build(Path(sys.argv[1]), sys.argv[2:]))
