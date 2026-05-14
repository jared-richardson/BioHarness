from __future__ import annotations

import pytest

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.bash_single_operation_policy import check_single_operation
from bio_harness.core.tool_registry import ToolRegistry
from bio_harness.skills.library.bcftools_filter_run import bcftools_filter_run


def test_bcftools_filter_run_renders_atomic_helper_command() -> None:
    command = bcftools_filter_run(
        input_vcf="/tmp/sample_raw.vcf.gz",
        output_vcf="/tmp/sample_filtered.vcf.gz",
        filter_expression="QUAL > 1",
    )

    assert str(preferred_helper_python_executable()) in command
    assert "run_bcftools_filter.py" in command
    assert "--input-vcf" in command
    assert "--output-vcf" in command
    assert "--filter-expression" in command
    assert "--output-type z" in command
    assert check_single_operation(command).passed is True


def test_bcftools_filter_run_supports_soft_filter_name_and_binary_output() -> None:
    command = bcftools_filter_run(
        input_vcf="/tmp/sample_raw.vcf.gz",
        output_vcf="/tmp/sample_filtered.bcf",
        filter_expression="QUAL > 30",
        output_type="b",
        soft_filter_name="LOW_QUAL",
    )

    assert "--output-type b" in command
    assert "--soft-filter-name LOW_QUAL" in command


def test_bcftools_filter_run_requires_required_arguments() -> None:
    with pytest.raises(
        ValueError,
        match="input_vcf, output_vcf, filter_expression",
    ):
        bcftools_filter_run(input_vcf="/tmp/sample_raw.vcf.gz")


def test_bcftools_filter_run_rejects_blank_filter_expression() -> None:
    with pytest.raises(
        ValueError,
        match="input_vcf, output_vcf, filter_expression",
    ):
        bcftools_filter_run(
            input_vcf="/tmp/sample_raw.vcf.gz",
            output_vcf="/tmp/sample_filtered.vcf.gz",
            filter_expression="   ",
        )


def test_bcftools_filter_run_registers_filtered_stage_metadata() -> None:
    registry = ToolRegistry.from_defaults(
        signal_equivalences={},
        parameter_knowledge_base={},
        skill_index_path=None,
    )

    meta = registry.get("bcftools_filter_run")
    assert meta is not None
    assert meta.consumes_stages == ["raw"]
    assert meta.produces_stages == ["filtered"]
