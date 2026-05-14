#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from bio_harness.core.tabular_io import detect_table_delimiter


def _parse_contrast(raw: str) -> tuple[str, str, str]:
    parts = str(raw or "").strip().split("_")
    if len(parts) >= 4 and parts[2].lower() == "vs":
        return parts[0], parts[1], parts[3]
    return "condition", "treatment", "control"


def _normalize_sample_name(raw: str) -> str:
    name = Path(str(raw or "")).name
    lowered = name.lower()
    for suffix in (".bam", ".sam", ".cram", ".fastq.gz", ".fq.gz", ".fastq", ".fq"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    for suffix in (".aligned.out", "_aligned.out"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _load_counts(path: Path) -> pd.DataFrame:
    counts = pd.read_csv(path, sep="\t", comment="#")
    counts = counts.rename(columns={counts.columns[0]: "gene_id"})
    if list(counts.columns[1:6]) == ["Chr", "Start", "End", "Strand", "Length"]:
        sample_cols = list(counts.columns[6:])
        data = counts[["gene_id"] + sample_cols].copy()
    else:
        sample_cols = list(counts.columns[1:])
        data = counts[["gene_id"] + sample_cols].copy()
    data = data.rename(columns={col: _normalize_sample_name(str(col)) for col in sample_cols})
    data = data.drop_duplicates("gene_id").set_index("gene_id")
    return data.apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)


def _load_metadata(path: Path, sample_names: list[str]) -> pd.DataFrame:
    metadata = pd.read_csv(path, sep=detect_table_delimiter(path))
    first_col = metadata.columns[0]
    if first_col != "sample":
        metadata = metadata.rename(columns={first_col: "sample"})
    if "condition" not in metadata.columns:
        raise ValueError("Metadata must contain a 'condition' column.")
    metadata["sample"] = metadata["sample"].map(lambda value: _normalize_sample_name(str(value)))
    metadata = metadata.set_index("sample")
    return metadata.loc[sample_names].copy()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--design", required=True)
    parser.add_argument("--contrast", required=True)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args()

    counts_path = Path(args.counts).expanduser().resolve()
    metadata_path = Path(args.metadata).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    count_df = _load_counts(counts_path)
    metadata = _load_metadata(metadata_path, list(count_df.columns))
    factor_name, treatment, control = _parse_contrast(args.contrast)

    dds = DeseqDataSet(
        counts=count_df.T,
        metadata=metadata,
        design_factors=factor_name,
        refit_cooks=True,
    )
    dds.deseq2()
    stats = DeseqStats(dds, contrast=(factor_name, treatment, control))
    stats.summary()
    results_df = stats.results_df.reset_index().rename(columns={"index": "gene_id"})
    results_df.to_csv(outdir / "deseq2_results.tsv", sep="\t", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
