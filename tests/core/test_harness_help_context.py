from __future__ import annotations

from bio_harness.core.harness_help_context import (
    build_harness_help_context,
    build_harness_help_payload,
    looks_like_harness_help_query,
)


def _sample_skills() -> dict[str, dict[str, object]]:
    return {
        "bash_run": {
            "name": "bash_run",
            "analysis_categories": ["general"],
            "capabilities": ["general"],
            "tools_required": ["bash"],
        },
        "star_align": {
            "name": "star_align",
            "analysis_categories": ["alignment"],
            "capabilities": ["alignment"],
            "tools_required": ["star"],
        },
        "salmon_quant": {
            "name": "salmon_quant",
            "analysis_categories": ["quantification"],
            "capabilities": ["quantification"],
            "tools_required": ["salmon"],
        },
        "sniffles_sv_call": {
            "name": "sniffles_sv_call",
            "analysis_categories": ["variant_calling"],
            "capabilities": ["structural_variation"],
            "tools_required": ["sniffles"],
        },
    }


def _sample_capability_catalog() -> dict[str, object]:
    return {
        "capabilities": [
            {"id": "alignment", "name": "Read Alignment", "description": "Alignment workflows.", "enabled": True},
            {
                "id": "quantification",
                "name": "Expression Quantification",
                "description": "Transcript quantification workflows.",
                "enabled": True,
            },
            {
                "id": "structural_variation",
                "name": "Structural Variation",
                "description": "Structural variant calling workflows.",
                "enabled": True,
            },
        ]
    }


def _sample_scientific_tool_catalog() -> dict[str, object]:
    return {
        "tools": [
            {"name": "star_align", "family": "alignment", "support_tier": "wrapped"},
            {"name": "salmon_quant", "family": "quantification", "support_tier": "wrapped"},
            {"name": "sniffles_sv_call", "family": "variant_calling", "support_tier": "wrapped"},
            {"name": "samtools", "family": "alignment_qc", "support_tier": "catalog_only"},
            {"name": "trusted_download", "family": "general", "support_tier": "helper_script"},
        ]
    }


def test_build_harness_help_payload_groups_categories_and_tool_families() -> None:
    payload = build_harness_help_payload(
        _sample_skills(),
        capability_catalog=_sample_capability_catalog(),
        scientific_tool_catalog=_sample_scientific_tool_catalog(),
    )

    assert payload["summary"]["wrapped_skills"] == 3
    assert payload["summary"]["analysis_categories"] == 3
    assert payload["summary"]["capability_ids"] == 3
    assert payload["support_tiers"] == {"wrapped": 3, "helper_script": 1, "catalog_only": 1}
    assert [row["category"] for row in payload["capability_categories"]] == [
        "Alignment",
        "Quantification",
        "Variant Calling",
    ]
    assert [row["family"] for row in payload["wrapped_tool_families"]] == [
        "Alignment",
        "Quantification",
        "Variant Calling",
    ]
    assert payload["user_entrypoints"]["setup_llm_backend"] == "python3 scripts/setup_llm_backend.py --help"
    assert payload["user_entrypoints"]["bootstrap_bioharness"] == "python3 scripts/bootstrap_bioharness.py"


def test_build_harness_help_context_mentions_user_entrypoints_and_extension_steps() -> None:
    text = build_harness_help_context(
        _sample_skills(),
        capability_catalog=_sample_capability_catalog(),
        scientific_tool_catalog=_sample_scientific_tool_catalog(),
    )

    assert "## Bio-Harness User Help" in text
    assert "local Bio-Harness repository in this workspace" in text
    assert "## Capability Categories" in text
    assert "## Wrapped Program Families" in text
    assert "`python3 scripts/bootstrap_bioharness.py`" in text
    assert "`python3 scripts/setup_llm_backend.py --help`" in text
    assert "`python3 scripts/doctor_bioharness.py --probe-llm-backend`" in text
    assert "`python3 scripts/stage_inputs.py --help`" in text
    assert "`python3 scripts/trusted_download.py --help`" in text
    assert "`python3 scripts/show_harness_help.py --help`" in text
    assert "bio_harness/skills/definitions/<skill>.md" in text
    assert "python3 scripts/upsert_scientific_tool.py --help" in text
    assert "Describe wrapped skills and wrapped tool families first" in text


def test_build_harness_help_context_can_focus_to_relevant_skill_slice() -> None:
    text = build_harness_help_context(
        _sample_skills(),
        capability_catalog=_sample_capability_catalog(),
        scientific_tool_catalog=_sample_scientific_tool_catalog(),
        retrieval_query="Which Bio-Harness skill should I use for STAR alignment?",
        compact=True,
    )

    assert "Focused help slice for this question: star_align" in text
    assert "Alignment (1 wrapped skills): star_align" in text
    assert "salmon_quant" not in text
    assert "sniffles_sv_call" not in text


def test_looks_like_harness_help_query_detects_help_intents() -> None:
    assert looks_like_harness_help_query("What capabilities does Bio-Harness have by category?")
    assert looks_like_harness_help_query("What wrapped programs or tool families can Bio-Harness run directly today?")
    assert looks_like_harness_help_query("How do I create a new skill and add a capability?")
    assert looks_like_harness_help_query("How do I add local input files and download a trusted manual into the workspace?")
    assert looks_like_harness_help_query("How do I stage inputs and download a manual into the workspace?")
    assert looks_like_harness_help_query("How do I set up Ollama and pull the right model?")
    assert looks_like_harness_help_query("What should I do on the first run to get the model backend ready?")
    assert not looks_like_harness_help_query("Run salmon quantification on paired-end FASTQ files.")
