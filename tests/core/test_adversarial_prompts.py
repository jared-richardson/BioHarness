"""Adversarial and near-miss prompt testing for contract inference.

This module tests contract inference robustness against:
- Paraphrased prompts (same intent, different wording)
- Near-miss prompts (similar wording but different analysis type)
- Ambiguous prompts (could be multiple types)
- Conflicting signals (contract says one thing, keywords say another)
- Minimal prompts (very short, underspecified)
"""

from __future__ import annotations

import pytest

from bio_harness.core.analysis_spec import infer_analysis_type


# ---------------------------------------------------------------------------
# Paraphrased prompts — same intent, different wording
# ---------------------------------------------------------------------------

class TestParaphrasedPrompts:
    """Prompts that should resolve to the same analysis type despite rewording."""

    @pytest.mark.parametrize("query", [
        "Find shared mutations between evolved E. coli lines and their ancestor.",
        "Identify variants present in both evolved strains but absent from the ancestral genome.",
        "Compare evolved bacterial isolates to their common ancestor to find de novo variants.",
        "Call variants in evolved lines relative to the ancestor using a bacterial evolution workflow.",
    ])
    def test_evolution_paraphrases(self, query: str) -> None:
        contract = {"must_include_capabilities": ["variant_calling"]}
        assert infer_analysis_type(query, contract) == "bacterial_evolution_variant_calling"

    @pytest.mark.parametrize("query", [
        "Quantify gene expression differences between control and treated samples.",
        "Perform differential expression analysis on paired-end RNA-seq.",
        "Run a DE analysis comparing control vs. treatment groups.",
        "Find differentially expressed genes from RNA-seq count data.",
    ])
    def test_de_paraphrases(self, query: str) -> None:
        contract = {"must_include_capabilities": ["differential_analysis"]}
        assert infer_analysis_type(query, contract) == "rna_seq_differential_expression"

    @pytest.mark.parametrize("query", [
        "Estimate transcript abundance using Salmon.",
        "Quantify transcript-level expression with pseudoalignment.",
        "Run transcript quantification on paired-end RNA-seq reads.",
    ])
    def test_transcript_quant_paraphrases(self, query: str) -> None:
        contract = {"must_include_capabilities": ["quantification"]}
        assert infer_analysis_type(query, contract) == "transcript_quantification"

    @pytest.mark.parametrize("query", [
        "Cluster single-cell RNA-seq data and identify marker genes.",
        "Perform scRNA-seq clustering with scanpy on 10x Chromium data.",
        "Analyze single cell expression data to find cell populations.",
    ])
    def test_sc_paraphrases(self, query: str) -> None:
        contract = {"must_include_capabilities": ["single_cell_analysis"]}
        assert infer_analysis_type(query, contract) == "single_cell_rna_seq"

    @pytest.mark.parametrize("query", [
        "Call germline variants and benchmark with GIAB truth set.",
        "Identify germline SNPs and indels using GATK HaplotypeCaller.",
        "Run germline variant calling on whole-genome paired-end reads from NIST sample.",
    ])
    def test_germline_vc_paraphrases(self, query: str) -> None:
        contract = {"must_include_capabilities": ["variant_calling"]}
        assert infer_analysis_type(query, contract) == "germline_variant_calling"


# ---------------------------------------------------------------------------
# Near-miss prompts — similar wording but should resolve differently
# ---------------------------------------------------------------------------

class TestNearMissPrompts:
    """Prompts that look similar to one type but should resolve to another."""

    def test_annotation_not_variant_calling(self) -> None:
        """'Annotate variants' should be annotation, not variant calling."""
        query = "Annotate variants with SnpEff and filter for high-impact mutations."
        contract = {"must_include_capabilities": ["variant_calling"]}
        result = infer_analysis_type(query, contract)
        assert result == "variant_annotation"

    def test_pathway_enrichment_not_plain_de(self) -> None:
        """DE + pathway keywords should route to multi_model_dge, not plain DE."""
        query = "Run differential expression and KEGG pathway enrichment across mouse models."
        contract = {"must_include_capabilities": ["differential_analysis"]}
        result = infer_analysis_type(query, contract)
        assert result == "multi_model_dge_pathway"

    def test_viral_not_bacterial_metagenomics(self) -> None:
        """Viral metagenomics should not route to bacterial metagenomics."""
        query = "Classify viral reads from paired-end virome sequencing data."
        contract = {"must_include_capabilities": ["metagenomics_profiling"]}
        result = infer_analysis_type(query, contract)
        assert result == "viral_metagenomics"

    def test_structural_variant_not_snp_calling(self) -> None:
        """Long-read SV calling should be structural, not germline/evolution."""
        query = "Call structural variants from long-read PacBio alignments."
        contract = {"must_include_capabilities": ["structural_variant_calling"]}
        result = infer_analysis_type(query, contract)
        assert result == "structural_variant_calling"


# ---------------------------------------------------------------------------
# Ambiguous prompts — could match multiple types
# ---------------------------------------------------------------------------

class TestAmbiguousPrompts:
    """Prompts with mixed signals; verify consistent resolution."""

    def test_evolution_plus_annotation_keywords(self) -> None:
        """Evolution workflow keywords should dominate over annotation hints."""
        query = "Find shared mutations in evolved isolates and annotate with SnpEff."
        contract = {"must_include_capabilities": ["variant_calling"]}
        result = infer_analysis_type(query, contract)
        # Should resolve to evolution (the primary intent)
        assert result == "bacterial_evolution_variant_calling"

    def test_de_with_quantification_keywords(self) -> None:
        """DE intent with quantification language should stay as DE."""
        query = "Quantify and compare gene expression between groups."
        contract = {"must_include_capabilities": ["differential_analysis"]}
        result = infer_analysis_type(query, contract)
        assert result == "rna_seq_differential_expression"

    def test_metagenomics_with_assembly_language(self) -> None:
        """Assembly + classification should be metagenomics, not pure assembly."""
        query = "Assemble and taxonomically classify paired-end metagenomics reads."
        contract = {"must_include_capabilities": ["metagenomics_profiling"]}
        result = infer_analysis_type(query, contract)
        assert result == "metagenomics_classification"


# ---------------------------------------------------------------------------
# Conflicting signals — contract vs. keywords disagree
# ---------------------------------------------------------------------------

class TestConflictingSignals:
    """Contract capabilities don't match query keywords."""

    def test_contract_says_variant_calling_query_says_de(self) -> None:
        """When contract says variant_calling but query says DE, contract wins."""
        query = "Compare gene expression between control and treated."
        contract = {"must_include_capabilities": ["variant_calling"]}
        result = infer_analysis_type(query, contract)
        # Contract-driven: variant_calling cap takes precedence
        # The query doesn't have evolution/germline keywords so it falls to
        # generic variant_calling
        assert "variant" in result

    def test_empty_contract_falls_to_keywords(self) -> None:
        """With no contract capabilities, keyword detection must work."""
        query = "Find shared mutations in evolved bacterial isolates relative to the ancestor."
        result = infer_analysis_type(query, {})
        assert result == "bacterial_evolution_variant_calling"

    def test_none_contract_falls_to_keywords(self) -> None:
        """None contract should not crash."""
        query = "Run differential expression on RNA-seq."
        result = infer_analysis_type(query, None)
        assert result  # should return something non-empty


# ---------------------------------------------------------------------------
# Minimal / underspecified prompts
# ---------------------------------------------------------------------------

class TestMinimalPrompts:
    """Very short or vague prompts that stress the inference system."""

    def test_single_word_de(self) -> None:
        contract = {"must_include_capabilities": ["differential_analysis"]}
        result = infer_analysis_type("DE", contract)
        assert result == "rna_seq_differential_expression"

    def test_single_word_variants(self) -> None:
        contract = {"must_include_capabilities": ["variant_calling"]}
        result = infer_analysis_type("variants", contract)
        # Should resolve to some variant calling type
        assert "variant" in result

    def test_completely_empty_query(self) -> None:
        result = infer_analysis_type("", {})
        assert isinstance(result, str)  # should not crash

    def test_gibberish_query(self) -> None:
        result = infer_analysis_type("asdfghjkl zxcvbnm qwerty", {})
        assert isinstance(result, str)  # should not crash

    def test_no_matching_capability(self) -> None:
        result = infer_analysis_type(
            "Do something with my data.",
            {"must_include_capabilities": ["nonexistent_capability"]},
        )
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Edge cases in contract inference
# ---------------------------------------------------------------------------

class TestContractInferenceEdgeCases:
    """Edge cases for the contract inference pipeline."""

    def test_clinvar_annotation_route(self) -> None:
        """ClinVar keywords should trigger annotation, not generic VC."""
        query = "Filter variants against ClinVar for clinical significance."
        contract = {"must_include_capabilities": ["variant_calling"]}
        result = infer_analysis_type(query, contract)
        assert result == "variant_annotation"

    def test_cystic_fibrosis_annotation_route(self) -> None:
        """Cystic fibrosis keywords should go through variant annotation path."""
        query = "Identify the CFTR causal variant in affected siblings with recessive inheritance."
        # Even without explicit variant_calling cap, keyword detection should work
        result = infer_analysis_type(query, {})
        # Should route to variant_annotation via keyword detection
        assert "variant" in result

    def test_report_requires_run_context(self) -> None:
        """Report generation needs both report AND run-context keywords."""
        # Just "multiqc" without run context should NOT route to reporting
        query = "Run multiqc on these files."
        result = infer_analysis_type(query, {})
        assert result != "run_reporting"

    def test_smoke_test_detection(self) -> None:
        """Direct skill smoke test phrases should always win."""
        query = "Run a direct one-step skill smoke test with bwa_mem_align."
        contract = {"must_include_capabilities": ["alignment"]}
        result = infer_analysis_type(query, contract)
        assert result == "direct_skill_smoke"

    def test_phylogenetics_keyword_detection(self) -> None:
        """Phylogenetics should be detected from keywords without contract."""
        query = "Infer a phylogenetic tree from these protein sequences."
        result = infer_analysis_type(query, {})
        assert result == "phylogenetics"
