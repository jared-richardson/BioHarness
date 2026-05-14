"""Contract inference, tool availability, reference resolution, and FASTQ sample utilities."""
from __future__ import annotations

import re
from typing import Any

from bio_harness.core.capability_catalog import (
    capability_index,
    infer_capabilities_from_text,
    infer_tool_hints_from_text,
)
from bio_harness.core.analysis_spec_support import is_direct_skill_smoke_query
from bio_harness.core.contract_inference_utils import requires_reference_inputs
from bio_harness.core.request_scope import (
    semantically_requests_long_read_rna_stringtie_pipeline,
    semantically_requests_stringtie_quant,
)
from bio_harness.core.request_output_intent import extract_requested_output_paths
from bio_harness.core.tool_env import requirement_available
from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.harness import contract_artifact_utils as _contract_artifact_utils
from bio_harness.harness import contract_fastq_utils as _contract_fastq_utils
from bio_harness.harness import contract_group_tags as _contract_group_tags
from bio_harness.harness import contract_prompt_intent as _contract_prompt_intent
from bio_harness.harness import contract_reference_utils as _contract_reference_utils
from bio_harness.harness import contract_tool_availability as _contract_tool_availability
from bio_harness.harness.stream_utils import _extract_paths_from_text, _normalize_contract_hint

_extract_reference_paths_from_plan = _contract_reference_utils._extract_reference_paths_from_plan
_find_alias_reference = _contract_reference_utils._find_alias_reference
_find_reference_candidate = _contract_reference_utils._find_reference_candidate
_find_reference_candidate_in_roots = _contract_reference_utils._find_reference_candidate_in_roots
_find_workspace_reference = _contract_reference_utils._find_workspace_reference
_looks_like_fasta_path = _contract_reference_utils._looks_like_fasta_path
_looks_like_task_local_generated_reference = _contract_reference_utils._looks_like_task_local_generated_reference
_planned_converted_gtf_path = _contract_reference_utils._planned_converted_gtf_path
_pick_reference_paths_from_text = _contract_reference_utils._pick_reference_paths_from_text
_repair_missing_references_in_plan = _contract_reference_utils._repair_missing_references_in_plan
_repair_requested_references_and_index_bases_in_plan = _contract_reference_utils._repair_requested_references_and_index_bases_in_plan
_resolve_reference_paths = _contract_reference_utils._resolve_reference_paths
_resolve_reference_paths_for_template_fallback = _contract_reference_utils._resolve_reference_paths_for_template_fallback
_stable_index_base_for_tool = _contract_reference_utils._stable_index_base_for_tool
_stable_quant_index_path_for_tool = _contract_reference_utils._stable_quant_index_path_for_tool
_workspace_reference_alias_candidates = _contract_reference_utils._workspace_reference_alias_candidates
_iter_pathlike_values = _contract_artifact_utils._iter_pathlike_values
_collect_planned_output_paths = _contract_artifact_utils._collect_planned_output_paths
_missing_input_paths_for_plan = _contract_artifact_utils._missing_input_paths_for_plan
_clean_stale_tmp_cache_paths = _contract_artifact_utils._clean_stale_tmp_cache_paths
_plan_contains_splicing_steps = _contract_artifact_utils._plan_contains_splicing_steps
_rmats_output_dirs = _contract_artifact_utils._rmats_output_dirs
_verify_run_outputs = _contract_artifact_utils._verify_run_outputs
_extract_fastq_sample_tag = _contract_fastq_utils._extract_fastq_sample_tag
_sample_tag_kind = _contract_fastq_utils._sample_tag_kind
_infer_evolution_step_sample_tag = _contract_fastq_utils._infer_evolution_step_sample_tag
_resolve_sample_pair = _contract_fastq_utils._resolve_sample_pair
_extract_fastq_mate = _contract_fastq_utils._extract_fastq_mate
_discover_fastq_pair_map = _contract_fastq_utils._discover_fastq_pair_map
_extract_sample_tags_from_plan = _contract_group_tags._extract_sample_tags_from_plan
_extract_group_tags_from_request_text = _contract_group_tags._extract_group_tags_from_request_text
_pixi_bin_dir = _contract_tool_availability._pixi_bin_dir
_which_with_pixi = _contract_tool_availability._which_with_pixi
_exec_hint_name = _contract_tool_availability._exec_hint_name
_blocked_tool_hints_from_text = _contract_prompt_intent.blocked_tool_hints_from_text
_downstream_capability_hints_from_text = _contract_prompt_intent.downstream_capability_hints_from_text
_is_completed_output_report_prompt = _contract_prompt_intent.is_completed_output_report_prompt
_is_count_matrix_de_request = _contract_prompt_intent.is_count_matrix_de_request
_is_direct_wrapper_prompt = _contract_prompt_intent.is_direct_wrapper_prompt
_is_precounted_scanpy_request = _contract_prompt_intent.is_precounted_scanpy_request
_has_explicit_single_cell_diff_request = _contract_prompt_intent.has_explicit_single_cell_diff_request
_negated_tool_spans = _contract_prompt_intent.negated_tool_spans
_required_tool_hints_from_text = _contract_prompt_intent.required_tool_hints_from_text
_requests_alignment = _contract_prompt_intent.requests_alignment
_strip_downstream_context_capabilities = _contract_prompt_intent.strip_downstream_context_capabilities
_strip_tool_family_false_positive_capabilities = _contract_prompt_intent.strip_tool_family_false_positive_capabilities
_strip_upstream_capabilities_for_direct_wrapper_prompt = _contract_prompt_intent.strip_upstream_capabilities_for_direct_wrapper_prompt
_tool_hint_aliases = _contract_prompt_intent.tool_hint_aliases


def _is_exec_tool_available(tool_name: str) -> bool:
    name = _exec_hint_name(tool_name)
    if not name:
        return True
    return requirement_available(name)


def _missing_exec_tools_for_plan(plan: dict[str, Any]) -> list[str]:
    registry = default_tool_registry()
    missing: set[str] = set()
    for step in plan.get("plan", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip()
        if not tool_name:
            continue
        exec_hints = registry.exec_hints_for(tool_name)
        if not exec_hints:
            continue
        if not any(_is_exec_tool_available(hint) for hint in exec_hints):
            missing.add(_exec_hint_name(exec_hints[0]))
    return sorted(missing)


# ---------------------------------------------------------------------------
# Contract / capability inference
# ---------------------------------------------------------------------------


def _capability_specs_from_catalog(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for cap_id, cap in capability_index(catalog, enabled_only=False).items():
        plan_signals = [str(x).strip().lower() for x in cap.get("plan_signals", []) if str(x).strip()]
        spec: dict[str, Any] = {"plan_signals": plan_signals}
        if cap_id == "group_comparison":
            spec["group_signal_mode"] = str(cap.get("group_signal_mode", "auto")).strip().lower() or "auto"
        specs[cap_id] = spec
    return specs


def _is_somatic_variant_prompt(text: str) -> bool:
    text_l = str(text or "").lower()
    if any(token in text_l for token in ("somatic", "mutect2", "mutect")):
        return True
    return "tumor" in text_l or "tumour" in text_l


def _is_shared_evolution_variant_prompt(text: str) -> bool:
    """Return whether the request is for bacterial-evolution shared-variant export.

    Fix #19: the canonical evolution benchmark asks for "variants shared by both
    evolved lines relative to an ancestor". Without a dedicated capability, the
    contract was satisfied by any plan containing `annotation` — so the stepwise
    planner declared done after emitting snpeff_annotate on evol1 and never
    processed evol2, performed the set intersection, or exported the final CSV.

    This detector fires when the prompt combines a "shared variants" concept
    with an evolved-lineage / ancestor context. It is intentionally narrow so it
    does not capture unrelated "variant annotation" prompts that happen to
    mention the word "shared".
    """

    text_l = str(text or "").lower()
    has_shared_variant_phrase = any(
        phrase in text_l
        for phrase in (
            "shared by both evolved",
            "shared by both evolutionary",
            "shared between both evolved",
            "shared between the evolved",
            "shared variants",
            "variants shared",
            "common variants",
            "intersecting variants",
            "variant intersection",
        )
    )
    has_evolution_context = any(
        token in text_l
        for token in (
            "evolved line",
            "evolved lines",
            "evolved strain",
            "evolved strains",
            "evolved population",
            "evolved populations",
            "evolutionary line",
            "evolutionary lineage",
            "evolutionary descendants",
            "ancestor line",
            "ancestor strain",
            "ancestral line",
            "ancestral strain",
            "relative to an ancestor",
            "relative to the ancestor",
            "relative to their ancestor",
            "compared to the ancestor",
            "compared to their ancestor",
        )
    )
    return has_shared_variant_phrase and has_evolution_context


def _is_structural_variant_prompt(text: str) -> bool:
    """Return whether the request is asking for structural-variant discovery."""

    text_l = str(text or "").lower()
    if any(token in text_l for token in ("sniffles", "sniffles_sv_call")):
        return True
    has_long_read_context = any(
        token in text_l
        for token in (
            "long-read",
            "long read",
            "long sequencing reads",
            "long sequencing read",
            "nanopore",
            "pacbio",
            "reference genome",
            "reference fasta",
            "bam",
            "cram",
        )
    )
    has_structural_phrase = any(
        token in text_l
        for token in (
            "structural variant",
            "structural variants",
            "structural variation",
            "structural change",
            "structural changes",
            "big structural change",
            "big structural changes",
            "large structural change",
            "large structural changes",
        )
    )
    has_sv_event = any(
        token in text_l
        for token in (
            "deletion",
            "deletions",
            "insertion",
            "insertions",
            "inversion",
            "inversions",
            "rearrangement",
            "rearrangements",
            "translocation",
            "translocations",
        )
    )
    has_reference_comparison = any(
        token in text_l
        for token in (
            "compared to the reference",
            "compared to reference",
            "against the reference",
            "relative to the reference",
        )
    )
    has_detection_intent = any(token in text_l for token in ("call", "detect", "identify", "report", "find", "figure out"))
    if has_structural_phrase and has_long_read_context:
        return True
    if has_sv_event and has_long_read_context and (has_reference_comparison or has_detection_intent):
        return True
    return False


def _is_long_read_rna_prompt(text: str) -> bool:
    """Return whether the request is for long-read RNA alignment or isoforms."""

    text_l = str(text or "").lower()
    has_long_read_context = any(
        token in text_l
        for token in (
            "direct-rna",
            "direct rna",
            "oxford nanopore",
            "nanopore",
            "ont",
            "long-read rna",
            "long read rna",
        )
    )
    has_rna_intent = any(
        token in text_l
        for token in (
            "isoform",
            "isoforms",
            "transcript isoform",
            "splice-aware",
            "splice aware",
            "quantify transcript",
            "quantify isoform",
        )
    )
    return has_long_read_context and has_rna_intent


def _is_spatial_transcriptomics_prompt(text: str) -> bool:
    """Return whether the request is for processed spatial transcriptomics."""

    text_l = str(text or "").lower()
    if any(
        token in text_l
        for token in (
            "spatial transcriptomics",
            "spatial gene expression",
            "spatial omics",
            "visium",
            "spatial domain",
            "spatial domains",
        )
    ):
        return True
    has_spatial = "spatial" in text_l
    has_context = any(
        token in text_l
        for token in (
            "spot",
            "spots",
            "tissue section",
            "coordinate",
            "coordinates",
            "pixel space",
            "array_row",
            "array_col",
            "h5ad",
            "anndata",
            "regions are different",
            "regions differ",
            "genes define them",
        )
    )
    has_expression = any(
        token in text_l
        for token in (
            "gene expression",
            "transcriptomics",
            "marker genes",
            "domain identification",
            "domain assignments",
        )
    )
    return has_spatial and (has_context or has_expression)


def _is_proteomics_prompt(text: str) -> bool:
    """Return whether the request is for table-first proteomics analysis."""

    text_l = str(text or "").lower()
    if any(
        token in text_l
        for token in (
            "proteomics",
            "protein abundance",
            "protein abundances",
            "protein intensity",
            "protein intensities",
            "lfq",
            "label-free quantification",
        )
    ):
        return True
    has_matrix_or_da_term = any(
        token in text_l
        for token in (
            "differential abundance",
            "differential protein abundance",
            "abundance matrix",
            "intensity matrix",
        )
    )
    has_protein_anchor = any(
        token in text_l
        for token in (
            "protein",
            "proteomics",
            "lfq",
            "label-free quantification",
        )
    )
    if has_matrix_or_da_term and has_protein_anchor:
        return True
    has_protein_context = any(
        token in text_l
        for token in (
            "protein expression",
            "protein expression data",
            "which proteins are different",
            "proteins are different",
            "protein data",
        )
    )
    has_comparison = any(
        token in text_l
        for token in (
            "differential",
            "different between",
            "differentially abundant",
            "two groups",
            "control",
            "treatment",
            "group",
            "condition",
            "metadata",
            "matrix",
        )
    )
    return has_protein_context and has_comparison


def _is_metabolomics_prompt(text: str) -> bool:
    """Return whether the request is for table-first metabolomics analysis."""

    text_l = str(text or "").lower()
    if any(
        token in text_l
        for token in (
            "metabolomics",
            "metabolite abundance",
            "metabolite abundances",
            "metabolite intensity",
            "metabolite intensities",
            "feature table",
            "peak table",
            "untargeted metabolomics",
            "metabolic profiling",
            "lc-ms",
            "lcms",
        )
    ):
        return True
    has_matrix_or_da_term = any(
        token in text_l
        for token in (
            "differential abundance",
            "differential metabolite abundance",
            "feature table",
            "feature intensity",
            "peak table",
            "intensity matrix",
        )
    )
    has_metabolite_anchor = any(
        token in text_l
        for token in (
            "metabolite",
            "metabolites",
            "metabolomics",
            "mass spec",
            "metabolic profiling",
        )
    )
    if has_matrix_or_da_term and has_metabolite_anchor:
        return True
    has_metabolite_context = any(
        token in text_l
        for token in (
            "which metabolites are different",
            "metabolites are changing",
            "feature table",
            "mass spec experiment",
            "metabolite data",
        )
    )
    has_comparison = any(
        token in text_l
        for token in (
            "differential",
            "different between",
            "changing between",
            "differentially abundant",
            "two groups",
            "control",
            "treatment",
            "group",
            "condition",
            "metadata",
            "matrix",
        )
    )
    return has_metabolite_context and has_comparison


def _annotation_explicitly_unavailable(text: str) -> bool:
    """Return whether the request explicitly says no annotation is available."""

    text_l = str(text or "").lower()
    return any(
        token in text_l
        for token in (
            "no annotation file is provided",
            "no annotation is provided",
            "no annotation file",
            "without annotation",
            "without a gtf",
            "without gtf",
            "no gtf",
            "no gff",
        )
    )


def _has_tumor_normal_comparison(text: str) -> bool:
    text_l = str(text or "").lower()
    has_tumor = "tumor" in text_l or "tumour" in text_l
    if not has_tumor:
        return False
    return any(
        token in text_l
        for token in (
            "matched normal",
            "normal sample",
            "normal control",
            "tumor normal",
            "tumour normal",
            "tumor/normal",
            "tumour/normal",
            "tumor vs normal",
            "tumour vs normal",
            "tumor versus normal",
            "tumour versus normal",
        )
    )


def _has_raw_rnaseq_reads(text: str) -> bool:
    """Return whether a request mentions raw read inputs that imply alignment."""

    text_l = str(text or "").lower()
    return any(
        token in text_l
        for token in (
            ".fastq",
            ".fastq.gz",
            ".fq",
            ".fq.gz",
            "paired-end reads",
            "paired end reads",
            "rna-seq reads",
            "rnaseq reads",
            "raw reads",
            "sequencing reads",
            "reads_1",
            "reads_2",
            "r1.fastq",
            "r2.fastq",
        )
    )


def _infer_request_contract(request_text: str, catalog: dict[str, Any]) -> dict[str, Any]:
    text = (request_text or "").lower()
    smoke_prompt = is_direct_skill_smoke_query(request_text)
    required_output_paths = extract_requested_output_paths(request_text)
    downstream_capability_hints = _contract_prompt_intent.downstream_capability_hints_from_text(request_text)
    caps: list[str] = []

    def _add(cap: str) -> None:
        if cap not in caps:
            caps.append(cap)

    # Context keywords used to gate bare "protein" from triggering protein_analysis
    # in phylogenetics contexts like "protein sequences for phylogenetics".
    _PHYLO_CONTEXT_KW = ("phylogenet", "tree building", "bootstrap",
                          "evolutionary relationship", "iqtree", "raxml", "mafft", "newick")
    _EXPLICIT_PROTEIN_KW = ("blastp", "hmmscan", "pfam", "domain annotation",
                             "homology search", "protein analysis", "proteomics")

    for cap in infer_capabilities_from_text(text, catalog, enabled_only=True):
        _add(cap)

    # Context-aware filter: catalog keyword "protein" matches phylogenetics prompts
    # like "protein sequences for phylogenetics", but protein_analysis is wrong there.
    # Remove it unless an explicit protein-analysis keyword is present.
    if ("protein_analysis" in caps
            and any(pk in text for pk in _PHYLO_CONTEXT_KW)
            and not any(epk in text for epk in _EXPLICIT_PROTEIN_KW)):
        caps.remove("protein_analysis")

    if any(k in text for k in ("fastqc", "quality control", "qc")):
        _add("fastqc")
    if _is_structural_variant_prompt(text):
        _add("structural_variant_calling")
        _add("alignment")
        _add("reference_inputs")
    if _is_spatial_transcriptomics_prompt(text):
        _add("spatial_transcriptomics")
        _add("single_cell_analysis")
    if _is_metabolomics_prompt(text):
        _add("metabolomics")
        _add("differential_analysis")
        _add("group_comparison")
    if _is_proteomics_prompt(text):
        for cap in ("protein_analysis", "annotation"):
            if cap in caps:
                caps.remove(cap)
        _add("proteomics")
        _add("differential_analysis")
        _add("group_comparison")
    if _contract_prompt_intent.requests_alignment(text):
        _add("alignment")
    elif "differential_analysis" in caps and _has_raw_rnaseq_reads(text):
        _add("alignment")
    # Use word-boundary matching for tool names that are substrings of common
    # words (e.g. "rmats" appears inside "formats").
    _SPLICING_KW_PLAIN = ("splicing", "majiq", "dexseq", "spladder", "whippet")
    _SPLICING_KW_REGEX = (r"\brmats\b",)
    if any(k in text for k in _SPLICING_KW_PLAIN) or any(re.search(p, text) for p in _SPLICING_KW_REGEX):
        _add("splicing_analysis")
        _add("alignment")
    if requires_reference_inputs(text):
        _add("reference_inputs")
    if any(
        k in text
        for k in (
            "differential expression",
            "differentially expressed",
            "differentially express",
            "differential gene expression",
            "deseq2",
            "edger",
            "limma",
        )
    ):
        _add("differential_analysis")
    if _contract_prompt_intent.is_count_matrix_de_request(text):
        _add("differential_analysis")
    if any(k in text for k in ("quantification", "quantify", "salmon", "kallisto", "transcript quant", "transcriptome")):
        _add("quantification")
    if _is_completed_output_report_prompt(request_text):
        _add("run_reporting")
    # "compare" alone is too broad -- "compared to their ancestor" (evolution) isn't
    # a group comparison.  Use specific group-indicating keywords instead.
    if _contract_prompt_intent.requests_group_comparison(text):
        _add("group_comparison")
    # Specific protein-analysis keywords always trigger protein_analysis + annotation.
    # The bare word "protein" alone is gated by phylogenetics context -- "protein sequences
    # for phylogenetics" is about tree inference, not protein annotation.
    if any(k in text for k in _EXPLICIT_PROTEIN_KW) and not _is_proteomics_prompt(text):
        _add("protein_analysis")
        _add("annotation")
    elif "protein" in text and not any(pk in text for pk in _PHYLO_CONTEXT_KW) and not _is_proteomics_prompt(text):
        _add("protein_analysis")
        _add("annotation")
    # "variant" alone is too broad -- "variant annotation" should trigger annotation,
    # not variant_calling.  Gate bare "variant" by annotation context.
    _ANNOTATION_CONTEXT_KW = ("annotation", "annotate", "snpeff", "snpsift", "clinvar",
                               "functional impact", "variant annotation")
    if any(k in text for k in ("germline", "haplotypecaller", "freebayes", "snv", "snp", "indel",
                                "variant call", "variant detection")):
        _add("variant_calling")
    elif "variant" in text and not any(ak in text for ak in _ANNOTATION_CONTEXT_KW):
        _add("variant_calling")
    if _is_somatic_variant_prompt(text):
        _add("variant_calling")
    if _has_tumor_normal_comparison(text):
        _add("group_comparison")

    # Fix #19: bacterial-evolution shared-variant intent. Require the full
    # assembly → alignment → variant-calling → annotation → shared-export
    # capability chain so the stepwise contract check does not pass until the
    # planner has emitted the final variants_shared.csv step. Without this the
    # planner would accept an early "done" after a single-lineage annotation
    # and the evaluator would see an empty selected/final/ directory.
    if _is_shared_evolution_variant_prompt(text):
        for cap in (
            "genome_assembly",
            "alignment",
            "variant_calling",
            "annotation",
            "reference_inputs",
                "shared_variant_export",
        ):
            _add(cap)

    if _annotation_is_reference_asset_context(text, caps):
        caps = [cap for cap in caps if cap != "annotation"]

    if _contract_prompt_intent.is_precounted_scanpy_request(text) and not _contract_prompt_intent.has_explicit_single_cell_diff_request(text):
        for cap in ("differential_analysis", "group_comparison"):
            if cap in caps:
                caps.remove(cap)

    if _is_long_read_rna_prompt(text) and _annotation_explicitly_unavailable(text):
        caps = [cap for cap in caps if cap not in {"annotation", "quantification"}]
        _add("alignment")
        _add("reference_inputs")
        hint = "annotation_limited_long_read_rna"
        if hint not in downstream_capability_hints:
            downstream_capability_hints.append(hint)

    explicit_tools: list[str] = infer_tool_hints_from_text(text, catalog, enabled_only=True)
    if _is_spatial_transcriptomics_prompt(text) and "spatial_transcriptomics_workflow" not in explicit_tools:
        explicit_tools.append("spatial_transcriptomics_workflow")
    if _is_metabolomics_prompt(text) and "metabolomics_diff_abundance" not in explicit_tools:
        explicit_tools.append("metabolomics_diff_abundance")
    if _is_proteomics_prompt(text) and "proteomics_diff_abundance" not in explicit_tools:
        explicit_tools.append("proteomics_diff_abundance")
    tool_token_map = _contract_prompt_intent.tool_hint_aliases()
    for raw_token, normalized in tool_token_map.items():
        # Use word boundary to avoid false matches (e.g. "rmats" in "formats")
        if re.search(rf"\b{re.escape(raw_token.lower())}\b", text) and normalized not in explicit_tools:
            explicit_tools.append(normalized)
    normalized_explicit_tools: list[str] = []
    for hint in explicit_tools:
        normalized_hint = tool_token_map.get(str(hint).strip().lower(), str(hint).strip())
        if normalized_hint and normalized_hint not in normalized_explicit_tools:
            normalized_explicit_tools.append(normalized_hint)
    explicit_tools = normalized_explicit_tools
    for m in re.finditer(r"`([^`]+)`", request_text or ""):
        raw = m.group(1).strip().split()[0]
        token = _normalize_contract_hint(raw)
        if token and token not in explicit_tools:
            explicit_tools.append(token)
    for p in _extract_paths_from_text(request_text):
        hint = _normalize_contract_hint(p)
        if hint and hint not in explicit_tools:
            explicit_tools.append(hint)
    blocked_tools = _contract_prompt_intent.blocked_tool_hints_from_text(request_text, explicit_tools)
    blocked_tool_set = {str(item).strip().lower() for item in blocked_tools if str(item).strip()}
    explicit_tools = [
        item
        for item in explicit_tools
        if str(item).strip().lower() not in blocked_tool_set
    ]
    required_tools = _contract_prompt_intent.required_tool_hints_from_text(request_text, explicit_tools)
    required_tools = [
        item
        for item in required_tools
        if str(item).strip().lower() not in blocked_tool_set
    ]
    if (
        semantically_requests_long_read_rna_stringtie_pipeline(request_text)
        and "stringtie_quant" not in blocked_tool_set
    ):
        if "minimap2_align" not in blocked_tool_set:
            if "minimap2_align" not in explicit_tools:
                explicit_tools.append("minimap2_align")
            if "minimap2_align" not in required_tools:
                required_tools.append("minimap2_align")
        if "stringtie_quant" not in explicit_tools:
            explicit_tools.append("stringtie_quant")
        if "stringtie_quant" not in required_tools:
            required_tools.append("stringtie_quant")
    if (
        semantically_requests_stringtie_quant(request_text)
        and "stringtie_quant" not in blocked_tool_set
    ):
        if "stringtie_quant" not in explicit_tools:
            explicit_tools.append("stringtie_quant")
        if "stringtie_quant" not in required_tools:
            required_tools.append("stringtie_quant")
    if (
        _is_spatial_transcriptomics_prompt(request_text)
        and "spatial_transcriptomics_workflow" not in blocked_tool_set
        and "spatial_transcriptomics_workflow" not in required_tools
    ):
        required_tools.append("spatial_transcriptomics_workflow")
    if (
        _is_metabolomics_prompt(request_text)
        and "metabolomics_diff_abundance" not in blocked_tool_set
        and "metabolomics_diff_abundance" not in required_tools
    ):
        required_tools.append("metabolomics_diff_abundance")
    if (
        _is_proteomics_prompt(request_text)
        and "proteomics_diff_abundance" not in blocked_tool_set
        and "proteomics_diff_abundance" not in required_tools
    ):
        required_tools.append("proteomics_diff_abundance")
    if _contract_prompt_intent.is_count_matrix_de_request(request_text) and not any(
        hint in explicit_tools or hint in required_tools
        for hint in ("deseq2_run", "edger_run", "limma_voom_run", "dexseq_run")
    ):
        explicit_tools.append("deseq2_run")
        required_tools.append("deseq2_run")
    if _is_completed_output_report_prompt(request_text):
        preferred_report_tool = ""
        if "multiqc_report" in explicit_tools or "multiqc_report" in required_tools or "multiqc" in text:
            preferred_report_tool = "multiqc_report"
        elif "quarto_report" in explicit_tools or "quarto_report" in required_tools or "quarto" in text:
            preferred_report_tool = "quarto_report"
        explicit_tools = [
            hint
            for hint in explicit_tools
            if hint not in {"fastqc", "fastqc_run"}
        ]
        required_tools = [
            hint
            for hint in required_tools
            if hint not in {"fastqc", "fastqc_run"}
        ]
        if preferred_report_tool:
            if preferred_report_tool not in explicit_tools:
                explicit_tools.append(preferred_report_tool)
            if preferred_report_tool not in required_tools:
                required_tools.append(preferred_report_tool)
        caps = [
            cap
            for cap in caps
            if cap not in {"fastqc", "alignment", "reference_inputs", "quantification"}
        ]
        if "run_reporting" not in caps:
            caps.insert(0, "run_reporting")
    caps = _contract_prompt_intent.strip_downstream_context_capabilities(
        caps,
        downstream_capability_hints=downstream_capability_hints,
        explicit_tools=explicit_tools,
        required_tools=required_tools,
    )
    caps = _contract_prompt_intent.strip_upstream_capabilities_for_direct_wrapper_prompt(
        request_text,
        caps,
        explicit_tools=explicit_tools,
        required_tools=required_tools,
    )
    caps = _contract_prompt_intent.strip_tool_family_false_positive_capabilities(
        request_text,
        caps,
        explicit_tools=explicit_tools,
        required_tools=required_tools,
    )

    if smoke_prompt:
        smoke_required = required_tools or explicit_tools
        return {
            "must_include_capabilities": [],
            "explicit_tool_hints": explicit_tools[:12],
            "required_tool_hints": smoke_required[:12],
            "required_output_paths": required_output_paths[:12],
            "blocked_tool_hints": blocked_tools[:12],
            "downstream_capability_hints": downstream_capability_hints[:12],
        }

    return {
        "must_include_capabilities": caps,
        "explicit_tool_hints": explicit_tools[:12],
        "required_tool_hints": required_tools[:12],
        "required_output_paths": required_output_paths[:12],
        "blocked_tool_hints": blocked_tools[:12],
        "downstream_capability_hints": downstream_capability_hints[:12],
    }


def _annotation_is_reference_asset_context(text: str, caps: list[str]) -> bool:
    """Return whether ``annotation`` means reference metadata, not workflow.

    RNA-seq DE prompts often say "reference genome and annotation" to describe
    a GTF/GFF input. That should require ``reference_inputs`` and the DE chain,
    but it should not force a separate functional/variant annotation step.
    """

    if "annotation" not in caps or "differential_analysis" not in caps:
        return False
    explicit_annotation_workflow_terms = (
        "annotate ",
        "annotate the",
        "annotation workflow",
        "functional annotation",
        "variant annotation",
        "protein annotation",
        "domain annotation",
        "prokka",
        "snpeff",
        "snp eff",
        "vep",
        "hmmscan",
        "blastp",
    )
    if any(term in text for term in explicit_annotation_workflow_terms):
        return False
    reference_asset_terms = (
        "reference genome and annotation",
        "reference genome, annotation",
        "reference annotation",
        "gene annotation",
        "annotation gtf",
        "annotation gff",
        "gtf",
        "gff",
    )
    return any(term in text for term in reference_asset_terms)


def _is_empty_contract(contract: dict[str, Any]) -> bool:
    caps = contract.get("must_include_capabilities", []) if isinstance(contract, dict) else []
    hints = contract.get("explicit_tool_hints", []) if isinstance(contract, dict) else []
    required = contract.get("required_tool_hints", []) if isinstance(contract, dict) else []
    return (not caps) and (not hints) and (not required)


# Reference-path extraction and reference-resolution helpers are re-exported
# from contract_reference_utils to keep this module as the stable import
# surface while extracting coherent deterministic seams.


# ---------------------------------------------------------------------------
# Planned output / missing input helpers
# ---------------------------------------------------------------------------
