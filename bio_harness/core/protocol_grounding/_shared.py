"""Shared constants, helpers, and plan-manipulation utilities.

This module holds module-level constants (knowledge bases, signal equivalences,
script paths) and small helper functions used by multiple sub-modules within
the ``protocol_grounding`` package.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.tool_env import which_with_pixi

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PROTOCOL_FILENAMES = (
    "run_script.sh",
    "README.md",
    "README.txt",
    "TASK.md",
    "task.md",
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SHARED_VARIANT_EXPORTER = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "export_shared_variants_csv.py"
STAR_INDEX_BUILD_SCRIPT = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "build_star_index.sh"
GFF3_TO_GTF_SCRIPT = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "gff3_to_gtf.py"
STAR_COUNTS_MATRIX_SCRIPT = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "build_star_gene_counts_matrix.py"
NORMALIZE_GFF_FOR_FEATURECOUNTS_SCRIPT = (
    PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "normalize_gff_for_featurecounts.py"
)
VARIANT_CALL_TOOLS = {
    "freebayes_call",
    "bcftools_call",
    "gatk_haplotypecaller",
    "gatk_mutect2_call",
    "varscan_call",
}
DEFAULT_SHARED_VARIANT_COLUMNS = ["CHROM", "POS", "REF", "ALT", "GENE", "IMPACT", "EFFECT", "STATUS"]
KRAKEN2_DB_SENTINELS = ("hash.k2d", "opts.k2d", "taxo.k2d")
DESEQ_METADATA_FILENAMES = (
    "sample_metadata.tsv",
    "sample_metadata.csv",
    "metadata.tsv",
    "metadata.csv",
    "samples.tsv",
    "samples.csv",
)
_NON_PATH_INPUT_OUTPUT_KEYS = frozenset({"output_format", "output_type", "input_type"})


def _declared_profile_setting_names(tool_name: str) -> set[str] | None:
    """Return declared setting names for a tool-scoped parameter profile row.

    Args:
        tool_name: Runtime tool name from the parameter profile.

    Returns:
        Declared parameter names when registry metadata is available, otherwise
        ``None`` so callers can preserve legacy behavior for unknown tools.
    """

    try:
        from bio_harness.core.tool_registry import default_tool_registry

        meta = default_tool_registry().get(tool_name)
    except Exception:  # pragma: no cover - defensive metadata lookup
        return None
    if meta is None or not meta.parameter_schema:
        return None
    declared = set(meta.parameter_schema)
    declared.update(meta.parameter_defaults)
    declared.update(meta.harness_managed_parameters)
    declared.update(meta.execution_output_parameters)
    return declared

# ---------------------------------------------------------------------------
# Global parameter knowledge base: critical defaults that should always be
# enforced when the LLM omits them.  Applied as a safety net by
# _apply_parameter_knowledge_base() regardless of whether a template
# compiler exists for the analysis type.
# ---------------------------------------------------------------------------
PARAMETER_KNOWLEDGE_BASE: dict[str, dict[str, Any]] = {
    "freebayes_call": {"ploidy": 1},  # bacteria default; overridden by templates if diploid
    "salmon_quant": {"library_type": "A"},  # auto-detect
    "star_align": {"outSAMtype": "BAM SortedByCoordinate"},
    "featurecounts_run": {"count_read_pairs": True},
    "scanpy_workflow": {"min_genes": 300, "min_cells": 20, "max_mito_pct": 15, "n_hvgs": 2000, "leiden_resolution": 0.3},
    "sc_count_and_cluster": {"min_genes": 3, "min_cells": 1, "kmer_size": 25, "leiden_resolution": 0.5},
    "bwa_mem_align": {"threads": 4},
    "spades_assemble": {"careful": True, "threads": 8, "memory_gb": 32},
}
PRODIGAL_GENE_RE = re.compile(r"^[0-9]+_[0-9]+$")

# ---------------------------------------------------------------------------
# Signal equivalences: maps a canonical signal name to all strings that
# should be treated as satisfying a requirement for that signal.  Used by
# assess_protocol_grounding() and importable by contracts.py.
# ---------------------------------------------------------------------------
SIGNAL_EQUIVALENCES: dict[str, list[str]] = {
    "vcffilter": [
        "vcffilter",
        "bcftools filter",
        "bcftools view -i",
        "bcftools view --include",
        "bcftools_filter_run",
    ],
    "freebayes": ["freebayes", "freebayes_call"],
    "spades": ["spades", "spades.py", "spades_assemble"],
    "snpeff": ["snpeff", "snpeff_annotate", "snpeff", "snpEff", "SnpEff"],
    "prokka": ["prokka", "prokka_annotate"],
    "prokka_annotate": ["prokka_annotate", "prokka"],
    "bwa": ["bwa", "bwa-mem2", "bwa_mem_align"],
    "star": ["star", "STAR", "star_align", "star_2pass_align"],
    "salmon": ["salmon", "salmon_quant"],
    "kallisto": ["kallisto", "kallisto_quant"],
    "trimmomatic": ["trimmomatic", "Trimmomatic"],
    "kraken2": ["kraken2", "Kraken2"],
    "bowtie2": ["bowtie2", "bowtie2_align"],
    "megahit": ["megahit", "MEGAHIT"],
    "kaiju": ["kaiju"],
    "deseq2": ["deseq2", "pydeseq2", "DESeq2", "deseq2_run"],
    "scanpy": ["scanpy", "scanpy_workflow", "sc_count_and_cluster"],
    "fastp": ["fastp"],
    "samtools": ["samtools"],
    "bcftools": ["bcftools", "bcftools_call"],
    "gatk": ["gatk", "gatk_haplotypecaller", "gatk_mutect2_call"],
    "varscan": ["varscan", "varscan_call", "VarScan"],
    "hisat2": ["hisat2", "hisat2_align"],
    "minimap2": ["minimap2", "minimap2_align"],
    "featurecounts": ["featurecounts", "featureCounts", "featurecounts_run"],
    "edger": ["edger", "edger_run", "edgeR"],
    "limma": ["limma", "limma_voom_run"],
    "rmats": ["rmats", "rmats.py", "rMATS", "rmats_run"],
    "vep": ["vep", "vep_annotate"],
    "prodigal": ["prodigal", "prodigal_annotate"],
}


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _signal_present_in_text(signal: str, plan_text: str) -> bool:
    """Check whether *signal* (or any of its equivalences) appears in *plan_text*.

    Both signal and plan_text are compared case-insensitively.
    """
    signal_lower = signal.strip().lower()
    text_lower = plan_text.lower()
    equivalents = list(SIGNAL_EQUIVALENCES.get(signal_lower, []))
    try:
        from bio_harness.core.tool_registry import default_tool_registry

        registry = default_tool_registry()
        for tool_name in registry.known_tool_names():
            if signal_lower in registry.signal_equivalences_for(tool_name):
                equivalents.append(str(tool_name))
    except Exception:
        pass
    if equivalents:
        return any(eq.lower() in text_lower for eq in equivalents)
    return signal_lower in text_lower


def _dedupe(values: list[str]) -> list[str]:
    """Return *values* with duplicates and blank entries removed, preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _is_path_bearing_argument_key(key: str) -> bool:
    """Return True when *key* typically carries a filesystem path."""
    key_l = str(key or "").strip().lower()
    if not key_l:
        return False
    if key_l in _NON_PATH_INPUT_OUTPUT_KEYS:
        return False
    if key_l.startswith(("input_", "output_")):
        return True
    return key_l.endswith(
        (
            "_path",
            "_paths",
            "_dir",
            "_dirs",
            "_file",
            "_files",
            "_fasta",
            "_fa",
            "_fna",
            "_gff",
            "_gff3",
            "_gtf",
            "_vcf",
            "_vcf_gz",
            "_bam",
            "_cram",
            "_counts",
        )
    )


# ---------------------------------------------------------------------------
# Plan normalisation / renumbering
# ---------------------------------------------------------------------------


def _normalize_steps(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract and normalise the step list from a plan dict."""
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return []
    out: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        row = dict(step)
        args = row.get("arguments", {})
        row["arguments"] = dict(args) if isinstance(args, dict) else {}
        out.append(row)
    return out


def _renumber_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Re-assign sequential ``step_id`` values to every step in *plan*."""
    patched = dict(plan) if isinstance(plan, dict) else {}
    steps = _normalize_steps(plan)
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    patched["plan"] = steps
    return patched


# ---------------------------------------------------------------------------
# Explicit-intent helpers
# ---------------------------------------------------------------------------


def _locked_argument_values_for_tool(
    analysis_spec: dict[str, Any] | None,
    tool_name: str,
) -> dict[str, Any]:
    """Return explicit locked arguments for one tool from the analysis spec."""

    intent = (
        analysis_spec.get("explicit_execution_intent", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    locked = intent.get("locked_argument_values", {})
    if not isinstance(locked, dict):
        return {}
    values = locked.get(str(tool_name or "").strip(), {})
    return dict(values) if isinstance(values, dict) else {}


def _preferred_argument_value(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    tool_name: str,
    argument_key: str,
    default: Any,
) -> Any:
    """Return a preferred argument value from the plan, locked intent, or default."""

    tool_name_l = str(tool_name or "").strip().lower()
    argument_name = str(argument_key or "").strip()
    steps = _normalize_steps(plan)
    for step in steps:
        if str(step.get("tool_name", "")).strip().lower() != tool_name_l:
            continue
        arguments = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if argument_name in arguments:
            value = arguments.get(argument_name)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value

    locked = _locked_argument_values_for_tool(analysis_spec, tool_name)
    if argument_name in locked:
        value = locked.get(argument_name)
        if not (isinstance(value, str) and not value.strip()):
            return value
    return default


# ---------------------------------------------------------------------------
# Parameter profile / knowledge base application
# ---------------------------------------------------------------------------


def _apply_parameter_profile(
    plan: dict[str, Any],
    parameter_profile: list[dict[str, Any]],
    *,
    preserve_existing_values_for_tools: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply analysis-specific parameter overrides from *parameter_profile* to *plan*.

    Args:
        plan: Candidate execution plan.
        parameter_profile: Tool-scoped default settings for the analysis type.
        preserve_existing_values_for_tools: Tool names whose existing explicit
            argument values must be preserved instead of overwritten.

    Returns:
        Tuple of (patched_plan, metadata_dict).
    """
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}
    # Tools with freeform arguments that must NOT receive profile settings.
    # bash_run only accepts "command"; LLM parameter_profiles sometimes include
    # descriptive metadata (e.g. script_type, dependencies, logic) that would
    # be injected as invalid keyword arguments.
    _SKIP_PROFILE_TOOLS = frozenset({"bash_run"})
    profile_map: dict[str, dict[str, Any]] = {}
    skipped_undeclared_settings: dict[str, list[str]] = {}
    for item in parameter_profile or []:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "")).strip().lower()
        if tool_name in _SKIP_PROFILE_TOOLS:
            continue
        settings = item.get("settings", {})
        if not tool_name or not isinstance(settings, dict) or not settings:
            continue
        declared_settings = _declared_profile_setting_names(tool_name)
        filtered_settings: dict[str, Any] = {}
        for raw_key, value in settings.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            if declared_settings is not None and key not in declared_settings:
                skipped_undeclared_settings.setdefault(tool_name, []).append(key)
                continue
            filtered_settings[key] = value
        if filtered_settings:
            profile_map.setdefault(tool_name, {}).update(filtered_settings)
    if not profile_map:
        meta = {"changed": False, "why": "no_parameter_profile"}
        if skipped_undeclared_settings:
            meta["skipped_undeclared_settings"] = {
                tool: sorted(set(keys))
                for tool, keys in skipped_undeclared_settings.items()
            }
        return plan, meta

    changed_steps: list[int] = []
    preserve_existing = {
        str(tool).strip().lower()
        for tool in (preserve_existing_values_for_tools or set())
        if str(tool).strip()
    }
    for idx, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name", "")).strip().lower()
        settings = profile_map.get(tool_name)
        if not settings:
            continue
        args = dict(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {})
        changed = False
        for key, value in settings.items():
            if _is_path_bearing_argument_key(key) and str(args.get(key, "")).strip():
                continue
            if (
                tool_name in preserve_existing
                and key in args
                and args.get(key) is not None
                and not (isinstance(args.get(key), str) and not str(args.get(key)).strip())
            ):
                continue
            if args.get(key) != value:
                args[key] = value
                changed = True
        if changed:
            step["arguments"] = args
            changed_steps.append(idx)
    if not changed_steps:
        meta = {"changed": False, "why": "profile_already_applied"}
        if skipped_undeclared_settings:
            meta["skipped_undeclared_settings"] = {
                tool: sorted(set(keys))
                for tool, keys in skipped_undeclared_settings.items()
            }
        return plan, meta
    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    meta = {
        "changed": True,
        "why": "parameter_profile_applied",
        "changed_steps": changed_steps,
    }
    if skipped_undeclared_settings:
        meta["skipped_undeclared_settings"] = {
            tool: sorted(set(keys))
            for tool, keys in skipped_undeclared_settings.items()
        }
    return _renumber_plan(patched), meta


def _apply_parameter_knowledge_base(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply PARAMETER_KNOWLEDGE_BASE defaults to any step missing them.

    Unlike _apply_parameter_profile (which uses per-analysis-type overrides),
    this applies global safety-net defaults that should always be present
    when the LLM omits critical parameters.  Existing values are NOT
    overwritten — only missing keys are filled in.
    """
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}
    registry = None
    try:
        from bio_harness.core.tool_registry import default_tool_registry

        registry = default_tool_registry()
    except Exception:
        registry = None
    changed_steps: list[int] = []
    for idx, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name", "")).strip().lower()
        defaults = (
            registry.parameter_defaults_for(tool_name)
            if registry is not None
            else dict(PARAMETER_KNOWLEDGE_BASE.get(tool_name, {}))
        )
        if not defaults:
            continue
        args = dict(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {})
        filled = False
        for key, value in defaults.items():
            if key not in args:
                args[key] = value
                filled = True
        if filled:
            step["arguments"] = args
            changed_steps.append(idx)
    if not changed_steps:
        return plan, {"changed": False, "why": "knowledge_base_already_applied"}
    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    return patched, {
        "changed": True,
        "why": "parameter_knowledge_base_applied",
        "changed_steps": changed_steps,
    }


# ---------------------------------------------------------------------------
# FASTQ and path helpers shared across compilers
# ---------------------------------------------------------------------------


def _discover_fastq_pairs(data_root: Path) -> dict[str, dict[str, str]]:
    """Discover paired FASTQ files under *data_root*.

    Returns:
        Dict mapping sample labels to ``{"reads_1": path, "reads_2": path}``.
    """
    pairs: dict[str, dict[str, str]] = {}
    if not data_root.exists():
        return pairs
    for path in sorted(data_root.glob("*")):
        if not path.is_file():
            continue
        name = path.name
        match = re.match(r"(?P<label>.+?)_(?P<read>R?[12])\.(?:fastq|fq)(?:\.gz)?$", name, flags=re.IGNORECASE)
        if not match:
            continue
        label = str(match.group("label") or "").strip()
        read = str(match.group("read") or "").upper().replace("R", "")
        if not label or read not in {"1", "2"}:
            continue
        row = pairs.setdefault(label, {})
        row[f"reads_{read}"] = str(path.resolve(strict=False))
    return {
        label: row
        for label, row in pairs.items()
        if str(row.get("reads_1", "")).strip() and str(row.get("reads_2", "")).strip()
    }


def _safe_label(label: str) -> str:
    """Sanitise a sample label for use in filenames."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "").strip()) or "sample"


def shlex_quote(value: str) -> str:
    """Shell-escape *value* for safe interpolation into commands."""
    return shlex.quote(str(value))


# ---------------------------------------------------------------------------
# Variant-related command builders (used by evolution compiler + externally)
# ---------------------------------------------------------------------------


def _bash_join(parts: list[str]) -> str:
    """Join non-empty shell fragments with ``&&``."""
    return " && ".join([part for part in parts if str(part).strip()])


def _resolved_cli(name: str) -> str:
    """Resolve a CLI tool through Pixi-aware lookup.

    Args:
        name: Executable name to resolve.

    Returns:
        A shell-quoted executable path or fallback command name.
    """

    return shlex_quote(which_with_pixi(name) or name)


def _translate_vcffilter_to_bcftools(expression: str) -> str:
    """Translate a vcffilter filter expression to bcftools -i syntax.

    vcffilter uses bare INFO field names (e.g. SAF, SAR, AO); bcftools
    requires the ``INFO/`` prefix for those fields.  Standard VCF columns
    (QUAL, CHROM, POS, …) do not need a prefix.
    """
    _STANDARD_VCF_FIELDS = {"CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"}
    try:
        result = expression.replace(" & ", " && ")

        def _prefix_info(m: re.Match) -> str:
            field = m.group(1)
            if field in _STANDARD_VCF_FIELDS:
                return field
            return f"INFO/{field}"

        result = re.sub(r"(?<!INFO/)(?<!\w)([A-Z][A-Z0-9_]{1,})\b", _prefix_info, result)
        return result
    except Exception:
        return expression.replace(" & ", " && ")


def _build_variant_filter_command(raw_vcf: str, filtered_vcf_gz: str, expression: str) -> str:
    """Build a shell command that filters a VCF using vcffilter or bcftools.

    Args:
        raw_vcf: Path to the input (unfiltered) VCF.
        filtered_vcf_gz: Path to the output filtered VCF (bgzipped).
        expression: The vcffilter-style filter expression.

    Returns:
        A shell command string.
    """
    variants_dir = Path(filtered_vcf_gz).expanduser().parent
    expr_q = shlex.quote(str(expression))
    bcftools_expr = _translate_vcffilter_to_bcftools(expression)
    bcftools_expr_q = shlex.quote(bcftools_expr)
    raw_q = shlex_quote(raw_vcf)
    filtered_q = shlex_quote(filtered_vcf_gz)
    vcffilter_cmd = _resolved_cli("vcffilter")
    bcftools_cmd = _resolved_cli("bcftools")
    bgzip_cmd = _resolved_cli("bgzip")
    tabix_cmd = _resolved_cli("tabix")
    return _bash_join(
        [
            "set -euo pipefail",
            f"mkdir -p {shlex_quote(str(variants_dir))}",
            # Idempotency: skip if filtered VCF already exists and is non-empty
            f"if [ -s {filtered_q} ]; then echo 'Filtered VCF exists, skipping'; else "
            f"if command -v vcffilter >/dev/null 2>&1; then zcat -f {raw_q} | {vcffilter_cmd} -f {expr_q} | {bgzip_cmd} > {filtered_q}; "
            f"else {bcftools_cmd} filter -i {bcftools_expr_q} {raw_q} -Oz -o {filtered_q}; fi; fi",
            f"{tabix_cmd} -f -p vcf {filtered_q}",
        ]
    )


def _build_normalize_vcf_command(input_vcf: str, output_vcf_gz: str, reference_fasta: str) -> str:
    """Build a shell command that normalises a VCF with bcftools norm.

    Args:
        input_vcf: Path to the input VCF.
        output_vcf_gz: Path to the output normalised VCF (bgzipped).
        reference_fasta: Path to the reference FASTA.

    Returns:
        A shell command string.
    """
    input_q = shlex_quote(input_vcf)
    output_q = shlex_quote(output_vcf_gz)
    ref_q = shlex_quote(reference_fasta)
    output_dir = shlex_quote(str(Path(output_vcf_gz).expanduser().parent))
    bcftools_cmd = _resolved_cli("bcftools")
    bgzip_cmd = _resolved_cli("bgzip")
    tabix_cmd = _resolved_cli("tabix")
    return _bash_join(
        [
            "set -euo pipefail",
            f"mkdir -p {output_dir}",
            # Idempotency: skip if normalized VCF already exists and is non-empty
            f"if [ -s {output_q} ]; then echo 'Normalized VCF exists, skipping'; else "
            + (
                f"if command -v bcftools >/dev/null 2>&1; then "
                f"{bcftools_cmd} norm -f {ref_q} -m -any {input_q} -Oz -o {output_q}; "
                f"else {bgzip_cmd} -f -c {input_q} > {output_q}; fi"
            )
            + "; fi",
            f"{tabix_cmd} -f -p vcf {output_q}",
        ]
    )
