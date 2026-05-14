from __future__ import annotations

import pytest

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.bash_single_operation_policy import check_single_operation
from bio_harness.skills.library.bcftools_isec_run import bcftools_isec_run


def test_bcftools_isec_run_splits_space_separated_inputs() -> None:
    command = bcftools_isec_run(
        input_vcfs="/tmp/a.vcf.gz /tmp/b.vcf.gz",
        output_dir="/tmp/isec",
        mode="intersection",
        min_matches=2,
    )

    assert str(preferred_helper_python_executable()) in command
    assert "run_bcftools_isec.py" in command
    assert command.count("--input-vcf") == 2
    assert "--mode intersection" in command
    assert "--min-matches 2" in command
    check = check_single_operation(command)
    assert check.passed is True


def test_bcftools_isec_run_accepts_list_inputs_and_private_mode() -> None:
    command = bcftools_isec_run(
        input_vcfs=["/tmp/a.vcf.gz", "/tmp/b.vcf.gz", "/tmp/c.vcf.gz"],
        output_dir="/tmp/isec",
        output_vcf="/tmp/evol2.ancestor_subtracted.vcf.gz",
        mode="private",
        min_matches=3,
    )

    assert command.count("--input-vcf") == 3
    assert "--output-vcf /tmp/evol2.ancestor_subtracted.vcf.gz" in command
    assert "--mode private" in command
    assert "--min-matches 3" in command


def test_bcftools_isec_run_requires_multiple_inputs_and_output_dir() -> None:
    with pytest.raises(ValueError, match="input_vcfs, output_dir"):
        bcftools_isec_run(input_vcfs=["/tmp/a.vcf.gz"], output_dir="/tmp/isec")
