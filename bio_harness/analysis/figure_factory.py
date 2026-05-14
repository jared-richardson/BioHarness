"""Reusable figure rendering helpers for manuscript and researcher outputs."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import fill
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np


PALETTE = {
    "ink": "#0F172A",
    "slate": "#475569",
    "teal": "#0F766E",
    "coral": "#C2410C",
    "gold": "#D97706",
    "sand": "#F8FAFC",
    "grid": "#CBD5E1",
    "green": "#15803D",
    "red": "#B91C1C",
    "mint": "#ECFDF5",
    "peach": "#FFF7ED",
}


def set_figure_style() -> None:
    """Apply a consistent figure style."""
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": PALETTE["grid"],
            "axes.labelcolor": PALETTE["ink"],
            "xtick.color": PALETTE["slate"],
            "ytick.color": PALETTE["slate"],
            "text.color": PALETTE["ink"],
            "axes.titleweight": "bold",
            "axes.titlesize": 14,
            "axes.labelsize": 11,
            "font.size": 10,
            "font.family": "DejaVu Sans",
            "grid.color": PALETTE["grid"],
            "grid.alpha": 0.4,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def save_figure(fig: plt.Figure, path: Path) -> None:
    """Write both SVG and PNG versions of a figure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg", bbox_inches="tight")
    fig.savefig(path.with_suffix(".png"), format="png", dpi=240, bbox_inches="tight")
    plt.close(fig)


def render_horizontal_bar(
    *,
    title: str,
    labels: list[str],
    values: list[float],
    output_path: Path,
    color: str = PALETTE["teal"],
    xlabel: str = "",
    note: str = "",
    wrap_width: int = 18,
) -> None:
    """Render a publication-style horizontal bar chart."""
    figure, axis = plt.subplots(figsize=(11, 6))
    wrapped = [fill(str(label), width=wrap_width) for label in labels]
    order = list(range(len(labels)))[::-1]
    ordered_labels = [wrapped[idx] for idx in order]
    ordered_values = [values[idx] for idx in order]
    axis.barh(ordered_labels, ordered_values, color=color, alpha=0.9)
    if xlabel:
        axis.set_xlabel(xlabel)
    axis.set_title(title, loc="left", pad=12)
    axis.grid(axis="x", linestyle="--")
    for idx, value in enumerate(ordered_values):
        axis.text(value + max(0.2, 0.01 * max(ordered_values or [1])), idx, f"{value:g}", va="center", ha="left")
    if note:
        axis.text(
            0.99,
            0.02,
            note,
            transform=axis.transAxes,
            ha="right",
            va="bottom",
            fontsize=9,
            color=PALETTE["slate"],
        )
    save_figure(figure, output_path)


def render_grouped_bar(
    *,
    title: str,
    groups: list[str],
    series: list[dict[str, Any]],
    output_path: Path,
    ylabel: str = "",
    note: str = "",
) -> None:
    """Render a grouped bar chart."""
    figure, axis = plt.subplots(figsize=(11, 6))
    positions = np.arange(len(groups))
    width = 0.8 / max(1, len(series))
    offsets = np.linspace(-(len(series) - 1) / 2, (len(series) - 1) / 2, max(1, len(series))) * width
    for idx, row in enumerate(series):
        values = [float(value) for value in row.get("values", [])]
        axis.bar(
            positions + offsets[idx],
            values,
            width=width,
            label=str(row.get("label", f"Series {idx + 1}")),
            color=str(row.get("color", PALETTE["teal"])),
        )
    axis.set_xticks(positions, groups)
    axis.set_title(title, loc="left", pad=12)
    if ylabel:
        axis.set_ylabel(ylabel)
    axis.grid(axis="y", linestyle="--")
    if note:
        axis.text(
            0.99,
            0.02,
            note,
            transform=axis.transAxes,
            ha="right",
            va="bottom",
            fontsize=9,
            color=PALETTE["slate"],
        )
    axis.legend(frameon=False)
    save_figure(figure, output_path)


def render_status_matrix(
    *,
    title: str,
    row_labels: list[str],
    column_labels: list[str],
    values: list[list[int]],
    output_path: Path,
    pass_label: str = "pass",
    fail_label: str = "fail",
) -> None:
    """Render a status heatmap matrix."""
    figure, axis = plt.subplots(figsize=(9, 6.5))
    matrix = np.array(values)
    cmap = plt.matplotlib.colors.ListedColormap(["#FEE2E2", "#DCFCE7"])
    axis.imshow(matrix, cmap=cmap, aspect="auto")
    axis.set_xticks(np.arange(len(column_labels)), column_labels, rotation=20, ha="right")
    axis.set_yticks(np.arange(len(row_labels)), row_labels)
    axis.set_title(title, loc="left", pad=12)
    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            text = pass_label if matrix[row_index, column_index] else fail_label
            axis.text(column_index, row_index, text, ha="center", va="center", fontsize=9, color=PALETTE["ink"])
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.set_xticks(np.arange(-0.5, len(column_labels), 1), minor=True)
    axis.set_yticks(np.arange(-0.5, len(row_labels), 1), minor=True)
    axis.grid(which="minor", color="white", linewidth=2)
    axis.tick_params(which="minor", bottom=False, left=False)
    save_figure(figure, output_path)


def render_lane_diagram(
    *,
    title: str,
    lanes: list[dict[str, Any]],
    output_path: Path,
    footer: str = "",
) -> None:
    """Render a simple multi-lane workflow diagram."""
    figure, axis = plt.subplots(figsize=(14, 7))
    axis.set_xlim(0, 14)
    axis.set_ylim(0, 8)
    axis.axis("off")
    lane_width = 12.8 / max(1, len(lanes))
    origin_x = 0.4
    for lane_index, lane in enumerate(lanes):
        lane_x = origin_x + lane_index * lane_width
        lane_color = str(lane.get("lane_color", PALETTE["mint"]))
        edge_color = str(lane.get("edge_color", PALETTE["grid"]))
        title_color = str(lane.get("title_color", PALETTE["ink"]))
        axis.add_patch(
            patches.FancyBboxPatch(
                (lane_x, 0.7),
                lane_width - 0.35,
                6.6,
                boxstyle="round,pad=0.02,rounding_size=0.18",
                fc=lane_color,
                ec=edge_color,
                lw=1.5,
            )
        )
        axis.text(
            lane_x + (lane_width - 0.35) / 2,
            7.0,
            str(lane.get("title", "")),
            ha="center",
            va="center",
            fontsize=15,
            fontweight="bold",
            color=title_color,
        )
        steps = [str(item) for item in lane.get("steps", [])]
        step_height = 1.05
        start_y = 5.8
        for step_index, step in enumerate(steps):
            box_y = start_y - step_index * 1.35
            axis.add_patch(
                patches.FancyBboxPatch(
                    (lane_x + 0.28, box_y),
                    lane_width - 0.91,
                    step_height,
                    boxstyle="round,pad=0.02,rounding_size=0.12",
                    fc="white",
                    ec=edge_color,
                    lw=1.2,
                )
            )
            axis.text(
                lane_x + (lane_width - 0.35) / 2,
                box_y + step_height / 2,
                fill(step, width=28),
                ha="center",
                va="center",
                fontsize=10.5,
                color=PALETTE["ink"],
            )
        if lane_index < len(lanes) - 1:
            axis.annotate(
                "",
                xy=(lane_x + lane_width - 0.18, 4.6),
                xytext=(lane_x + lane_width - 0.55, 4.6),
                arrowprops=dict(arrowstyle="-|>", lw=1.8, color=PALETTE["slate"], mutation_scale=14),
            )
    if footer:
        axis.text(
            7.0,
            0.25,
            footer,
            ha="center",
            va="center",
            fontsize=11,
            color=PALETTE["ink"],
            bbox=dict(boxstyle="round,pad=0.35", facecolor=PALETTE["sand"], edgecolor=PALETTE["grid"]),
        )
    axis.set_title(title, loc="left", pad=14)
    save_figure(figure, output_path)


def _render_from_spec(spec: dict[str, Any], output_path: Path) -> None:
    figure_type = str(spec.get("type", "")).strip().lower()
    if figure_type == "horizontal_bar":
        render_horizontal_bar(
            title=str(spec.get("title", "")),
            labels=[str(item) for item in spec.get("labels", [])],
            values=[float(item) for item in spec.get("values", [])],
            output_path=output_path,
            color=str(spec.get("color", PALETTE["teal"])),
            xlabel=str(spec.get("xlabel", "")),
            note=str(spec.get("note", "")),
            wrap_width=int(spec.get("wrap_width", 18)),
        )
        return
    if figure_type == "grouped_bar":
        render_grouped_bar(
            title=str(spec.get("title", "")),
            groups=[str(item) for item in spec.get("groups", [])],
            series=list(spec.get("series", [])),
            output_path=output_path,
            ylabel=str(spec.get("ylabel", "")),
            note=str(spec.get("note", "")),
        )
        return
    if figure_type == "status_matrix":
        render_status_matrix(
            title=str(spec.get("title", "")),
            row_labels=[str(item) for item in spec.get("row_labels", [])],
            column_labels=[str(item) for item in spec.get("column_labels", [])],
            values=[[int(value) for value in row] for row in spec.get("values", [])],
            output_path=output_path,
            pass_label=str(spec.get("pass_label", "pass")),
            fail_label=str(spec.get("fail_label", "fail")),
        )
        return
    if figure_type == "lane_diagram":
        render_lane_diagram(
            title=str(spec.get("title", "")),
            lanes=list(spec.get("lanes", [])),
            output_path=output_path,
            footer=str(spec.get("footer", "")),
        )
        return
    raise ValueError(f"Unsupported figure spec type: {figure_type}")


def render_figure_spec(spec_path: Path, output_path: Path | None = None) -> Path:
    """Render a figure from a JSON spec."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError(f"Figure spec must be a JSON object: {spec_path}")
    set_figure_style()
    resolved_output = output_path or spec_path.with_suffix(".svg")
    _render_from_spec(spec, resolved_output)
    return resolved_output
