from __future__ import annotations

import queue
from types import SimpleNamespace
from pathlib import Path

import bio_harness.agents.orchestrator as orchestrator_mod
from bio_harness.agents.orchestrator import (
    Orchestrator,
    _trim_planner_skill_selection,
)
from bio_harness.core.llm_types import LLMOutputSchema
from bio_harness.core.step_completion import write_completion_manifest
from bio_harness.core.tool_cards import tool_card_from_draft, write_tool_card
from bio_harness.skills.library.shell import bash_run


def _orchestrator_stub() -> Orchestrator:
    return Orchestrator.__new__(Orchestrator)


def test_planner_skill_selection_respects_budget_and_keeps_bash(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3-coder-next:latest"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "2")
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {"name": "star_align", "description": "RNA alignment with STAR.", "parameters": {}},
        {"name": "bcftools_call", "description": "Variant calling with bcftools.", "parameters": {}},
        {"name": "prokka_annotate", "description": "Annotate genomes.", "parameters": {}},
        {"name": "gatk_haplotypecaller", "description": "Call variants with GATK.", "parameters": {}},
        {"name": "freebayes_call", "description": "Variant caller.", "parameters": {}},
        {"name": "featurecounts_run", "description": "Generate count matrix.", "parameters": {}},
        {"name": "deseq2_run", "description": "Differential expression.", "parameters": {}},
    ]
    selected, meta = orchestrator._select_planner_skill_metadata(
        "Please run variant calling with bcftools on this sample.",
        skills,
    )
    names = {str(s.get("name", "")).strip() for s in selected}
    assert len(selected) == 6
    assert "bash_run" in names
    assert "bcftools_call" in names
    assert meta.get("selection_mode") == "query_weighted_subset"


def test_planner_skill_selection_uses_all_when_budget_allows(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3-coder-next:latest"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "20")
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {"name": "star_align", "description": "RNA alignment with STAR.", "parameters": {}},
        {"name": "bcftools_call", "description": "Variant calling with bcftools.", "parameters": {}},
    ]
    selected, meta = orchestrator._select_planner_skill_metadata("run RNA alignment", skills)
    assert len(selected) == len(skills)
    assert meta.get("selection_mode") == "all"


def test_planner_skill_selection_prioritizes_relevant_multianalysis_tools(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3-coder-next:latest"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "8")
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {"name": "subread_align", "description": "Align reads with the Subread suite.", "parameters": {}},
        {"name": "featurecounts_run", "description": "Generate count matrix with featureCounts.", "parameters": {}},
        {"name": "deseq2_run", "description": "Differential expression with DESeq2.", "parameters": {}},
        {"name": "rmats_run", "description": "Run rMATS alternative splicing analysis.", "parameters": {}},
        {"name": "bcftools_call", "description": "Variant calling with bcftools.", "parameters": {}},
        {"name": "gatk_haplotypecaller", "description": "Call variants with GATK.", "parameters": {}},
        {"name": "varscan_call", "description": "Variant calling with VarScan2.", "parameters": {}},
        {"name": "fallback_skill_builder", "description": "Build deterministic fallback coverage.", "parameters": {}},
        {"name": "hmmscan_search", "description": "Protein domain search.", "parameters": {}},
    ]
    selected, _ = orchestrator._select_planner_skill_metadata(
        "Perform differential expression, alternative splicing, and variant calling. Please use subread for alignment.",
        skills,
    )
    names = {str(s.get("name", "")).strip() for s in selected}
    assert "subread_align" in names
    assert "featurecounts_run" in names
    assert "deseq2_run" in names
    assert "rmats_run" in names
    assert "bcftools_call" in names
    assert "gatk_haplotypecaller" in names
    assert "varscan_call" in names
    assert "fallback_skill_builder" not in names


def test_available_skill_metadata_filters_skills_with_missing_tools(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "bash_run": {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}, "tools_required": []},
            "salmon_quant": {"name": "salmon_quant", "description": "Transcript quantification with Salmon.", "parameters": {}, "tools_required": ["salmon"]},
            "kallisto_quant": {"name": "kallisto_quant", "description": "Transcript quantification with kallisto.", "parameters": {}, "tools_required": ["kallisto"]},
        }
    )
    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda tool: tool != "kallisto")

    available = orchestrator._available_skill_metadata()
    names = {str(skill.get("name", "")).strip() for skill in available}

    assert "bash_run" in names
    assert "salmon_quant" in names
    assert "kallisto_quant" not in names


def test_available_skill_metadata_allows_deseq2_run_via_pydeseq2(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "deseq2_run": {
                "name": "deseq2_run",
                "description": "Differential expression with DESeq2.",
                "parameters": {},
                "tools_required": ["rscript", "deseq2"],
            }
        }
    )

    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda tool: tool == "rscript")
    monkeypatch.setattr(orchestrator_mod.importlib.util, "find_spec", lambda name: object() if name == "pydeseq2" else None)

    available = orchestrator._available_skill_metadata()

    assert [skill["name"] for skill in available] == ["deseq2_run"]


def test_available_skill_metadata_includes_launcher_backed_tools(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "prokka_annotate": {
                "name": "prokka_annotate",
                "description": "Annotate prokaryotic assemblies with Prokka.",
                "parameters": {},
                "tools_required": ["prokka"],
            }
        }
    )

    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda tool: tool == "prokka")

    available = orchestrator._available_skill_metadata()

    assert [skill["name"] for skill in available] == ["prokka_annotate"]


def test_planner_skill_selection_prioritizes_experimental_evolution_tools(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3-coder-next:latest"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "8")
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {"name": "fastqc_run", "description": "Quality control for FASTQ files.", "parameters": {}},
        {"name": "star_align", "description": "RNA alignment with STAR.", "parameters": {}},
        {"name": "rmats_run", "description": "Alternative splicing analysis.", "parameters": {}},
        {"name": "spades_assemble", "description": "Assemble short reads into contigs.", "parameters": {}},
        {"name": "bwa_mem_align", "description": "Align reads to a reference genome.", "parameters": {}},
        {"name": "bcftools_call", "description": "Variant calling and VCF operations.", "parameters": {}},
        {"name": "prodigal_annotate", "description": "Predict bacterial genes from assembled contigs.", "parameters": {}},
        {"name": "snpeff_annotate", "description": "Annotate variant impact and effect.", "parameters": {}},
        {"name": "deseq2_run", "description": "Differential expression with DESeq2.", "parameters": {}},
    ]

    selected, _ = orchestrator._select_planner_skill_metadata(
        "Identify and annotate genome variants in two evolved lines relative to an ancestor E. coli line and report shared moderate or higher severity variants.",
        skills,
    )
    names = {str(s.get("name", "")).strip() for s in selected}

    assert "bash_run" in names
    assert "spades_assemble" in names
    assert "bwa_mem_align" in names
    assert "bcftools_call" in names
    assert "prodigal_annotate" in names
    assert "snpeff_annotate" in names
    assert "star_align" not in names
    assert "rmats_run" not in names


def test_planner_skill_selection_respects_analysis_preferences(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3-coder-next:latest"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "4")
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {"name": "bcftools_call", "description": "Variant calling with bcftools.", "parameters": {}},
        {"name": "freebayes_call", "description": "Variant calling with FreeBayes.", "parameters": {}},
        {"name": "gatk_haplotypecaller", "description": "Variant calling with GATK.", "parameters": {}},
        {"name": "spades_assemble", "description": "Assemble short reads.", "parameters": {}},
        {"name": "bwa_mem_align", "description": "Align reads.", "parameters": {}},
        {"name": "prodigal_annotate", "description": "Predict bacterial genes.", "parameters": {}},
        {"name": "snpeff_annotate", "description": "Annotate variant effects.", "parameters": {}},
        {"name": "rmats_run", "description": "Alternative splicing.", "parameters": {}},
        {"name": "deseq2_run", "description": "Differential expression.", "parameters": {}},
    ]
    selected, meta = orchestrator._select_planner_skill_metadata(
        "Call variants in evolved bacterial isolates relative to an ancestor.",
        skills,
        analysis_spec={
            "preferred_tools": ["freebayes_call", "spades_assemble", "bwa_mem_align"],
            "discouraged_tools": ["gatk_haplotypecaller"],
            "chosen_method": "freebayes_call",
        },
    )
    names = [str(s.get("name", "")).strip() for s in selected]
    assert "freebayes_call" in names
    assert "spades_assemble" in names
    assert "bwa_mem_align" in names
    assert "prodigal_annotate" in names
    assert "gatk_haplotypecaller" not in names
    assert "freebayes_call" in meta.get("analysis_preferred_tools", [])


def test_planner_skill_selection_keeps_preferred_wrappers_and_demotes_fallback_siblings(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3-coder-next:latest"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "9")
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {"name": "spades_assemble", "description": "Assemble short reads.", "parameters": {}},
        {"name": "bwa_mem_align", "description": "Align reads.", "parameters": {}},
        {"name": "freebayes_call", "description": "Variant calling with FreeBayes.", "parameters": {}},
        {"name": "bcftools_call", "description": "Variant calling with bcftools.", "parameters": {}},
        {"name": "prokka_annotate", "description": "Annotate bacterial assemblies.", "parameters": {}},
        {"name": "prodigal_annotate", "description": "Predict bacterial genes.", "parameters": {}},
        {"name": "snpeff_annotate", "description": "Annotate variant effects.", "parameters": {}},
        {"name": "bcftools_isec_run", "description": "Atomic bcftools isec wrapper.", "parameters": {}},
        {"name": "bcftools_norm_run", "description": "Atomic bcftools norm wrapper.", "parameters": {}},
        {"name": "shared_variants_export_run", "description": "Atomic shared variant export wrapper.", "parameters": {}},
        {"name": "tabix_index_run", "description": "Atomic tabix indexing wrapper.", "parameters": {}},
    ]

    selected, _ = orchestrator._select_planner_skill_metadata(
        "Identify shared variants in evolved bacterial isolates relative to an ancestor and export the shared variant CSV.",
        skills,
        analysis_spec={
            "preferred_tools": [
                "spades_assemble",
                "bwa_mem_align",
                "freebayes_call",
                "prokka_annotate",
                "snpeff_annotate",
                "bcftools_isec_run",
                "bcftools_norm_run",
                "shared_variants_export_run",
            ],
            "chosen_method": "freebayes_call",
        },
    )

    names = {str(s.get("name", "")).strip() for s in selected}
    assert "freebayes_call" in names
    assert "prokka_annotate" in names
    assert "bcftools_isec_run" in names
    assert "bcftools_norm_run" in names
    assert "shared_variants_export_run" in names
    assert "bcftools_call" not in names
    assert "prodigal_annotate" not in names


def test_trim_planner_skill_selection_keeps_late_preferred_tools() -> None:
    scored_rows = [
        (100, "core_prepare", {"name": "core_prepare"}),
        (96, "core_align", {"name": "core_align"}),
        (92, "core_call", {"name": "core_call"}),
        (88, "legacy_annotation", {"name": "legacy_annotation"}),
        (84, "shared_export", {"name": "shared_export"}),
        (80, "bash_run", {"name": "bash_run"}),
        (76, "legacy_helper", {"name": "legacy_helper"}),
        (60, "normalize_step", {"name": "normalize_step"}),
        (56, "intersect_step", {"name": "intersect_step"}),
        (52, "index_step", {"name": "index_step"}),
    ]
    selected = [
        {"name": "core_prepare"},
        {"name": "core_align"},
        {"name": "core_call"},
        {"name": "legacy_annotation"},
        {"name": "shared_export"},
        {"name": "bash_run"},
        {"name": "legacy_helper"},
        {"name": "normalize_step"},
        {"name": "intersect_step"},
        {"name": "index_step"},
    ]

    trimmed = _trim_planner_skill_selection(
        selected,
        scored_rows=scored_rows,
        budget=8,
        protected_names={
            "bash_run",
            "normalize_step",
            "intersect_step",
            "index_step",
            "core_prepare",
            "core_align",
            "core_call",
            "shared_export",
            "legacy_annotation",
        },
        preferred_tool_order=[
            "normalize_step",
            "intersect_step",
            "index_step",
            "core_prepare",
            "core_align",
            "core_call",
            "shared_export",
            "legacy_annotation",
        ],
        retrieval_protected={"normalize_step", "shared_export"},
        stage_aware_names={"normalize_step", "intersect_step", "index_step", "shared_export"},
    )

    names = [str(item.get("name", "")).strip() for item in trimmed]
    assert len(names) == 8
    assert "normalize_step" in names
    assert "intersect_step" in names
    assert "index_step" in names
    assert "legacy_helper" not in names
    assert "bash_run" not in names


def test_trim_planner_skill_selection_preserves_atomic_wrappers_from_turn7_shape() -> None:
    selected = [
        {"name": "freebayes_call"},
        {"name": "spades_assemble"},
        {"name": "snpeff_annotate"},
        {"name": "prokka_annotate"},
        {"name": "bash_run"},
        {"name": "bwa_mem_align"},
        {"name": "shared_variants_export_run"},
        {"name": "prodigal_annotate"},
        {"name": "bcftools_isec_run"},
        {"name": "bcftools_norm_run"},
        {"name": "tabix_index_run"},
    ]
    scored_rows = [
        (110, "freebayes_call", {"name": "freebayes_call"}),
        (108, "spades_assemble", {"name": "spades_assemble"}),
        (106, "snpeff_annotate", {"name": "snpeff_annotate"}),
        (104, "prokka_annotate", {"name": "prokka_annotate"}),
        (102, "bash_run", {"name": "bash_run"}),
        (100, "bwa_mem_align", {"name": "bwa_mem_align"}),
        (98, "shared_variants_export_run", {"name": "shared_variants_export_run"}),
        (96, "prodigal_annotate", {"name": "prodigal_annotate"}),
        (94, "bcftools_isec_run", {"name": "bcftools_isec_run"}),
        (92, "bcftools_norm_run", {"name": "bcftools_norm_run"}),
        (90, "tabix_index_run", {"name": "tabix_index_run"}),
    ]

    trimmed = _trim_planner_skill_selection(
        selected,
        scored_rows=scored_rows,
        budget=8,
        protected_names={
            "bash_run",
            "bcftools_isec_run",
            "bcftools_norm_run",
            "bwa_mem_align",
            "freebayes_call",
            "prodigal_annotate",
            "prokka_annotate",
            "shared_variants_export_run",
            "snpeff_annotate",
            "spades_assemble",
            "tabix_index_run",
        },
        preferred_tool_order=[
            "bcftools_isec_run",
            "bcftools_norm_run",
            "bwa_mem_align",
            "freebayes_call",
            "prodigal_annotate",
            "prokka_annotate",
            "shared_variants_export_run",
            "snpeff_annotate",
            "spades_assemble",
            "tabix_index_run",
        ],
        retrieval_protected={"bcftools_norm_run", "shared_variants_export_run"},
        stage_aware_names={
            "bcftools_isec_run",
            "bcftools_norm_run",
            "bwa_mem_align",
            "freebayes_call",
            "shared_variants_export_run",
            "snpeff_annotate",
            "spades_assemble",
            "tabix_index_run",
        },
    )

    names = {str(item.get("name", "")).strip() for item in trimmed}
    assert "bcftools_norm_run" in names
    assert "bcftools_isec_run" in names
    assert "tabix_index_run" in names
    assert "shared_variants_export_run" in names
    assert "prodigal_annotate" not in names


def test_planner_skill_selection_keeps_late_preferred_tools_after_trim(monkeypatch) -> None:
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3.6:35b-a3b"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "8")
    monkeypatch.setattr(
        orchestrator_mod._orchestrator_skill_retrieval,
        "planner_skill_retrieval_boosts",
        lambda *args, **kwargs: (
            {"shared_export": 12, "normalize_step": 10},
            {"shared_export", "normalize_step"},
            {
                "retrieval_enabled": True,
                "retrieval_profile": "test",
                "retrieval_limit": 6,
                "tool_cards_dir": "",
                "retrieval_selected_skill_names": [
                    "shared_export",
                    "normalize_step",
                    "intersect_step",
                    "index_step",
                ],
                "retrieval_protected_skill_names": [
                    "normalize_step",
                    "shared_export",
                ],
                "retrieval_matches": [],
            },
        ),
    )
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {"name": "core_prepare", "description": "core prepare mapping.", "parameters": {}},
        {"name": "core_align", "description": "core align mapping.", "parameters": {}},
        {"name": "core_call", "description": "core call mapping.", "parameters": {}},
        {"name": "legacy_annotation", "description": "legacy annotation helper.", "parameters": {}},
        {"name": "shared_export", "description": "shared export output helper.", "parameters": {}},
        {"name": "legacy_helper", "description": "generic helper for reports.", "parameters": {}},
        {"name": "normalize_step", "description": "normalize one VCF wrapper.", "parameters": {}},
        {"name": "intersect_step", "description": "intersect one VCF wrapper.", "parameters": {}},
        {"name": "index_step", "description": "index one VCF wrapper.", "parameters": {}},
    ]

    selected, _ = orchestrator._select_planner_skill_metadata(
        "core prepare core align core call legacy annotation shared export",
        skills,
        analysis_spec={
            "preferred_tools": [
                "normalize_step",
                "intersect_step",
                "index_step",
                "core_prepare",
                "core_align",
                "core_call",
                "shared_export",
                "legacy_annotation",
            ],
        },
    )

    names = {str(item.get("name", "")).strip() for item in selected}
    assert "normalize_step" in names
    assert "intersect_step" in names
    assert "index_step" in names
    assert "shared_export" in names
    assert "legacy_helper" not in names


def test_planner_skill_selection_keeps_protocol_grounded_required_tools(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3-coder-next:latest"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "6")
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {"name": "subread_align", "description": "Align reads with Subread.", "parameters": {}},
        {"name": "featurecounts_run", "description": "Generate count matrix.", "parameters": {}},
        {"name": "deseq2_run", "description": "Differential expression with DESeq2.", "parameters": {}},
        {"name": "fastqc_run", "description": "Quality control.", "parameters": {}},
        {"name": "salmon_quant", "description": "Transcript quantification.", "parameters": {}},
        {"name": "dexseq_run", "description": "Exon-level differential usage.", "parameters": {}},
        {"name": "sc_count_and_cluster", "description": "Single-cell clustering.", "parameters": {}},
        {"name": "rmats_run", "description": "Splicing analysis.", "parameters": {}},
    ]

    selected, _ = orchestrator._select_planner_skill_metadata(
        "Identify differentially expressed genes between planktonic and biofilm conditions using DESeq2.",
        skills,
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "preferred_tools": ["subread_align", "featurecounts_run"],
            "protocol_grounding": {
                "required_tools": ["subread_align", "featurecounts_run"],
                "required_plan_signals": ["subread_align", "featurecounts_run", "deseq2_run"],
            },
        },
    )
    names = {str(s.get("name", "")).strip() for s in selected}
    assert "bash_run" in names
    assert "subread_align" in names
    assert "featurecounts_run" in names
    assert "deseq2_run" in names


def test_planner_skill_selection_uses_tool_card_dir_from_env(monkeypatch, tmp_path: Path):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "qwen3-coder-next:latest"
    cards_dir = tmp_path / "cards"
    write_tool_card(
        tool_card_from_draft(
            {
                "skill_name": "novel_taxonomic_profile",
                "description": "Profile metagenomes into profiling.tsv.",
                "tools_required": ["novel_profiler"],
                "capabilities": ["taxonomic_profiling"],
                "parameters": {"reads_fastq": {"type": "path", "required": True}},
                "output_types": ["profiling.tsv"],
                "when_to_use": "Use for taxonomic profiling with profiling.tsv output.",
            }
        ),
        tool_cards_dir=cards_dir,
    )
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "2")
    orchestrator.tool_cards_dir = cards_dir
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {
            "name": "novel_taxonomic_profile",
            "description": "Generic profiling wrapper.",
            "parameters": {"reads_fastq": {"type": "path"}},
            "tools_required": ["novel_profiler"],
            "analysis_categories": ["general"],
            "capabilities": ["profiling"],
        },
        {
            "name": "featurecounts_run",
            "description": "Gene counting.",
            "parameters": {"input_bams": {"type": "path"}},
            "tools_required": ["featureCounts"],
            "analysis_categories": ["gene_counting"],
            "capabilities": ["gene_counting"],
        },
    ]

    selected, meta = orchestrator._select_planner_skill_metadata(
        "taxonomic profiling to profiling tsv",
        skills,
    )

    names = {str(item.get("name", "")).strip() for item in selected}
    assert "novel_taxonomic_profile" in names
    assert meta["tool_cards_dir"] == str(cards_dir.resolve())
    assert "bash_run" in names
    assert "novel_taxonomic_profile" in meta["retrieval_selected_skill_names"]


def test_planner_skill_selection_records_retrieval_metadata(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.model_name = "gemma4:26b"
    monkeypatch.setenv("BIO_HARNESS_PLANNER_SKILL_BUDGET", "3")
    skills = [
        {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}},
        {
            "name": "stringtie_quant",
            "description": "Assemble and quantify transcripts from aligned RNA-seq BAM files.",
            "parameters": {},
            "capabilities": ["quantification"],
            "analysis_categories": ["transcript_quantification"],
            "canonical_output_filenames": {
                "output_gtf": "assembled.gtf",
                "gene_abundance_tsv": "gene_abundances.tsv",
            },
        },
        {
            "name": "featurecounts_run",
            "description": "Generate a count matrix from aligned reads.",
            "parameters": {},
            "capabilities": ["quantification"],
            "analysis_categories": ["gene_counting"],
        },
        {"name": "bcftools_call", "description": "Variant calling with bcftools.", "parameters": {}},
    ]

    selected, meta = orchestrator._select_planner_skill_metadata(
        "Write assembled.gtf and gene_abundances.tsv from aligned RNA-seq BAM files.",
        skills,
    )

    names = {str(skill.get("name", "")).strip() for skill in selected}
    assert "stringtie_quant" in names
    assert meta["retrieval_enabled"] is True
    assert meta["retrieval_profile"] == "compact_model"
    assert "stringtie_quant" in meta["retrieval_selected_skill_names"]


def test_execute_plan_honors_current_step_idx(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.tools_context = ""
    orchestrator._available_skill_metadata = lambda: []
    orchestrator._normalize_plan_json = lambda plan: plan
    orchestrator._extract_step_contracts = lambda plan: []

    captured = {}

    def fake_executor(state, log_queue, **kwargs):
        captured["current_step_idx"] = state["current_step_idx"]
        captured["plan_length"] = len(state["plan"].plan)
        return {**state, "error_message": None}

    orchestrator._executor_node = fake_executor

    orchestrator.execute_plan(
        {
            "thought_process": "test",
            "plan": [
                {"tool_name": "bash_run", "arguments": {"command": "echo 1"}, "step_id": 1},
                {"tool_name": "bash_run", "arguments": {"command": "echo 2"}, "step_id": 2},
            ],
        },
        queue.Queue(),
        current_step_idx=1,
    )

    assert captured["current_step_idx"] == 1
    assert captured["plan_length"] == 2


def test_think_uses_available_skills_metadata_override() -> None:
    orchestrator = _orchestrator_stub()
    orchestrator.tools_context = ""
    orchestrator._available_skill_metadata = lambda: [{"name": "default_tool"}]

    captured = {}

    def _fake_planner_node(state):
        captured["available_skills_metadata"] = state["available_skills_metadata"]
        return {
            **state,
            "plan": {"thought_process": "", "plan": []},
            "error_message": None,
        }

    orchestrator._planner_node = _fake_planner_node

    orchestrator.think(
        "Plan one step.",
        available_skills_metadata_override=[
            {"name": "selected_tool"},
            {"name": "other_selected_tool"},
        ],
    )

    assert [item["name"] for item in captured["available_skills_metadata"]] == [
        "selected_tool",
        "other_selected_tool",
    ]


def test_execute_plan_emits_executor_prestep_statuses_and_skips_skill_scan(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.tools_context = ""
    orchestrator.skill_registry = SimpleNamespace(_skills={"bash_run": {"name": "bash_run"}})
    orchestrator._available_skill_metadata = lambda: (_ for _ in ()).throw(
        AssertionError("execute_plan should not rebuild available skill metadata")
    )
    orchestrator._normalize_plan_json = lambda plan: plan
    orchestrator._extract_step_contracts = lambda plan: []

    captured = {}

    def fake_executor(state, log_queue, **kwargs):
        captured["available_skills_metadata"] = state["available_skills_metadata"]
        return {**state, "error_message": None}

    orchestrator._executor_node = fake_executor

    q = queue.Queue()
    orchestrator.execute_plan(
        {
            "thought_process": "test",
            "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo 1"}, "step_id": 1}],
        },
        q,
    )

    emitted = []
    while not q.empty():
        emitted.append(q.get_nowait())

    combined = "".join(str(item) for item in emitted)
    assert "[status] phase=executor_preflight" in combined
    assert "[status] phase=pre_execution_validation" in combined
    assert "[status] phase=executor_state_init" in combined
    assert "[status] phase=executor_dispatch" in combined
    assert captured["available_skills_metadata"] == []


def test_executor_continues_when_matching_completion_manifest_marks_step_success(tmp_path: Path) -> None:
    orchestrator = _orchestrator_stub()
    orchestrator._step_validation_agent = lambda step, cwd: {"passed": True, "issues": [], "fixes": []}
    orchestrator._validate_deliverables = lambda contract, cwd: {"passed": True, "reason": ""}
    orchestrator._loaded_skill_functions = {
        "bwa_mem_align": lambda **kwargs: "fake-command",
    }

    output_bam = tmp_path / "alignments" / "evol2.bam"

    def _fake_run_command(command, log_queue, cwd=None, allowed_root=None, cancel_event=None):
        del command, cwd, allowed_root, cancel_event
        write_completion_manifest(
            output_bam.parent,
            tool_name="bwa_mem_align",
            outputs=[str(output_bam)],
            exit_code=0,
            success=True,
        )
        log_queue.put("[exit_code=1]\n")
        log_queue.put(None)

    orchestrator.command_runner = SimpleNamespace(run_command=_fake_run_command)

    state = {
        "plan": LLMOutputSchema(
            thought_process="test",
            plan=[
                {
                    "tool_name": "bwa_mem_align",
                    "arguments": {
                        "reference_fasta": "/tmp/ref.fa",
                        "reads_1": "/tmp/evol2_R1.fastq.gz",
                        "reads_2": "/tmp/evol2_R2.fastq.gz",
                        "output_bam": str(output_bam),
                        "sample_name": "evol2",
                    },
                    "step_id": 1,
                }
            ],
        ),
        "current_step_idx": 0,
        "execution_log": [],
    }

    q = queue.Queue()
    result = orchestrator._executor_node(state, q, cwd=str(tmp_path))

    emitted = []
    while not q.empty():
        emitted.append(str(q.get_nowait()))
    combined = "".join(emitted)

    assert result["error_message"] is None
    assert result["current_step_idx"] == 1
    assert "completion manifest marked it successful. Continuing." in combined
    assert "Error executing step" not in combined


def test_executor_honors_bash_run_working_directory(tmp_path: Path) -> None:
    orchestrator = _orchestrator_stub()
    orchestrator._loaded_skill_functions = {"bash_run": bash_run}

    captured: dict[str, str | None] = {}

    def _fake_run_command(command, log_queue, cwd=None, allowed_root=None, cancel_event=None):
        del allowed_root, cancel_event
        captured["command"] = command
        captured["cwd"] = cwd
        log_queue.put("[exit_code=0]\n")
        log_queue.put(None)

    orchestrator.command_runner = SimpleNamespace(run_command=_fake_run_command)

    working_dir = tmp_path / "selected" / "variants"
    state = {
        "plan": LLMOutputSchema(
            thought_process="test",
            plan=[
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": "pwd",
                        "working_directory": str(working_dir),
                    },
                    "step_id": 1,
                }
            ],
        ),
        "current_step_idx": 0,
        "execution_log": [],
    }

    q = queue.Queue()
    result = orchestrator._executor_node(
        state,
        q,
        cwd=str(tmp_path / "selected"),
        allowed_root=str(tmp_path / "selected"),
    )

    assert result["error_message"] is None
    assert captured["cwd"] == str(working_dir)
    assert working_dir.is_dir()
    assert str(captured["command"]).startswith(f"cd {working_dir}")


def test_build_analysis_spec_preserves_fallback_parameter_profile(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "sc_count_and_cluster": {"name": "sc_count_and_cluster", "description": "Single-cell workflow.", "parameters": {}, "tools_required": []},
            "bash_run": {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}, "tools_required": []},
        }
    )
    orchestrator.biollm = SimpleNamespace(
        design_analysis=lambda *args, **kwargs: {
            "analysis_type": "single_cell_rna_seq",
            "chosen_method": "sc_count_and_cluster",
            "preferred_tools": ["sc_count_and_cluster"],
        }
    )
    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: True)

    spec = orchestrator.build_analysis_spec(
        "Analyze the single-cell RNA-seq data to cluster cells and identify marker genes.",
        contract={"must_include_capabilities": ["alignment", "single_cell_analysis", "reference_inputs"]},
        benchmark_policy="official_bioagentbench",
    )

    profiles = spec.get("parameter_profile", [])
    sc_profile = next(item for item in profiles if item.get("tool_name") == "sc_count_and_cluster")
    assert sc_profile["settings"]["min_genes"] == 3
    assert sc_profile["settings"]["min_cells"] == 1
    assert sc_profile["settings"]["kmer_size"] == 25
    assert sc_profile["settings"]["leiden_resolution"] == 0.5


def test_build_analysis_spec_preserves_selected_dir_after_analysis_review(monkeypatch, tmp_path):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "spades_assemble": {"name": "spades_assemble", "description": "Assemble reads.", "parameters": {}, "tools_required": []},
            "bwa_mem_align": {"name": "bwa_mem_align", "description": "Align reads.", "parameters": {}, "tools_required": []},
            "freebayes_call": {"name": "freebayes_call", "description": "Call variants.", "parameters": {}, "tools_required": []},
            "prodigal_annotate": {"name": "prodigal_annotate", "description": "Annotate contigs.", "parameters": {}, "tools_required": []},
            "snpeff_annotate": {"name": "snpeff_annotate", "description": "Annotate variants.", "parameters": {}, "tools_required": []},
        }
    )
    orchestrator.biollm = SimpleNamespace(
        design_analysis=lambda *args, **kwargs: {
            "analysis_type": "bacterial_evolution_variant_calling",
            "chosen_method": "freebayes_call",
            "preferred_tools": ["spades_assemble", "prodigal_annotate", "bwa_mem_align", "freebayes_call", "snpeff_annotate"],
        }
    )
    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: True)

    selected_dir = tmp_path / "run"
    data_root = tmp_path / "data"
    data_root.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()

    spec = orchestrator.build_analysis_spec(
        "Identify shared evolved variants relative to the ancestor.",
        contract={"must_include_capabilities": ["alignment", "variant_calling", "reference_inputs"]},
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        project_root=str(project_root),
        benchmark_policy="bioagentbench_planning_strict",
    )

    assert spec["selected_dir"] == str(selected_dir.resolve())


def test_build_analysis_spec_honors_manifest_analysis_type_override(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "subread_align": {
                "name": "subread_align",
                "description": "Align RNA-seq reads.",
                "parameters": {},
                "tools_required": [],
            },
            "featurecounts_run": {
                "name": "featurecounts_run",
                "description": "Count reads per gene.",
                "parameters": {},
                "tools_required": [],
            },
            "deseq2_run": {
                "name": "deseq2_run",
                "description": "Run DESeq2.",
                "parameters": {},
                "tools_required": [],
            },
        }
    )
    orchestrator.biollm = SimpleNamespace(
        backend_reachable=lambda timeout_seconds=0.5: True,
        design_analysis=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("deterministic override should avoid live review")
        ),
    )
    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: True)

    spec = orchestrator.build_analysis_spec(
        "Identify changed genes between planktonic and biofilm conditions.",
        analysis_type_override="rna_seq_differential_expression",
        benchmark_policy="scientific_harness",
    )

    assert spec["analysis_type"] == "rna_seq_differential_expression"
    assert spec["chosen_method"] == "featurecounts_run + deseq2_run"


def test_build_analysis_spec_discovers_sibling_reference_assets(monkeypatch, tmp_path):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "subread_align": {
                "name": "subread_align",
                "description": "Align RNA-seq reads.",
                "parameters": {},
                "tools_required": [],
            },
            "featurecounts_run": {
                "name": "featurecounts_run",
                "description": "Count reads per gene.",
                "parameters": {},
                "tools_required": [],
            },
            "deseq2_run": {
                "name": "deseq2_run",
                "description": "Run DESeq2.",
                "parameters": {},
                "tools_required": [],
            },
        }
    )
    orchestrator.biollm = SimpleNamespace(
        backend_reachable=lambda timeout_seconds=0.5: False,
        design_analysis=lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)

    task_root = tmp_path / "tasks" / "deseq"
    data_root = task_root / "data"
    references = task_root / "references"
    selected_dir = tmp_path / "official_runs" / "deseq" / "attempt1"
    data_root.mkdir(parents=True)
    references.mkdir(parents=True)
    (data_root / "sample_R1.fastq").write_text("@r1\nACGT\n+\nIIII\n", encoding="utf-8")
    (data_root / "sample_R2.fastq").write_text("@r2\nTGCA\n+\nIIII\n", encoding="utf-8")
    reference_fasta = references / "C_parapsilosis_CDC317_current_chromosomes.fasta"
    annotation_gff = references / "C_parapsilosis_CDC317_current_features.gff"
    reference_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    annotation_gff.write_text("##gff-version 3\n", encoding="utf-8")

    spec = orchestrator.build_analysis_spec(
        "Identify differentially expressed genes from RNA-seq reads.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        project_root=str(tmp_path),
        benchmark_policy="scientific_harness",
        analysis_type_override="rna_seq_differential_expression",
    )

    manifest = spec["file_manifest"]
    assert manifest.resolve("reference_genome") == str(reference_fasta)
    assert manifest.resolve("annotation_gff") == str(annotation_gff)
    assert spec["plan_skeleton"][0][0] == "subread_align"


def test_build_analysis_spec_skips_live_review_when_backend_unreachable(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "rmats_run": {"name": "rmats_run", "description": "Splicing workflow.", "parameters": {}, "tools_required": []},
        }
    )
    orchestrator.biollm = SimpleNamespace(
        backend_reachable=lambda timeout_seconds=0.5: False,
        design_analysis=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("design_analysis should not be called")),
    )
    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: True)

    spec = orchestrator.build_analysis_spec(
        "Run alternative splicing analysis with rMATS for control vs treatment.",
        contract={"must_include_capabilities": ["splicing_analysis"]},
        benchmark_policy="scientific_harness",
    )

    assert spec["analysis_type"] == "alternative_splicing"


def test_build_analysis_spec_prefers_deterministic_seed_over_extra_review(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "rmats_run": {"name": "rmats_run", "description": "Splicing workflow.", "parameters": {}, "tools_required": []},
        }
    )
    orchestrator.biollm = SimpleNamespace(
        backend_reachable=lambda timeout_seconds=0.5: True,
        design_analysis=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("design_analysis should not be called")),
    )
    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: True)

    spec = orchestrator.build_analysis_spec(
        "Run alternative splicing analysis with rMATS for control vs treatment.",
        contract={"must_include_capabilities": ["splicing_analysis"]},
        benchmark_policy="scientific_harness",
    )

    assert spec["analysis_type"] == "alternative_splicing"
    assert spec["chosen_method"] == "rmats_run"


def test_build_analysis_spec_records_deterministic_warnings_for_grounding_and_data_discovery(monkeypatch, tmp_path):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "bash_run": {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}, "tools_required": []},
        }
    )
    orchestrator.biollm = SimpleNamespace()

    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        orchestrator_mod,
        "extract_protocol_grounding",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("grounding boom")),
    )
    monkeypatch.setattr(
        orchestrator_mod,
        "discover_data_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("discovery boom")),
    )

    selected_dir = tmp_path / "run"
    data_root = tmp_path / "data"
    project_root = tmp_path / "project"
    selected_dir.mkdir()
    data_root.mkdir()
    project_root.mkdir()

    spec = orchestrator.build_analysis_spec(
        "Analyze the supplied data.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        project_root=str(project_root),
        benchmark_policy="scientific_harness",
    )

    warnings = {row["subsystem"]: row for row in spec["deterministic_warnings"]}
    assert warnings["protocol_grounding"]["exception_class"] == "RuntimeError"
    assert warnings["protocol_grounding"]["message"] == "grounding boom"
    assert warnings["data_discovery"]["exception_class"] == "RuntimeError"
    assert warnings["data_discovery"]["message"] == "discovery boom"


def test_build_analysis_spec_records_analysis_review_warning(monkeypatch):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "bash_run": {"name": "bash_run", "description": "Execute shell commands.", "parameters": {}, "tools_required": []},
        }
    )
    orchestrator.biollm = SimpleNamespace(
        backend_reachable=lambda timeout_seconds=0.5: True,
        design_analysis=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("review boom")),
    )

    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: True)

    spec = orchestrator.build_analysis_spec(
        "Analyze the supplied data.",
        benchmark_policy="scientific_harness",
    )

    assert any(
        row["subsystem"] == "analysis_review" and row["message"] == "review boom"
        for row in spec["deterministic_warnings"]
    )


def test_build_analysis_spec_anchors_proteomics_from_discovered_files(monkeypatch, tmp_path):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "proteomics_diff_abundance": {
                "name": "proteomics_diff_abundance",
                "description": "Run processed proteomics differential abundance.",
                "parameters": {},
                "tools_required": [],
            },
            "deseq2_run": {
                "name": "deseq2_run",
                "description": "Differential expression with DESeq2.",
                "parameters": {},
                "tools_required": [],
            },
            "featurecounts_run": {
                "name": "featurecounts_run",
                "description": "Generate RNA-seq count matrices.",
                "parameters": {},
                "tools_required": [],
            },
        }
    )
    orchestrator.biollm = SimpleNamespace(
        backend_reachable=lambda timeout_seconds=0.5: False,
        design_analysis=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("design_analysis should not be called")),
    )

    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(orchestrator_mod, "extract_protocol_grounding", lambda **_kwargs: {})

    selected_dir = tmp_path / "run"
    data_root = tmp_path / "data"
    project_root = tmp_path / "project"
    selected_dir.mkdir()
    data_root.mkdir()
    project_root.mkdir()
    (data_root / "abundance_matrix.csv").write_text("protein,s1,s2\nP1,1,2\n", encoding="utf-8")
    (data_root / "metadata.csv").write_text("sample,condition\ns1,ctrl\ns2,trt\n", encoding="utf-8")

    spec = orchestrator.build_analysis_spec(
        "Analyze differential abundance. The metadata has multiple columns; use the condition column.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        project_root=str(project_root),
        benchmark_policy="scientific_harness",
    )

    assert spec["analysis_type"] == "proteomics"
    assert spec["chosen_method"] == "proteomics_diff_abundance"
    assert spec["execution_contract"]["analysis_family"] == "proteomics"


def test_build_analysis_spec_anchors_metabolomics_from_discovered_files(monkeypatch, tmp_path):
    orchestrator = _orchestrator_stub()
    orchestrator.skill_registry = SimpleNamespace(
        _skills={
            "metabolomics_diff_abundance": {
                "name": "metabolomics_diff_abundance",
                "description": "Run processed metabolomics differential abundance.",
                "parameters": {},
                "tools_required": [],
            },
            "proteomics_diff_abundance": {
                "name": "proteomics_diff_abundance",
                "description": "Run processed proteomics differential abundance.",
                "parameters": {},
                "tools_required": [],
            },
        }
    )
    orchestrator.biollm = SimpleNamespace(
        backend_reachable=lambda timeout_seconds=0.5: False,
        design_analysis=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("design_analysis should not be called")),
    )

    monkeypatch.setattr(orchestrator, "_tool_binary_available", lambda _tool: True)
    monkeypatch.setattr(orchestrator_mod, "should_generate_analysis_review", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(orchestrator_mod, "extract_protocol_grounding", lambda **_kwargs: {})

    selected_dir = tmp_path / "run"
    data_root = tmp_path / "data"
    project_root = tmp_path / "project"
    selected_dir.mkdir()
    data_root.mkdir()
    project_root.mkdir()
    (data_root / "feature_table.csv").write_text("feature,s1,s2\nmz100_rt1,1,2\n", encoding="utf-8")
    (data_root / "metadata.csv").write_text("sample,condition\ns1,ctrl\ns2,trt\n", encoding="utf-8")

    spec = orchestrator.build_analysis_spec(
        "Analyze differential abundance from this metabolomics feature table.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        project_root=str(project_root),
        benchmark_policy="scientific_harness",
    )

    assert spec["analysis_type"] == "metabolomics"
    assert spec["chosen_method"] == "metabolomics_diff_abundance"
    assert spec["execution_contract"]["analysis_family"] == "metabolomics"
