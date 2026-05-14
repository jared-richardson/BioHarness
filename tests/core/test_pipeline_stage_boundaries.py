"""Pipeline stage boundary tests.

These tests verify that each stage of the pipeline (contract inference ->
plan generation -> validation -> normalization -> execution) produces
outputs conforming to the expectations of the next stage.

The fixtures represent synthetic but structurally realistic intermediate
artifacts.  When real benchmark artifacts are available, they can be dropped
into ``tests/fixtures/pipeline_traces/`` and loaded here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bio_harness.core.analysis_spec import (
    deterministic_analysis_spec,
    infer_analysis_type,
)
from bio_harness.core.analysis_spec_seed import _PROFILE_BUILDERS
from bio_harness.core.analysis_spec_support import CANONICAL_ANALYSIS_TYPES
from bio_harness.core.plan_validation import validate_plan


# ---------------------------------------------------------------------------
# Fixtures: synthetic pipeline stage artifacts
# ---------------------------------------------------------------------------

_EVOLUTION_CONTRACT = {
    "must_include_capabilities": ["alignment", "variant_calling", "reference_inputs"],
}
_EVOLUTION_QUERY = (
    "Identify shared variants in evolved E. coli isolates relative to "
    "an ancestor using paired-end reads."
)
_EVOLUTION_SKILLS = [
    "spades_assemble", "bwa_mem_align", "freebayes_call",
    "snpeff_annotate", "prodigal_annotate", "bash_run",
]

_DE_CONTRACT = {
    "must_include_capabilities": ["differential_analysis", "quantification"],
}
_DE_QUERY = (
    "Run differential expression analysis on paired-end RNA-seq reads "
    "with a reference genome and annotation GTF."
)
_DE_SKILLS = [
    "star_align", "featurecounts_run", "deseq2_run",
    "salmon_quant", "bash_run",
]

_SC_CONTRACT = {
    "must_include_capabilities": ["single_cell_analysis"],
}
_SC_QUERY = "Cluster single-cell RNA-seq 10x Chromium data and find marker genes."
_SC_SKILLS = ["sc_count_and_cluster", "scanpy_workflow", "bash_run"]


# ---------------------------------------------------------------------------
# Stage 1 -> Stage 2: Contract inference -> Analysis spec
# ---------------------------------------------------------------------------

class TestContractToAnalysisSpec:
    """Verify that contract inference produces specs consumed by planning."""

    def test_evolution_contract_yields_spec_with_plan_skeleton(self) -> None:
        analysis_type = infer_analysis_type(_EVOLUTION_QUERY, _EVOLUTION_CONTRACT)
        assert analysis_type == "bacterial_evolution_variant_calling"
        spec = deterministic_analysis_spec(
            _EVOLUTION_QUERY,
            contract=_EVOLUTION_CONTRACT,
            available_skill_names=_EVOLUTION_SKILLS,
        )
        assert "plan_skeleton" in spec
        assert len(spec["plan_skeleton"]) > 0
        assert spec["biological_objective"]
        assert spec["acceptance_checks"]

    def test_de_contract_yields_spec_with_chosen_method(self) -> None:
        analysis_type = infer_analysis_type(_DE_QUERY, _DE_CONTRACT)
        assert analysis_type == "rna_seq_differential_expression"
        spec = deterministic_analysis_spec(
            _DE_QUERY,
            contract=_DE_CONTRACT,
            available_skill_names=_DE_SKILLS,
        )
        assert spec["chosen_method"]  # non-empty
        assert "deseq2_run" in spec["chosen_method"]

    def test_sc_contract_yields_spec_with_parameter_profile(self) -> None:
        analysis_type = infer_analysis_type(_SC_QUERY, _SC_CONTRACT)
        assert analysis_type == "single_cell_rna_seq"
        spec = deterministic_analysis_spec(
            _SC_QUERY,
            contract=_SC_CONTRACT,
            available_skill_names=_SC_SKILLS,
        )
        assert spec["parameter_profile"]
        # Parameter profile should include tool-specific settings
        tool_names = [p["tool_name"] for p in spec["parameter_profile"]]
        assert "sc_count_and_cluster" in tool_names


# ---------------------------------------------------------------------------
# Stage 2 -> Stage 3: Spec + skeleton -> Validatable plan
# ---------------------------------------------------------------------------

class TestSpecToPlanValidation:
    """Verify that plan skeletons from specs pass plan validation."""

    @pytest.mark.parametrize("analysis_type", sorted(
        at for at in CANONICAL_ANALYSIS_TYPES
        if at in _PROFILE_BUILDERS
    ))
    def test_skeleton_produces_validatable_plan(self, analysis_type: str) -> None:
        """Each profile builder's plan_skeleton should pass validation."""
        dummy_skills = {
            "bash_run", "bwa_mem_align", "freebayes_call", "deseq2_run",
            "salmon_quant", "spades_assemble", "snpeff_annotate",
            "sc_count_and_cluster", "gatk_haplotypecaller", "fastp_run",
            "prodigal_annotate", "featurecounts_run", "star_align",
            "sniffles_sv_call", "minimap2_align", "scanpy_workflow",
            "kallisto_quant", "multiqc_report", "quarto_report",
            "artifact_schema_profile", "fastqc_run",
        }
        builder = _PROFILE_BUILDERS[analysis_type]
        seed = builder("test query", dummy_skills, sorted(dummy_skills))

        skeleton = seed.get("plan_skeleton", [])
        if not skeleton:
            pytest.skip(f"No plan_skeleton for {analysis_type}")

        # Convert skeleton tuples to plan dict format
        steps = []
        for i, entry in enumerate(skeleton):
            tool_name = entry[0] if len(entry) > 0 else ""
            description = entry[1] if len(entry) > 1 else ""
            args = entry[2] if len(entry) > 2 else {}
            steps.append({
                "step_id": i + 1,
                "tool_name": tool_name,
                "arguments": args if isinstance(args, dict) else {},
                "description": description,
            })

        plan = {"plan": steps}
        result = validate_plan(plan, known_skill_names=dummy_skills)
        # Should not have errors (warnings are OK)
        assert result.passed, (
            f"Plan skeleton for {analysis_type} failed validation: "
            + "; ".join(e.message for e in result.errors)
        )


# ---------------------------------------------------------------------------
# Stage 3 -> Stage 4: Plan validation -> Protocol normalization readiness
# ---------------------------------------------------------------------------

class TestPlanValidationToNormalization:
    """Verify validated plans have the structure normalization expects."""

    def test_valid_plan_has_steps_with_tool_name_key(self) -> None:
        """Normalization requires each step to have 'tool_name' not 'tool'."""
        plan = {
            "plan": [
                {"step_id": 1, "tool_name": "star_align", "arguments": {}},
                {"step_id": 2, "tool_name": "featurecounts_run", "arguments": {}},
                {"step_id": 3, "tool_name": "deseq2_run", "arguments": {}},
            ]
        }
        result = validate_plan(plan, known_skill_names={"star_align", "featurecounts_run", "deseq2_run"})
        assert result.passed

        # Verify all steps have tool_name (not tool)
        for step in plan["plan"]:
            assert "tool_name" in step
            assert step["tool_name"]  # non-empty

    def test_duplicate_step_ids_flagged_before_normalization(self) -> None:
        plan = {
            "plan": [
                {"step_id": 1, "tool_name": "star_align", "arguments": {}},
                {"step_id": 1, "tool_name": "featurecounts_run", "arguments": {}},
            ]
        }
        result = validate_plan(plan)
        # Duplicate step IDs should produce at least a warning-level finding
        dup_findings = [f for f in result.findings if f.code == "DUPLICATE_STEP_ID"]
        assert dup_findings, "Expected a finding about duplicate step IDs"


# ---------------------------------------------------------------------------
# Cross-stage: Full pipeline trace (synthetic)
# ---------------------------------------------------------------------------

class TestFullPipelineTrace:
    """End-to-end trace from contract to validated plan for key pipelines."""

    def _run_pipeline_trace(
        self,
        query: str,
        contract: dict[str, Any],
        skills: list[str],
        expected_type: str,
    ) -> None:
        # Stage 1: Contract inference
        analysis_type = infer_analysis_type(query, contract)
        assert analysis_type == expected_type

        # Stage 2: Analysis spec with profile seed
        spec = deterministic_analysis_spec(
            query,
            contract=contract,
            available_skill_names=skills,
        )
        assert spec["biological_objective"]

        # Stage 3: Build plan from skeleton and validate
        skeleton = spec.get("plan_skeleton", [])
        if skeleton:
            steps = []
            for i, entry in enumerate(skeleton):
                tool_name = entry[0] if len(entry) > 0 else ""
                args = entry[2] if len(entry) > 2 else {}
                steps.append({
                    "step_id": i + 1,
                    "tool_name": tool_name,
                    "arguments": args if isinstance(args, dict) else {},
                })
            plan = {"plan": steps}
            result = validate_plan(plan, known_skill_names=set(skills))
            assert result.passed, (
                f"Trace failed at validation for {expected_type}: "
                + "; ".join(e.message for e in result.errors)
            )

    def test_evolution_trace(self) -> None:
        self._run_pipeline_trace(
            _EVOLUTION_QUERY, _EVOLUTION_CONTRACT,
            _EVOLUTION_SKILLS, "bacterial_evolution_variant_calling",
        )

    def test_de_trace(self) -> None:
        self._run_pipeline_trace(
            _DE_QUERY, _DE_CONTRACT,
            _DE_SKILLS, "rna_seq_differential_expression",
        )

    def test_sc_trace(self) -> None:
        self._run_pipeline_trace(
            _SC_QUERY, _SC_CONTRACT,
            _SC_SKILLS, "single_cell_rna_seq",
        )

    def test_germline_vc_trace(self) -> None:
        self._run_pipeline_trace(
            "Call germline variants from paired-end reads with a GIAB truth set.",
            {"must_include_capabilities": ["variant_calling"]},
            ["bwa_mem_align", "gatk_haplotypecaller", "bash_run"],
            "germline_variant_calling",
        )

    def test_transcript_quant_trace(self) -> None:
        self._run_pipeline_trace(
            "Quantify transcript abundance from paired-end RNA-seq with salmon.",
            {"must_include_capabilities": ["quantification"]},
            ["salmon_quant", "kallisto_quant", "bash_run"],
            "transcript_quantification",
        )

    def test_metagenomics_trace(self) -> None:
        self._run_pipeline_trace(
            "Assemble and classify paired-end metagenomics reads.",
            {"must_include_capabilities": ["alignment"]},
            ["spades_assemble", "bash_run", "fastqc_run"],
            "metagenomics_classification",
        )

    def test_phylogenetics_trace(self) -> None:
        self._run_pipeline_trace(
            "Infer a phylogenetic tree from protein homologs in a multi-FASTA file.",
            {"must_include_capabilities": []},
            ["bash_run"],
            "phylogenetics",
        )
