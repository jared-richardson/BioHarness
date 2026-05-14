from __future__ import annotations

import gzip
from pathlib import Path

from bio_harness.pipeline_scripts.classify_viral_reads_kmer import main


def _write_fastq(path: Path, entries: list[tuple[str, str]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for name, sequence in entries:
            handle.write(f"@{name}\n{sequence}\n+\n{'I' * len(sequence)}\n")


def test_classify_viral_reads_kmer_writes_report_and_detected_list(tmp_path: Path) -> None:
    reference_dir = tmp_path / "references"
    reference_dir.mkdir()
    virus_a = ("ACGTTGCAAGTCCTA" * 16)[:240]
    virus_b = ("TGCACCTGAATCGGA" * 16)[:240]
    (reference_dir / "virus_a.fna").write_text(">virus_a\n" + virus_a + "\n", encoding="utf-8")
    (reference_dir / "virus_b.fna").write_text(">virus_b\n" + virus_b + "\n", encoding="utf-8")
    reads_1 = tmp_path / "reads_1.fastq.gz"
    reads_2 = tmp_path / "reads_2.fastq.gz"
    _write_fastq(reads_1, [("r1", virus_a[0:80]), ("r2", virus_b[0:80])])
    _write_fastq(reads_2, [("r1", virus_a[80:160]), ("r2", virus_b[80:160])])
    output_report = tmp_path / "output" / "classification_report.tsv"
    output_detected = tmp_path / "output" / "detected_viruses.txt"

    rc = main(
        [
            "--reads-1",
            str(reads_1),
            "--reads-2",
            str(reads_2),
            "--reference-dir",
            str(reference_dir),
            "--output-report",
            str(output_report),
            "--output-detected",
            str(output_detected),
            "--coverage-threshold",
            "10",
            "--kmer-size",
            "15",
        ]
    )

    assert rc == 0
    report_text = output_report.read_text(encoding="utf-8")
    detected = output_detected.read_text(encoding="utf-8")
    assert report_text.startswith("virus_name\tref_length\tmapped_reads\tcovered_bases\tcoverage_pct\trelative_abundance\n")
    assert "virus_a" in report_text
    assert "virus_b" in report_text
    assert "virus_a" in detected
    assert "virus_b" in detected
