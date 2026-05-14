from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from bio_harness.pipeline_scripts.proteomics_diff_abundance import (
    run_proteomics_diff_abundance,
)


def _write_proteomics_inputs(
    tmp_path: Path,
    *,
    ambiguous_metadata: bool = False,
    malformed: bool = False,
    missingness: bool = False,
) -> tuple[Path, Path]:
    abundance_path = tmp_path / "abundance_matrix.csv"
    metadata_path = tmp_path / "metadata.csv"
    abundance_path.write_text(
        "\n".join(
            [
                "protein,sample_0,sample_1,sample_2,sample_3",
                "PROT_0001,20,20.5,23.2,24.0",
                "PROT_0002,19.5,20.0,22.8,23.0",
                "PROT_0003,21.0,20.8,20.9,21.1",
                "PROT_0004,18.0,18.3,17.9,18.1",
                "PROT_0005,17.0,,19.4,19.8" if missingness else "PROT_0005,17.0,17.2,19.4,19.8",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if malformed:
        abundance_path.write_text(
            abundance_path.read_text(encoding="utf-8") + "BROKEN,X,X,X,X\n",
            encoding="utf-8",
        )
    if ambiguous_metadata:
        metadata_path.write_text(
            "\n".join(
                [
                    "id,group,batch",
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
    return abundance_path, metadata_path


def test_proteomics_pipeline_writes_canonical_outputs(tmp_path: Path) -> None:
    abundance_path, metadata_path = _write_proteomics_inputs(tmp_path)
    output_dir = tmp_path / "out"

    summary = run_proteomics_diff_abundance(
        abundance_matrix=abundance_path,
        metadata_table=metadata_path,
        output_dir=output_dir,
    )

    assert summary["proteins_input"] == 5
    assert summary["proteins_retained"] == 5
    assert summary["sample_id_column"] == "sample"
    assert summary["group_column"] == "condition"
    result = pd.read_csv(output_dir / "proteomics_differential_abundance.csv")
    assert list(result.columns)[:4] == ["protein_id", "log2FoldChange", "pvalue", "mean_group_a"]
    assert (output_dir / "proteomics_qc_summary.json").is_file()
    assert (output_dir / "normalized_abundance_matrix.tsv").is_file()
    assert (output_dir / "volcano_plot_data.tsv").is_file()
    assert (output_dir / "proteomics_summary.md").is_file()


def test_proteomics_pipeline_infers_ambiguous_metadata_group_column(tmp_path: Path) -> None:
    abundance_path, metadata_path = _write_proteomics_inputs(tmp_path, ambiguous_metadata=True)

    summary = run_proteomics_diff_abundance(
        abundance_matrix=abundance_path,
        metadata_table=metadata_path,
        output_dir=tmp_path / "out",
    )

    assert summary["sample_id_column"] == "id"
    assert summary["group_column"] == "group"


def test_proteomics_pipeline_accepts_semantic_group_column_alias(tmp_path: Path) -> None:
    abundance_path, metadata_path = _write_proteomics_inputs(tmp_path, ambiguous_metadata=True)

    summary = run_proteomics_diff_abundance(
        abundance_matrix=abundance_path,
        metadata_table=metadata_path,
        output_dir=tmp_path / "out",
        group_column="condition",
    )

    assert summary["sample_id_column"] == "id"
    assert summary["group_column"] == "group"


def test_proteomics_pipeline_imputes_missing_values(tmp_path: Path) -> None:
    abundance_path, metadata_path = _write_proteomics_inputs(tmp_path, missingness=True)

    summary = run_proteomics_diff_abundance(
        abundance_matrix=abundance_path,
        metadata_table=metadata_path,
        output_dir=tmp_path / "out",
    )

    assert summary["imputed_value_count"] == 1


def test_proteomics_pipeline_rejects_nonnumeric_abundance_values(tmp_path: Path) -> None:
    abundance_path, metadata_path = _write_proteomics_inputs(tmp_path, malformed=True)

    with pytest.raises(ValueError, match="non-numeric values"):
        run_proteomics_diff_abundance(
            abundance_matrix=abundance_path,
            metadata_table=metadata_path,
            output_dir=tmp_path / "out",
        )


def test_proteomics_pipeline_cli_emits_format_input_marker_for_malformed_abundance(tmp_path: Path) -> None:
    abundance_path, metadata_path = _write_proteomics_inputs(tmp_path, malformed=True)

    proc = subprocess.run(
        [
            sys.executable,
            "<BIO_HARNESS_ROOT>/bio_harness/pipeline_scripts/proteomics_diff_abundance.py",
            "--abundance-matrix",
            str(abundance_path),
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
