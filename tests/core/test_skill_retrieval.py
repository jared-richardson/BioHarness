from __future__ import annotations

from dataclasses import replace

from bio_harness.core.skill_retrieval import (
    build_skill_retrieval_record,
    search_skill_records,
)
from bio_harness.core.tool_cards import tool_card_from_draft


def test_search_skill_records_ranks_relevant_rna_quant_skill() -> None:
    quant = build_skill_retrieval_record(
        {
            "name": "salmon_quant",
            "description": "Quantify transcript abundance from RNA-seq reads.",
            "tools_required": ["salmon"],
            "capabilities": ["transcript_quantification"],
            "analysis_categories": ["rna_seq_quantification"],
            "when_to_use": "Use for transcript-level RNA-seq quantification.",
            "parameters": {"reads_1": {"type": "path"}},
            "file_path": "salmon_quant.md",
        }
    )
    variant = build_skill_retrieval_record(
        {
            "name": "freebayes_call",
            "description": "Call small variants from aligned reads.",
            "tools_required": ["freebayes"],
            "capabilities": ["variant_calling"],
            "analysis_categories": ["germline_variant_calling"],
            "when_to_use": "Use for germline variant calling.",
            "parameters": {"input_bam": {"type": "path"}},
            "file_path": "freebayes_call.md",
        }
    )

    matches = search_skill_records(
        "quantify transcripts from RNA seq reads",
        (quant, variant),
        limit=2,
    )

    assert matches[0].name == "salmon_quant"
    assert matches[0].score > matches[1].score
    assert {"quantify", "rna", "seq"} <= set(matches[0].matched_terms)


def test_tool_card_outputs_boost_retrieval_for_output_named_query() -> None:
    base_card = tool_card_from_draft(
        {
            "skill_name": "stringtie_quant",
            "description": "Assemble and quantify transcripts from aligned reads.",
            "tools_required": ["stringtie"],
            "capabilities": ["transcript_quantification"],
            "parameters": {
                "input_bam": {"type": "path", "required": True},
                "annotation_gtf": {"type": "path", "required": True},
            },
            "command_template": "stringtie {input_bam} -G {annotation_gtf} -o {output_gtf}",
        }
    )
    enriched_card = replace(
        base_card,
        canonical_outputs=("assembled.gtf", "gene_abundances.tsv"),
    )
    enriched = build_skill_retrieval_record(
        {
            "name": "stringtie_quant",
            "description": "Transcript assembly and quantification.",
            "tools_required": ["stringtie"],
            "capabilities": ["transcript_quantification"],
            "analysis_categories": ["rna_seq_quantification"],
            "file_path": "stringtie_quant.md",
        },
        tool_card=enriched_card,
    )
    plain = build_skill_retrieval_record(
        {
            "name": "featurecounts_run",
            "description": "Gene-level read counting from BAM files.",
            "tools_required": ["featureCounts"],
            "capabilities": ["read_counting"],
            "analysis_categories": ["gene_counting"],
            "file_path": "featurecounts_run.md",
        }
    )

    matches = search_skill_records(
        "gene abundance table assembled gtf",
        (plain, enriched),
        limit=2,
    )

    assert matches[0].name == "stringtie_quant"
    assert "gtf" in matches[0].matched_terms


def test_canonical_output_filenames_boost_retrieval_without_tool_card() -> None:
    stringtie = build_skill_retrieval_record(
        {
            "name": "stringtie_quant",
            "description": "Transcript assembly and quantification.",
            "tools_required": ["stringtie"],
            "capabilities": ["transcript_quantification"],
            "analysis_categories": ["rna_seq_quantification"],
            "canonical_output_filenames": {
                "output_gtf": "assembled.gtf",
                "gene_abundance_tsv": "gene_abundances.tsv",
            },
            "file_path": "stringtie_quant.md",
        }
    )
    counting = build_skill_retrieval_record(
        {
            "name": "featurecounts_run",
            "description": "Gene-level read counting from BAM files.",
            "tools_required": ["featureCounts"],
            "capabilities": ["read_counting"],
            "analysis_categories": ["gene_counting"],
            "file_path": "featurecounts_run.md",
        }
    )

    matches = search_skill_records(
        "assembled gtf gene abundances table",
        (counting, stringtie),
        limit=2,
    )

    assert matches[0].name == "stringtie_quant"
    assert "assembled" in matches[0].matched_terms
