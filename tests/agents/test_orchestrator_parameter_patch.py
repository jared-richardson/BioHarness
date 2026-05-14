from __future__ import annotations

from bio_harness.agents.orchestrator_parameter_patch import post_plan_parameter_patch


def test_post_plan_parameter_patch_adds_contextual_defaults() -> None:
    plan = {
        "plan": [
            {"tool_name": "freebayes_call", "arguments": {}},
            {"tool_name": "star_align", "arguments": {}},
            {"tool_name": "featurecounts_run", "arguments": {}},
        ]
    }

    patched = post_plan_parameter_patch(
        plan,
        {"analysis_type": "paired_rna_seq_differential_expression_bacterial_variant"},
    )

    assert patched["plan"][0]["arguments"]["ploidy"] == 1
    assert patched["plan"][1]["arguments"]["twopassMode"] == "Basic"
    assert patched["plan"][2]["arguments"]["is_paired_end"] is True
    assert patched["plan"][2]["arguments"]["count_read_pairs"] is True


def test_post_plan_parameter_patch_preserves_existing_arguments() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "salmon_quant",
                "arguments": {"validateMappings": False},
            }
        ]
    }

    patched = post_plan_parameter_patch(
        plan,
        {"analysis_type": "transcript_quantification"},
    )

    assert patched["plan"][0]["arguments"]["validateMappings"] is False
    assert patched["plan"][0]["arguments"]["library_type"] == "A"


def test_post_plan_parameter_patch_ignores_non_matching_contexts() -> None:
    plan = {"plan": [{"tool_name": "spades_assemble", "arguments": {}}]}

    patched = post_plan_parameter_patch(
        plan,
        {"analysis_type": "single_cell_rna_seq"},
    )

    assert patched["plan"][0]["arguments"] == {}
