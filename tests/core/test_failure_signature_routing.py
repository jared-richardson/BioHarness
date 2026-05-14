"""Tests for ordered runtime failure-signature routing."""

from __future__ import annotations

from bio_harness.core.failure_signatures import route_runtime_failure_signature


def test_route_runtime_failure_signature_prefers_unresolved_placeholders() -> None:
    route = route_runtime_failure_signature(
        command="bcftools norm -m -any -f <reference_fasta> sample.vcf -o out.vcf",
        error_text="Validation blocked: placeholder_token_in_path:/tmp/<reference_fasta>/ref.fa",
        tool_name="bash_run",
        issues=[],
    )

    assert route == "unresolved_placeholder_in_command"


def test_route_runtime_failure_signature_only_marks_bcftools_isec_when_command_matches() -> None:
    placeholder_route = route_runtime_failure_signature(
        command="bcftools norm -m -any -f ref.fa sample.vcf -o out.vcf",
        error_text="invalid_bcftools_isec_output_mode",
        tool_name="bash_run",
        issues=[{"issue": "invalid_bcftools_isec_output_mode"}],
    )
    isec_route = route_runtime_failure_signature(
        command="bcftools isec -C -w1 sample_A.vcf.gz sample_B.vcf.gz -p out_dir",
        error_text="invalid_bcftools_isec_output_mode",
        tool_name="bash_run",
        issues=[{"issue": "invalid_bcftools_isec_output_mode"}],
    )

    assert placeholder_route is None
    assert isec_route == "bcftools_isec_semantic"
