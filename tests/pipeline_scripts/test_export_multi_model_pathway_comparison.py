from __future__ import annotations

import csv
from pathlib import Path

from bio_harness.pipeline_scripts.export_multi_model_pathway_comparison import (
    export_multi_model_pathway_comparison,
)
from scripts.validate_alzheimer_mouse import validate


def test_export_multi_model_pathway_comparison_intersects_and_sorts(tmp_path: Path) -> None:
    files = {}
    rows_by_label = {
        "5xFAD": [("Pathway A", 1e-6), ("Pathway B", 0.02), ("Pathway X", 0.4)],
        "3xTG_AD": [("Pathway B", 0.03), ("Pathway A", 0.3), ("Pathway Y", 0.7)],
        "PS3O1S": [("Pathway A", 0.04), ("Pathway B", 0.2), ("Pathway Z", 0.9)],
    }
    for label, rows in rows_by_label.items():
        path = tmp_path / f"KEGG_{label}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["Term", "P-value"])
            writer.writeheader()
            for pathway, pvalue in rows:
                writer.writerow({"Term": pathway, "P-value": pvalue})
        files[label] = path

    output = tmp_path / "final" / "pathway_comparison.csv"
    meta = export_multi_model_pathway_comparison(label_to_csv=files, output_csv=output)

    rows = list(csv.DictReader(output.open("r", encoding="utf-8")))
    assert [row["Pathway"] for row in rows] == ["Pathway A", "Pathway B"]
    assert list(rows[0].keys()) == ["Pathway", "5xFAD_pvalue", "3xTG_AD_pvalue", "PS3O1S_pvalue"]
    assert meta["labels"] == ["5xFAD", "3xTG_AD", "PS3O1S"]
    assert meta["row_count"] == 2


def test_validate_alzheimer_mouse_accepts_close_pathway_profile(tmp_path: Path) -> None:
    truth = tmp_path / "truth.csv"
    output = tmp_path / "output.csv"
    truth.write_text(
        "\n".join(
            [
                "Pathway,5xFAD_pvalue,3xTG_AD_pvalue,PS3O1S_pvalue",
                "Pathway A,1e-09,0.31,0.44",
                "Pathway B,4.6e-05,0.044,0.26",
                "Pathway C,0.0019,0.38,0.22",
                "Pathway D,0.0073,0.12,0.58",
                "Pathway E,0.97,0.33,0.018",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output.write_text(
        "\n".join(
            [
                "Pathway,5xFAD_pvalue,3xTG_AD_pvalue,PS3O1S_pvalue",
                "Pathway A,2e-09,0.29,0.40",
                "Pathway B,5.1e-05,0.040,0.20",
                "Pathway C,0.0025,0.35,0.28",
                "Pathway D,0.01,0.11,0.50",
                "Pathway E,0.90,0.36,0.021",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert validate(truth, output) is True
