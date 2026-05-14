from __future__ import annotations

from bio_harness.harness.execution_contract_scope import (
    is_compatible_tool_hint,
    scope_contract_to_execution_mode,
    scoped_capabilities_for_execution_contract,
)


def test_scope_contract_to_execution_mode_filters_sibling_wrapper_hints() -> None:
    scoped, compatible = scope_contract_to_execution_mode(
        {
            "explicit_tool_hints": ["salmon", "stringtie_quant"],
            "required_tool_hints": ["salmon", "stringtie_quant"],
            "must_include_capabilities": ["alignment", "quantification"],
        },
        {
            "execution_contract": {
                "analysis_family": "transcript_quantification",
                "input_mode": "aligned_bam",
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["stringtie_quant"],
            },
            "explicit_execution_intent": {"locked_tools": ["stringtie_quant"]},
        },
    )

    assert scoped["explicit_tool_hints"] == ["stringtie_quant"]
    assert scoped["required_tool_hints"] == ["stringtie_quant"]
    assert scoped["must_include_capabilities"] == ["quantification"]
    assert compatible == {"stringtie_quant"}


def test_scoped_capabilities_for_execution_contract_drops_alignment_for_bam_quant() -> None:
    assert scoped_capabilities_for_execution_contract(
        analysis_family="transcript_quantification",
        input_mode="aligned_bam",
        capabilities=["alignment", "annotation", "quantification"],
    ) == ["annotation", "quantification"]


def test_scoped_capabilities_for_execution_contract_trims_long_read_rna_to_alignment_boundary() -> None:
    assert scoped_capabilities_for_execution_contract(
        analysis_family="long_read_rna",
        input_mode="raw_fastq",
        capabilities=["alignment", "annotation", "reference_inputs", "quantification"],
    ) == ["alignment", "reference_inputs"]


def test_scoped_capabilities_for_execution_contract_trims_long_read_assembly_to_assembly_only() -> None:
    assert scoped_capabilities_for_execution_contract(
        analysis_family="long_read_assembly",
        input_mode="raw_fastq",
        capabilities=["alignment", "genome_assembly", "reference_inputs"],
    ) == ["genome_assembly"]


def test_scoped_capabilities_for_execution_contract_preserves_long_read_sv_boundary() -> None:
    assert scoped_capabilities_for_execution_contract(
        analysis_family="structural_variant_calling",
        input_mode="raw_fastq",
        capabilities=["alignment", "reference_inputs", "structural_variant_calling", "variant_calling"],
    ) == ["alignment", "reference_inputs", "structural_variant_calling"]


def test_is_compatible_tool_hint_matches_alias_prefixes() -> None:
    assert is_compatible_tool_hint("stringtie", {"stringtie_quant"})


def test_scope_contract_to_execution_mode_suppresses_discouraged_tool_family_hints() -> None:
    scoped, compatible = scope_contract_to_execution_mode(
        {
            "explicit_tool_hints": ["mutect2", "bcftools"],
            "required_tool_hints": ["mutect2"],
            "must_include_capabilities": ["variant_calling"],
        },
        {
            "execution_contract": {
                "analysis_family": "somatic_variant_calling",
                "input_mode": "raw_fastq",
                "execution_mode": "compiled_pipeline",
                "compatible_tools": [],
            },
        },
        preference_profile={"discouraged_tools": ["gatk"]},
    )

    assert scoped["explicit_tool_hints"] == ["bcftools"]
    assert scoped["required_tool_hints"] == []
    assert scoped["must_include_capabilities"] == ["variant_calling"]
    assert compatible == set()
