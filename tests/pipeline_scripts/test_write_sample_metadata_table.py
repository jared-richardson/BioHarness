from __future__ import annotations

from bio_harness.pipeline_scripts.write_sample_metadata_table import main


def test_write_sample_metadata_table_writes_tsv(tmp_path):
    output_path = tmp_path / "sample_metadata.tsv"

    exit_code = main(
        [
            "--output",
            str(output_path),
            "--sample-condition",
            "SRR1278968=Plankton",
            "--sample-condition",
            "SRR1278971=Biofilm",
        ]
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8") == (
        "sample\tcondition\n"
        "SRR1278968\tPlankton\n"
        "SRR1278971\tBiofilm\n"
    )


def test_write_sample_metadata_table_accepts_tab_separated_entries(tmp_path):
    output_path = tmp_path / "sample_metadata.tsv"

    exit_code = main(
        [
            "--output",
            str(output_path),
            "--sample-condition",
            r"SRR1278968\tPlankton",
            "--sample-condition",
            "SRR1278971\tBiofilm",
        ]
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8") == (
        "sample\tcondition\n"
        "SRR1278968\tPlankton\n"
        "SRR1278971\tBiofilm\n"
    )
