from __future__ import annotations

from pathlib import Path

from bio_harness.core.request_scope import (
    _SKILL_ANALYSIS_TYPE_OVERRIDES,
    _SKILL_REQUEST_ALIASES,
)
from bio_harness.core.wrapper_contracts import (
    normalize_bwa_read_group,
    normalize_snpeff_codon_table,
    normalize_wrapper_argument_value,
    wrapper_multi_input_args,
    wrapper_path_args,
    wrapper_has_contract,
)


def test_wrapper_contracts_cover_explicit_request_scope_skills() -> None:
    prompt_lockable = set(_SKILL_ANALYSIS_TYPE_OVERRIDES) | set(_SKILL_REQUEST_ALIASES.values())

    missing = sorted(skill for skill in prompt_lockable if not wrapper_has_contract(skill))

    assert missing == []


def test_normalize_wrapper_argument_value_splits_multi_input_featurecounts_bams() -> None:
    normalized = normalize_wrapper_argument_value(
        "featurecounts_run",
        "input_bams",
        "/tmp/A.bam /tmp/B.bam",
    )

    assert normalized == ["/tmp/A.bam", "/tmp/B.bam"]


def test_normalize_wrapper_argument_value_splits_and_canonicalizes_isec_inputs() -> None:
    selected_dir = Path("/tmp") / "selected"

    normalized = normalize_wrapper_argument_value(
        "bcftools_isec_run",
        "input_vcfs",
        "./sample_A.vcf.gz ./sample_B.vcf.gz",
        cwd=selected_dir,
    )

    assert normalized == [
        str((selected_dir / "sample_A.vcf.gz").resolve(strict=False)),
        str((selected_dir / "sample_B.vcf.gz").resolve(strict=False)),
    ]


def test_wrapper_multi_input_args_include_atomic_isec_inputs() -> None:
    assert wrapper_multi_input_args("bcftools_isec_run") == frozenset({"input_vcfs"})
    assert wrapper_path_args("bcftools_isec_run") == frozenset(
        {"input_vcfs", "output_dir", "output_vcf"}
    )


def test_wrapper_contract_exposes_atomic_filter_paths() -> None:
    assert wrapper_has_contract("bcftools_filter_run") is True
    assert wrapper_path_args("bcftools_filter_run") == frozenset({"input_vcf", "output_vcf"})


def test_normalize_wrapper_argument_value_rejects_malformed_bracketed_multi_input() -> None:
    normalized = normalize_wrapper_argument_value(
        "featurecounts_run",
        "input_bams",
        "[not really a list]",
    )

    assert normalized == []


def test_normalize_wrapper_argument_value_canonicalizes_relative_path_args() -> None:
    selected_dir = Path("/tmp") / "selected"

    normalized = normalize_wrapper_argument_value(
        "snpeff_annotate",
        "input_vcf",
        "./evol1_subtracted_anc.vcf.gz",
        cwd=selected_dir,
    )

    expected = (selected_dir / "evol1_subtracted_anc.vcf.gz").resolve(strict=False)
    assert normalized == str(expected)


def test_normalize_bwa_read_group_promotes_sample_only_payload() -> None:
    normalized = normalize_bwa_read_group("SM:evol2", sample_name="evol2")

    assert normalized == r"@RG\tID:evol2\tSM:evol2\tPL:ILLUMINA\tLB:lib1"


def test_normalize_snpeff_codon_table_clears_numeric_bacterial_hint() -> None:
    normalized = normalize_snpeff_codon_table("11")

    assert normalized == ""


def test_normalize_snpeff_codon_table_clears_plain_bacterial_hint() -> None:
    normalized = normalize_snpeff_codon_table("Bacterial")

    assert normalized == ""


def test_normalize_snpeff_codon_table_preserves_named_non_bacterial_override() -> None:
    normalized = normalize_snpeff_codon_table("Vertebrate_Mitochondrial")

    assert normalized == "Vertebrate_Mitochondrial"
