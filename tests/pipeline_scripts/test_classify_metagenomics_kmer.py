from __future__ import annotations

import gzip
from pathlib import Path

from bio_harness.pipeline_scripts.classify_metagenomics_kmer import main


def _write_fastq(path: Path, entries: list[tuple[str, str]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for name, sequence in entries:
            handle.write(f"@{name}\n{sequence}\n+\n{'I' * len(sequence)}\n")


def test_classify_metagenomics_kmer_writes_kraken_style_report(tmp_path: Path) -> None:
    reference_dir = tmp_path / "references"
    reference_dir.mkdir()
    ref_a = ("ACGTTGCAAGTCCTA" * 16)[:240]
    ref_b = ("TGCACCTGAATCGGA" * 16)[:240]
    (reference_dir / "ref_a.fna").write_text(">acc_a Escherichia coli example\n" + ref_a + "\n", encoding="utf-8")
    (reference_dir / "ref_b.fna").write_text(">acc_b Bacillus subtilis example\n" + ref_b + "\n", encoding="utf-8")
    taxonomy_tsv = tmp_path / "ktaxonomy.tsv"
    taxonomy_tsv.write_text(
        "\n".join(
            [
                "561 | 1 | G | 0 | Escherichia",
                "562 | 561 | S | 0 | Escherichia coli",
                "1385 | 1 | G | 0 | Bacillus",
                "1423 | 1385 | S | 0 | Bacillus subtilis",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    reads_1 = tmp_path / "reads_1.fastq.gz"
    reads_2 = tmp_path / "reads_2.fastq.gz"
    _write_fastq(reads_1, [("r1", ref_a[0:80]), ("r2", ref_b[0:80]), ("r3", "N" * 80)])
    _write_fastq(reads_2, [("r1", ref_a[80:160]), ("r2", ref_b[80:160]), ("r3", "N" * 80)])
    output_report = tmp_path / "output" / "report.txt"

    rc = main(
        [
            "--reads-1",
            str(reads_1),
            "--reads-2",
            str(reads_2),
            "--reference-dir",
            str(reference_dir),
            "--taxonomy-tsv",
            str(taxonomy_tsv),
            "--output-report",
            str(output_report),
            "--kmer-size",
            "15",
        ]
    )

    assert rc == 0
    text = output_report.read_text(encoding="utf-8")
    assert "\tU\t0\tunclassified" in text
    assert "\tR\t1\troot" in text
    assert "\tG\t561\tEscherichia" in text
    assert "\tS\t562\tEscherichia coli" in text
    assert "\tG\t1385\tBacillus" in text
    assert "\tS\t1423\tBacillus subtilis" in text
