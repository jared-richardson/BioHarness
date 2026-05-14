from __future__ import annotations

import json
import re
from typing import Any

from bio_harness.core.protocol_grounding import _signal_present_in_text
from bio_harness.core.tool_registry import default_tool_registry

_TOOL_SIGNAL_ALIASES: dict[str, tuple[str, ...]] = {
    "fastqc_run": ("fastqc",),
    "star_solo_count": ("starsolo",),
}
_METAGENOMICS_PROFILING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "metagenomics_kraken2_bracken_style",
    }
)
_METAGENOMICS_PROFILING_COMMAND_SIGNALS: tuple[str, ...] = (
    "bracken",
    "centrifuge",
    "classify_metagenomics_kmer.py",
    "classify_viral_reads_kmer.py",
    "kaiju",
    "kraken",
    "metaphlan",
)


def _collect_plan_hay(plan: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Extract plan content as searchable haystack text.

    Flattens tool names, argument names, argument values, and commands into
    a lowercased newline-separated string for signal matching.

    Args:
        plan: Plan dict containing a 'plan' key with step list.

    Returns:
        Tuple of (haystack_text, normalized_steps_list).
    """
    steps = (plan or {}).get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return "", []

    hay_parts: list[str] = []
    normalized_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        normalized_steps.append(step)
        tool_name = str(step.get("tool_name", "")).lower()
        hay_parts.append(tool_name)
        hay_parts.extend(_TOOL_SIGNAL_ALIASES.get(tool_name, ()))
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        for arg_key, raw_value in args.items():
            arg_name = str(arg_key).strip().lower()
            if arg_name:
                hay_parts.append(arg_name)
            if arg_name == "command":
                continue
            if raw_value is None:
                continue
            if isinstance(raw_value, str):
                hay_parts.append(raw_value.lower())
                continue
            if isinstance(raw_value, (int, float, bool)):
                hay_parts.append(str(raw_value).lower())
                continue
            if isinstance(raw_value, (list, tuple, set)):
                for item in raw_value:
                    if item is None:
                        continue
                    hay_parts.append(str(item).lower())
                continue
            try:
                hay_parts.append(json.dumps(raw_value, ensure_ascii=True, sort_keys=True).lower())
            except Exception:
                hay_parts.append(str(raw_value).lower())
        command = str(args.get("command", "")).lower()
        hay_parts.append(command)
    return "\n".join(hay_parts), normalized_steps


def _collect_tool_hint_hay(steps: list[dict[str, Any]]) -> str:
    """Extract only tool-selection-relevant text for tool-hint validation.

    Tool-hint matching should reflect the actual selected tools or explicit shell
    commands, not incidental strings inside output paths.
    """
    hay_parts: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        if tool_name:
            hay_parts.append(tool_name)
            hay_parts.extend(_TOOL_SIGNAL_ALIASES.get(tool_name, ()))
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            hay_parts.append(command.lower())
    return "\n".join(hay_parts)


def _selected_tool_capabilities(steps: list[dict[str, Any]]) -> set[str]:
    """Return normalized capability IDs declared by selected wrapped tools."""

    registry = default_tool_registry()
    capabilities: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        if not tool_name:
            continue
        capabilities.update(
            str(capability).strip().lower()
            for capability in registry.capabilities_for(tool_name)
            if str(capability).strip()
        )
    return capabilities


def _has_metagenomics_profiling_signal(steps: list[dict[str, Any]]) -> bool:
    """Return whether the selected work performs taxonomic profiling.

    Metagenomics task directories often contain strings such as
    ``domain_metagenomics`` in output paths. Those are context labels, not
    evidence that a classifier/profiler ran, so this check intentionally reads
    only concrete tool names and shell commands.
    """

    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        if tool_name in _METAGENOMICS_PROFILING_TOOL_NAMES:
            return True
        args = step.get("arguments", {})
        if not isinstance(args, dict):
            continue
        command = str(args.get("command", "") or "").strip().lower()
        if command and any(signal in command for signal in _METAGENOMICS_PROFILING_COMMAND_SIGNALS):
            return True
    return False


def _has_splicing_group_signal(hay: str) -> bool:
    """Check if haystack contains rMATS-style splicing group signals (--b1/--b2).

    Args:
        hay: Lowercased plan haystack text.

    Returns:
        True if splicing group signals are detected.
    """
    if not hay.strip():
        return False
    if "run_rmats_if_needed.sh" in hay:
        return True
    if re.search(r"--b1\b", hay) and re.search(r"--b2\b", hay):
        return True
    if re.search(r"\b(?:--)?b1\b", hay) and re.search(r"\b(?:--)?b2\b", hay):
        return True
    return False


def _has_general_group_signal(hay: str) -> bool:
    """Check if haystack contains two-group comparison signals.

    Detects control/treatment pairs, group labels, DE tool + design matrix
    patterns, and comparison keywords (vs, versus, compare).

    Args:
        hay: Lowercased plan haystack text.

    Returns:
        True if general group comparison signals are detected.
    """
    if not hay.strip():
        return False

    paired_markers = (
        ("control", "treatment"),
        ("case", "control"),
        ("group1", "group2"),
        ("condition1", "condition2"),
        ("cohort1", "cohort2"),
        ("cohort_a", "cohort_b"),
    )
    for left, right in paired_markers:
        if left in hay and right in hay:
            return True

    explicit_group_argument_markers = (
        "group_column",
        "condition_column",
        "group_a",
        "group_b",
        "control_group",
        "treatment_group",
    )
    if any(marker in hay for marker in explicit_group_argument_markers):
        return True

    if re.search(r"\bgroup[_\s-]?(?:a|b|1|2)\b", hay):
        return True
    if re.search(r"\bcondition[_\s-]?(?:a|b|1|2)\b", hay):
        return True

    de_markers = ("deseq2", "edger", "limma", "voom", "differential expression", "diffexp")
    design_markers = ("design", "~ condition", "~ group", "contrast", "results(", "coldata", "metadata", "sample_table")
    if any(m in hay for m in de_markers) and any(m in hay for m in design_markers):
        return True

    if " compare " in f" {hay} " or " versus " in f" {hay} " or " vs " in f" {hay} ":
        return True
    return False


DEFAULT_CAPABILITY_SPECS: dict[str, dict[str, Any]] = {
    "fastqc": {"plan_signals": ["fastqc"]},
    "alignment": {
        "plan_signals": [
            "star",
            "star_align",
            "star_2pass_align",
            "star_solo_count",
            "cellranger_count",
            "sc_count_and_cluster",
            "hisat2",
            "hisat2_align",
            "bwa",
            "bwa-mem2",
            "minimap2",
            "bowtie",
            "subread",
            "subjunc",
            "subread_align",
            "align",
            "samtools sort",
            "infer_phylogeny_biopython.py",
            "classify_viral_reads_kmer.py",
        ]
    },
    "splicing_analysis": {
        "plan_signals": ["rmats", "majiq", "dexseq", "spladder", "whippet", "splicing", "run_rmats_if_needed.sh"]
    },
    "structural_variant_calling": {
        "plan_signals": [
            "structural variant",
            "structural variants",
            "structural variation",
            "sniffles",
            "sniffles_sv_call",
        ]
    },
    "reference_inputs": {
        "plan_signals": [
            ".gtf",
            ".gff",
            ".gff3",
            ".fa",
            ".fasta",
            ".fna",
            ".vcf",
            ".vcf.gz",
            "mouse_gtf",
            "mouse_fasta",
            "reference_fasta",
            "input_vcf",
            "clinvar",
            "transcriptome.fa",
            "transcriptome.fa.gz",
        ]
    },
    "differential_analysis": {
        "plan_signals": [
            "deseq2",
            "deseq2_run",
            "edger",
            "edger_run",
            "limma",
            "limma_voom_run",
            "scanpy_workflow",
            "sc_count_and_cluster",
            "diffexp",
            "differential",
            "ttest_ind",
            "ttest",
            "rmats",
            "dexseq",
            "majiq",
            "whippet",
            "run_rmats_if_needed.sh",
        ]
    },
    "pathway_enrichment": {
        "plan_signals": [
            "pathway",
            "kegg",
            "gsea",
            "go enrichment",
            "enrichr",
            "gseapy",
            "fisher_exact",
            "overlap",
            "pathway_comparison",
        ]
    },
    "quantification": {
        "plan_signals": [
            "featurecounts",
            "featurecounts_run",
            "salmon",
            "salmon_quant",
            "kallisto",
            "kallisto_quant",
            "htseq",
            "counts",
        ]
    },
    "variant_calling": {
        "plan_signals": [
            "variant",
            "vcf",
            "gatk",
            "gatk_haplotypecaller",
            "bcftools",
            "bcftools_call",
            "freebayes",
            "freebayes_call",
            "varscan",
            "varscan_call",
            "mutect",
        ]
    },
    "single_cell_analysis": {
        "plan_signals": [
            "single-cell",
            "single cell",
            "scanpy",
            "scanpy_workflow",
            "seurat",
            "seurat_rscript_workflow",
            "cellranger",
            "cellranger_count",
            "star_solo_count",
            "sc_count_and_cluster",
        ]
    },
    "proteomics": {
        "plan_signals": [
            "proteomics",
            "protein abundance",
            "protein expression",
            "differential abundance",
            "abundance_matrix",
            "proteomics_diff_abundance",
        ]
    },
    "annotation": {
        "plan_signals": [
            "annotation",
            "snpeff",
            "snpeff_annotate",
            "vep",
            "vep_annotate",
            "prokka",
            "prokka_annotate",
            "blastp",
            "blastp_search",
            "hmmscan",
            "hmmscan_search",
            "protein",
        ]
    },
    "protein_analysis": {
        "plan_signals": [
            "protein",
            "blastp",
            "blastp_search",
            "hmmscan",
            "hmmscan_search",
            "pfam",
            "prokka",
            "prokka_annotate",
        ]
    },
    "genome_assembly": {
        "plan_signals": [
            "assembly",
            "spades",
            "spades_assemble",
            "flye",
            "flye_assemble",
            "trinity",
            "trinity_assemble",
        ]
    },
    "chipseq_analysis": {
        "plan_signals": [
            "chip-seq",
            "chipseq",
            "peak",
            "macs2",
            "macs2_chipseq_callpeak",
        ]
    },
    "atacseq_analysis": {
        "plan_signals": [
            "atac-seq",
            "atacseq",
            "accessibility",
            "peak",
            "macs2",
            "macs2_atacseq_callpeak",
        ]
    },
    "methylation_analysis": {
        "plan_signals": [
            "methylation",
            "bisulfite",
            "bismark",
            "methylation_bismark_style",
        ]
    },
    "metagenomics_profiling": {
        "plan_signals": [
            "metagenomics",
            "kraken2",
            "bracken",
            "metagenomics_kraken2_bracken_style",
            "classify_metagenomics_kmer.py",
            "classify_viral_reads_kmer.py",
        ]
    },
    "fusion_detection": {
        "plan_signals": [
            "fusion",
            "star-fusion",
            "star fusion",
            "fusion_star_fusion_style",
        ]
    },
    "cnv_analysis": {
        "plan_signals": [
            "cnv",
            "copy number",
            "cnvkit",
            "cnv_cnvkit_style",
        ]
    },
    "immune_repertoire_profiling": {
        "plan_signals": [
            "immune repertoire",
            "mixcr",
            "tcr",
            "bcr",
            "immune_repertoire_mixcr_style",
        ]
    },
    "phylogenetics": {
        "plan_signals": [
            "phylogeny",
            "phylogenetics",
            "iqtree",
            "iqtree2",
            "phylogenetics_iqtree_style",
            "infer_phylogeny_biopython.py",
        ]
    },
    # Fix #19: shared-variant export capability for bacterial-evolution / shared-
    # variant prompts ("variants shared by both evolved lines"). Plan signals
    # cover the first-class wrapper skill, the bundled exporter script, and the
    # canonical output filename the evaluator checks for.
    "shared_variant_export": {
        "plan_signals": [
            "shared_variants_export_run",
            "export_shared_variants_csv.py",
            "variants_shared.csv",
            "shared_variant_exporter",
        ]
    },
    "group_comparison": {"plan_signals": ["control", "treatment", "case", "group", "condition"], "group_signal_mode": "auto"},
}


def _merged_capability_specs(
    capability_specs: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Merge user-provided capability specs with the built-in defaults.

    Combines plan_signals lists (deduplicating) and overlays any extra keys.

    Args:
        capability_specs: Optional overrides keyed by capability ID.

    Returns:
        Merged capability specs dict.
    """
    merged = {k: dict(v) for k, v in DEFAULT_CAPABILITY_SPECS.items()}
    if not isinstance(capability_specs, dict):
        return merged
    for raw_id, raw_spec in capability_specs.items():
        cap_id = str(raw_id).strip()
        if not cap_id:
            continue
        base = dict(merged.get(cap_id, {}))
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        base_signals = [str(x).strip().lower() for x in base.get("plan_signals", []) if str(x).strip()]
        override_signals = spec.get("plan_signals", [])
        normalized_overrides = [str(x).strip().lower() for x in override_signals if str(x).strip()]
        normalized_signals: list[str] = []
        for signal in [*base_signals, *normalized_overrides]:
            if signal and signal not in normalized_signals:
                normalized_signals.append(signal)
        merged[cap_id] = {
            **base,
            **spec,
            "plan_signals": normalized_signals,
        }
    return merged


def assess_plan_contract(
    plan: dict[str, Any],
    contract: dict[str, Any],
    *,
    capability_specs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a plan against a contract specifying required capabilities and tools.

    Checks that all required capabilities have matching signals in the plan
    and that all required tool hints are present.

    Args:
        plan: Plan dict with a 'plan' key containing step list.
        contract: Contract dict with 'must_include_capabilities',
            'required_tool_hints', and 'explicit_tool_hints'.
        capability_specs: Optional capability spec overrides.

    Returns:
        Dict with 'passed' bool, 'missing_capabilities', 'missing_required_tool_hints',
        and 'missing_tool_hints' lists.
    """
    hay, steps = _collect_plan_hay(plan)
    selected_capabilities = _selected_tool_capabilities(steps)

    specs = _merged_capability_specs(capability_specs)

    missing_caps: list[str] = []
    requested_caps = contract.get("must_include_capabilities", []) if isinstance(contract, dict) else []
    requested_caps_set = {str(x) for x in requested_caps}
    for cap in requested_caps:
        cap_key = str(cap)
        if cap_key.lower() in selected_capabilities:
            continue
        if cap_key == "metagenomics_profiling":
            if not _has_metagenomics_profiling_signal(steps):
                missing_caps.append(cap_key)
            continue
        if cap_key == "group_comparison":
            group_mode = str(specs.get("group_comparison", {}).get("group_signal_mode", "auto")).strip().lower()
            # For non-splicing contracts, require general two-group signals (e.g. DE design/contrast).
            # Splicing contracts also accept rMATS-style b1/b2 signals.
            has_group_signal = "group_comparison" in selected_capabilities or _has_general_group_signal(hay)
            if group_mode == "splicing":
                has_group_signal = _has_splicing_group_signal(hay)
            elif group_mode == "auto" and (not has_group_signal) and "splicing_analysis" in requested_caps_set:
                has_group_signal = _has_splicing_group_signal(hay)
            if not has_group_signal:
                missing_caps.append(cap_key)
            continue
        pats = specs.get(cap_key, {}).get("plan_signals", [cap_key.lower()])
        if not any(p in hay for p in pats):
            missing_caps.append(cap_key)

    missing_required_tools: list[str] = []
    missing_tools: list[str] = []
    has_bash_steps = any(
        isinstance(step, dict) and step.get("tool_name", "") == "bash_run"
        for step in steps
    )
    tool_hay = _collect_tool_hint_hay(steps)
    for hint in (contract.get("required_tool_hints", []) if isinstance(contract, dict) else []):
        hint_norm = str(hint).strip().lower()
        if hint_norm in {"bash", "sh"} and has_bash_steps:
            continue
        if hint_norm and not _signal_present_in_text(hint_norm, tool_hay):
            missing_required_tools.append(hint_norm)
    for hint in (contract.get("explicit_tool_hints", []) if isinstance(contract, dict) else []):
        hint_norm = str(hint).strip().lower()
        if hint_norm in {"bash", "sh"} and has_bash_steps:
            continue
        if hint_norm and not _signal_present_in_text(hint_norm, tool_hay):
            missing_tools.append(hint_norm)

    return {
        "passed": not missing_caps and not missing_required_tools and bool(steps),
        "missing_capabilities": missing_caps,
        "missing_required_tool_hints": missing_required_tools,
        "missing_tool_hints": missing_tools,
    }
