from __future__ import annotations

import json
from pathlib import Path

from bio_harness.analysis.figure_factory import render_figure_spec


def test_render_horizontal_bar_spec_writes_svg_and_png(tmp_path: Path) -> None:
    spec = {
        "type": "horizontal_bar",
        "title": "Example",
        "labels": ["A", "B"],
        "values": [1, 2],
        "xlabel": "Count",
    }
    spec_path = tmp_path / "bar.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    output_path = render_figure_spec(spec_path)

    assert output_path.exists()
    assert output_path.with_suffix(".png").exists()


def test_render_lane_diagram_spec_writes_svg_and_png(tmp_path: Path) -> None:
    spec = {
        "type": "lane_diagram",
        "title": "Workflow",
        "footer": "Example footer",
        "lanes": [
            {"title": "One", "steps": ["A", "B"]},
            {"title": "Two", "steps": ["C", "D"]},
        ],
    }
    spec_path = tmp_path / "workflow.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    output_path = render_figure_spec(spec_path)

    assert output_path.exists()
    assert output_path.with_suffix(".png").exists()
