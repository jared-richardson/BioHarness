from __future__ import annotations

from pathlib import Path

from bio_harness.agents.orchestrator_skill_retrieval import (
    planner_skill_retrieval_boosts,
    planner_skill_retrieval_profile,
)
from bio_harness.core.tool_cards import tool_card_from_draft, write_tool_card


def test_planner_skill_retrieval_profile_uses_compact_settings_for_small_models() -> None:
    profile = planner_skill_retrieval_profile("gemma4:26b", budget=4)

    assert profile.name == "compact_model"
    assert profile.boost_weight == 24
    assert profile.protected_top_k == 3


def test_planner_skill_retrieval_boosts_output_shaped_queries() -> None:
    boosts, protected, meta = planner_skill_retrieval_boosts(
        "write assembled gtf and gene abundances table from aligned rna bam",
        [
            {
                "name": "stringtie_quant",
                "description": "Assemble and quantify transcripts from aligned RNA-seq BAM files.",
                "tools_required": ["stringtie"],
                "capabilities": ["quantification"],
                "analysis_categories": ["transcript_quantification"],
                "canonical_output_filenames": {
                    "output_gtf": "assembled.gtf",
                    "gene_abundance_tsv": "gene_abundances.tsv",
                },
                "parameters": {"input_bam": {"type": "path"}},
            },
            {
                "name": "featurecounts_run",
                "description": "Generate a gene count matrix from aligned reads.",
                "tools_required": ["featureCounts"],
                "capabilities": ["quantification"],
                "analysis_categories": ["gene_counting"],
                "parameters": {"input_bams": {"type": "path"}},
            },
        ],
        model_name="gemma4:26b",
        budget=4,
    )

    assert boosts["stringtie_quant"] > boosts["featurecounts_run"]
    assert "stringtie_quant" in protected
    assert meta["retrieval_profile"] == "compact_model"
    assert meta["retrieval_selected_skill_names"][0] == "stringtie_quant"


def test_planner_skill_retrieval_boosts_can_use_tool_cards(tmp_path: Path) -> None:
    cards_dir = tmp_path / "cards"
    write_tool_card(
        tool_card_from_draft(
            {
                "skill_name": "novel_taxonomic_profile",
                "description": "Taxonomic profiling with a novel wrapper.",
                "tools_required": ["novel_profiler"],
                "capabilities": ["taxonomic_profiling"],
                "parameters": {"reads_fastq": {"type": "path", "required": True}},
                "output_types": ["profiling.tsv"],
                "command_template": "novel_profiler {reads_fastq} -o {output_tsv}",
                "when_to_use": "Use for metagenomic profiling that writes profiling.tsv.",
            }
        ),
        tool_cards_dir=cards_dir,
    )

    boosts, protected, meta = planner_skill_retrieval_boosts(
        "write profiling tsv for metagenomic taxonomic profiling",
        [
            {
                "name": "novel_taxonomic_profile",
                "description": "A generic profile wrapper.",
                "tools_required": ["novel_profiler"],
                "capabilities": ["profiling"],
                "analysis_categories": ["general"],
                "parameters": {"reads_fastq": {"type": "path"}},
            },
            {
                "name": "featurecounts_run",
                "description": "Gene counting from aligned reads.",
                "tools_required": ["featureCounts"],
                "capabilities": ["gene_counting"],
                "analysis_categories": ["gene_counting"],
                "parameters": {"input_bams": {"type": "path"}},
            },
        ],
        model_name="gemma4:26b",
        budget=4,
        tool_cards_dir=cards_dir,
    )

    assert boosts["novel_taxonomic_profile"] > boosts.get("featurecounts_run", 0)
    assert "novel_taxonomic_profile" in protected
    assert meta["tool_cards_dir"] == str(cards_dir.resolve())
