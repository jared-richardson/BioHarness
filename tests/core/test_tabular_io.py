from __future__ import annotations

from pathlib import Path

from bio_harness.core.tabular_io import detect_table_delimiter, load_delimited_dict_rows


def test_detect_table_delimiter_prefers_tab_content_over_csv_suffix(tmp_path: Path) -> None:
    metadata_path = tmp_path / "sample_metadata.csv"
    metadata_path.write_text(
        "sample\tcondition\n"
        "SRR1278968\tcontrol\n"
        "SRR1278971\ttreatment\n",
        encoding="utf-8",
    )

    assert detect_table_delimiter(metadata_path) == "\t"


def test_load_delimited_dict_rows_reads_tab_delimited_csv_suffix(tmp_path: Path) -> None:
    metadata_path = tmp_path / "sample_metadata.csv"
    metadata_path.write_text(
        "sample\tcondition\n"
        "SRR1278968\tcontrol\n"
        "SRR1278971\ttreatment\n",
        encoding="utf-8",
    )

    columns, rows, delimiter = load_delimited_dict_rows(metadata_path)

    assert delimiter == "\t"
    assert columns == ["sample", "condition"]
    assert rows[0]["sample"] == "SRR1278968"
    assert rows[1]["condition"] == "treatment"
