from __future__ import annotations

import pytest

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.bash_single_operation_policy import check_single_operation
from bio_harness.skills.library.shared_variants_export_run import shared_variants_export_run


def test_shared_variants_export_run_renders_atomic_helper_command() -> None:
    command = shared_variants_export_run(
        input_vcf_a="/tmp/a.annotated.vcf.gz",
        input_vcf_b="/tmp/b.annotated.vcf.gz",
        output_csv="/tmp/shared.csv",
    )

    assert str(preferred_helper_python_executable()) in command
    assert "export_shared_variants_csv.py" in command
    assert "--input-vcf-a" in command
    assert "--input-vcf-b" in command
    assert "--output-csv" in command
    assert "--min-impact MODERATE" in command
    assert "--status shared" in command
    assert "--header-case upper" in command
    assert "--dedupe-by-gene" in command
    check = check_single_operation(command)
    assert check.passed is True


def test_shared_variants_export_run_allows_disabling_gene_deduplication() -> None:
    command = shared_variants_export_run(
        input_vcf_a="/tmp/a.annotated.vcf.gz",
        input_vcf_b="/tmp/b.annotated.vcf.gz",
        output_csv="/tmp/shared.csv",
        dedupe_by_gene=False,
        header_case="lower",
    )

    assert "--dedupe-by-gene" not in command
    assert "--header-case lower" in command


def test_shared_variants_export_run_requires_all_paths() -> None:
    with pytest.raises(ValueError, match="input_vcf_a, input_vcf_b, output_csv"):
        shared_variants_export_run(input_vcf_a="/tmp/a.vcf.gz", output_csv="/tmp/shared.csv")
