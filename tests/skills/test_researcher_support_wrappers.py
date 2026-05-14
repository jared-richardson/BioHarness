from __future__ import annotations

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.skills.library.artifact_schema_profile import artifact_schema_profile
from bio_harness.skills.library.multiqc_report import multiqc_report
from bio_harness.skills.library.quarto_report import quarto_report


def test_artifact_schema_profile_wrapper_renders_python_command() -> None:
    cmd = artifact_schema_profile("/tmp/results/output.csv", "/tmp/results/output.schema.json", sample_rows=12)
    assert str(preferred_helper_python_executable()) in cmd
    assert "profile_artifact_schema.py" in cmd
    assert "--output-json" in cmd
    assert "--sample-rows 12" in cmd


def test_multiqc_report_wrapper_enables_multiqc_bundle() -> None:
    cmd = multiqc_report("/tmp/run_dir", "/tmp/report_bundle")
    assert str(preferred_helper_python_executable()) in cmd
    assert "build_run_report_bundle.py" in cmd
    assert "--run-multiqc" in cmd
    assert "--output" in cmd


def test_quarto_report_wrapper_enables_quarto_bundle() -> None:
    cmd = quarto_report("/tmp/run_dir", "/tmp/report_bundle")
    assert str(preferred_helper_python_executable()) in cmd
    assert "build_run_report_bundle.py" in cmd
    assert "--render-quarto" in cmd
    assert "--output" in cmd
