from __future__ import annotations

import json
import re
from typing import Any

from bio_harness.core.analysis_spec_support import (
    AnalysisSpecSchema,
    CANONICAL_ANALYSIS_TYPES,
    HIGH_IMPACT_CAPABILITIES,
    ParameterRecommendation,
    _canonicalize_analysis_type,
    _dedupe,
    is_direct_skill_smoke_query,
)
from bio_harness.core.benchmark_policy import (
    is_blind_bioagentbench_policy,
    normalize_benchmark_policy,
)
from bio_harness.core.analysis_spec_grounding import (
    _apply_blind_rna_seq_alignment_preferences,
    _grounded_chosen_method_hint,
)
from bio_harness.core.analysis_spec_seed import _profile_seed
from bio_harness.core.request_scope import (
    infer_explicit_requested_skill,
    requested_skill_analysis_type,
    semantically_requests_stringtie_quant,
)
from bio_harness.core.analysis_spec_data import (
    analysis_spec_preference_profile,
    discover_data_files,
)
from bio_harness.core.execution_mode import build_execution_contract, infer_input_mode
from bio_harness.core.wrapper_contracts import (
    normalize_wrapper_argument_value,
    wrapper_has_contract,
    wrapper_lock_requires_evidence,
    wrapper_supports_input_mode,
)
from bio_harness.core.literature_planning_support import (
    literature_planning_support_brief_lines,
)
from bio_harness.core.request_output_intent import (
    extract_requested_deliverable_paths,
    extract_requested_output_paths,
)
from bio_harness.core.tool_output_bindings import requested_output_bindings_for_tool
from bio_harness.core.tool_registry import default_tool_registry

__all__ = [
    "AnalysisSpecSchema",
    "CANONICAL_ANALYSIS_TYPES",
    "HIGH_IMPACT_CAPABILITIES",
    "ParameterRecommendation",
    "_canonicalize_analysis_type",
    "_profile_seed",
    "analysis_spec_preference_profile",
    "build_analysis_brief",
    "deterministic_analysis_spec",
    "discover_data_files",
    "infer_analysis_type",
    "normalize_analysis_spec",
    "should_generate_analysis_review",
]


_STRUCTURAL_VARIANT_TERMS = (
    "structural variant",
    "structural variants",
    "structural variation",
)
_STRUCTURAL_VARIANT_CHANGE_TERMS = (
    "structural change",
    "structural changes",
    "big structural change",
    "big structural changes",
    "large structural change",
    "large structural changes",
)
_STRUCTURAL_VARIANT_EVENT_TERMS = (
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
_STRUCTURAL_VARIANT_CONTEXT = (
    "long-read",
    "long read",
    "long sequencing read",
    "long sequencing reads",
    "nanopore",
    "pacbio",
    "bam",
    "cram",
    "aligned bam",
    "aligned cram",
    "reference genome",
    "reference fasta",
)
_STRUCTURAL_VARIANT_REFERENCE_TERMS = (
    "reference genome",
    "reference fasta",
    "compared to the reference",
    "compared to reference",
    "against the reference",
    "relative to the reference",
)
_LONG_READ_CONTEXT_TERMS = (
    "long-read",
    "long read",
    "nanopore",
    "oxford nanopore",
    "ont",
    "pacbio",
    "hifi",
    "direct-rna",
    "direct rna",
)
_LONG_READ_ASSEMBLY_TERMS = (
    "assemble",
    "assembly",
    "contig",
    "contigs",
    "scaffold",
    "scaffolds",
)
_LONG_READ_RNA_TERMS = (
    "isoform",
    "isoforms",
    "transcript isoform",
    "spliced",
    "splice-aware",
    "splice aware",
    "direct-rna",
    "direct rna",
)
_VARIANT_ANNOTATION_KEYWORDS = (
    "snpeff",
    "snpsift",
    "clinvar",
    "variant annotation",
    "functional impact",
)
_SOMATIC_CONTEXT_TERMS = (
    "somatic",
    "tumor",
    "tumour",
    "mutect",
    "tumor-normal",
    "tumor normal",
    "paired normal",
    "matched normal",
)
_SOMATIC_CALL_TERMS = (
    "variant",
    "call",
    "snv",
    "mutation",
    "normal",
)
_SPATIAL_CORE_TERMS = (
    "spatial transcriptomics",
    "spatial gene expression",
    "spatial omics",
    "visium",
    "spatial domain",
    "spatial domains",
)
_SPATIAL_CONTEXT_TERMS = (
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
)
_METABOLOMICS_CORE_TERMS = (
    "metabolomics",
    "metabolite abundance",
    "metabolite abundances",
    "metabolite intensity",
    "metabolite intensities",
    "feature intensity",
    "feature intensities",
    "feature table",
    "peak table",
    "untargeted metabolomics",
    "metabolic profiling",
    "lc-ms",
    "lcms",
)
_METABOLOMICS_ASSAY_TERMS = (
    "metabolomics",
    "metabolite",
    "metabolites",
    "mass spec",
    "mass spectrometry",
    "metabolic profiling",
    "lc-ms",
    "lcms",
)
_PROTEOMICS_CORE_TERMS = (
    "proteomics",
    "protein abundance",
    "protein abundances",
    "protein intensity",
    "protein intensities",
    "differential abundance",
    "differential protein abundance",
    "abundance matrix",
    "intensity matrix",
    "lfq",
    "label-free quantification",
)
_PROTEOMICS_CONTEXT_TERMS = (
    "protein expression",
    "protein expression data",
    "which proteins are different",
    "proteins are different",
    "two groups",
    "control",
    "treatment",
    "condition",
    "group",
    "metadata",
)


def _is_spatial_transcriptomics_request(query_l: str) -> bool:
    """Return whether the prompt is explicitly about processed spatial omics."""

    text_l = str(query_l or "").lower()
    if any(token in text_l for token in _SPATIAL_CORE_TERMS):
        return True
    has_spatial = "spatial" in text_l
    has_context = any(token in text_l for token in _SPATIAL_CONTEXT_TERMS)
    has_expression = any(
        token in text_l
        for token in (
            "gene expression",
            "transcriptomics",
            "marker genes",
            "regions are different",
            "regions differ",
            "genes define them",
        )
    )
    return has_spatial and (has_context or has_expression)


def _is_metabolomics_request(query_l: str) -> bool:
    """Return whether the prompt is explicitly about processed metabolomics."""

    text_l = str(query_l or "").lower()
    if any(token in text_l for token in _METABOLOMICS_CORE_TERMS):
        return True
    has_assay_anchor = any(token in text_l for token in _METABOLOMICS_ASSAY_TERMS)
    if not has_assay_anchor:
        return False
    has_feature_table = any(
        token in text_l
        for token in (
            "feature table",
            "peak table",
            "feature intensity",
            "intensity matrix",
            "differential metabolite analysis",
            "differential metabolomics analysis",
        )
    )
    if has_feature_table:
        return True
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
        )
    )
    return has_comparison


def _is_proteomics_request(query_l: str) -> bool:
    """Return whether the prompt is explicitly about processed proteomics."""

    text_l = str(query_l or "").lower()
    strong_terms = (
        "proteomics",
        "protein abundance",
        "protein abundances",
        "protein intensity",
        "protein intensities",
        "lfq",
        "label-free quantification",
    )
    if any(token in text_l for token in strong_terms):
        return True
    has_matrix_or_da_term = any(
        token in text_l
        for token in (
            "abundance matrix",
            "intensity matrix",
            "differential abundance",
            "differential protein abundance",
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
            "abundance",
            "expression",
            "groups",
        )
    )
    return has_protein_context and has_comparison


def _contract_caps(contract: dict[str, Any] | None) -> set[str]:
    if not isinstance(contract, dict):
        return set()
    return {
        str(cap).strip()
        for cap in (contract.get("must_include_capabilities", []) or [])
        if str(cap).strip()
    }


def _normalize_explicit_intent_value(value: Any) -> Any:
    """Coerce one prompt token into a stable scalar when possible."""

    if isinstance(value, str):
        token = value.strip().strip(",.;")
        if not token:
            return ""
        lowered = token.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        if re.fullmatch(r"-?[0-9]+", token):
            try:
                return int(token)
            except ValueError:
                return token
        if re.fullmatch(r"-?(?:[0-9]+\.[0-9]*|\.[0-9]+)", token):
            try:
                return float(token)
            except ValueError:
                return token
        return token
    return value


def _extract_explicit_argument_values(
    *,
    user_query: str,
    tool_name: str,
) -> dict[str, Any]:
    """Return explicit tool argument values mentioned directly in the request."""

    registry = default_tool_registry()
    values: dict[str, Any] = {}
    for param_name in sorted(registry.parameter_schema_for(tool_name)):
        token = str(param_name or "").strip()
        if not token:
            continue
        match = re.search(
            rf"\b{re.escape(token)}\b\s*(?:=|to)?\s*(?P<value>[^,\s;]+)",
            str(user_query or ""),
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        raw_value = str(match.group("value") or "").strip()
        if not raw_value:
            continue
        normalized = _normalize_explicit_intent_value(raw_value)
        if normalized not in ("", None):
            values[token] = normalized
    requested_output_bindings = requested_output_bindings_for_tool(
        tool_name,
        extract_requested_output_paths(user_query),
        registry=registry,
    )
    for param_name, value in requested_output_bindings.items():
        values.setdefault(param_name, value)
    return values


def _normalize_requested_output_paths(
    *,
    user_query: str,
    contract: dict[str, Any] | None,
) -> list[str]:
    """Return ordered requested output paths from the request and contract."""

    raw_values = []
    if isinstance(contract, dict):
        raw_values.extend(contract.get("required_output_paths", []) or [])
    raw_values.extend(extract_requested_output_paths(user_query))
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in raw_values:
        path = str(raw or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return normalized


def _normalize_required_deliverables(
    *,
    user_query: str,
    contract: dict[str, Any] | None,
) -> list[str]:
    """Return ordered requested final deliverables from the request and contract."""

    requested = _normalize_requested_output_paths(user_query=user_query, contract=contract)
    explicit_deliverables = extract_requested_deliverable_paths(user_query)
    deliverable_set = {str(path).strip() for path in explicit_deliverables if str(path).strip()}
    normalized: list[str] = []
    for path in requested:
        if path in deliverable_set or "/final/" in path.replace("\\", "/").lower():
            normalized.append(path)
    for path in explicit_deliverables:
        if path not in normalized:
            normalized.append(path)
    return normalized


def _build_explicit_execution_intent(
    *,
    analysis_type: str,
    user_query: str,
    contract: dict[str, Any] | None,
    available_skill_names: list[str],
    discovered_data_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build explicit execution-intent locks from the request and contract."""

    explicit_skill = infer_explicit_requested_skill(user_query, available_skill_names)
    if not explicit_skill:
        required_hints = [
            str(item).strip()
            for item in ((contract or {}).get("required_tool_hints", []) if isinstance(contract, dict) else [])
            if str(item).strip()
        ]
        if len(required_hints) == 1 and required_hints[0] in set(available_skill_names):
            explicit_skill = required_hints[0]
    if not explicit_skill:
        hints = [
            str(item).strip()
            for item in ((contract or {}).get("explicit_tool_hints", []) if isinstance(contract, dict) else [])
            if str(item).strip()
        ]
        if len(hints) == 1 and hints[0] in set(available_skill_names):
            explicit_skill = hints[0]
    if not explicit_skill:
        return {}
    if not _should_preserve_explicit_skill_lock(
        analysis_type=analysis_type,
        explicit_skill=explicit_skill,
        user_query=user_query,
        contract=contract,
        discovered_data_files=discovered_data_files,
    ):
        return {}

    locked_arguments = _extract_explicit_argument_values(
        user_query=user_query,
        tool_name=explicit_skill,
    )
    requested_output_paths = _normalize_requested_output_paths(
        user_query=user_query,
        contract=contract,
    )
    required_deliverables = _normalize_required_deliverables(
        user_query=user_query,
        contract=contract,
    )
    return {
        "requested_tools": [explicit_skill],
        "locked_tools": [explicit_skill],
        "preserve_existing_values_for_tools": [explicit_skill],
        "locked_argument_values": {
            explicit_skill: locked_arguments,
        }
        if locked_arguments
        else {},
        "preserve_input_paths": True,
        "preserve_output_paths": bool(
            locked_arguments or requested_output_paths or required_deliverables
        ),
    }


def _normalize_explicit_execution_intent(
    intent: Any,
    *,
    analysis_type: str,
    user_query: str,
    contract: dict[str, Any] | None,
    available_skill_names: list[str],
    discovered_data_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize explicit execution-intent state for the analysis spec."""

    normalized = dict(intent) if isinstance(intent, dict) and intent else {}
    if not normalized:
        normalized = _build_explicit_execution_intent(
            analysis_type=analysis_type,
            user_query=user_query,
            contract=contract,
            available_skill_names=available_skill_names,
            discovered_data_files=discovered_data_files,
        )
    if not normalized:
        return {}

    available = {str(name).strip() for name in available_skill_names if str(name).strip()}

    def _normalize_tools(values: Any) -> list[str]:
        raw = values if isinstance(values, list) else [values]
        tools = _dedupe([str(item).strip() for item in raw if str(item).strip()])
        if available:
            tools = [tool for tool in tools if tool in available]
        return tools

    requested_tools = _normalize_tools(normalized.get("requested_tools", []))
    locked_tools = _normalize_tools(normalized.get("locked_tools", []))
    preserve_existing_values_for_tools = _normalize_tools(
        normalized.get("preserve_existing_values_for_tools", [])
    )

    locked_argument_values: dict[str, dict[str, Any]] = {}
    for tool_name, raw_args in (normalized.get("locked_argument_values", {}) or {}).items():
        tool = str(tool_name or "").strip()
        if not tool or (available and tool not in available) or not isinstance(raw_args, dict):
            continue
        cleaned: dict[str, Any] = {}
        for key, value in raw_args.items():
            arg_key = str(key or "").strip()
            if not arg_key:
                continue
            cleaned[arg_key] = normalize_wrapper_argument_value(
                tool,
                arg_key,
                _normalize_explicit_intent_value(value),
            )
        if cleaned:
            locked_argument_values[tool] = cleaned

    if locked_tools:
        allowed_locked_tools = [
            tool
            for tool in locked_tools
            if _should_preserve_explicit_skill_lock(
                analysis_type=analysis_type,
                explicit_skill=tool,
                user_query=user_query,
                contract=contract,
                discovered_data_files=discovered_data_files,
            )
        ]
        if not allowed_locked_tools:
            return {}
        allowed_set = set(allowed_locked_tools)
        locked_tools = allowed_locked_tools
        requested_tools = [tool for tool in requested_tools if tool in allowed_set]
        preserve_existing_values_for_tools = [
            tool for tool in preserve_existing_values_for_tools if tool in allowed_set
        ]
        locked_argument_values = {
            tool: args for tool, args in locked_argument_values.items() if tool in allowed_set
        }

    return {
        "requested_tools": requested_tools,
        "locked_tools": locked_tools,
        "preserve_existing_values_for_tools": preserve_existing_values_for_tools,
        "locked_argument_values": locked_argument_values,
        "preserve_input_paths": bool(normalized.get("preserve_input_paths", False)),
        "preserve_output_paths": bool(normalized.get("preserve_output_paths", False)),
    }


def _query_mentions_exact_skill_name(user_query: str, skill_name: str) -> bool:
    """Return whether the prompt explicitly names the repo wrapper."""

    token = str(skill_name or "").strip().lower()
    if not token:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", str(user_query or "").lower()))


def _should_preserve_explicit_skill_lock(
    *,
    analysis_type: str,
    explicit_skill: str,
    user_query: str,
    contract: dict[str, Any] | None,
    discovered_data_files: list[dict[str, Any]] | None,
) -> bool:
    """Return whether a prompt-level skill mention should lock execution."""

    skill_name = str(explicit_skill or "").strip()
    if not skill_name:
        return False

    inferred_mode = infer_input_mode(
        user_query=user_query,
        analysis_type=analysis_type,
        explicit_tools=[skill_name],
        discovered_data_files=discovered_data_files,
    )
    if wrapper_has_contract(skill_name):
        if not wrapper_lock_requires_evidence(skill_name):
            return True
        return wrapper_supports_input_mode(skill_name, inferred_mode)
    return _query_mentions_exact_skill_name(user_query, skill_name)


def _is_structural_variant_request(query_l: str) -> bool:
    """Return True when the prompt specifically describes structural-variant calling."""
    if any(term in query_l for term in ("sniffles", "sniffles_sv_call")):
        return True
    has_long_read_context = any(term in query_l for term in _STRUCTURAL_VARIANT_CONTEXT)
    has_reference_context = any(term in query_l for term in _STRUCTURAL_VARIANT_REFERENCE_TERMS)
    has_structural_phrase = any(term in query_l for term in _STRUCTURAL_VARIANT_TERMS)
    has_change_phrase = any(term in query_l for term in _STRUCTURAL_VARIANT_CHANGE_TERMS)
    has_event_phrase = any(term in query_l for term in _STRUCTURAL_VARIANT_EVENT_TERMS)
    has_detection_intent = any(
        term in query_l for term in ("call", "detect", "identify", "report", "find", "figure out")
    )
    if has_structural_phrase and has_long_read_context:
        return True
    if has_change_phrase and (has_long_read_context or has_reference_context):
        return True
    return has_event_phrase and has_long_read_context and (has_detection_intent or has_reference_context)


def _is_long_read_assembly_request(query_l: str) -> bool:
    """Return whether the prompt is asking for long-read de novo assembly."""

    has_long_read_context = any(term in query_l for term in _LONG_READ_CONTEXT_TERMS)
    has_assembly_intent = any(term in query_l for term in _LONG_READ_ASSEMBLY_TERMS)
    return has_long_read_context and has_assembly_intent


def _is_long_read_rna_request(query_l: str) -> bool:
    """Return whether the prompt is asking for long-read RNA alignment or isoforms."""

    has_long_read_context = any(term in query_l for term in _LONG_READ_CONTEXT_TERMS)
    has_rna_intent = any(term in query_l for term in _LONG_READ_RNA_TERMS) or (
        "transcript" in query_l and "quantif" in query_l
    )
    return has_long_read_context and has_rna_intent


def _is_variant_annotation_request(query_l: str) -> bool:
    """Return whether the prompt is asking to annotate existing variants."""

    if any(term in query_l for term in _VARIANT_ANNOTATION_KEYWORDS):
        return True
    return "annotat" in query_l and any(
        term in query_l for term in ("variant", "variants", "vcf", "mutation", "mutations")
    )


def _is_somatic_variant_call_request(query_l: str) -> bool:
    """Return whether the prompt is asking to call somatic variants."""

    if _is_variant_annotation_request(query_l):
        return False
    has_somatic_context = any(term in query_l for term in _SOMATIC_CONTEXT_TERMS)
    has_call_intent = any(term in query_l for term in _SOMATIC_CALL_TERMS)
    return has_somatic_context and has_call_intent


def should_generate_analysis_review(user_query: str, contract: dict[str, Any] | None) -> bool:
    """Return ``True`` if the task warrants a pre-execution analysis review."""
    query_l = str(user_query or "").lower()
    if is_direct_skill_smoke_query(query_l):
        return False
    caps = _contract_caps(contract)
    if caps.intersection(HIGH_IMPACT_CAPABILITIES):
        return True
    return any(
        token in query_l
        for token in (
            "variant",
            "salmon",
            "kallisto",
            "transcript",
            "sniffles",
            "structural variant",
            "differential expression",
            "deseq2",
            "edger",
            "limma",
            "splicing",
            "rmats",
            "dexseq",
            "majiq",
        )
    )


def infer_analysis_type(
    user_query: str,
    contract: dict[str, Any] | None,
    available_skill_names: list[str] | None = None,
) -> str:
    """Infer the canonical analysis type from the user query and contract capabilities.

    Returns one of the ``CANONICAL_ANALYSIS_TYPES`` strings, or ``""`` if the
    analysis type cannot be determined.
    """
    query_l = str(user_query or "").lower()
    caps = _contract_caps(contract)

    if is_direct_skill_smoke_query(query_l):
        return "direct_skill_smoke"

    explicit_skill = infer_explicit_requested_skill(user_query, available_skill_names)
    explicit_analysis_type = requested_skill_analysis_type(explicit_skill)
    if explicit_analysis_type:
        return explicit_analysis_type

    if any(
        term in query_l
        for term in (
            "artifact_schema_profile",
            "schema profile",
            "schema json",
            "data dictionary",
            "profile the schema",
            "profile schema",
        )
    ):
        return "artifact_schema_profiling"

    if any(
        term in query_l
        for term in (
            "multiqc_report",
            "quarto_report",
            "report bundle",
            "run report",
            "researcher-facing report",
            "researcher facing report",
            "multiqc",
            "quarto",
        )
    ) and any(term in query_l for term in ("completed run", "completed selected-dir", "selected-dir", "result.json", "run at ")):
        return "run_reporting"

    # Spatial requests should override generic single-cell routing when the
    # prompt is explicit about a processed Visium-style assay.
    if _is_spatial_transcriptomics_request(query_l):
        return "spatial_transcriptomics"
    if _is_metabolomics_request(query_l):
        return "metabolomics"
    if _is_proteomics_request(query_l):
        return "proteomics"

    # Long-read families should override generic capability labels like
    # quantification when the prompt is explicit about assay type.
    if _is_long_read_assembly_request(query_l):
        return "long_read_assembly"
    if _is_long_read_rna_request(query_l):
        return "long_read_rna"

    # ---- Contract-driven detection (highest confidence) ----
    if "splicing_analysis" in caps:
        return "alternative_splicing"
    if "structural_variant_calling" in caps:
        return "structural_variant_calling"
    if "variant_calling" in caps:
        if _is_structural_variant_request(query_l):
            return "structural_variant_calling"
        if any(term in query_l for term in ("evolution", "evolved", "ancestor", "bacteria", "bacterial", "haploid", "isolate")):
            return "bacterial_evolution_variant_calling"
        if any(term in query_l for term in ("giab", "germline", "deepvariant", "haplotypecaller", "nist", "genome in a bottle")):
            return "germline_variant_calling"
        # "variant_calling" from contract may be a false positive when the prompt
        # is about variant *annotation* (e.g. "annotate variants with SnpEff").
        # Check annotation-specific keywords before defaulting to variant_calling.
        if _is_variant_annotation_request(query_l):
            return "variant_annotation"
        if _is_somatic_variant_call_request(query_l):
            return "somatic_variant_calling"
        return "variant_calling"
    if "spatial_transcriptomics" in caps:
        return "spatial_transcriptomics"
    if "metabolomics" in caps:
        return "metabolomics"
    if "proteomics" in caps:
        return "proteomics"
    if "single_cell_analysis" in caps:
        if _is_spatial_transcriptomics_request(query_l):
            return "spatial_transcriptomics"
        return "single_cell_rna_seq"
    if "differential_analysis" in caps:
        if _is_metabolomics_request(query_l):
            return "metabolomics"
        if _is_proteomics_request(query_l):
            return "proteomics"
        # If the query also mentions pathway/enrichment keywords, route to multi_model_dge_pathway
        _PATHWAY_KW = ("pathway", "gsea", "gene set enrichment", "go enrichment", "kegg",
                       "enrichment analysis", "enriched pathway")
        if any(pk in query_l for pk in _PATHWAY_KW):
            return "multi_model_dge_pathway"
        return "rna_seq_differential_expression"
    if "pathway_enrichment" in caps:
        return "multi_model_dge_pathway"
    if "quantification" in caps:
        return "transcript_quantification"
    if "metagenomics_profiling" in caps:
        if any(term in query_l for term in ("viral", "virome", "virus")):
            return "viral_metagenomics"
        return "metagenomics_classification"
    # ---- Keyword-driven detection (for queries without explicit contract) ----

    # Bacterial evolution variant calling
    if any(term in query_l for term in ("evolution", "evolved", "ancestor")) and any(
        term in query_l for term in ("bacteria", "bacterial", "variant", "haploid", "isolate", "e. coli", "ecoli", "mutation")
    ):
        return "bacterial_evolution_variant_calling"

    # Germline variant calling
    if any(term in query_l for term in ("giab", "germline", "deepvariant", "haplotypecaller", "sarek", "genome in a bottle", "hap.py")):
        return "germline_variant_calling"

    # Somatic variant calling
    if _is_variant_annotation_request(query_l):
        return "variant_annotation"

    if _is_somatic_variant_call_request(query_l):
        return "somatic_variant_calling"

    # Structural-variant calling
    if _is_structural_variant_request(query_l):
        return "structural_variant_calling"

    # Differential expression (but pathway+DE combos go to multi_model_dge_pathway)
    if _is_metabolomics_request(query_l):
        return "metabolomics"
    if _is_proteomics_request(query_l):
        return "proteomics"
    if any(
        term in query_l
        for term in (
            "differential expression",
            "differentially expressed",
            "differentially express",
            "differential gene expression",
            "deseq2",
            "pydeseq2",
            "edger",
            "limma",
            "diffexp",
        )
    ):
        _PW_KW = ("pathway", "gsea", "gene set enrichment", "go enrichment", "kegg",
                   "enrichment analysis", "enriched pathway")
        if any(pk in query_l for pk in _PW_KW):
            return "multi_model_dge_pathway"
        return "rna_seq_differential_expression"

    # Transcript quantification
    if semantically_requests_stringtie_quant(user_query):
        return "transcript_quantification"
    if any(term in query_l for term in ("transcript quant", "quantification", "salmon", "kallisto", "transcriptome")):
        return "transcript_quantification"

    # Viral metagenomics (before general metagenomics)
    if any(term in query_l for term in ("viral metagenom", "virome", "virus")) and any(
        term in query_l for term in ("metagenom", "host removal", "minimap", "classif", "identify", "detect")
    ):
        return "viral_metagenomics"

    # Metagenomics
    if any(term in query_l for term in ("metagenom", "kraken", "bracken", "taxonom", "microbial communit", "16s", "shotgun metagenom")):
        return "metagenomics_classification"

    # Spatial transcriptomics
    if _is_spatial_transcriptomics_request(query_l):
        return "spatial_transcriptomics"

    # Single-cell
    if any(term in query_l for term in ("single-cell", "single cell", "scrnaseq", "sc-rna", "scanpy", "seurat", "cellranger", "10x genomics")):
        return "single_cell_rna_seq"

    # Variant annotation / cystic fibrosis style
    if any(term in query_l for term in ("cystic fibrosis", "cftr")):
        return "variant_annotation"

    # Phylogenetics / tree inference
    if any(term in query_l for term in ("phylogenet", "tree inference", "iqtree", "iq-tree", "newick", "multiple sequence alignment", "mafft", "evolutionary relationship")):
        return "phylogenetics"

    # Comparative genomics
    if any(term in query_l for term in ("comparative genomic", "comparative genom", "synteny", "ortholog", "pairwise ani", "average nucleotide identity", "genome comparison", "genome distance")):
        return "comparative_genomics"
    if "ani" in query_l and "genome" in query_l:
        return "comparative_genomics"
    if "compare" in query_l and "genome" in query_l:
        return "comparative_genomics"

    # Pathway / DGE analysis (Alzheimer mouse style)
    if any(term in query_l for term in ("pathway", "gsea", "gene set enrichment", "go enrichment", "kegg", "alzheimer", "neurodegenerat")):
        return "multi_model_dge_pathway"

    # Alternative splicing (keyword fallback)
    if any(term in query_l for term in ("splicing", "rmats", "dexseq", "majiq", "alternative splicing")):
        return "alternative_splicing"

    # General variant calling (keyword fallback)
    if any(term in query_l for term in ("variant call", "freebayes", "gatk", "bcftools call", "varscan")):
        return "variant_calling"

    return "generic_analysis"


def deterministic_analysis_spec(
    user_query: str,
    contract: dict[str, Any] | None = None,
    available_skill_names: list[str] | None = None,
    discovered_data_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    skill_names = [str(name).strip() for name in (available_skill_names or []) if str(name).strip()]
    analysis_type = infer_analysis_type(user_query, contract, skill_names)
    seed = _profile_seed(analysis_type, user_query, skill_names)
    base = {
        "analysis_type": analysis_type,
        **seed,
    }
    return normalize_analysis_spec(
        base,
        user_query=user_query,
        contract=contract,
        available_skill_names=skill_names,
        discovered_data_files=discovered_data_files,
    )


def normalize_analysis_spec(
    raw: dict[str, Any] | None,
    *,
    user_query: str,
    contract: dict[str, Any] | None = None,
    available_skill_names: list[str] | None = None,
    benchmark_policy: str | None = None,
    discovered_data_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    skill_names = [str(name).strip() for name in (available_skill_names or []) if str(name).strip()]
    available = {name for name in skill_names if name}
    analysis_type = infer_analysis_type(user_query, contract, list(available))
    raw_analysis_hint = ""
    if isinstance(raw, dict):
        raw_analysis_hint = str(raw.get("analysis_type", "") or "").strip()
    seed_analysis_type = (
        _canonicalize_analysis_type(raw_analysis_hint, analysis_type)
        if raw_analysis_hint
        else analysis_type
    )
    fallback_seed = _profile_seed(seed_analysis_type, user_query, list(available))
    fallback = {"analysis_type": seed_analysis_type, **fallback_seed}
    payload = dict(fallback)
    if isinstance(raw, dict):
        payload.update({k: v for k, v in raw.items() if v is not None})
    if benchmark_policy is not None:
        payload["benchmark_policy"] = normalize_benchmark_policy(benchmark_policy)
    elif "benchmark_policy" in payload:
        payload["benchmark_policy"] = normalize_benchmark_policy(payload.get("benchmark_policy"))
    raw_analysis_type = str(payload.get("analysis_type", "") or fallback.get("analysis_type", "generic_analysis")).strip() or "generic_analysis"
    payload["analysis_type"] = _canonicalize_analysis_type(raw_analysis_type, analysis_type)
    requested_output_paths = payload.get("requested_output_paths", [])
    if not isinstance(requested_output_paths, list) or not requested_output_paths:
        requested_output_paths = _normalize_requested_output_paths(
            user_query=user_query,
            contract=contract,
        )
    else:
        requested_output_paths = [
            str(path).strip()
            for path in requested_output_paths
            if str(path).strip()
        ]
    payload["requested_output_paths"] = requested_output_paths
    required_deliverables = payload.get("required_deliverables", [])
    if not isinstance(required_deliverables, list) or not required_deliverables:
        required_deliverables = _normalize_required_deliverables(
            user_query=user_query,
            contract=contract,
        )
    else:
        required_deliverables = [
            str(path).strip()
            for path in required_deliverables
            if str(path).strip()
        ]
    payload["required_deliverables"] = required_deliverables
    if discovered_data_files is not None and "discovered_data_files" not in payload:
        payload["discovered_data_files"] = discovered_data_files
    payload["explicit_execution_intent"] = _normalize_explicit_execution_intent(
        payload.get("explicit_execution_intent", {}),
        analysis_type=str(payload.get("analysis_type", "") or "").strip(),
        user_query=user_query,
        contract=contract,
        available_skill_names=skill_names,
        discovered_data_files=payload.get("discovered_data_files", discovered_data_files),
    )
    payload["biological_objective"] = str(payload.get("biological_objective", "") or fallback.get("biological_objective", "")).strip()
    for key in (
        "context_facts",
        "candidate_methods",
        "preferred_tools",
        "discouraged_tools",
        "acceptance_checks",
        "rerun_triggers",
        "source_provenance",
        "open_risks",
    ):
        values = payload.get(key, [])
        if not isinstance(values, list):
            values = [values]
        cleaned = _dedupe([str(v).strip() for v in values if str(v).strip()])
        if available and key in {"candidate_methods", "preferred_tools", "discouraged_tools"}:
            cleaned = [item for item in cleaned if item in available]
        payload[key] = cleaned
    plan_skeleton = payload.get("plan_skeleton", [])
    if not isinstance(plan_skeleton, list):
        plan_skeleton = []
    if is_blind_bioagentbench_policy(payload.get("benchmark_policy")):
        # Keep benchmark-mode assay guidance compact and tool-centric. Model-
        # emitted skeletons that expand into path-heavy executable pseudo-plans
        # increase token pressure and leak too much runtime detail into later
        # planning stages. The deterministic assay seed already provides the
        # compact structure we want in blind benchmark modes.
        plan_skeleton = list(fallback.get("plan_skeleton", []) or [])
    payload["plan_skeleton"] = plan_skeleton
    if (
        is_blind_bioagentbench_policy(payload.get("benchmark_policy"))
        and str(payload.get("analysis_type", "") or "").strip() == "germline_variant_calling"
    ):
        payload["preferred_tools"] = [
            tool
            for tool in (payload.get("preferred_tools", []) or [])
            if str(tool).strip() != "bash_run"
        ]
        payload["acceptance_checks"] = [
            check
            for check in (payload.get("acceptance_checks", []) or [])
            if "hap.py" not in str(check).lower()
        ]
        payload["context_facts"] = _dedupe(
            list(payload.get("context_facts", []) or [])
            + [
                "Blind benchmark mode forbids using truth-set benchmarking inside the run; emit the requested variant-call deliverable only.",
            ]
        )
        payload["plan_skeleton"] = [
            step
            for step in (payload.get("plan_skeleton", []) or [])
            if not (
                isinstance(step, (list, tuple))
                and len(step) >= 3
                and str(step[0]).strip() == "bash_run"
                and "hap.py" in json.dumps(step[2], ensure_ascii=True).lower()
            )
        ]
    if str(payload.get("analysis_type", "") or "").strip() == "multi_model_dge_pathway":
        if not available or "bash_run" in available:
            payload["chosen_method"] = "bash_run"
            payload["preferred_tools"] = ["bash_run"]
            payload["candidate_methods"] = ["bash_run"]
            discouraged = [str(x).strip() for x in (payload.get("discouraged_tools", []) or []) if str(x).strip()]
            for tool_name in ("dexseq_run", "deseq2_run", "edger_run", "limma_voom_run"):
                if not available or tool_name in available:
                    discouraged.append(tool_name)
            payload["discouraged_tools"] = _dedupe(discouraged)
            payload["context_facts"] = _dedupe(
                list(payload.get("context_facts", []) or [])
                + [
                    "Use repo-local helper scripts that already exist in the project when they implement the requested multi-model comparison.",
                    "The repo-local compare_pathways.py helper under bio_harness/pipeline_scripts is available for real multi-model DE plus KEGG comparison.",
                    "Do not replace this assay type with exon-level or count-only DE wrappers.",
                    "Do not fabricate placeholder pathway databases, toy pathway names, or mock enrichment results.",
                    "Do not guess missing intermediate files; downstream steps must consume concrete outputs produced by prior steps.",
                ]
            )
    chosen_method = str(payload.get("chosen_method", "") or "").strip()
    if available and chosen_method:
        if all(token.strip() in available for token in chosen_method.split("+")):
            chosen_method = " + ".join([token.strip() for token in chosen_method.split("+") if token.strip()])
        elif chosen_method not in available:
            chosen_method = ""
    if not chosen_method:
        preferred = payload.get("preferred_tools", [])
        chosen_method = preferred[0] if preferred else str(fallback.get("chosen_method", "") or "").strip()
        if available and chosen_method and chosen_method not in available and " + " not in chosen_method:
            chosen_method = ""
    explicit_intent = (
        payload.get("explicit_execution_intent", {})
        if isinstance(payload.get("explicit_execution_intent", {}), dict)
        else {}
    )
    locked_tools = [
        str(tool).strip()
        for tool in (explicit_intent.get("locked_tools", []) or [])
        if str(tool).strip()
    ]
    if locked_tools:
        chosen_method = locked_tools[0]
        payload["preferred_tools"] = _dedupe(locked_tools + list(payload.get("preferred_tools", []) or []))
    payload["chosen_method"] = chosen_method
    param_items = payload.get("parameter_profile", [])
    if not isinstance(param_items, list):
        param_items = []
    normalized_params: list[dict[str, Any]] = []
    for item in param_items:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "")).strip()
        if available and tool_name and tool_name not in available:
            continue
        settings = item.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}
        normalized_params.append(
            {
                "tool_name": tool_name,
                "settings": settings,
                "rationale": str(item.get("rationale", "") or "").strip(),
            }
        )
    payload["parameter_profile"] = normalized_params
    protocol_grounding = payload.get("protocol_grounding", {})
    if not isinstance(protocol_grounding, dict):
        protocol_grounding = {}
    payload["protocol_grounding"] = protocol_grounding
    _apply_blind_rna_seq_alignment_preferences(payload, available=available)
    payload["chosen_method"] = _grounded_chosen_method_hint(
        analysis_type=str(payload.get("analysis_type", "") or ""),
        chosen_method=str(payload.get("chosen_method", "") or "").strip(),
        preferred_tools=list(payload.get("preferred_tools", []) or []),
        protocol_grounding=protocol_grounding,
        available=available,
    )
    execution_contract = build_execution_contract(
        analysis_type=str(payload.get("analysis_type", "") or "").strip(),
        user_query=user_query,
        chosen_method=str(payload.get("chosen_method", "") or "").strip(),
        contract=contract,
        explicit_execution_intent=explicit_intent,
        available_skill_names=skill_names,
        discovered_data_files=payload.get("discovered_data_files", discovered_data_files),
    )
    payload["execution_contract"] = execution_contract
    if execution_contract:
        protocol_grounding.setdefault(
            "analysis_family",
            str(execution_contract.get("analysis_family", "") or "").strip(),
        )
        protocol_grounding.setdefault(
            "input_mode",
            str(execution_contract.get("input_mode", "") or "").strip(),
        )
        protocol_grounding.setdefault(
            "execution_mode",
            str(execution_contract.get("execution_mode", "") or "").strip(),
        )
        protocol_grounding.setdefault(
            "compatible_tools",
            [
                str(tool).strip()
                for tool in (execution_contract.get("compatible_tools", []) or [])
                if str(tool).strip()
            ],
        )
    validated = AnalysisSpecSchema(**payload)
    return validated.model_dump()


def build_analysis_brief(spec: dict[str, Any] | None) -> str:
    if not isinstance(spec, dict):
        return ""
    lines: list[str] = []
    benchmark_policy = str(spec.get("benchmark_policy", "") or "").strip()
    hide_protocol_details = is_blind_bioagentbench_policy(benchmark_policy)
    analysis_type = str(spec.get("analysis_type", "") or "").strip()
    chosen_method = str(spec.get("chosen_method", "") or "").strip()
    objective = str(spec.get("biological_objective", "") or "").strip()
    if analysis_type:
        lines.append(f"analysis_type={analysis_type}")
    if objective:
        lines.append(f"objective={objective}")
    if chosen_method:
        lines.append(f"chosen_method={chosen_method}")
    preferred = [str(x).strip() for x in (spec.get("preferred_tools", []) or []) if str(x).strip()]
    if preferred:
        lines.append("preferred_tools=" + ", ".join(preferred[:6]))
    discouraged = [str(x).strip() for x in (spec.get("discouraged_tools", []) or []) if str(x).strip()]
    if discouraged:
        lines.append("discouraged_tools=" + ", ".join(discouraged[:6]))
    checks = [str(x).strip() for x in (spec.get("acceptance_checks", []) or []) if str(x).strip()]
    if checks:
        lines.append("acceptance_checks=" + " | ".join(checks[:3]))
    params = []
    for item in (spec.get("parameter_profile", []) or [])[:4]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool_name", "")).strip()
        settings = item.get("settings", {})
        if not tool or not isinstance(settings, dict) or not settings:
            continue
        kv = ", ".join(f"{k}={v}" for k, v in list(settings.items())[:4])
        params.append(f"{tool}({kv})")
    if params:
        lines.append("parameter_hints=" + "; ".join(params))
    required_deliverables = [
        str(path).strip()
        for path in (spec.get("required_deliverables", []) or [])
        if str(path).strip()
    ]
    if required_deliverables:
        lines.append("required_deliverables=" + " | ".join(required_deliverables[:4]))
        lines.append(
            "deliverable_policy=Requested final deliverables belong in `final_deliverables` or step `deliverables`, not undocumented tool arguments."
        )
    execution_contract = spec.get("execution_contract", {}) if isinstance(spec.get("execution_contract", {}), dict) else {}
    input_mode = str(execution_contract.get("input_mode", "") or "").strip()
    execution_mode = str(execution_contract.get("execution_mode", "") or "").strip()
    if input_mode:
        lines.append(f"input_mode={input_mode}")
    if execution_mode:
        lines.append(f"execution_mode={execution_mode}")
    plan_skeleton = spec.get("plan_skeleton", [])
    if isinstance(plan_skeleton, list) and plan_skeleton:
        skeleton_lines = []
        for idx, entry in enumerate(plan_skeleton, 1):
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                tool, purpose = entry[0], entry[1]
                skeleton_lines.append(f"  {idx}. {tool} ({purpose})")
            elif isinstance(entry, dict):
                tool = entry.get("tool_name", "bash_run")
                purpose = entry.get("purpose", "")
                skeleton_lines.append(f"  {idx}. {tool} ({purpose})")
        if skeleton_lines:
            lines.append("plan_skeleton=YOUR PLAN MUST include these tools in this order:\n" + "\n".join(skeleton_lines))

    # Include graph-suggested pipeline when available
    graph_pipeline_text = str(spec.get("graph_pipeline_text", "") or "").strip()
    if graph_pipeline_text and not plan_skeleton:
        # Only include graph suggestion when no explicit skeleton exists
        lines.append(graph_pipeline_text)

    grounding = spec.get("protocol_grounding", {}) if isinstance(spec.get("protocol_grounding", {}), dict) else {}
    if grounding and not hide_protocol_details:
        task_name = str(grounding.get("task_name", "")).strip()
        if task_name:
            lines.append(f"protocol_task={task_name}")
        protocol_input_mode = str(grounding.get("input_mode", "") or "").strip()
        if protocol_input_mode:
            lines.append(f"protocol_input_mode={protocol_input_mode}")
        protocol_execution_mode = str(grounding.get("execution_mode", "") or "").strip()
        if protocol_execution_mode:
            lines.append(f"protocol_execution_mode={protocol_execution_mode}")
        required_tools = [str(x).strip() for x in (grounding.get("required_tools", []) or []) if str(x).strip()]
        if required_tools:
            lines.append("protocol_required_tools=" + ", ".join(required_tools[:6]))
        compatible_tools = [str(x).strip() for x in (grounding.get("compatible_tools", []) or []) if str(x).strip()]
        if compatible_tools:
            lines.append("protocol_compatible_tools=" + ", ".join(compatible_tools[:6]))
        required_signals = [str(x).strip() for x in (grounding.get("required_plan_signals", []) or []) if str(x).strip()]
        if required_signals:
            lines.append("protocol_required_signals=" + ", ".join(required_signals[:6]))
        binding_rules = [str(x).strip() for x in (grounding.get("binding_rules", []) or []) if str(x).strip()]
        if binding_rules:
            lines.append("protocol_rules=" + " | ".join(binding_rules[:3]))
        output_columns = [str(x).strip() for x in (grounding.get("output_columns", []) or []) if str(x).strip()]
        if output_columns:
            lines.append("protocol_output_columns=" + ", ".join(output_columns[:8]))
        analytical_method = str(grounding.get("analytical_method", "") or "").strip()
        if analytical_method:
            lines.append(f"protocol_analytical_method={analytical_method}")
        benchmark_profile = grounding.get("benchmark_profile", {}) if isinstance(grounding.get("benchmark_profile", {}), dict) else {}
        benchmark_profile_id = str(benchmark_profile.get("profile_id", "") or "").strip()
        if benchmark_profile_id:
            lines.append(f"protocol_benchmark_profile={benchmark_profile_id}")
        source_files = [str(x).strip() for x in (grounding.get("source_files", []) or []) if str(x).strip()]
        if source_files:
            lines.append("protocol_sources=" + " | ".join(source_files[:3]))
    literature_support = (
        spec.get("literature_planning_support", {})
        if isinstance(spec.get("literature_planning_support", {}), dict)
        else {}
    )
    if literature_support and not hide_protocol_details:
        lines.extend(literature_planning_support_brief_lines(literature_support))
    sel_dir = str(spec.get("selected_dir", "") or "").strip()
    if sel_dir:
        lines.append(f"output_directory=ALL output files MUST be written under {sel_dir}")
    # Use FileManifest if available (richer role-based listing), else legacy format
    manifest = spec.get("file_manifest")
    if manifest is not None:
        try:
            lines.append(manifest.as_brief_block())
            lines.append(manifest.as_role_instructions())
        except Exception:
            manifest = None  # fall through to legacy
    if manifest is None:
        discovered = spec.get("discovered_data_files", [])
        if isinstance(discovered, list) and discovered:
            lines.append("available_input_files=USE THESE EXACT PATHS in your plan:")
            for entry in discovered[:30]:
                if isinstance(entry, dict):
                    lines.append(f"  {entry.get('name', '?')} -> {entry.get('path', '?')}")
                else:
                    lines.append(f"  {entry}")
    return "\n".join(lines)
