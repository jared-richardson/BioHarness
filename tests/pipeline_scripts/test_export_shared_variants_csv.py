from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


def test_export_shared_variants_csv_filters_and_dedupes(tmp_path: Path):
    left = tmp_path / "left.vcf"
    right = tmp_path / "right.vcf"
    out = tmp_path / "final" / "variants_shared.csv"
    left.write_text(
        "\n".join(
            [
                "##fileformat=VCFv4.2",
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
                "chr1\t10\t.\tA\tG\t.\tPASS\tANN=G|missense_variant|MODERATE|geneA|geneA",
                "chr1\t11\t.\tC\tT\t.\tPASS\tANN=T|synonymous_variant|LOW|geneB|geneB",
                "chr1\t12\t.\tG\tA\t.\tPASS\tANN=A|frameshift_variant|HIGH|geneA|geneA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    right.write_text(
        "\n".join(
            [
                "##fileformat=VCFv4.2",
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
                "chr1\t10\t.\tA\tG\t.\tPASS\tANN=G|missense_variant|MODERATE|geneA|geneA",
                "chr1\t12\t.\tG\tA\t.\tPASS\tANN=A|frameshift_variant|HIGH|geneA|geneA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[2] / "bio_harness" / "pipeline_scripts" / "export_shared_variants_csv.py"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--input-vcf-a",
            str(left),
            "--input-vcf-b",
            str(right),
            "--output-csv",
            str(out),
            "--min-impact",
            "MODERATE",
            "--status",
            "shared",
            "--header-case",
            "upper",
            "--dedupe-by-gene",
        ],
        check=True,
    )

    rows = list(csv.reader(out.open("r", encoding="utf-8")))
    assert rows == [
        ["CHROM", "POS", "REF", "ALT", "GENE", "IMPACT", "EFFECT", "STATUS"],
        ["chr1", "10", "A", "G", "geneA", "MODERATE", "missense_variant", "shared"],
    ]
