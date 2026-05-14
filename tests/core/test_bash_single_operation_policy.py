"""Regression tests for the atomic bash single-operation policy."""

from __future__ import annotations

from bio_harness.core.bash_single_operation_policy import check_single_operation


def test_check_single_operation_allows_one_simple_command() -> None:
    result = check_single_operation(
        "bcftools norm -m -any -f ref.fa sample_A.annotated.vcf -o sample_A.normalized.vcf"
    )

    assert result.passed is True
    assert result.operation_count == 1
    assert result.violations == []


def test_check_single_operation_rejects_pipe_and_chain() -> None:
    result = check_single_operation(
        "mkdir -p /tmp/out && bwa mem ref.fa R1.fq R2.fq | samtools sort -o /tmp/out/sample.bam -"
    )

    assert result.passed is False
    assert "compound_and" in result.violations
    assert "compound_pipe" in result.violations


def test_check_single_operation_rejects_compound_and() -> None:
    result = check_single_operation("echo sample_A && echo sample_B")

    assert result.passed is False
    assert "compound_and" in result.violations


def test_check_single_operation_rejects_compound_semicolon() -> None:
    result = check_single_operation("echo sample_A; echo sample_B")

    assert result.passed is False
    assert "compound_semicolon" in result.violations


def test_check_single_operation_rejects_missing_separator_multiline_command() -> None:
    result = check_single_operation(
        "bcftools norm -m -any -f ref.fa sample_A.annotated.vcf -o sample_A.normalized.vcf\n"
        "bcftools norm -m -any -f ref.fa sample_B.annotated.vcf -o sample_B.normalized.vcf"
    )

    assert result.passed is False
    assert result.operation_count == 2
    assert "missing_command_separator" in result.violations


def test_check_single_operation_rejects_large_compound_export_sequence() -> None:
    result = check_single_operation(
        "bcftools norm -m -any -f ref.fa sample_A.annotated.vcf -o sample_A.norm.vcf && "
        "tabix -f -p vcf sample_A.norm.vcf && "
        "bcftools norm -m -any -f ref.fa sample_B.annotated.vcf -o sample_B.norm.vcf && "
        "tabix -f -p vcf sample_B.norm.vcf && "
        "python3 scripts/export_shared_variants_csv.py --input-a sample_A.norm.vcf "
        "--input-b sample_B.norm.vcf --output shared.csv"
    )

    assert result.passed is False
    assert "compound_and" in result.violations


def test_check_single_operation_returns_parse_error_without_raising() -> None:
    result = check_single_operation("echo 'unterminated")

    assert result.passed is False
    assert result.operation_count == 0
    assert any(violation.startswith("parse_error:") for violation in result.violations)
