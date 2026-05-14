"""Tests for planner-time literature assistance policy."""

from __future__ import annotations

from bio_harness.core.benchmark_policy import BIOAGENTBENCH_PLANNING_STRICT_POLICY
from bio_harness.core.literature_planning_policy import (
    decide_literature_planning_support,
    tool_candidates_from_analysis_spec,
)


def test_tool_candidates_from_analysis_spec_prefers_distinct_preferred_and_chosen_tools() -> None:
    tools = tool_candidates_from_analysis_spec(
        {
            "preferred_tools": ["minimap2", "nanopolish"],
            "chosen_method": "minimap2 + medaka",
        }
    )

    assert tools == ("minimap2", "nanopolish", "medaka")


def test_decide_literature_planning_support_blocks_blind_benchmark_policy() -> None:
    decision = decide_literature_planning_support(
        "What minimap2 preset is recommended in published methods?",
        {"preferred_tools": ["minimap2"]},
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    )

    assert not decision.allowed
    assert decision.trigger_reason == "blind_benchmark_policy"


def test_decide_literature_planning_support_detects_parameter_recommendation() -> None:
    decision = decide_literature_planning_support(
        "What minimap2 preset is recommended in published methods?",
        {"preferred_tools": ["minimap2"]},
        benchmark_policy="scientific_harness",
    )

    assert decision.allowed
    assert decision.query_class == "parameter_recommendation"
    assert decision.parameter_name == "preset"
    assert decision.tool_name == "minimap2"


def test_decide_literature_planning_support_detects_protocol_choice() -> None:
    decision = decide_literature_planning_support(
        "Use published methods to choose the best workflow for ATAC-seq preprocessing.",
        {"analysis_type": "atac_seq", "preferred_tools": ["macs2", "atacseqqc"]},
        benchmark_policy="scientific_harness",
    )

    assert decision.allowed
    assert decision.query_class == "protocol_choice"
    assert decision.trigger_reason == "explicit_best_practice_request"


def test_decide_literature_planning_support_ignores_trigger_terms_inside_paths() -> None:
    decision = decide_literature_planning_support(
        (
            "Call germline variants from FASTQs and write outputs to "
            "/tmp/planner_literature/variant_calling/selected/variants.vcf."
        ),
        {"analysis_type": "variant_calling", "preferred_tools": ["gatk_haplotypecaller"]},
        benchmark_policy="scientific_harness",
    )

    assert not decision.allowed
    assert decision.trigger_reason == "no_literature_trigger"


def test_decide_literature_planning_support_does_not_fire_on_plain_workflow_word() -> None:
    decision = decide_literature_planning_support(
        "Run this workflow on the provided ATAC-seq counts and write results to output.",
        {"analysis_type": "atac_seq", "preferred_tools": ["atacseqqc"]},
        benchmark_policy="scientific_harness",
    )

    assert not decision.allowed
    assert decision.trigger_reason == "no_literature_trigger"


def test_decide_literature_planning_support_does_not_treat_moderate_as_mode() -> None:
    decision = decide_literature_planning_support(
        (
            "Identify and annotate genome variants in two evolved lines relative to an ancestor line "
            "of E. coli; report only variants shared by both evolved lines with moderate or higher "
            "predicted severity."
        ),
        {
            "analysis_type": "bacterial_evolution_variant_calling",
            "preferred_tools": ["spades_assemble", "freebayes_call", "snpeff_annotate"],
        },
        benchmark_policy="scientific_harness",
    )

    assert not decision.allowed
    assert decision.trigger_reason == "no_literature_trigger"


def test_decide_literature_planning_support_requires_explicit_parameter_request_framing() -> None:
    decision = decide_literature_planning_support(
        "Run minimap2 with the splice preset on these direct RNA reads.",
        {"analysis_type": "long_read_rna", "preferred_tools": ["minimap2"]},
        benchmark_policy="scientific_harness",
    )

    assert not decision.allowed
    assert decision.trigger_reason == "no_literature_trigger"
