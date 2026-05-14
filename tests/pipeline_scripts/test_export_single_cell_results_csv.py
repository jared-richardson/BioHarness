from __future__ import annotations

import csv
import json
from pathlib import Path

from bio_harness.pipeline_scripts.export_single_cell_results_csv import (
    export_single_cell_results_csv,
    infer_cluster_cell_types,
)


def test_infer_cluster_cell_types_orders_synthetic_marker_blocks() -> None:
    mapping = infer_cluster_cell_types(
        {
            "2": ["Gene0030", "Gene0031", "Gene0032"],
            "0": ["Gene0000", "Gene0001", "Gene0002"],
            "1": ["Gene0015", "Gene0016", "Gene0017"],
        }
    )
    assert mapping == {"0": "TypeA", "1": "TypeB", "2": "TypeC"}


def test_export_single_cell_results_csv_writes_upstream_shape(tmp_path: Path) -> None:
    cluster_assignments = tmp_path / "cluster_assignments.json"
    marker_genes = tmp_path / "marker_genes.json"
    raw_counts = tmp_path / "raw_counts.json"
    output_csv = tmp_path / "final" / "single_cell_results.csv"

    cluster_assignments.write_text(
        json.dumps({"BC1": "0", "BC2": "0", "BC3": "1", "BC4": "1"}, indent=2),
        encoding="utf-8",
    )
    marker_genes.write_text(
        json.dumps(
            {
                "0": ["Gene0000", "Gene0001", "Gene0002"],
                "1": ["Gene0015", "Gene0016", "Gene0017"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    raw_counts.write_text(
        json.dumps(
            {
                "BC1": {"Gene0000": 10, "Gene0001": 8, "Gene0015": 1},
                "BC2": {"Gene0000": 7, "Gene0002": 9, "Gene0016": 1},
                "BC3": {"Gene0015": 11, "Gene0016": 7, "Gene0000": 1},
                "BC4": {"Gene0015": 8, "Gene0017": 9, "Gene0001": 1},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    rows = export_single_cell_results_csv(
        cluster_assignments=cluster_assignments,
        marker_genes=marker_genes,
        raw_counts=raw_counts,
        output_csv=output_csv,
        top_k_markers=2,
    )

    assert len(rows) == 4
    written_rows = list(csv.DictReader(output_csv.open("r", encoding="utf-8")))
    assert written_rows[0].keys() == {
        "cluster_id",
        "predicted_cell_type",
        "gene_name",
        "logfoldchanges",
        "pvals",
        "pvals_adj",
        "direction",
        "abs_logfc",
    }
    assert {row["predicted_cell_type"] for row in written_rows} == {"TypeA", "TypeB"}
    assert {row["direction"] for row in written_rows} == {"up"}
