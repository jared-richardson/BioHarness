from __future__ import annotations

from pathlib import Path

from bio_harness.core.benchmark_asset_integrity import (
    repair_benchmark_input_assets,
    render_bioagentbench_deseq_sample_metadata,
)


def test_repair_benchmark_input_assets_restores_canonical_deseq_metadata(
    tmp_path: Path,
) -> None:
    data_root = (
        tmp_path
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "data"
    )
    data_root.mkdir(parents=True, exist_ok=True)
    metadata_path = data_root / "sample_metadata.tsv"
    metadata_path.write_text(
        "sample\tcondition\ncondition\tunknown\n",
        encoding="utf-8",
    )

    report = repair_benchmark_input_assets(
        data_root=data_root,
        analysis_type="rna_seq_differential_expression",
    )

    assert report.matched_profile == "bioagentbench_deseq_v1"
    assert report.changed is True
    assert len(report.actions) == 1
    assert report.actions[0].repair_id == "bioagentbench_deseq_sample_metadata_v1"
    assert metadata_path.read_text(encoding="utf-8") == (
        render_bioagentbench_deseq_sample_metadata()
    )


def test_repair_benchmark_input_assets_ignores_nonbenchmark_roots(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "inputs"
    data_root.mkdir(parents=True, exist_ok=True)
    metadata_path = data_root / "sample_metadata.tsv"
    original_text = "sample\tcondition\ncondition\tunknown\n"
    metadata_path.write_text(original_text, encoding="utf-8")

    report = repair_benchmark_input_assets(
        data_root=data_root,
        analysis_type="rna_seq_differential_expression",
    )

    assert report.matched_profile == ""
    assert report.changed is False
    assert report.actions == ()
    assert metadata_path.read_text(encoding="utf-8") == original_text
