"""Shared wrapper contracts for routing and direct-wrapper locking.

This module centralizes direct-wrapper compatibility metadata so analysis-spec
normalization, execution-mode inference, and future contract validation use one
source of truth rather than drifting allowlists.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Final, Mapping


_WRAPPER_CONTRACTS: Final[dict[str, dict[str, object]]] = {
    "bwa_mem_align": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "bcftools_isec_run": {
        "allowed_input_modes": frozenset({"vcf"}),
        "lock_requires_evidence": True,
        "multi_input_args": frozenset({"input_vcfs"}),
        "path_args": frozenset({"input_vcfs", "output_dir", "output_vcf"}),
    },
    "bcftools_filter_run": {
        "allowed_input_modes": frozenset({"vcf"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"input_vcf", "output_vcf"}),
    },
    "bcftools_norm_run": {
        "allowed_input_modes": frozenset({"vcf"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"input_vcf", "reference_fasta", "output_vcf"}),
    },
    "cellranger_count": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "cutadapt_run": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "deseq2_run": {
        "allowed_input_modes": frozenset({"count_matrix"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"counts_matrix", "metadata_table", "output_dir"}),
    },
    "dexseq_run": {
        "allowed_input_modes": frozenset({"count_matrix"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"counts_matrix", "metadata_table", "output_dir"}),
    },
    "edger_run": {
        "allowed_input_modes": frozenset({"count_matrix"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"counts_matrix", "metadata_table", "output_dir"}),
    },
    "featurecounts_run": {
        "allowed_input_modes": frozenset({"aligned_bam"}),
        "lock_requires_evidence": True,
        "multi_input_args": frozenset({"input_bams"}),
        "path_args": frozenset({"input_bams", "annotation_gtf", "output_counts"}),
    },
    "fastp_run": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "fastqc_run": {
        "allowed_input_modes": frozenset({"raw_fastq", "aligned_bam"}),
        "lock_requires_evidence": True,
    },
    "flye_assemble": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "gatk_haplotypecaller": {
        "allowed_input_modes": frozenset({"aligned_bam"}),
        "lock_requires_evidence": True,
    },
    "hisat2_align": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "kallisto_quant": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "limma_voom_run": {
        "allowed_input_modes": frozenset({"count_matrix"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"counts_matrix", "metadata_table", "output_dir"}),
    },
    "metabolomics_diff_abundance": {
        "allowed_input_modes": frozenset({"count_matrix"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"counts_matrix", "metadata_table", "output_dir"}),
    },
    "metagenomics_kraken2_bracken_style": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
        "path_args": frozenset(
            {
                "database",
                "reads_1",
                "reads_2",
                "output_dir",
                "output_report",
                "reference_fasta",
                "taxonomy_names",
                "taxonomy_nodes",
            }
        ),
    },
    "minimap2_align": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "proteomics_diff_abundance": {
        "allowed_input_modes": frozenset({"count_matrix"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"counts_matrix", "metadata_table", "output_dir"}),
    },
    "phylogenetics_iqtree_style": {
        "allowed_input_modes": frozenset({"protein_fasta"}),
        "lock_requires_evidence": True,
    },
    "salmon_quant": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "scanpy_workflow": {
        "allowed_input_modes": frozenset({"processed_single_cell", "count_matrix"}),
        "lock_requires_evidence": True,
    },
    "sc_count_and_cluster": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "seurat_rscript_workflow": {
        "allowed_input_modes": frozenset({"processed_single_cell", "count_matrix"}),
        "lock_requires_evidence": True,
    },
    "snpeff_annotate": {
        "allowed_input_modes": frozenset({"vcf"}),
        "lock_requires_evidence": True,
        "path_args": frozenset(
            {"input_vcf", "output_vcf", "reference_fasta", "annotation_gff", "config_dir"}
        ),
    },
    "sniffles_sv_call": {
        "allowed_input_modes": frozenset({"aligned_bam"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"input_bam", "reference_fasta", "output_vcf"}),
    },
    "shared_variants_export_run": {
        "allowed_input_modes": frozenset({"vcf"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"input_vcf_a", "input_vcf_b", "output_csv"}),
    },
    "spatial_transcriptomics_workflow": {
        "allowed_input_modes": frozenset({"processed_single_cell"}),
        "lock_requires_evidence": True,
    },
    "star_align": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "star_2pass_align": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "star_solo_count": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "stringtie_quant": {
        "allowed_input_modes": frozenset({"aligned_bam"}),
        "lock_requires_evidence": True,
    },
    "subread_align": {
        "allowed_input_modes": frozenset({"raw_fastq"}),
        "lock_requires_evidence": True,
    },
    "tabix_index_run": {
        "allowed_input_modes": frozenset({"vcf"}),
        "lock_requires_evidence": True,
        "path_args": frozenset({"input_file"}),
    },
}

_SNPEFF_CODON_TABLE_EMPTY_NORMALIZED_HINTS: Final[frozenset[str]] = frozenset(
    {
        "11",
        "table_11",
        "codon_table_11",
        "bacterial_and_plant_plastid",
        "bacterial_and_plant_plastid_codon_table",
        "bacterial_and_plastid",
        "bacterial",
        "bacterial_codon_table",
        "bacteria",
        "prokaryotic",
        "prokaryotic_codon_table",
        "prokaryote",
    }
)


def wrapper_has_contract(wrapper_name: str) -> bool:
    """Return whether one wrapper has centralized routing metadata.

    Args:
        wrapper_name: Candidate wrapper name.

    Returns:
        ``True`` when the wrapper has a contract entry.
    """

    return str(wrapper_name or "").strip() in _WRAPPER_CONTRACTS


def wrapper_allowed_input_modes(wrapper_name: str) -> frozenset[str]:
    """Return allowed input modes for one wrapper.

    Args:
        wrapper_name: Candidate wrapper name.

    Returns:
        A frozenset of supported input-mode tokens.
    """

    contract = _WRAPPER_CONTRACTS.get(str(wrapper_name or "").strip(), {})
    modes = contract.get("allowed_input_modes", frozenset())
    return modes if isinstance(modes, frozenset) else frozenset(modes)


def wrapper_lock_requires_evidence(wrapper_name: str) -> bool:
    """Return whether preserving one wrapper lock requires evidence.

    Args:
        wrapper_name: Candidate wrapper name.

    Returns:
        ``True`` when wrapper locks should only survive if input evidence
        matches the wrapper contract.
    """

    contract = _WRAPPER_CONTRACTS.get(str(wrapper_name or "").strip(), {})
    return bool(contract.get("lock_requires_evidence", False))


def wrapper_supports_input_mode(wrapper_name: str, input_mode: str) -> bool:
    """Return whether one wrapper contract supports the given input mode.

    Args:
        wrapper_name: Candidate wrapper name.
        input_mode: Inferred input-mode token.

    Returns:
        ``True`` when the wrapper explicitly supports the given input mode.
    """

    mode = str(input_mode or "").strip()
    return bool(mode) and mode in wrapper_allowed_input_modes(wrapper_name)


def wrapper_multi_input_args(wrapper_name: str) -> frozenset[str]:
    """Return canonical multi-input argument names for one wrapper.

    Args:
        wrapper_name: Candidate wrapper name.

    Returns:
        A frozenset of argument names that should normalize list-like payloads
        before validation instead of treating one whitespace-joined string as a
        single path.
    """

    contract = _WRAPPER_CONTRACTS.get(str(wrapper_name or "").strip(), {})
    values = contract.get("multi_input_args", frozenset())
    return values if isinstance(values, frozenset) else frozenset(values)


def wrapper_path_args(wrapper_name: str) -> frozenset[str]:
    """Return canonical path-like argument names for one wrapper.

    Args:
        wrapper_name: Candidate wrapper name.

    Returns:
        A frozenset of argument names whose values should be canonicalized
        against the active working directory before validation or command
        rendering.
    """

    contract = _WRAPPER_CONTRACTS.get(str(wrapper_name or "").strip(), {})
    values = contract.get("path_args", frozenset())
    return values if isinstance(values, frozenset) else frozenset(values)


def normalize_wrapper_argument_value(
    wrapper_name: str,
    argument_name: str,
    value: Any,
    *,
    cwd: str | Path | None = None,
) -> Any:
    """Normalize one wrapper argument using the centralized contract metadata.

    Args:
        wrapper_name: Candidate wrapper name.
        argument_name: Candidate argument name.
        value: Raw argument payload.
        cwd: Optional working directory used to canonicalize path-typed
            wrapper arguments.

    Returns:
        A normalized scalar or list-like value. Arguments marked as
        ``multi_input_args`` are converted into stable string lists so the
        validator, direct-wrapper binders, and runtime renderer all reason
        about the same structure. Arguments marked as ``path_args`` are
        canonicalized against ``cwd`` when one is available.
    """

    arg_name = str(argument_name or "").strip()
    normalized = value
    if arg_name in wrapper_multi_input_args(wrapper_name):
        normalized = _normalize_multi_input_value(value)
    if arg_name in wrapper_path_args(wrapper_name):
        normalized = _normalize_path_argument_value(normalized, cwd=cwd)
    return normalized


def normalize_wrapper_arguments(
    wrapper_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Normalize one wrapper argument payload using contract-aware rules.

    Args:
        wrapper_name: Candidate wrapper name.
        arguments: Raw wrapper argument payload.
        cwd: Optional working directory used to canonicalize path-typed
            wrapper arguments.

    Returns:
        A normalized argument mapping. Non-mapping payloads yield an empty
        mapping.
    """

    if not isinstance(arguments, Mapping):
        return {}
    normalized = {
        str(key): normalize_wrapper_argument_value(
            wrapper_name,
            str(key),
            value,
            cwd=cwd,
        )
        for key, value in arguments.items()
        if str(key).strip()
    }
    if str(wrapper_name or "").strip() == "bwa_mem_align" and "read_group" in normalized:
        normalized["read_group"] = normalize_bwa_read_group(
            normalized.get("read_group"),
            sample_name=str(normalized.get("sample_name", "") or ""),
        )
    if str(wrapper_name or "").strip() == "snpeff_annotate" and "codon_table" in normalized:
        normalized["codon_table"] = normalize_snpeff_codon_table(normalized.get("codon_table"))
    return normalized


def normalize_bwa_read_group(
    read_group: Any,
    *,
    sample_name: str = "",
) -> str:
    """Return a canonical BWA read-group string.

    Args:
        read_group: Raw planner- or caller-supplied read-group payload.
        sample_name: Optional fallback sample name used to fill missing fields.

    Returns:
        A canonical ``@RG`` string using escaped tab separators, or an empty
        string when both ``read_group`` and ``sample_name`` are empty.
    """

    sample = str(sample_name or "").strip()
    raw = str(read_group or "").strip()
    if not raw and not sample:
        return ""
    if not raw:
        return _build_bwa_read_group(sample or "sample")

    fields = _parse_bwa_read_group_fields(raw)
    if fields:
        inferred_sample = (
            str(fields.get("SM", "") or "").strip()
            or str(fields.get("ID", "") or "").strip()
            or sample
            or "sample"
        )
        ordered: dict[str, str] = {
            "ID": str(fields.get("ID", "") or inferred_sample).strip() or inferred_sample,
            "SM": str(fields.get("SM", "") or inferred_sample).strip() or inferred_sample,
            "PL": str(fields.get("PL", "") or "ILLUMINA").strip() or "ILLUMINA",
            "LB": str(fields.get("LB", "") or "lib1").strip() or "lib1",
        }
        for key, value in fields.items():
            key_text = str(key or "").strip()
            value_text = str(value or "").strip()
            if not key_text or not value_text or key_text in ordered:
                continue
            ordered[key_text] = value_text
        return "@RG\\t" + "\\t".join(f"{key}:{value}" for key, value in ordered.items())

    if ":" in raw:
        _, _, tail = raw.partition(":")
        fallback_sample = tail.strip() or sample or "sample"
    else:
        fallback_sample = raw or sample or "sample"
    return _build_bwa_read_group(fallback_sample)


def normalize_snpeff_codon_table(codon_table: Any) -> str:
    """Return a SnpEff-safe codon-table override.

    Args:
        codon_table: Raw planner- or caller-supplied codon-table payload.

    Returns:
        A normalized codon-table string. Known SnpEff 5.3a-incompatible
        bacterial/plastid hints resolve to the empty string so custom database
        builds can fall back to the packaged default codon handling.
    """

    raw = str(codon_table or "").strip()
    if not raw:
        return ""
    normalized_hint = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    if normalized_hint in _SNPEFF_CODON_TABLE_EMPTY_NORMALIZED_HINTS:
        return ""
    if raw.isdigit():
        return ""
    return raw


def _normalize_multi_input_value(value: Any) -> list[str]:
    """Return a canonical list for one multi-input wrapper argument."""

    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]

    raw = str(value).strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        inner = raw[1:-1]
        tokens = [item.strip().strip("'\"") for item in inner.split(",")]
        normalized = [item for item in tokens if item]
        if normalized and "," in inner:
            return normalized
        return []
    try:
        tokens = [str(item).strip() for item in shlex.split(raw, posix=True) if str(item).strip()]
    except ValueError:
        tokens = [item for item in raw.split() if item]
    return tokens or [raw]


def _normalize_path_argument_value(
    value: Any,
    *,
    cwd: str | Path | None,
) -> Any:
    """Canonicalize one path-typed wrapper argument against the active cwd."""

    if cwd is None:
        return value
    if isinstance(value, (list, tuple, set)):
        normalized_items = [
            _normalize_path_scalar(item, cwd=cwd)
            for item in value
        ]
        return [item for item in normalized_items if str(item).strip()]
    return _normalize_path_scalar(value, cwd=cwd)


def _normalize_path_scalar(value: Any, *, cwd: str | Path) -> Any:
    """Return one scalar path-like value resolved against ``cwd`` when safe."""

    if isinstance(value, (int, float, bool)) or value is None:
        return value
    raw = str(value).strip()
    if not raw or _looks_like_uri(raw):
        return value
    base = Path(cwd).expanduser().resolve(strict=False)
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    return str((base / path).resolve(strict=False))


def _build_bwa_read_group(sample_name: str) -> str:
    """Return the default canonical BWA read-group string."""

    sample = str(sample_name or "").strip() or "sample"
    return rf"@RG\tID:{sample}\tSM:{sample}\tPL:ILLUMINA\tLB:lib1"


def _parse_bwa_read_group_fields(raw: str) -> dict[str, str]:
    """Parse simple ``@RG``-style field payloads into a mapping."""

    text = str(raw or "").strip()
    if not text:
        return {}
    normalized = text.replace("\t", "\\t")
    tokens = [token.strip() for token in normalized.split("\\t") if token.strip()]
    if not tokens:
        return {}
    if tokens[0] == "@RG":
        tokens = tokens[1:]
    elif tokens[0].startswith("@RG:"):
        tokens[0] = tokens[0][4:]
    fields: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition(":")
        key_text = key.strip()
        value_text = value.strip()
        if not sep or not key_text or not value_text:
            continue
        fields[key_text] = value_text
    return fields


def _looks_like_uri(value: str) -> bool:
    """Return whether one text value should be treated as a URI instead of a path."""

    lowered = str(value or "").strip().lower()
    if not lowered:
        return False
    return "://" in lowered


__all__ = [
    "wrapper_allowed_input_modes",
    "wrapper_has_contract",
    "wrapper_lock_requires_evidence",
    "normalize_wrapper_arguments",
    "normalize_wrapper_argument_value",
    "normalize_bwa_read_group",
    "wrapper_multi_input_args",
    "wrapper_path_args",
    "wrapper_supports_input_mode",
]
