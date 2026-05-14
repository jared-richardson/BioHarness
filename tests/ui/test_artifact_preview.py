from __future__ import annotations

from pathlib import Path

import pandas as pd

from bio_harness.ui.artifact_preview import (
    artifact_preview_metadata,
    human_readable_size,
    table_preview_profile,
)


def test_human_readable_size_formats_common_units() -> None:
    assert human_readable_size(999) == "999 B"
    assert human_readable_size(2048) == "2.0 KB"
    assert human_readable_size(5 * 1024 * 1024) == "5.0 MB"


def test_artifact_preview_metadata_reports_size_and_kind(tmp_path: Path) -> None:
    path = tmp_path / "counts.tsv"
    path.write_text("gene\tcount\nA\t10\n", encoding="utf-8")
    metadata = artifact_preview_metadata(path, "table")
    assert metadata["name"] == "counts.tsv"
    assert metadata["kind"] == "table"
    assert metadata["size"].endswith("B")
    assert metadata["modified"]


def test_table_preview_profile_builds_chart_and_histogram_frames() -> None:
    frame = pd.DataFrame(
        {
            "sample": ["a", "b", "c", "d"],
            "count": [10, 20, 15, 30],
            "depth": [100, 120, 110, 160],
        }
    )
    profile = table_preview_profile(frame, max_series=2, chart_rows=4, histogram_bins=4)
    assert profile["row_count"] == 4
    assert profile["column_count"] == 3
    assert profile["numeric_columns"] == ["count", "depth"]
    assert list(profile["chart_frame"].columns) == ["count", "depth"]
    assert not profile["histogram_frame"].empty
    assert profile["metric_rows"][0]["column"] == "count"


def test_table_preview_profile_handles_non_numeric_tables() -> None:
    frame = pd.DataFrame({"sample": ["a", "b"], "group": ["control", "treated"]})
    profile = table_preview_profile(frame)
    assert profile["numeric_columns"] == []
    assert profile["chart_frame"].empty
    assert profile["histogram_frame"].empty
    assert profile["metric_rows"] == []


def test_table_preview_profile_suppresses_single_row_trend_chart() -> None:
    frame = pd.DataFrame({"sample": ["a"], "count": [10]})
    profile = table_preview_profile(frame)
    assert profile["chart_frame"].empty
    assert not profile["histogram_frame"].empty


def test_table_preview_profile_drops_non_finite_numeric_rows() -> None:
    frame = pd.DataFrame({"count": [float("nan"), float("inf"), 4.0, 8.0]})
    profile = table_preview_profile(frame)
    assert list(profile["chart_frame"]["count"]) == [4.0, 8.0]
