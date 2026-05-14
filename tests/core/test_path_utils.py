from __future__ import annotations

from pathlib import Path

from bio_harness.harness.path_utils import _redirect_output_paths_to_selected_dir


def test_redirect_output_paths_preserves_absolute_inputs_when_output_dir_is_relative(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": "/tmp/workspace/non_bioagent_real_data/airway/airway_counts.tsv",
                    "metadata_table": "/tmp/workspace/non_bioagent_real_data/airway/airway_metadata_dex.tsv",
                    "design_formula": "~ dex",
                    "contrast": "dex",
                    "output_dir": "work",
                },
            }
        ]
    }

    repaired, meta = _redirect_output_paths_to_selected_dir(
        plan,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["counts_matrix"] == "/tmp/workspace/non_bioagent_real_data/airway/airway_counts.tsv"
    assert args["metadata_table"] == "/tmp/workspace/non_bioagent_real_data/airway/airway_metadata_dex.tsv"
    assert args["output_dir"] == str((selected_dir / "work").resolve(strict=False))


def test_redirect_output_paths_uses_registry_outputs_without_rewriting_reference_gtf(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                    "output_gtf": "/tmp/outside/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/outside/gene_abundances.tsv",
                },
            }
        ]
    }

    repaired, meta = _redirect_output_paths_to_selected_dir(
        plan,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["annotation_gtf"] == "/tmp/refs/genes.gtf"
    assert args["output_gtf"] == str((selected_dir / "assembled.gtf").resolve(strict=False))
    assert args["gene_abundance_tsv"] == str(
        (selected_dir / "gene_abundances.tsv").resolve(strict=False)
    )


def test_redirect_output_paths_does_not_rewrite_rmats_annotation_gtf(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "rmats_run",
                "arguments": {
                    "group1_bams": "/tmp/treatment1.bam,/tmp/treatment2.bam",
                    "group2_bams": "/tmp/control1.bam,/tmp/control2.bam",
                    "annotation_gtf": "/tmp/refs/annotation.gtf",
                    "output_dir": "/tmp/outside/splicing",
                },
            }
        ]
    }

    repaired, meta = _redirect_output_paths_to_selected_dir(
        plan,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["annotation_gtf"] == "/tmp/refs/annotation.gtf"
    assert args["output_dir"] == str((selected_dir / "splicing").resolve(strict=False))
