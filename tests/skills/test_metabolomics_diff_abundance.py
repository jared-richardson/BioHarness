from __future__ import annotations

import sys
from pathlib import Path

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.skills.library.metabolomics_diff_abundance import (
    BUNDLED_METABOLOMICS_WORKFLOW,
    metabolomics_diff_abundance,
)


def test_metabolomics_wrapper_defaults_to_bundled_script() -> None:
    cmd = metabolomics_diff_abundance(
        feature_table="/tmp/feature_table.csv",
        metadata_table="/tmp/metadata.csv",
        output_dir="/tmp/out",
    )

    assert str(BUNDLED_METABOLOMICS_WORKFLOW) in cmd
    assert "--feature-table /tmp/feature_table.csv" in cmd
    assert "--metadata-table /tmp/metadata.csv" in cmd
    assert "--output-dir /tmp/out" in cmd
    assert "--normalization-method median_center" in cmd
    assert "--impute-method feature_median" in cmd


def test_metabolomics_wrapper_uses_existing_custom_script(tmp_path: Path) -> None:
    custom_script = tmp_path / "custom_metabolomics.py"
    custom_script.write_text("print('ok')\n", encoding="utf-8")
    cmd = metabolomics_diff_abundance(
        feature_table="/tmp/feature_table.csv",
        metadata_table="/tmp/metadata.csv",
        output_dir="/tmp/out",
        script_path=str(custom_script),
        group_column="condition",
    )

    expected_python = sys.executable or "python3"
    helper_python = str(preferred_helper_python_executable())
    assert (
        cmd.startswith(expected_python)
        or cmd.startswith(helper_python)
        or cmd.startswith("env ")
    )
    assert str(custom_script) in cmd
    assert "--group-column condition" in cmd


def test_metabolomics_wrapper_requires_required_inputs() -> None:
    try:
        metabolomics_diff_abundance(output_dir="/tmp/out")
    except ValueError as exc:
        assert "feature_table" in str(exc) or "metadata_table" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for missing required inputs")
