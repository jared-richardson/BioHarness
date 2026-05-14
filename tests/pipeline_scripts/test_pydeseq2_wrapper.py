from __future__ import annotations

from pathlib import Path

from bio_harness.pipeline_scripts.pydeseq2_wrapper import _load_counts, _load_metadata, _normalize_sample_name


def test_normalize_sample_name_strips_alignment_suffixes():
    assert _normalize_sample_name("/tmp/SRR1278968.bam") == "SRR1278968"
    assert _normalize_sample_name("SRR1278968.sam") == "SRR1278968"
    assert _normalize_sample_name("SRR1278968.fastq.gz") == "SRR1278968"
    assert _normalize_sample_name("SRR1278968.Aligned.out") == "SRR1278968"


def test_load_metadata_matches_featurecounts_bam_columns(tmp_path: Path):
    counts_path = tmp_path / "gene_counts.txt"
    counts_path.write_text(
        "# Program:featureCounts\n"
        "Geneid\tChr\tStart\tEnd\tStrand\tLength\t/tmp/SRR1278968.bam\t/tmp/SRR1278969.bam\n"
        "g1\tchr1\t1\t2\t+\t2\t10\t11\n",
        encoding="utf-8",
    )
    metadata_path = tmp_path / "sample_metadata.tsv"
    metadata_path.write_text(
        "sample\tcondition\n"
        "SRR1278968\tPlankton\n"
        "SRR1278969\tBiofilm\n",
        encoding="utf-8",
    )

    counts = _load_counts(counts_path)
    metadata = _load_metadata(metadata_path, list(counts.columns))

    assert list(counts.columns) == ["SRR1278968", "SRR1278969"]
    assert list(metadata.index) == ["SRR1278968", "SRR1278969"]


def test_load_metadata_accepts_tab_delimited_csv_suffix(tmp_path: Path):
    counts_path = tmp_path / "gene_counts.txt"
    counts_path.write_text(
        "# Program:featureCounts\n"
        "Geneid\tChr\tStart\tEnd\tStrand\tLength\t/tmp/SRR1278968.bam\t/tmp/SRR1278969.bam\n"
        "g1\tchr1\t1\t2\t+\t2\t10\t11\n",
        encoding="utf-8",
    )
    metadata_path = tmp_path / "sample_metadata.csv"
    metadata_path.write_text(
        "sample\tcondition\n"
        "SRR1278968\tPlankton\n"
        "SRR1278969\tBiofilm\n",
        encoding="utf-8",
    )

    counts = _load_counts(counts_path)
    metadata = _load_metadata(metadata_path, list(counts.columns))

    assert list(metadata.index) == ["SRR1278968", "SRR1278969"]
    assert list(metadata["condition"]) == ["Plankton", "Biofilm"]
