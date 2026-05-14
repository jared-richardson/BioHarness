from __future__ import annotations

import sys
from pathlib import Path

from bio_harness.skills.library.spatial_transcriptomics_workflow import (
    BUNDLED_SPATIAL_WORKFLOW,
    spatial_transcriptomics_workflow,
)


def test_spatial_workflow_defaults_to_bundled_script_and_pixi_python(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.spatial_transcriptomics_workflow.which_with_pixi",
        lambda name: "/opt/pixi/python3" if name == "python3" else None,
    )
    cmd = spatial_transcriptomics_workflow(
        input_path="/tmp/spatial.h5ad",
        output_dir="/tmp/out",
    )
    assert cmd.startswith("/opt/pixi/python3 ")
    assert str(BUNDLED_SPATIAL_WORKFLOW) in cmd
    assert "--input-path /tmp/spatial.h5ad" in cmd
    assert "--output-dir /tmp/out" in cmd
    assert "--min-genes 3" in cmd
    assert "--n-hvgs 50" in cmd


def test_spatial_workflow_accepts_legacy_input_h5ad(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.spatial_transcriptomics_workflow.which_with_pixi",
        lambda _name: None,
    )
    cmd = spatial_transcriptomics_workflow(
        input_h5ad="/tmp/spatial.h5ad",
        output_dir="/tmp/out",
        min_genes=5,
        min_cells=3,
        n_hvgs=24,
        n_pcs=6,
    )
    assert str(BUNDLED_SPATIAL_WORKFLOW) in cmd
    assert "--input-path /tmp/spatial.h5ad" in cmd
    assert "--min-genes 5" in cmd
    assert "--min-cells 3" in cmd
    assert "--n-hvgs 24" in cmd
    assert "--n-pcs 6" in cmd


def test_spatial_workflow_uses_existing_custom_script(monkeypatch, tmp_path: Path) -> None:
    custom_script = tmp_path / "custom_spatial.py"
    custom_script.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(
        "bio_harness.skills.library.spatial_transcriptomics_workflow.which_with_pixi",
        lambda _name: None,
    )
    cmd = spatial_transcriptomics_workflow(
        input_path="/tmp/spatial.h5ad",
        output_dir="/tmp/out",
        script_path=str(custom_script),
    )
    expected_python = sys.executable or "python3"
    assert cmd.startswith(expected_python)
    assert str(custom_script) in cmd


def test_spatial_workflow_requires_input_path() -> None:
    try:
        spatial_transcriptomics_workflow(output_dir="/tmp/out")
    except ValueError as exc:
        assert "input_path" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for missing input_path")
