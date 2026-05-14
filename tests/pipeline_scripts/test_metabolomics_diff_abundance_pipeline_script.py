from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from bio_harness.pipeline_scripts.metabolomics_diff_abundance import (
    run_metabolomics_diff_abundance,
)


def _write_metabolomics_inputs(
    tmp_path: Path,
    *,
    ambiguous_metadata: bool = False,
    malformed: bool = False,
    missingness: bool = False,
) -> tuple[Path, Path]:
    feature_path = tmp_path / "feature_table.csv"
    metadata_path = tmp_path / "metadata.csv"
    feature_path.write_text(
        "\n".join(
            [
                "feature,sample_0,sample_1,sample_2,sample_3",
                "mz100_rt1,20,20.5,23.2,24.0",
                "mz101_rt2,19.5,20.0,22.8,23.0",
                "mz102_rt3,21.0,20.8,20.9,21.1",
                "mz103_rt4,18.0,18.3,17.9,18.1",
                "mz104_rt5,17.0,,19.4,19.8" if missingness else "mz104_rt5,17.0,17.2,19.4,19.8",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if malformed:
        feature_path.write_text(
            feature_path.read_text(encoding="utf-8") + "BROKEN,X,X,X,X\n",
            encoding="utf-8",
        )
    if ambiguous_metadata:
        metadata_path.write_text(
            "\n".join(
                [
                    "id,treatment_group,batch",
                    "sample_0,control,b0",
                    "sample_1,control,b1",
                    "sample_2,treatment,b0",
                    "sample_3,treatment,b1",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        metadata_path.write_text(
            "\n".join(
                [
                    "sample,condition",
                    "sample_0,control",
                    "sample_1,control",
                    "sample_2,treatment",
                    "sample_3,treatment",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return feature_path, metadata_path


def test_metabolomics_pipeline_writes_canonical_outputs(tmp_path: Path) -> None:
    feature_path, metadata_path = _write_metabolomics_inputs(tmp_path)
    output_dir = tmp_path / "out"

    summary = run_metabolomics_diff_abundance(
        feature_table=feature_path,
        metadata_table=metadata_path,
        output_dir=output_dir,
    )

    assert summary["features_input"] == 5
    assert summary["features_retained"] == 5
    assert summary["sample_id_column"] == "sample"
    assert summary["group_column"] == "condition"
    result = pd.read_csv(output_dir / "metabolomics_differential_abundance.csv")
    assert list(result.columns)[:4] == ["feature_id", "log2FoldChange", "pvalue", "mean_group_a"]
    assert (output_dir / "metabolomics_qc_summary.json").is_file()
    assert (output_dir / "normalized_feature_matrix.tsv").is_file()
    assert (output_dir / "volcano_plot_data.tsv").is_file()
    assert (output_dir / "metabolomics_summary.md").is_file()


def test_metabolomics_pipeline_infers_ambiguous_metadata_group_column(tmp_path: Path) -> None:
    feature_path, metadata_path = _write_metabolomics_inputs(tmp_path, ambiguous_metadata=True)

    summary = run_metabolomics_diff_abundance(
        feature_table=feature_path,
        metadata_table=metadata_path,
        output_dir=tmp_path / "out",
    )

    assert summary["sample_id_column"] == "id"
    assert summary["group_column"] == "treatment_group"


def test_metabolomics_pipeline_imputes_missing_values(tmp_path: Path) -> None:
    feature_path, metadata_path = _write_metabolomics_inputs(tmp_path, missingness=True)

    summary = run_metabolomics_diff_abundance(
        feature_table=feature_path,
        metadata_table=metadata_path,
        output_dir=tmp_path / "out",
    )

    assert summary["imputed_value_count"] == 1


def test_metabolomics_pipeline_rejects_nonnumeric_feature_values(tmp_path: Path) -> None:
    feature_path, metadata_path = _write_metabolomics_inputs(tmp_path, malformed=True)

    with pytest.raises(ValueError, match="non-numeric values"):
        run_metabolomics_diff_abundance(
            feature_table=feature_path,
            metadata_table=metadata_path,
            output_dir=tmp_path / "out",
        )


def test_metabolomics_pipeline_cli_emits_format_input_marker_for_malformed_feature_table(tmp_path: Path) -> None:
    feature_path, metadata_path = _write_metabolomics_inputs(tmp_path, malformed=True)

    proc = subprocess.run(
        [
            sys.executable,
            "<BIO_HARNESS_ROOT>/bio_harness/pipeline_scripts/metabolomics_diff_abundance.py",
            "--feature-table",
            str(feature_path),
            "--metadata-table",
            str(metadata_path),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "__FORMAT_INPUT_ERROR__:" in proc.stderr
    assert "non-numeric values" in proc.stderr
