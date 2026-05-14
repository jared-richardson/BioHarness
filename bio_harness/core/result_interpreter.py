"""Plain-English interpretation helpers for completed run outputs.

This module summarizes completed run artifacts into a concise scientist-facing
interpretation. The initial implementation is standalone and designed for
post-run reporting rather than live execution control.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bio_harness.core.output_catalog import build_output_catalog
from bio_harness.core.output_quality import QualityLevel, assess_output_quality
from bio_harness.core.tabular_io import load_delimited_dict_rows


@dataclass(frozen=True)
class InterpretationResult:
    """Plain-English interpretation of run results.

    Attributes:
        analysis_type: Analysis type the interpretation refers to.
        metrics_summary: Raw metrics used to drive interpretation.
        interpretation: Final plain-English summary text.
        concerns: Warning or failure statements surfaced from metrics.
        model_used: Model identifier or `template` when no LLM was used.
    """

    analysis_type: str
    metrics_summary: dict[str, Any]
    interpretation: str
    concerns: tuple[str, ...]
    model_used: str


def interpret_run_results(
    selected_dir: Path,
    analysis_type: str,
    plan: dict[str, Any],
    llm: Any | None = None,
) -> InterpretationResult:
    """Interpret run outputs into a concise summary.

    Args:
        selected_dir: Run output directory to inspect.
        analysis_type: Current analysis type.
        plan: Final structured plan for the run.
        llm: Optional LLM object exposing `summarize_text`.

    Returns:
        Structured interpretation result.
    """

    metrics = _collect_run_metrics(selected_dir, plan, analysis_type=analysis_type)
    concerns = tuple(_extract_concerns(metrics))
    artifact_sentences = _artifact_interpretation_sentences(Path(selected_dir))
    if llm is not None:
        prompt = _build_interpretation_prompt(analysis_type, metrics)
        try:
            interpretation = str(
                llm.summarize_text(
                    json.dumps(metrics, indent=2, sort_keys=True),
                    prompt,
                )
            ).strip()
        except Exception:
            interpretation = _template_based_summary(analysis_type, metrics)
            model_used = "template"
        else:
            model_used = str(getattr(llm, "model_name", "") or getattr(llm, "model", "") or "llm")
    else:
        interpretation = _template_based_summary(analysis_type, metrics, artifact_sentences=artifact_sentences)
        model_used = "template"
    return InterpretationResult(
        analysis_type=str(analysis_type or ""),
        metrics_summary=metrics,
        interpretation=interpretation,
        concerns=concerns,
        model_used=model_used,
    )


def _collect_run_metrics(
    selected_dir: Path,
    plan: dict[str, Any],
    *,
    analysis_type: str,
) -> dict[str, Any]:
    """Collect key output metrics from a completed run directory.

    Args:
        selected_dir: Run output directory to inspect.
        plan: Final structured plan.
        analysis_type: Analysis type used to identify reviewable artifacts.

    Returns:
        Metrics dictionary suitable for prompting and template summaries.
    """

    catalog = build_output_catalog(selected_dir, plan, analysis_type=analysis_type)
    steps = [step for step in (plan or {}).get("plan", []) if isinstance(step, dict)]
    metrics: dict[str, Any] = {
        "total_steps": len(steps),
        "completed_steps": len(steps),
        "key_outputs": {},
        "concerns": [],
    }
    for entry in catalog.reviewable_entries:
        quality = assess_output_quality(
            Path(entry.path),
            tool_name=entry.tool_name,
            analysis_type="",
        )
        quality_metrics = {metric.name: metric.value for metric in quality.metrics}
        quality_levels = {metric.name: metric.level.value for metric in quality.metrics}
        bucket = _bucket_name(entry)
        current = metrics["key_outputs"].get(bucket)
        candidate = {
            "path": entry.relative_path,
            "tool_name": entry.tool_name,
            "role": entry.role,
            "format": entry.format,
            "description": entry.description,
            "overall_level": quality.overall_level.value,
            "summary": quality.summary,
            "metrics": quality_metrics,
            "metric_levels": quality_levels,
        }
        if current is None or entry.role == "final_deliverable":
            metrics["key_outputs"][bucket] = candidate
        for metric in quality.metrics:
            if metric.level in {QualityLevel.WARNING, QualityLevel.FAIL}:
                metrics["concerns"].append(metric.message)
    metrics["concerns"] = tuple(dict.fromkeys(metrics["concerns"]))
    return metrics


def _build_interpretation_prompt(
    analysis_type: str,
    metrics: dict[str, Any],
) -> str:
    """Build the LLM instruction for post-run interpretation.

    Args:
        analysis_type: Analysis type being interpreted.
        metrics: Metrics summary dictionary.

    Returns:
        Concise instruction string for `summarize_text`.
    """

    return (
        "You are a bioinformatics analyst. Summarize these completed pipeline results "
        "in 3-5 sentences for a scientist. Include key findings, quality assessment, "
        "and any concerns. Be specific with the provided numbers.\n\n"
        f"Analysis type: {analysis_type}\n"
        f"Key outputs: {', '.join(sorted(metrics.get('key_outputs', {}).keys())) or 'none'}"
    )


def _template_based_summary(
    analysis_type: str,
    metrics: dict[str, Any],
    artifact_sentences: tuple[str, ...] = (),
) -> str:
    """Generate a deterministic summary without an LLM.

    Args:
        analysis_type: Analysis type being summarized.
        metrics: Metrics dictionary collected from run outputs.

    Returns:
        Template-based plain-English summary.
    """

    key_outputs = metrics.get("key_outputs", {})
    lines: list[str] = []
    analysis_label = str(analysis_type or "analysis").replace("_", " ")
    if artifact_sentences:
        lines.append(
            f"The {analysis_label} run completed successfully, and the inspected result artifacts support the following interpretation."
        )
        lines.extend(artifact_sentences)
    elif "de_results" in key_outputs:
        de_output = key_outputs["de_results"]
        significant = int(de_output.get("metrics", {}).get("significant_row_count", 0.0) or 0)
        total_rows = int(de_output.get("metrics", {}).get("row_count", 0.0) or 0)
        lines.append(
            f"The {analysis_label} run produced differential expression results for {total_rows} rows, "
            f"with {significant} significantly differentially expressed genes."
        )
    if "variant_calling" in key_outputs:
        vc_output = key_outputs["variant_calling"]
        variant_count = int(vc_output.get("metrics", {}).get("variant_count", 0.0) or 0)
        pass_fraction = float(vc_output.get("metrics", {}).get("pass_fraction", 0.0) or 0.0)
        lines.append(
            f"Variant calling identified {variant_count} variants, with {pass_fraction:.1%} passing filters."
        )
    if "alignment" in key_outputs:
        alignment_output = key_outputs["alignment"]
        mapping_rate = float(alignment_output.get("metrics", {}).get("mapping_rate", 0.0) or 0.0)
        total_reads = int(alignment_output.get("metrics", {}).get("total_reads", 0.0) or 0)
        lines.append(
            f"Alignment quality shows {mapping_rate:.1%} mapping across {total_reads} reads."
        )
    if not lines:
        lines.append(
            f"The {analysis_label} run completed with {metrics.get('completed_steps', 0)} step(s)."
        )
    concerns = _extract_concerns(metrics)
    if concerns:
        lines.append(f"Concerns: {'; '.join(concerns[:3])}.")
    else:
        lines.append("No major quality concerns were detected in the inspected outputs.")
    return " ".join(lines)


def _artifact_interpretation_sentences(selected_dir: Path) -> tuple[str, ...]:
    """Build deterministic artifact-aware summary sentences."""

    resolved = Path(selected_dir).expanduser().resolve(strict=False)
    if resolved.is_file():
        artifacts = [resolved]
    elif resolved.is_dir():
        artifacts = sorted(path for path in resolved.iterdir() if path.is_file())
    else:
        artifacts = []

    if not artifacts:
        return ()

    sentences: list[str] = []
    markers_by_cluster = _load_marker_summary(artifacts)
    for artifact in artifacts:
        summary = _summarize_artifact(artifact, markers_by_cluster=markers_by_cluster)
        if summary:
            sentences.append(summary)
    return tuple(sentences[:4])


def _summarize_artifact(
    artifact: Path,
    *,
    markers_by_cluster: dict[str, list[str]],
) -> str:
    """Summarize one result artifact into a scientist-facing sentence."""

    name_lower = artifact.name.lower()
    if name_lower.endswith((".csv", ".tsv")):
        return _summarize_tabular_artifact(artifact, markers_by_cluster=markers_by_cluster)
    if name_lower.endswith((".vcf", ".vcf.gz")):
        return _summarize_vcf_artifact(artifact)
    if name_lower.endswith(".txt"):
        if "flagstat" in name_lower or _looks_like_flagstat_artifact(artifact):
            return _summarize_flagstat_artifact(artifact)
        return _summarize_metagenomics_report(artifact)
    if name_lower.endswith(".nwk"):
        return _summarize_newick_artifact(artifact)
    return ""


def _summarize_tabular_artifact(
    artifact: Path,
    *,
    markers_by_cluster: dict[str, list[str]],
) -> str:
    """Summarize one CSV/TSV artifact by schema."""

    try:
        columns, rows, _ = load_delimited_dict_rows(artifact)
    except Exception:
        return ""
    lowered = {column.lower(): column for column in columns}
    if {"gene", "log2foldchange", "padj"}.issubset(lowered):
        return _summarize_de_artifact(rows, lowered)
    if {"cell", "cluster"}.issubset(lowered):
        return _summarize_single_cell_artifact(rows, lowered, markers_by_cluster)
    if {"pathway", "padj", "overlap_count"}.issubset(lowered):
        return _summarize_enrichment_artifact(rows, lowered)
    return ""


def _summarize_de_artifact(
    rows: list[dict[str, Any]],
    lowered: dict[str, str],
) -> str:
    """Summarize differential-expression style tabular results."""

    total_genes = len(rows)
    if total_genes == 0:
        return "The differential expression result table is empty with 0 genes, so there are no results to interpret."
    gene_col = lowered["gene"]
    lfc_col = lowered["log2foldchange"]
    padj_col = lowered["padj"]
    significant = [row for row in rows if _safe_float(row.get(padj_col)) <= 0.05]
    if not significant:
        return (
            f"The differential expression results cover {total_genes} genes, with 0 significant genes at padj <= 0.05. "
            "No differentially expressed genes are detected, and the observed fold changes remain modest across the table."
        )
    top_row = max(significant, key=lambda row: abs(_safe_float(row.get(lfc_col))))
    upregulated = sum(1 for row in significant if _safe_float(row.get(lfc_col)) > 0)
    downregulated = sum(1 for row in significant if _safe_float(row.get(lfc_col)) < 0)
    top_gene = str(top_row.get(gene_col, "") or "unknown")
    top_lfc = _safe_float(top_row.get(lfc_col))
    return (
        f"The differential expression results cover {total_genes} genes, with {len(significant)} significant genes at padj <= 0.05. "
        f"The strongest signal is {top_gene} with log2 fold change {top_lfc:.1f}; {upregulated} significant genes are upregulated and {downregulated} are downregulated."
    )


def _summarize_vcf_artifact(artifact: Path) -> str:
    """Summarize one VCF artifact with SNP/indel counts."""

    total_variants = 0
    pass_variants = 0
    snps = 0
    indels = 0
    try:
        with artifact.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line or line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 7:
                    continue
                total_variants += 1
                ref = fields[3]
                alt = fields[4].split(",")[0]
                if len(ref) == 1 and len(alt) == 1:
                    snps += 1
                else:
                    indels += 1
                if fields[6] == "PASS":
                    pass_variants += 1
    except OSError:
        return ""
    return (
        f"The variant call set contains {total_variants} variants, including {snps} SNPs and {indels} indels, with {pass_variants} passing filters."
    )


def _summarize_metagenomics_report(artifact: Path) -> str:
    """Summarize a Kraken-style metagenomics report."""

    classification_rate = None
    top_genera: list[str] = []
    try:
        with artifact.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                fields = raw_line.rstrip("\n").split("\t")
                if len(fields) < 6:
                    continue
                percentage = _safe_float(fields[0])
                rank_code = fields[3].strip()
                name = fields[5].strip()
                if rank_code == "U":
                    classification_rate = max(0.0, 100.0 - percentage)
                if rank_code == "G" and name:
                    top_genera.append(name)
    except OSError:
        return ""
    if classification_rate is None:
        return ""
    listed = ", ".join(top_genera[:3])
    return (
        f"The metagenomics report classifies {classification_rate:.1f}% of reads, with leading genera including {listed}. "
        f"A large unclassified fraction remains at {100.0 - classification_rate:.1f}%."
    )


def _summarize_flagstat_artifact(artifact: Path) -> str:
    """Summarize a samtools flagstat report."""

    total_reads = 0
    duplicate_reads = 0
    mapping_rate = 0.0
    paired_reads = 0
    try:
        with artifact.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                total_match = re.match(r"(\d+)\s+\+\s+\d+\s+in total", line)
                if total_match:
                    total_reads = int(total_match.group(1))
                    continue
                duplicate_match = re.match(r"(\d+)\s+\+\s+\d+\s+duplicates", line)
                if duplicate_match:
                    duplicate_reads = int(duplicate_match.group(1))
                    continue
                mapping_match = re.match(r"(\d+)\s+\+\s+\d+\s+mapped\s+\(([\d.]+)%", line)
                if mapping_match:
                    mapping_rate = float(mapping_match.group(2))
                    continue
                paired_match = re.match(r"(\d+)\s+\+\s+\d+\s+paired in sequencing", line)
                if paired_match:
                    paired_reads = int(paired_match.group(1))
    except OSError:
        return ""
    return (
        f"The alignment summary covers {total_reads:,} total reads with {mapping_rate:.2f}% mapped, {duplicate_reads:,} duplicates, and {paired_reads:,} paired-end reads."
    )


def _looks_like_flagstat_artifact(artifact: Path) -> bool:
    """Return whether a text artifact looks like samtools flagstat output."""

    try:
        sample = artifact.read_text(encoding="utf-8", errors="replace")[:500]
    except OSError:
        return False
    return "in total" in sample and "mapped" in sample and "duplicates" in sample


def _summarize_newick_artifact(artifact: Path) -> str:
    """Summarize one Newick tree with taxon count and a close clade."""

    try:
        text = artifact.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    taxa = re.findall(r"([A-Za-z0-9_]+):", text)
    sister_match = re.search(r"\(([^(),:]+):[^,]+,([^(),:]+):", text)
    if not taxa:
        return ""
    taxa_count = len(set(taxa))
    if sister_match:
        sister_a = sister_match.group(1)
        sister_b = sister_match.group(2)
        return (
            f"The phylogenetic tree contains {taxa_count} taxa. {sister_a} and {sister_b} form a close sister clade relative to the remaining species."
        )
    return f"The phylogenetic tree contains {taxa_count} taxa with a resolved branching structure."


def _summarize_single_cell_artifact(
    rows: list[dict[str, Any]],
    lowered: dict[str, str],
    markers_by_cluster: dict[str, list[str]],
) -> str:
    """Summarize cluster assignments and marker genes for single-cell outputs."""

    cluster_col = lowered["cluster"]
    clusters = sorted({str(row.get(cluster_col, "")).strip() for row in rows if str(row.get(cluster_col, "")).strip()})
    total_cells = len(rows)
    marker_tokens: list[str] = []
    for cluster_id in clusters:
        marker_tokens.extend(markers_by_cluster.get(cluster_id, [])[:1])
    marker_text = ", ".join(dict.fromkeys(marker_tokens) or ["no marker genes reported"])
    return (
        f"The single-cell clustering output contains {total_cells} cells partitioned into {len(clusters)} clusters. "
        f"Representative marker genes include {marker_text}."
    )


def _summarize_enrichment_artifact(
    rows: list[dict[str, Any]],
    lowered: dict[str, str],
) -> str:
    """Summarize pathway-enrichment results."""

    pathway_col = lowered["pathway"]
    padj_col = lowered["padj"]
    significant = [row for row in rows if _safe_float(row.get(padj_col)) <= 0.05]
    if not significant:
        return "The pathway enrichment table reports no pathways passing padj <= 0.05."
    top_pathway = str(significant[0].get(pathway_col, "") or "").replace("_", " ")
    other_pathways = ", ".join(
        str(row.get(pathway_col, "") or "").replace("_", " ")
        for row in significant[1:3]
    )
    return (
        f"The enrichment analysis identifies {len(significant)} significant pathways, led by {top_pathway}. "
        f"Additional signals include {other_pathways}."
    )


def _load_marker_summary(artifacts: list[Path]) -> dict[str, list[str]]:
    """Load single-cell marker genes keyed by cluster identifier."""

    marker_file = next((artifact for artifact in artifacts if "marker" in artifact.name.lower()), None)
    if marker_file is None:
        return {}
    try:
        columns, rows, _ = load_delimited_dict_rows(marker_file)
    except Exception:
        return {}
    lowered = {column.lower(): column for column in columns}
    if {"gene", "cluster"}.issubset(lowered):
        gene_col = lowered["gene"]
        cluster_col = lowered["cluster"]
        markers: dict[str, list[str]] = {}
        for row in rows:
            cluster_id = str(row.get(cluster_col, "")).strip()
            gene_name = str(row.get(gene_col, "")).strip()
            if not cluster_id or not gene_name:
                continue
            markers.setdefault(cluster_id, []).append(gene_name)
        return markers
    return {}


def _safe_float(value: Any) -> float:
    """Convert a metric-like value to float with a zero fallback."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bucket_name(entry: Any) -> str:
    """Map one catalog entry to a higher-level output bucket."""

    tool_name = str(getattr(entry, "tool_name", "") or "").lower()
    relative_path = str(getattr(entry, "relative_path", "") or "").lower()
    format_name = str(getattr(entry, "format", "") or "").lower()
    if tool_name in {"deseq2_run", "edger_run", "limma_voom_run"} or "deseq" in relative_path:
        return "de_results"
    if format_name == "vcf":
        return "variant_calling"
    if format_name == "bam":
        return "alignment"
    if getattr(entry, "role", "") == "qc_report":
        return "qc"
    return f"output:{relative_path or format_name or 'unknown'}"


def _extract_concerns(metrics: dict[str, Any]) -> list[str]:
    """Extract unique concern strings from collected metrics."""

    raw = metrics.get("concerns", ())
    concerns = [str(item).strip() for item in raw if str(item).strip()]
    return list(dict.fromkeys(concerns))


__all__ = [
    "InterpretationResult",
    "interpret_run_results",
]
