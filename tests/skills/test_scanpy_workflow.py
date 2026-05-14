from __future__ import annotations

import sys
from pathlib import Path

from bio_harness.skills.library.scanpy_workflow import (
    BUNDLED_SCANPY_WORKFLOW,
    scanpy_workflow,
)


def test_scanpy_workflow_defaults_to_bundled_script_and_pixi_python(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.scanpy_workflow.which_with_pixi",
        lambda name: "/opt/pixi/python3" if name == "python3" else None,
    )
    cmd = scanpy_workflow(
        input_path="/tmp/input.h5ad",
        output_dir="/tmp/out",
    )
    assert cmd.startswith("/opt/pixi/python3 ")
    assert str(BUNDLED_SCANPY_WORKFLOW) in cmd
    assert "--input-path /tmp/input.h5ad" in cmd
    assert "--output-dir /tmp/out" in cmd
    assert "--min-genes 300" in cmd
    assert "--max-mito-pct 15" in cmd


def test_scanpy_workflow_accepts_legacy_input_h5ad(monkeypatch) -> None:
    monkeypatch.setattr(
        "bio_harness.skills.library.scanpy_workflow.which_with_pixi",
        lambda _name: None,
    )
    cmd = scanpy_workflow(
        input_h5ad="/tmp/input.h5ad",
        output_dir="/tmp/out",
        min_genes=3,
        min_cells=1,
        max_mito_pct=100,
        n_hvgs=48,
        leiden_resolution=0.3,
    )
    assert str(BUNDLED_SCANPY_WORKFLOW) in cmd
    assert "--input-path /tmp/input.h5ad" in cmd
    assert "--min-genes 3" in cmd
    assert "--min-cells 1" in cmd
    assert "--n-hvgs 48" in cmd


def test_scanpy_workflow_uses_existing_custom_script(monkeypatch, tmp_path: Path) -> None:
    custom_script = tmp_path / "custom_scanpy.py"
    custom_script.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(
        "bio_harness.skills.library.scanpy_workflow.which_with_pixi",
        lambda _name: None,
    )
    cmd = scanpy_workflow(
        input_path="/tmp/input.h5ad",
        output_dir="/tmp/out",
        script_path=str(custom_script),
    )
    expected_python = sys.executable or "python3"
    assert cmd.startswith(expected_python)
    assert str(custom_script) in cmd


def test_scanpy_workflow_requires_input_path() -> None:
    try:
        scanpy_workflow(output_dir="/tmp/out")
    except ValueError as exc:
        assert "input_path" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for missing input_path")
