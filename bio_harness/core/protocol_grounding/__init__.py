"""Protocol grounding sub-package.

Provides template compilers, protocol grounding extraction, plan assessment,
and deterministic repair logic for bioinformatics analysis pipelines.

All public names are re-exported here so that existing ``from
bio_harness.core.protocol_grounding import X`` statements continue to work
unchanged.
"""
from __future__ import annotations

# -- _shared.py: constants and helpers ----------------------------------------
from bio_harness.core.protocol_grounding._shared import (
    DEFAULT_SHARED_VARIANT_COLUMNS,
    DESEQ_METADATA_FILENAMES,
    GFF3_TO_GTF_SCRIPT,
    KRAKEN2_DB_SENTINELS,
    NORMALIZE_GFF_FOR_FEATURECOUNTS_SCRIPT,
    PARAMETER_KNOWLEDGE_BASE,
    PRODIGAL_GENE_RE,
    PROJECT_ROOT,
    PROTOCOL_FILENAMES,
    SHARED_VARIANT_EXPORTER,
    SIGNAL_EQUIVALENCES,
    STAR_COUNTS_MATRIX_SCRIPT,
    STAR_INDEX_BUILD_SCRIPT,
    VARIANT_CALL_TOOLS,
    _apply_parameter_knowledge_base,
    _apply_parameter_profile,
    _bash_join,
    _build_normalize_vcf_command,
    _build_variant_filter_command,
    _dedupe,
    _discover_fastq_pairs,
    _normalize_steps,
    _renumber_plan,
    _safe_label,
    _signal_present_in_text,
    _translate_vcffilter_to_bcftools,
    shlex_quote,
)

# -- _grounding.py: protocol extraction and assessment -----------------------
from bio_harness.core.protocol_grounding._grounding import (
    _append_param_hint,
    _benchmark_protocol_profile,
    _discover_reference_annotation_path,
    _discover_sample_groups_from_metadata,
    _generic_analysis_grounding,
    _infer_deseq_contrast,
    _merge_param_hints,
    _normalize_metadata_token,
    _read_excerpt,
    _result_file_header_constraints,
    _task_tokens_from_paths,
    analysis_patch_from_protocol,
    assess_protocol_grounding,
    discover_protocol_files,
    extract_protocol_grounding,
)

# -- _plan_merge.py: plan patching -------------------------------------------
from bio_harness.core.protocol_grounding._plan_merge import (
    _classify_bash_purpose,
    _match_steps,
    _merge_step_arguments,
    _patch_llm_plan_with_template,
    _tools_equivalent,
)

# -- _repair.py: dispatcher --------------------------------------------------
from bio_harness.core.protocol_grounding._repair import (
    TEMPLATE_COMPILER_TYPES,
    deterministic_protocol_repair,
)

# -- Compiler modules --------------------------------------------------------
from bio_harness.core.protocol_grounding._compiler_evolution import (
    _compile_bacterial_evolution_shared_plan,
    _infer_reference_and_samples,
    _shared_export_settings,
)
from bio_harness.core.protocol_grounding._compiler_rna_seq import (
    _compile_rna_seq_de_plan,
)
from bio_harness.core.protocol_grounding._compiler_transcript import (
    _compile_transcript_quant_plan,
)
from bio_harness.core.protocol_grounding._compiler_metagenomics import (
    _compile_metagenomics_plan,
    _looks_like_kraken2_db_dir,
    _metagenomics_taxon_expectations,
    _resolve_metagenomics_kraken2_db,
    _validate_kraken2_db_taxa,
)
from bio_harness.core.protocol_grounding._compiler_single_cell import (
    _compile_single_cell_plan,
)
from bio_harness.core.protocol_grounding._compiler_germline import (
    _compile_germline_variant_calling_plan,
)
from bio_harness.core.protocol_grounding._compiler_annotation import (
    _compile_variant_annotation_plan,
)
from bio_harness.core.protocol_grounding._compiler_phylogenetics import (
    _compile_phylogenetics_plan,
)
from bio_harness.core.protocol_grounding._compiler_comparative import (
    _compile_comparative_genomics_plan,
)
from bio_harness.core.protocol_grounding._compiler_viral import (
    _compile_viral_metagenomics_plan,
)
from bio_harness.core.protocol_grounding._compiler_dge import (
    _compile_multi_model_dge_plan,
)

__all__ = [
    # Constants
    "DEFAULT_SHARED_VARIANT_COLUMNS",
    "DESEQ_METADATA_FILENAMES",
    "GFF3_TO_GTF_SCRIPT",
    "KRAKEN2_DB_SENTINELS",
    "NORMALIZE_GFF_FOR_FEATURECOUNTS_SCRIPT",
    "PARAMETER_KNOWLEDGE_BASE",
    "PRODIGAL_GENE_RE",
    "PROJECT_ROOT",
    "PROTOCOL_FILENAMES",
    "SHARED_VARIANT_EXPORTER",
    "SIGNAL_EQUIVALENCES",
    "STAR_COUNTS_MATRIX_SCRIPT",
    "STAR_INDEX_BUILD_SCRIPT",
    "TEMPLATE_COMPILER_TYPES",
    "VARIANT_CALL_TOOLS",
    # Shared helpers
    "_apply_parameter_knowledge_base",
    "_apply_parameter_profile",
    "_bash_join",
    "_build_normalize_vcf_command",
    "_build_variant_filter_command",
    "_dedupe",
    "_discover_fastq_pairs",
    "_normalize_steps",
    "_renumber_plan",
    "_safe_label",
    "_signal_present_in_text",
    "_translate_vcffilter_to_bcftools",
    "shlex_quote",
    # Grounding
    "_append_param_hint",
    "_benchmark_protocol_profile",
    "_discover_reference_annotation_path",
    "_discover_sample_groups_from_metadata",
    "_generic_analysis_grounding",
    "_infer_deseq_contrast",
    "_merge_param_hints",
    "_normalize_metadata_token",
    "_read_excerpt",
    "_result_file_header_constraints",
    "_task_tokens_from_paths",
    "analysis_patch_from_protocol",
    "assess_protocol_grounding",
    "discover_protocol_files",
    "extract_protocol_grounding",
    # Plan merge
    "_classify_bash_purpose",
    "_match_steps",
    "_merge_step_arguments",
    "_patch_llm_plan_with_template",
    "_tools_equivalent",
    # Repair
    "deterministic_protocol_repair",
    # Compilers
    "_compile_bacterial_evolution_shared_plan",
    "_compile_comparative_genomics_plan",
    "_compile_germline_variant_calling_plan",
    "_compile_metagenomics_plan",
    "_compile_multi_model_dge_plan",
    "_compile_phylogenetics_plan",
    "_compile_rna_seq_de_plan",
    "_compile_single_cell_plan",
    "_compile_transcript_quant_plan",
    "_compile_variant_annotation_plan",
    "_compile_viral_metagenomics_plan",
    "_infer_reference_and_samples",
    "_looks_like_kraken2_db_dir",
    "_metagenomics_taxon_expectations",
    "_resolve_metagenomics_kraken2_db",
    "_shared_export_settings",
    "_validate_kraken2_db_taxa",
]
