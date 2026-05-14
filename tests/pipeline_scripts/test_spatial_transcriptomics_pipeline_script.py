from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from bio_harness.pipeline_scripts.spatial_transcriptomics_workflow import (
    run_spatial_transcriptomics_workflow,
)


def _make_spatial_adata() -> ad.AnnData:
    coords = np.array(
        [(x, y) for x in range(6) for y in range(6)],
        dtype=float,
    )[:18]
    truth = np.array(
        ["DomainA" if x < 2 else "DomainB" if x < 4 else "DomainC" for x, _y in coords],
        dtype=object,
    )
    matrix = np.random.RandomState(0).poisson(3, (coords.shape[0], 12)).astype(float)
    matrix[truth == "DomainA", 0:2] += 20
    matrix[truth == "DomainB", 2:4] += 20
    matrix[truth == "DomainC", 4:6] += 20
    adata = ad.AnnData(
        X=sp.csr_matrix(matrix),
        obs=pd.DataFrame({"domain_truth": truth}, index=[f"spot_{i}" for i in range(coords.shape[0])]),
        var=pd.DataFrame(index=[f"Gene_{i}" for i in range(matrix.shape[1])]),
    )
    adata.obsm["spatial"] = coords
    return adata


def test_spatial_workflow_writes_canonical_outputs(tmp_path: Path) -> None:
    input_path = tmp_path / "input.h5ad"
    output_dir = tmp_path / "out"
    _make_spatial_adata().write_h5ad(str(input_path))

    summary = run_spatial_transcriptomics_workflow(
        input_path=input_path,
        output_dir=output_dir,
    )

    assert summary["spots_input"] == 18
    assert summary["spots_retained"] > 0
    assert summary["domains_detected"] >= 2
    assert (output_dir / "spatial_domain_assignments.csv").is_file()
    assert (output_dir / "spatial_marker_genes.csv").is_file()
    assert (output_dir / "spatial_results.h5ad").is_file()
    assert (output_dir / "spatial_qc_summary.json").is_file()
    assert (output_dir / "spatial_summary.md").is_file()


def test_spatial_workflow_rejects_nonfinite_coordinates(tmp_path: Path) -> None:
    adata = _make_spatial_adata()
    adata.obsm["spatial"][0, 0] = np.nan
    input_path = tmp_path / "bad_input.h5ad"
    adata.write_h5ad(str(input_path))

    with pytest.raises(ValueError, match="non-finite"):
        run_spatial_transcriptomics_workflow(
            input_path=input_path,
            output_dir=tmp_path / "out",
        )


def test_spatial_workflow_cli_emits_format_input_marker_for_nonfinite_coordinates(tmp_path: Path) -> None:
    adata = _make_spatial_adata()
    adata.obsm["spatial"][0, 0] = np.nan
    input_path = tmp_path / "bad_input.h5ad"
    adata.write_h5ad(str(input_path))

    proc = subprocess.run(
        [
            sys.executable,
            "<BIO_HARNESS_ROOT>/bio_harness/pipeline_scripts/spatial_transcriptomics_workflow.py",
            "--input-path",
            str(input_path),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "__FORMAT_INPUT_ERROR__:" in proc.stderr
    assert "non-finite values" in proc.stderr
