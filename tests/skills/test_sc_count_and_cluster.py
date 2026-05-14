from __future__ import annotations

import gzip
import os
import subprocess
import sys
from pathlib import Path

from bio_harness.skills.library.sc_count_and_cluster import (
    count_matrix,
    infer_barcode_whitelist,
    resolve_whitelist_path,
    sc_count_and_cluster,
)


def _write_fastq(path: Path, sequences: list[str]) -> None:
    """Write a minimal gzipped FASTQ from sequence strings."""

    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for index, sequence in enumerate(sequences, start=1):
            handle.write(f"@read_{index}\n{sequence}\n+\n{'I' * len(sequence)}\n")


def test_sc_count_and_cluster_wrapper_allows_missing_whitelist() -> None:
    cmd = sc_count_and_cluster(
        r1="/data/sample_R1.fastq.gz",
        r2="/data/sample_R2.fastq.gz",
        reference="/refs/genome.fa",
        gtf="/refs/genes.gtf",
        output_dir="/out/sc_raw",
    )

    assert "--whitelist" not in cmd
    assert "--r1 /data/sample_R1.fastq.gz" in cmd
    assert "--output-dir /out/sc_raw" in cmd


def test_infer_barcode_whitelist_prefers_repeated_prefixes(tmp_path: Path) -> None:
    r1_path = tmp_path / "sample_R1.fastq.gz"
    sequences = [
        "AAAAAAAAAAAAAAAA" + "TTTTTTTTTTTT",
        "AAAAAAAAAAAAAAAA" + "CCCCCCCCCCCC",
        "CCCCCCCCCCCCCCCC" + "GGGGGGGGGGGG",
        "CCCCCCCCCCCCCCCC" + "AAAAAAAAAAAA",
        "GGGGGGGGGGGGGGGG" + "TTTTTTTTTTTT",
    ]
    _write_fastq(r1_path, sequences)

    whitelist = infer_barcode_whitelist(str(r1_path), barcode_len=16, min_observations=2)

    assert whitelist == ["AAAAAAAAAAAAAAAA", "CCCCCCCCCCCCCCCC"]


def test_resolve_whitelist_path_infers_file_when_missing(tmp_path: Path) -> None:
    r1_path = tmp_path / "sample_R1.fastq.gz"
    out_dir = tmp_path / "sc_raw"
    _write_fastq(
        r1_path,
        [
            "AAAAAAAAAAAAAAAA" + "TTTTTTTTTTTT",
            "AAAAAAAAAAAAAAAA" + "CCCCCCCCCCCC",
            "CCCCCCCCCCCCCCCC" + "GGGGGGGGGGGG",
            "CCCCCCCCCCCCCCCC" + "AAAAAAAAAAAA",
        ],
    )

    resolved = resolve_whitelist_path(
        "",
        r1_path=str(r1_path),
        output_dir=str(out_dir),
        barcode_len=16,
    )

    inferred_path = Path(resolved)
    assert inferred_path == (out_dir / "inferred_barcodes_whitelist.txt").resolve(strict=False)
    assert inferred_path.read_text(encoding="utf-8").splitlines() == [
        "AAAAAAAAAAAAAAAA",
        "CCCCCCCCCCCCCCCC",
    ]


def test_resolve_whitelist_path_replaces_unusable_existing_file(tmp_path: Path) -> None:
    r1_path = tmp_path / "sample_R1.fastq.gz"
    out_dir = tmp_path / "sc_raw"
    bad_whitelist = tmp_path / "10x_v3_whitelist.txt"
    bad_whitelist.write_text("-e AAAAAAAAAAAAAAAAAAAA-1\nTTTTTTTTTTTTTTTTTTTT-1\n", encoding="utf-8")
    _write_fastq(
        r1_path,
        [
            "AAAAAAAAAAAAAAAA" + "TTTTTTTTTTTT",
            "AAAAAAAAAAAAAAAA" + "CCCCCCCCCCCC",
            "CCCCCCCCCCCCCCCC" + "GGGGGGGGGGGG",
            "CCCCCCCCCCCCCCCC" + "AAAAAAAAAAAA",
        ],
    )

    resolved = resolve_whitelist_path(
        str(bad_whitelist),
        r1_path=str(r1_path),
        output_dir=str(out_dir),
        barcode_len=16,
    )

    inferred_path = Path(resolved)
    assert inferred_path == (out_dir / "inferred_barcodes_whitelist.txt").resolve(strict=False)
    assert inferred_path.read_text(encoding="utf-8").splitlines() == [
        "AAAAAAAAAAAAAAAA",
        "CCCCCCCCCCCCCCCC",
    ]


def test_count_matrix_falls_back_to_alignment_free_features(tmp_path: Path) -> None:
    r1_path = tmp_path / "sample_R1.fastq.gz"
    r2_path = tmp_path / "sample_R2.fastq.gz"
    whitelist_path = tmp_path / "barcodes_whitelist.txt"
    reference_path = tmp_path / "genome.fa"
    gtf_path = tmp_path / "genes.gtf"

    _write_fastq(
        r1_path,
        [
            "AAAAAAAAAAAAAAAA" + "TTTTTTTTTTTT",
            "AAAAAAAAAAAAAAAA" + "CCCCCCCCCCCC",
            "AAAAAAAAAAAAAAAA" + "GGGGGGGGGGGG",
            "CCCCCCCCCCCCCCCC" + "TTTTTTTTTTTT",
            "CCCCCCCCCCCCCCCC" + "CCCCCCCCCCCC",
            "CCCCCCCCCCCCCCCC" + "GGGGGGGGGGGG",
        ],
    )
    _write_fastq(
        r2_path,
        [
            "ACAGATGAAAAAATTACAGATGAAAAAATT",
            "ACAGATGAAAAAATTACAGATGAAAAAATT",
            "ACAGATGAAAAAATTACAGATGAAAAAATT",
            "TTGCCTCTAGTTTCTTTGCCTCTAGTTTCT",
            "TTGCCTCTAGTTTCTTTGCCTCTAGTTTCT",
            "TTGCCTCTAGTTTCTTTGCCTCTAGTTTCT",
        ],
    )
    whitelist_path.write_text("AAAAAAAAAAAAAAAA\nCCCCCCCCCCCCCCCC\n", encoding="utf-8")
    reference_path.write_text(">chr1\n" + ("A" * 200) + "\n", encoding="utf-8")
    gtf_path.write_text(
        'chr1\tsynth\texon\t1\t200\t.\t+\t.\tgene_id "gene_1"; transcript_id "tx_1";\n',
        encoding="utf-8",
    )

    counts, features = count_matrix(
        r1_path=str(r1_path),
        r2_path=str(r2_path),
        whitelist_path=str(whitelist_path),
        reference_fasta=str(reference_path),
        gtf_path=str(gtf_path),
        barcode_len=16,
        umi_len=12,
        kmer_size=25,
    )

    assert sorted(counts) == ["AAAAAAAAAAAAAAAA", "CCCCCCCCCCCCCCCC"]
    assert features
    assert all(feature.startswith("kmer_") for feature in features)


def test_sc_count_and_cluster_script_help_runs_outside_repo_root(tmp_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[2] / "bio_harness" / "skills" / "library" / "sc_count_and_cluster.py"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Single-cell counting + clustering" in completed.stdout
