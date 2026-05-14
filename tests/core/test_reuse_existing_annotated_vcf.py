from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = Path("scripts/reuse_existing_annotated_vcf.py")


def test_reuse_existing_annotated_vcf_copies_annotated_input(tmp_path: Path) -> None:
    input_vcf = tmp_path / "input.vcf"
    output_vcf = tmp_path / "output.vcf"
    input_vcf.write_text(
        "\n".join(
            [
                "##fileformat=VCFv4.2",
                '##INFO=<ID=ANN,Number=.,Type=String,Description="Annotation">',
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
                "chr1\t1\t.\tA\tG\t.\tPASS\tANN=example",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(input_vcf), str(output_vcf)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert output_vcf.read_text(encoding="utf-8") == input_vcf.read_text(encoding="utf-8")


def test_reuse_existing_annotated_vcf_exits_nonzero_without_ann_header(tmp_path: Path) -> None:
    input_vcf = tmp_path / "input.vcf"
    output_vcf = tmp_path / "output.vcf"
    input_vcf.write_text(
        "\n".join(
            [
                "##fileformat=VCFv4.2",
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
                "chr1\t1\t.\tA\tG\t.\tPASS\t.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(input_vcf), str(output_vcf)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    assert not output_vcf.exists()
