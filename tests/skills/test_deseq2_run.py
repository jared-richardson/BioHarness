from __future__ import annotations

from pathlib import Path

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.skills.library.deseq2_run import BUNDLED_DESEQ2_WRAPPER, deseq2_run
import bio_harness.skills.library.deseq2_run as deseq2_run_mod


def test_deseq2_run_falls_back_to_bundled_wrapper_when_script_missing():
    cmd = deseq2_run(
        script_path="/tmp/definitely_missing_wrapper.R",
        counts_matrix="/tmp/counts.tsv",
        metadata_table="/tmp/meta.tsv",
        design_formula="~ condition",
        contrast="condition_treat_vs_ctrl",
        output_dir="/tmp/out",
    )
    assert str(BUNDLED_DESEQ2_WRAPPER) in cmd


def test_deseq2_run_uses_explicit_script_when_present(tmp_path):
    script = tmp_path / "wrapper.R"
    script.write_text("cat('ok\\n')\n", encoding="utf-8")
    cmd = deseq2_run(
        script_path=str(script),
        counts_matrix="/tmp/counts.tsv",
        metadata_table="/tmp/meta.tsv",
        design_formula="~ condition",
        contrast="condition_treat_vs_ctrl",
        output_dir="/tmp/out",
    )
    assert str(script) in cmd


def test_deseq2_run_resolves_placeholder_data_and_results_paths(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    output = workspace / "output"
    output.mkdir(parents=True, exist_ok=True)
    counts = output / "gene_counts.txt"
    metadata = output / "sample_metadata.tsv"
    counts.write_text("Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1\tS2\ng1\tchr1\t1\t2\t+\t2\t10\t11\n", encoding="utf-8")
    metadata.write_text("sample\tcondition\nS1\tcontrol\nS2\ttreated\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    cmd = deseq2_run(
        script_path="/tmp/definitely_missing_wrapper.R",
        counts_matrix="/data/counts_matrix.tsv",
        metadata_table="/data/metadata.tsv",
        design_formula="~ condition",
        contrast="condition_treated_vs_control",
        output_dir="/results/deseq2_results",
    )

    assert str(counts) in cmd
    assert str(metadata) in cmd
    assert str(workspace / "output" / "deseq2_results") in cmd


def test_deseq2_run_uses_managed_python_for_pydeseq2_backend():
    cmd = deseq2_run(
        engine="pydeseq2",
        counts_matrix="/tmp/counts.tsv",
        metadata_table="/tmp/meta.tsv",
        design_formula="~ condition",
        contrast="condition_treat_vs_ctrl",
        output_dir="/tmp/out",
    )

    project_root = Path(__file__).resolve().parents[2]
    assert f"env PYTHONPATH={project_root}" in cmd
    assert str(preferred_helper_python_executable()) in cmd
    assert str(BUNDLED_DESEQ2_WRAPPER.parents[0] / "pydeseq2_wrapper.py") in cmd


def test_deseq2_run_uses_resolved_rscript_for_deseq2_engine(monkeypatch):
    monkeypatch.setattr(
        deseq2_run_mod,
        "rscript_for_requirement",
        lambda name: "/tmp/pixi-r/bin/Rscript" if name == "deseq2" else None,
    )

    cmd = deseq2_run(
        engine="deseq2",
        counts_matrix="/tmp/counts.tsv",
        metadata_table="/tmp/meta.tsv",
        design_formula="~ condition",
        contrast="condition_treat_vs_ctrl",
        output_dir="/tmp/out",
    )

    assert "/tmp/pixi-r/bin/Rscript" in cmd
