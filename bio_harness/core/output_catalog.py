"""Structured output catalog helpers for completed runs.

This module builds a deterministic inventory of files produced under one
selected run directory. The initial implementation is reporting-safe and does
not change execution behavior.
"""

from __future__ import annotations

import gzip
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, IO

from bio_harness.core.tabular_io import load_delimited_dict_rows
from bio_harness.core.tool_env import which_with_pixi


@dataclass(frozen=True)
class CatalogEntry:
    """One output file with role and description metadata.

    Attributes:
        path: Absolute file path.
        relative_path: Path relative to the selected directory.
        size_bytes: File size in bytes.
        role: Semantic role such as final deliverable or intermediate file.
        tool_name: Tool name for the producing step when known.
        step_id: Producing step identifier, or 0 when unmatched.
        description: Human-readable file description.
        format: Lightweight detected file format.
        review_scope: Whether the file is reviewable, provenance, internal, or QC-only.
        review_action: Whether the file should affect deterministic result review.
        exclude_reason: Stable explanation when the file is excluded from review.
    """

    path: str
    relative_path: str
    size_bytes: int
    role: str
    tool_name: str
    step_id: int
    description: str
    format: str
    review_scope: str
    review_action: str
    exclude_reason: str


@dataclass(frozen=True)
class OutputCatalog:
    """Catalog of outputs found under a completed run directory.

    Attributes:
        selected_dir: Root directory scanned for outputs.
        entries: All discovered file entries.
        primary_results: Entries classified as final deliverables.
        intermediate_files: Entries classified as intermediate outputs.
        qc_reports: Entries classified as QC reports.
        reviewable_entries: Entries allowed to affect deterministic result review.
        provenance_files: Entries retained for provenance but excluded from review.
        internal_files: Tool internals and caches excluded from review.
        summary: One-line summary of the discovered outputs.
    """

    selected_dir: str
    entries: tuple[CatalogEntry, ...]
    primary_results: tuple[CatalogEntry, ...]
    intermediate_files: tuple[CatalogEntry, ...]
    qc_reports: tuple[CatalogEntry, ...]
    reviewable_entries: tuple[CatalogEntry, ...]
    provenance_files: tuple[CatalogEntry, ...]
    internal_files: tuple[CatalogEntry, ...]
    summary: str


def build_output_catalog(
    selected_dir: Path,
    plan: dict[str, Any],
    step_statuses: list[str] | None = None,
    analysis_type: str = "",
) -> OutputCatalog:
    """Build an output catalog for a completed run directory.

    Args:
        selected_dir: Directory containing run artifacts.
        plan: Structured plan used to produce the run.
        step_statuses: Optional step statuses aligned to plan order. Non-completed
            steps are skipped when matching expected files.
        analysis_type: Optional assay family used to identify reviewable outputs.

    Returns:
        Structured catalog of all files discovered under ``selected_dir``.
    """

    resolved_dir = Path(selected_dir).expanduser().resolve(strict=False)
    steps = [step for step in (plan or {}).get("plan", []) if isinstance(step, dict)]
    status_map = _step_status_map(steps, step_statuses)
    path_index = _build_plan_path_index(resolved_dir, plan, steps, status_map)
    entries: list[CatalogEntry] = []

    if resolved_dir.exists():
        for file_path in sorted(path for path in resolved_dir.rglob("*") if path.is_file()):
            relative_path = str(file_path.relative_to(resolved_dir))
            matched_step = path_index.get(relative_path) or path_index.get(file_path.name)
            role = _classify_role(file_path, matched_step, plan)
            format_name = _detect_format(file_path)
            review_scope, review_action, exclude_reason = _classify_review_behavior(
                file_path,
                relative_path=relative_path,
                role=role,
                format_name=format_name,
                matched_step=matched_step,
                analysis_type=analysis_type,
            )
            entry = CatalogEntry(
                path=str(file_path),
                relative_path=relative_path,
                size_bytes=int(file_path.stat().st_size),
                role=role,
                tool_name=str((matched_step or {}).get("tool_name", "") or ""),
                step_id=int((matched_step or {}).get("step_id", 0) or 0),
                description=_describe_file(
                    file_path,
                    format_name,
                    str((matched_step or {}).get("tool_name", "") or ""),
                ),
                format=format_name,
                review_scope=review_scope,
                review_action=review_action,
                exclude_reason=exclude_reason,
            )
            entries.append(entry)

    primary_results = tuple(entry for entry in entries if entry.role == "final_deliverable")
    intermediate_files = tuple(entry for entry in entries if entry.role == "intermediate")
    qc_reports = tuple(entry for entry in entries if entry.role == "qc_report")
    reviewable_entries = tuple(entry for entry in entries if entry.review_action == "assess_quality")
    provenance_files = tuple(entry for entry in entries if entry.review_scope == "provenance")
    internal_files = tuple(entry for entry in entries if entry.review_scope == "internal")
    summary = (
        f"Cataloged {len(entries)} files: "
        f"{len(primary_results)} primary result(s), "
        f"{len(intermediate_files)} intermediate file(s), "
        f"{len(qc_reports)} QC report(s), "
        f"{len(reviewable_entries)} reviewable artifact(s)."
    )
    return OutputCatalog(
        selected_dir=str(resolved_dir),
        entries=tuple(entries),
        primary_results=primary_results,
        intermediate_files=intermediate_files,
        qc_reports=qc_reports,
        reviewable_entries=reviewable_entries,
        provenance_files=provenance_files,
        internal_files=internal_files,
        summary=summary,
    )


def catalog_to_markdown(catalog: OutputCatalog) -> str:
    """Render an output catalog as Markdown.

    Args:
        catalog: Catalog to render.

    Returns:
        Markdown summary for human inspection.
    """

    lines = [
        "# Output Catalog",
        "",
        f"- Selected dir: `{catalog.selected_dir}`",
        f"- Summary: {catalog.summary}",
        "",
        "## Primary Results",
        "",
    ]
    lines.extend(_entry_lines(catalog.primary_results))
    if not catalog.primary_results:
        lines.append("- None")
    lines.extend(["", "## QC Reports", ""])
    lines.extend(_entry_lines(catalog.qc_reports))
    if not catalog.qc_reports:
        lines.append("- None")
    lines.extend(["", "## Reviewable Artifacts", ""])
    lines.extend(_entry_lines(catalog.reviewable_entries))
    if not catalog.reviewable_entries:
        lines.append("- None")
    lines.extend(["", "## Intermediate Files", ""])
    lines.extend(_entry_lines(catalog.intermediate_files))
    if not catalog.intermediate_files:
        lines.append("- None")
    lines.extend(["", "## Provenance Files", ""])
    lines.extend(_entry_lines(catalog.provenance_files))
    if not catalog.provenance_files:
        lines.append("- None")
    lines.extend(["", "## Internal Files", ""])
    lines.extend(_entry_lines(catalog.internal_files))
    if not catalog.internal_files:
        lines.append("- None")
    return "\n".join(lines)


def catalog_to_json(catalog: OutputCatalog) -> dict[str, Any]:
    """Serialize an output catalog into JSON-friendly primitives.

    Args:
        catalog: Catalog to serialize.

    Returns:
        Dictionary ready for JSON encoding.
    """

    return {
        "selected_dir": catalog.selected_dir,
        "entries": [asdict(entry) for entry in catalog.entries],
        "primary_results": [asdict(entry) for entry in catalog.primary_results],
        "intermediate_files": [asdict(entry) for entry in catalog.intermediate_files],
        "qc_reports": [asdict(entry) for entry in catalog.qc_reports],
        "reviewable_entries": [asdict(entry) for entry in catalog.reviewable_entries],
        "provenance_files": [asdict(entry) for entry in catalog.provenance_files],
        "internal_files": [asdict(entry) for entry in catalog.internal_files],
        "summary": catalog.summary,
    }


def _classify_review_behavior(
    path: Path,
    *,
    relative_path: str,
    role: str,
    format_name: str,
    matched_step: dict[str, Any] | None,
    analysis_type: str,
) -> tuple[str, str, str]:
    """Classify whether one artifact can drive deterministic result review."""

    if _is_internal_artifact(relative_path):
        return "internal", "ignore_for_decision", "tool_internal_artifact"
    if _is_provenance_artifact(path, relative_path=relative_path):
        return "provenance", "ignore_for_decision", "run_provenance_artifact"
    if _is_input_artifact(path):
        return "provenance", "ignore_for_decision", "input_artifact"
    if role == "qc_report":
        return "qc_support", "summarize_only", "qc_report"
    if format_name == "html":
        return "qc_support", "summarize_only", "html_report_artifact"
    if _is_reviewable_primary_artifact(
        path,
        relative_path=relative_path,
        role=role,
        format_name=format_name,
        matched_step=matched_step,
        analysis_type=analysis_type,
    ):
        return "reviewable", "assess_quality", ""
    return "intermediate", "ignore_for_decision", "non_deliverable_intermediate"


def _is_internal_artifact(relative_path: str) -> bool:
    """Return whether a file is a tool-internal cache or generated helper asset."""

    lowered = relative_path.replace("\\", "/").lower()
    parts = [part for part in lowered.split("/") if part]
    internal_dirs = {
        "_snpeff",
        "planner",
        "knowledge",
        "scripts",
        "__pycache__",
        ".pixi",
        ".snakemake",
        "cache",
        "caches",
        "reports",
    }
    if any(part in internal_dirs for part in parts):
        return True
    if any(part in {"alignments", "intermediate", "work", "tmp"} for part in parts[:-1]):
        return True
    return any(part.startswith(".") and part not in {".", ".."} for part in parts[:-1])


def _is_provenance_artifact(path: Path, *, relative_path: str) -> bool:
    """Return whether a file is provenance/debug metadata rather than a result."""

    name_lower = path.name.lower()
    relative_lower = relative_path.replace("\\", "/").lower()
    provenance_names = {
        "result.json",
        "state.json",
        "manifest.json",
        "assistance_manifest.json",
        "executor.json",
        "exit.json",
        "summary.json",
        "summary.md",
        "path_decisions.json",
        "events.jsonl",
        ".step_completion.json",
        "tooling_status.json",
        "output_catalog.json",
        "output_catalog.md",
        "result_review.json",
        "result_review.md",
        "interpretation.json",
        "interpretation.md",
        "failure_diagnosis.json",
        "failure_diagnosis.md",
    }
    if name_lower in provenance_names:
        return True
    if name_lower.endswith((".log", ".stderr", ".stdout", ".jsonl", ".sh")):
        return True
    if relative_lower.startswith("reports/") or relative_lower.startswith("figures/"):
        return True
    return False


def _is_input_artifact(path: Path) -> bool:
    """Return whether a file name indicates a staged input rather than an output."""

    name_lower = path.name.lower()
    return name_lower.startswith("input_") or name_lower.startswith("inputs_")


def _is_reviewable_primary_artifact(
    path: Path,
    *,
    relative_path: str,
    role: str,
    format_name: str,
    matched_step: dict[str, Any] | None,
    analysis_type: str,
) -> bool:
    """Return whether a file should drive deterministic result review."""

    if role == "final_deliverable":
        return True
    tool_name = str((matched_step or {}).get("tool_name", "") or "").lower()
    analysis_token = str(analysis_type or "").lower()
    if "proteomics" in analysis_token or tool_name == "proteomics_diff_abundance":
        return path.name.lower() in {
            "proteomics_differential_abundance.csv",
            "proteomics_qc_summary.json",
            "normalized_abundance_matrix.tsv",
            "volcano_plot_data.tsv",
            "proteomics_summary.md",
        }
    if "metabolomics" in analysis_token or tool_name == "metabolomics_diff_abundance":
        return path.name.lower() in {
            "metabolomics_differential_abundance.csv",
            "metabolomics_qc_summary.json",
            "normalized_feature_matrix.tsv",
            "volcano_plot_data.tsv",
            "metabolomics_summary.md",
        }
    if format_name not in {"bam", "vcf", "fastq", "csv", "tsv", "gtf", "h5ad"}:
        return False
    relative_lower = relative_path.replace("\\", "/").lower()
    if relative_lower.startswith("final/") or relative_lower.startswith("output/"):
        return True
    if "/" not in relative_lower:
        return True
    if "transcript_quant" in analysis_token or tool_name in {"stringtie_quant", "salmon_quant", "kallisto_quant"}:
        return path.name.lower() in {"gene_abundances.tsv", "assembled.gtf", "transcript_abundances.tsv"}
    if "single_cell" in analysis_token or tool_name in {"scanpy_workflow", "seurat_workflow"}:
        return path.name.lower() in {
            "cluster_assignments.csv",
            "marker_genes.csv",
            "markers.csv",
            "clusters.csv",
            "single_cell_results.csv",
        }
    if "spatial_transcriptomics" in analysis_token or tool_name == "spatial_transcriptomics_workflow":
        return path.name.lower() in {
            "spatial_domain_assignments.csv",
            "spatial_marker_genes.csv",
            "spatial_results.h5ad",
        }
    if "proteomics" in analysis_token or tool_name == "proteomics_diff_abundance":
        return path.name.lower() in {
            "proteomics_differential_abundance.csv",
            "proteomics_qc_summary.json",
            "normalized_abundance_matrix.tsv",
            "volcano_plot_data.tsv",
            "proteomics_summary.md",
        }
    if "metabolomics" in analysis_token or tool_name == "metabolomics_diff_abundance":
        return path.name.lower() in {
            "metabolomics_differential_abundance.csv",
            "metabolomics_qc_summary.json",
            "normalized_feature_matrix.tsv",
            "volcano_plot_data.tsv",
            "metabolomics_summary.md",
        }
    if "variant" in analysis_token or "vcf" == format_name:
        return path.suffix.lower() == ".vcf" or path.name.lower().endswith(".vcf.gz")
    return False


def _classify_role(
    path: Path,
    step: dict[str, Any] | None,
    plan: dict[str, Any],
) -> str:
    """Classify one file's semantic role.

    Args:
        path: File path under the selected directory.
        step: Matched producing step when known.
        plan: Full plan dictionary for deliverable matching.

    Returns:
        Stable role label for the file.
    """

    name_lower = path.name.lower()
    if name_lower.endswith((".bai", ".csi", ".tbi", ".fai")):
        return "index"
    if name_lower.endswith(".log"):
        return "log"
    if name_lower.endswith((".html", ".json")) and str((step or {}).get("tool_name", "")).lower() in {
        "fastp_run",
        "multiqc_report",
        "fastqc_run",
    }:
        return "qc_report"
    if _matches_plan_entries(path, (plan or {}).get("final_deliverables", [])):
        return "final_deliverable"
    if step and _matches_plan_entries(path, step.get("deliverables", [])):
        return "final_deliverable"
    if step and _matches_plan_entries(path, step.get("expected_files", [])):
        return "intermediate"
    return "intermediate"


def _describe_file(path: Path, format: str, tool_name: str) -> str:
    """Generate a brief description for one output file.

    Args:
        path: Output file to describe.
        format: Lightweight detected format name.
        tool_name: Producing tool name when known.

    Returns:
        Short human-readable description.
    """

    if format in {"csv", "tsv"}:
        try:
            columns, rows, _ = load_delimited_dict_rows(path)
            return f"Tabular data ({len(rows)} rows, {len(columns)} columns)"
        except Exception:
            pass
    if format == "vcf":
        variant_count = _count_vcf_records(path)
        return f"Variant calls ({variant_count} variants)"
    if format == "fastq":
        read_count = _count_fastq_reads(path)
        return f"Sequencing reads ({read_count} reads)"
    if format == "bam":
        read_count = _count_bam_reads(path)
        if read_count is not None:
            return f"Aligned reads ({read_count} reads)"
        return "Aligned reads (BAM output)"
    if format == "json" and tool_name:
        return f"JSON report from {tool_name}"
    if format == "html" and tool_name:
        return f"HTML report from {tool_name}"
    if format == "log":
        return f"Log output from {tool_name or 'run'}"
    return f"{format} output from {tool_name or 'unknown_tool'}"


def _build_plan_path_index(
    selected_dir: Path,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    status_map: dict[int, str],
) -> dict[str, dict[str, Any]]:
    """Index expected and deliverable paths back to producing steps."""

    index: dict[str, dict[str, Any]] = {}
    for step in steps:
        step_id = int(step.get("step_id", 0) or 0)
        if status_map and status_map.get(step_id) not in {"", "completed", "passed", "success"}:
            continue
        for raw in list(step.get("expected_files", []) or []) + list(step.get("deliverables", []) or []):
            for key in _entry_keys(raw, selected_dir):
                index.setdefault(key, step)
    for raw in (plan or {}).get("final_deliverables", []) or []:
        for key in _entry_keys(raw, selected_dir):
            index.setdefault(key, {})
    return index


def _matches_plan_entries(path: Path, entries: Any) -> bool:
    """Return whether a file path matches a loose plan entry list."""

    if not isinstance(entries, (list, tuple)):
        return False
    normalized = {path.name, path.as_posix()}
    for candidate in entries:
        if not isinstance(candidate, (str, Path)):
            continue
        token = str(candidate).strip()
        if not token:
            continue
        normalized.add(str(Path(token)).replace("\\", "/"))
    for candidate in entries:
        if not isinstance(candidate, (str, Path)):
            continue
        raw = str(candidate).strip().replace("\\", "/")
        if not raw:
            continue
        if raw in {path.name, path.as_posix(), str(path)}:
            return True
        if Path(raw).name == path.name:
            return True
    return False


def _entry_keys(entry: Any, selected_dir: Path) -> set[str]:
    """Generate lookup keys for one loose plan path entry."""

    if not isinstance(entry, (str, Path)):
        return set()
    token = str(entry).strip()
    if not token:
        return set()
    candidate = Path(token)
    keys = {candidate.name}
    if candidate.is_absolute():
        resolved = candidate.expanduser().resolve(strict=False)
        keys.add(str(resolved))
        try:
            keys.add(str(resolved.relative_to(selected_dir)).replace("\\", "/"))
        except ValueError:
            pass
        return keys
    keys.add(str(candidate).replace("\\", "/"))
    return keys


def _step_status_map(steps: list[dict[str, Any]], step_statuses: list[str] | None) -> dict[int, str]:
    """Build a step-id to status map when statuses are provided."""

    if not isinstance(step_statuses, list):
        return {}
    mapping: dict[int, str] = {}
    for index, step in enumerate(steps):
        if index >= len(step_statuses):
            break
        mapping[int(step.get("step_id", 0) or 0)] = str(step_statuses[index] or "").lower()
    return mapping


def _entry_lines(entries: tuple[CatalogEntry, ...]) -> list[str]:
    """Render entries as Markdown bullet lines."""

    lines: list[str] = []
    for entry in entries:
        lines.append(
            f"- `{entry.relative_path}` [{entry.role}] ({entry.size_bytes} bytes): {entry.description}"
        )
    return lines


def _detect_format(path: Path) -> str:
    """Detect a lightweight file format label."""

    name_lower = path.name.lower()
    if name_lower.endswith(".bam"):
        return "bam"
    if name_lower.endswith(".sam"):
        return "bam"
    if name_lower.endswith(".vcf") or name_lower.endswith(".vcf.gz"):
        return "vcf"
    if name_lower.endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz")):
        return "fastq"
    if name_lower.endswith(".csv"):
        return "csv"
    if name_lower.endswith(".tsv"):
        return "tsv"
    if name_lower.endswith(".json"):
        return "json"
    if name_lower.endswith(".html"):
        return "html"
    if name_lower.endswith(".log"):
        return "log"
    if name_lower.endswith((".bai", ".csi", ".tbi", ".fai")):
        return "index"
    return path.suffix.lower().lstrip(".") or "other"


def _count_vcf_records(path: Path) -> int:
    """Count data rows in a VCF-like file."""

    count = 0
    try:
        with _open_text_auto(path) as handle:
            for line in handle:
                if line and not line.startswith("#"):
                    count += 1
    except OSError:
        return 0
    return count


def _count_fastq_reads(path: Path) -> int:
    """Count FASTQ records."""

    lines = 0
    try:
        with _open_text_auto(path) as handle:
            for _ in handle:
                lines += 1
    except OSError:
        return 0
    return lines // 4


def _count_bam_reads(path: Path) -> int | None:
    """Count BAM reads when samtools is available."""

    samtools_bin = which_with_pixi("samtools")
    if not samtools_bin:
        return None
    try:
        import subprocess

        result = subprocess.run(
            [samtools_bin, "view", "-c", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return int((result.stdout or "").strip())
    except ValueError:
        return None


def _open_text_auto(path: Path) -> IO[str]:
    """Open plain or gzipped text for reading."""

    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


__all__ = [
    "CatalogEntry",
    "OutputCatalog",
    "build_output_catalog",
    "catalog_to_json",
    "catalog_to_markdown",
]
