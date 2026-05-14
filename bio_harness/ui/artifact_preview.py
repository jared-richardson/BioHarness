"""Artifact preview helpers for the chat-first Streamlit UI.

These helpers keep file-preview summarization deterministic and testable. They
prepare lightweight metadata and chart-ready table summaries without depending
on Streamlit widgets or browser state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def human_readable_size(size_bytes: int) -> str:
    """Format one byte count for UI display.

    Args:
        size_bytes: Raw byte count.

    Returns:
        A compact human-readable size string.
    """
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(max(0, int(size_bytes)))
    unit_index = 0
    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def artifact_preview_metadata(path: Path, kind: str) -> dict[str, str]:
    """Build compact metadata for one previewable artifact.

    Args:
        path: Artifact path.
        kind: Preview kind token.

    Returns:
        A mapping with stable text labels for UI rendering.
    """
    stats = path.stat()
    return {
        "name": path.name,
        "kind": kind,
        "size": human_readable_size(stats.st_size),
        "modified": pd.Timestamp(stats.st_mtime, unit="s").strftime("%Y-%m-%d %H:%M"),
    }


def _select_numeric_columns(frame: pd.DataFrame, *, max_series: int) -> list[str]:
    numeric = [
        str(column)
        for column in frame.select_dtypes(include="number").columns.tolist()
        if str(column).strip()
    ]
    return numeric[: max(1, int(max_series))]


def table_preview_profile(
    frame: pd.DataFrame,
    *,
    max_series: int = 3,
    chart_rows: int = 40,
    histogram_bins: int = 10,
) -> dict[str, Any]:
    """Build one lightweight preview profile for a tabular artifact.

    Args:
        frame: Parsed preview dataframe.
        max_series: Maximum number of numeric columns to include in charts.
        chart_rows: Maximum number of rows for the trend chart.
        histogram_bins: Number of histogram bins for the primary numeric column.

    Returns:
        A mapping containing row/column counts, numeric column summaries, a
        chart-ready dataframe, a histogram dataframe, and compact metric rows.
    """
    rows = int(frame.shape[0])
    columns = int(frame.shape[1])
    numeric_columns = _select_numeric_columns(frame, max_series=max_series)
    chart_frame = pd.DataFrame()
    histogram_frame = pd.DataFrame()
    metric_rows: list[dict[str, str]] = []

    if numeric_columns:
        chart_frame = frame.loc[:, numeric_columns].head(max(1, int(chart_rows))).copy()
        chart_frame = (
            chart_frame.apply(pd.to_numeric, errors="coerce")
            .replace([float("inf"), float("-inf")], pd.NA)
            .dropna(how="all")
        )
        if len(chart_frame.index) < 2:
            chart_frame = pd.DataFrame()
        primary = numeric_columns[0]
        primary_series = pd.to_numeric(frame[primary], errors="coerce").dropna()
        if not primary_series.empty:
            try:
                bins = min(max(4, int(histogram_bins)), max(4, primary_series.nunique()))
                bucketed = pd.cut(primary_series, bins=bins, include_lowest=True)
                counts = bucketed.value_counts(sort=False)
                histogram_frame = pd.DataFrame(
                    {
                        "bin": [str(interval) for interval in counts.index],
                        "count": counts.astype(int).tolist(),
                    }
                )
                if histogram_frame["count"].sum() <= 0 or len(histogram_frame.index) < 2:
                    histogram_frame = pd.DataFrame()
            except ValueError:
                histogram_frame = pd.DataFrame()

        for column in numeric_columns:
            series = pd.to_numeric(frame[column], errors="coerce").dropna()
            if series.empty:
                continue
            metric_rows.append(
                {
                    "column": column,
                    "min": f"{series.min():.3g}",
                    "max": f"{series.max():.3g}",
                    "mean": f"{series.mean():.3g}",
                }
            )

    return {
        "row_count": rows,
        "column_count": columns,
        "numeric_columns": numeric_columns,
        "chart_frame": chart_frame,
        "histogram_frame": histogram_frame,
        "metric_rows": metric_rows,
    }
