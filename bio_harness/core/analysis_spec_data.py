from __future__ import annotations

# ruff: noqa: F403,F405
from bio_harness.core.analysis_spec_support import *

def _has_data_extension(name: str) -> bool:
    """Check if filename has a recognised bioinformatics data extension."""
    nl = name.lower()
    for ext in _DATA_EXTENSIONS:
        if nl.endswith(ext):
            return True
    return False


def discover_data_files(
    data_root: str | Path,
    *,
    max_files: int = 50,
) -> List[Dict[str, str]]:
    """Scan *data_root* for bioinformatics input files.

    Returns a list of ``{"name": <filename>, "path": <absolute path>}`` dicts,
    sorted by name.  Hidden files and directories are skipped.
    """
    root = Path(data_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        return []

    results: List[Dict[str, str]] = []
    try:
        for p in sorted(root.rglob("*")):
            if any(part.startswith(".") for part in p.parts):
                continue
            if not p.is_file():
                continue
            if not _has_data_extension(p.name):
                continue
            results.append({"name": p.name, "path": str(p)})
            if len(results) >= max_files:
                break
    except Exception:
        pass
    return results


def analysis_spec_preference_profile(spec: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    preferred_tools = _dedupe([str(x).strip() for x in (spec.get("preferred_tools", []) or []) if str(x).strip()])
    discouraged_tools = _dedupe([str(x).strip() for x in (spec.get("discouraged_tools", []) or []) if str(x).strip()])
    preferred_pipelines: List[str] = []
    analysis_type = str(spec.get("analysis_type", "") or "").strip()
    if analysis_type == "bacterial_evolution_variant_calling":
        preferred_pipelines.extend(["germline_variant_freebayes"])
    elif analysis_type == "long_read_assembly":
        preferred_pipelines.extend(["long_read_assembly_flye"])
    elif analysis_type == "long_read_rna":
        preferred_pipelines.extend(["long_read_rna_minimap2"])
    elif analysis_type == "metabolomics":
        preferred_pipelines.extend(["metabolomics_differential_abundance"])
    elif analysis_type == "proteomics":
        preferred_pipelines.extend(["proteomics_differential_abundance"])
    elif analysis_type == "rna_seq_differential_expression":
        preferred_pipelines.extend(["differential_expression_deseq2", "differential_expression_deseq2_from_counts"])
    elif analysis_type == "transcript_quantification":
        preferred_pipelines.extend([])
    elif analysis_type == "alternative_splicing":
        preferred_pipelines.extend(["alt_splicing_dexseq", "alt_splicing_majiq"])
    elif analysis_type == "metagenomics_classification":
        preferred_pipelines.extend(["metagenomics_kraken2_bracken"])
    elif analysis_type == "single_cell_rna_seq":
        preferred_pipelines.extend(["single_cell_scanpy"])
    elif analysis_type == "spatial_transcriptomics":
        preferred_pipelines.extend(["spatial_transcriptomics_processed"])
    elif analysis_type == "germline_variant_calling":
        preferred_pipelines.extend(["germline_variant_gatk"])
    elif analysis_type == "viral_metagenomics":
        preferred_pipelines.extend(["viral_metagenomics_minimap2"])
    elif analysis_type == "variant_annotation":
        preferred_pipelines.extend(["variant_annotation_snpeff"])
    elif analysis_type == "comparative_genomics":
        preferred_pipelines.extend(["comparative_genomics_minimap2"])
    elif analysis_type == "multi_model_dge_pathway":
        preferred_pipelines.extend(["dge_pathway_enrichment"])
    return {
        "analysis_type": analysis_type,
        "chosen_method": str(spec.get("chosen_method", "") or "").strip(),
        "preferred_tools": preferred_tools,
        "discouraged_tools": discouraged_tools,
        "preferred_pipeline_ids": preferred_pipelines,
        "acceptance_checks": list(spec.get("acceptance_checks", []) or []),
        "protocol_grounding": spec.get("protocol_grounding", {}) if isinstance(spec.get("protocol_grounding", {}), dict) else {},
    }
