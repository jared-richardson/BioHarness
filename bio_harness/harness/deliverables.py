"""Post-execution deliverable materialization.

Functions that collect pipeline outputs and produce the final CSV
deliverables expected by benchmark validation.
"""
from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path
from typing import Any

from bio_harness.core.request_output_intent import extract_requested_deliverable_paths
from bio_harness.core.shell_parse import split_shell_segments
from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.pipeline_scripts.export_cystic_fibrosis_csv import (
    export_cystic_fibrosis_csv,
)
from bio_harness.pipeline_scripts.export_multi_model_pathway_comparison import (
    export_multi_model_pathway_comparison,
)
from bio_harness.pipeline_scripts.export_single_cell_results_csv import (
    export_single_cell_results_csv,
)
from bio_harness.harness.plan_repair import (
    _is_cystic_fibrosis_task,
    _discover_cystic_fibrosis_inputs,
)


def _extract_quantification_counts_for_export(source_path: Path) -> tuple[list[tuple[str, int]], dict[str, Any]]:
    text = source_path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return [], {"source_kind": "empty", "row_count": 0}
    delimiter = "\t" if "\t" in lines[0] else ","
    header = [part.strip().lower() for part in lines[0].split(delimiter)]
    id_col = None
    count_col = None
    source_kind = "generic"
    for idx, value in enumerate(header):
        if value in {"name", "target_id", "transcript_id", "transcript"} and id_col is None:
            id_col = idx
        if value in {"numreads", "est_counts", "count", "expected_count", "counts"} and count_col is None:
            count_col = idx
            if value == "numreads":
                source_kind = "salmon_quant_sf"
            elif value == "est_counts":
                source_kind = "kallisto_abundance_tsv"
    if id_col is None:
        id_col = 0
    if count_col is None:
        if len(header) >= 2:
            count_col = 1
        else:
            raise ValueError(f"Unable to infer count column from quantification file: {source_path}")

    rows: list[tuple[str, int]] = []
    for line in lines[1:]:
        parts = line.split(delimiter)
        if len(parts) <= max(id_col, count_col):
            continue
        transcript_id = str(parts[id_col]).strip()
        if not transcript_id:
            continue
        try:
            count_value = int(float(str(parts[count_col]).strip()))
        except ValueError:
            continue
        rows.append((transcript_id, count_value))
    return rows, {"source_kind": source_kind, "row_count": len(rows)}


def _extract_deliverable_output_path_from_protocol_grounding(protocol_grounding: dict[str, Any]) -> str:
    output_path = str(protocol_grounding.get("output_path", "") or "").strip()
    if output_path:
        return output_path
    postprocess = protocol_grounding.get("postprocess", []) if isinstance(protocol_grounding.get("postprocess", []), list) else []
    for item in postprocess:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command", "") or "").strip()
        if not command:
            continue
        for segment in split_shell_segments(command):
            text = str(segment).strip()
            match = re.search(r">\s*([^\s;]+(?:transcript_counts\.tsv|variants(?:_shared)?\.csv|variants\.vcf))\b", text)
            if match:
                return str(match.group(1) or "").strip().strip("\"'")
    return ""


def _preferred_requested_deliverable_path(
    *,
    selected_dir: Path,
    analysis_spec: dict[str, Any],
    request_text: str,
    suffixes: tuple[str, ...],
) -> Path | None:
    """Return the first explicit requested final deliverable path.

    Args:
        selected_dir: Run-selected directory for resolving relative paths.
        analysis_spec: Normalized analysis spec for the run.
        request_text: Original user request text.
        suffixes: Allowed file suffixes for this deliverable type.

    Returns:
        The first explicit deliverable path matching the requested suffixes, or
        ``None`` when no explicit final deliverable was requested.
    """

    protocol_grounding = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec.get("protocol_grounding", {}), dict)
        else {}
    )
    candidate_values: list[str] = []
    output_path = _extract_deliverable_output_path_from_protocol_grounding(protocol_grounding)
    if output_path:
        candidate_values.append(output_path)
    for key in ("required_deliverables", "requested_output_paths"):
        values = analysis_spec.get(key, []) if isinstance(analysis_spec.get(key, []), list) else []
        candidate_values.extend(str(item).strip() for item in values if str(item).strip())
    candidate_values.extend(extract_requested_deliverable_paths(request_text))

    allowed_suffixes = {str(item).strip().lower() for item in suffixes if str(item).strip()}
    seen: set[str] = set()
    for raw_value in candidate_values:
        raw_path = str(raw_value or "").strip()
        if not raw_path:
            continue
        normalized_path = Path(raw_path).expanduser()
        if not normalized_path.is_absolute():
            normalized_path = selected_dir / normalized_path
        resolved_path = normalized_path.resolve(strict=False)
        dedupe_key = str(resolved_path)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        if allowed_suffixes and str(resolved_path.suffix or "").strip().lower() not in allowed_suffixes:
            continue
        return resolved_path
    return None


def _registered_output_candidates(
    step: dict[str, Any],
    *,
    selected_dir: Path | None = None,
) -> list[Path]:
    """Return registry-derived output candidates for one step."""

    if not isinstance(step, dict):
        return []
    registry = default_tool_registry()
    tool_name = str(step.get("tool_name", "") or "").strip().lower()
    args = step.get("arguments", {})
    arguments = args if isinstance(args, dict) else {}
    roots: list[Path] = []
    expected_output_files_by_key = registry.expected_output_files_by_key_for(tool_name)
    expected_candidates: list[Path] = []
    for key in registry.output_argument_keys_for(tool_name):
        raw_value = arguments.get(key, "")
        values = raw_value if isinstance(raw_value, (list, tuple, set)) else [raw_value]
        for value in values:
            rendered = str(value or "").strip()
            if not rendered:
                continue
            path = Path(rendered).expanduser()
            if not path.is_absolute() and selected_dir is not None:
                path = (selected_dir / path).resolve(strict=False)
            else:
                path = path.resolve(strict=False)
            roots.append(path)
            for relative_name in expected_output_files_by_key.get(key, []):
                expected_candidates.append((path / relative_name).resolve(strict=False))
    candidates: list[Path] = []
    for root in roots:
        candidates.append(root)
    if expected_candidates:
        candidates.extend(expected_candidates)
    else:
        for root in roots:
            for relative_name in registry.expected_output_files_for(tool_name):
                candidates.append((root / relative_name).resolve(strict=False))
    return candidates


def _materialize_transcript_quant_deliverable(
    *,
    selected_dir: Path,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any],
    request_text: str = "",
) -> tuple[bool, dict[str, Any]]:
    analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip().lower()
    if analysis_type != "transcript_quantification":
        return False, {"why": "analysis_type_not_transcript_quantification"}

    requested_output_path = _preferred_requested_deliverable_path(
        selected_dir=selected_dir,
        analysis_spec=analysis_spec,
        request_text=request_text,
        suffixes=(".tsv", ".csv"),
    )
    output_path = (
        requested_output_path
        if requested_output_path is not None
        else (selected_dir / "final" / "transcript_counts.tsv").resolve(strict=False)
    )
    if output_path.exists():
        return False, {"why": "deliverable_already_exists", "output_path": str(output_path)}

    source_path: Path | None = None
    source_kind = ""
    stringtie_abundance_path: Path | None = None
    for step in (plan.get("plan", []) if isinstance(plan, dict) else []):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        for candidate in _registered_output_candidates(step, selected_dir=selected_dir):
            if not candidate.exists():
                continue
            if candidate.name == "quant.sf":
                source_path = candidate
                source_kind = "salmon_quant_sf"
                break
            if candidate.name == "abundance.tsv":
                source_path = candidate
                source_kind = "kallisto_abundance_tsv"
                break
            if candidate.name in {"gene_abundances.tsv", "gene_abundance.tsv"}:
                stringtie_abundance_path = candidate
        if source_path is not None:
            break
        if tool_name == "salmon_quant":
            output_dir = str(args.get("output_dir", "") or "").strip()
            if output_dir:
                candidate = Path(output_dir).expanduser().resolve(strict=False) / "quant.sf"
                if candidate.exists():
                    source_path = candidate
                    source_kind = "salmon_quant_sf"
                    break
        if tool_name == "kallisto_quant":
            output_dir = str(args.get("output_dir", "") or "").strip()
            if output_dir:
                candidate = Path(output_dir).expanduser().resolve(strict=False) / "abundance.tsv"
                if candidate.exists():
                    source_path = candidate
                    source_kind = "kallisto_abundance_tsv"
                    break
        if tool_name == "stringtie_quant":
            abundance_tsv = str(args.get("gene_abundance_tsv", "") or "").strip()
            if abundance_tsv:
                candidate = Path(abundance_tsv).expanduser().resolve(strict=False)
                if candidate.exists():
                    stringtie_abundance_path = candidate
    if source_path is None:
        for rel_name in ("quant.sf", "abundance.tsv"):
            matches = sorted(selected_dir.rglob(rel_name))
            if matches:
                source_path = matches[0].resolve(strict=False)
                source_kind = "salmon_quant_sf" if rel_name == "quant.sf" else "kallisto_abundance_tsv"
                break
    if source_path is None and stringtie_abundance_path is None:
        stringtie_matches = sorted(selected_dir.rglob("*_abundance.tsv"))
        if not stringtie_matches:
            stringtie_matches = sorted(selected_dir.rglob("gene_abundances.tsv"))
        if not stringtie_matches:
            stringtie_matches = sorted(selected_dir.rglob("gene_abundance.tsv"))
        if stringtie_matches:
            stringtie_abundance_path = stringtie_matches[0].resolve(strict=False)
    if source_path is None:
        if stringtie_abundance_path is not None:
            return False, {
                "why": "stringtie_quant_outputs_present_without_count_export",
                "output_path": str(output_path),
                "source_path": str(stringtie_abundance_path),
                "source_kind": "stringtie_gene_abundance_tsv",
                "nonfatal": True,
            }
        return False, {"why": "no_quantification_source_found", "output_path": str(output_path)}

    rows, parse_meta = _extract_quantification_counts_for_export(source_path)
    if not rows:
        return False, {
            "why": "quantification_source_empty",
            "output_path": str(output_path),
            "source_path": str(source_path),
            "source_kind": source_kind or parse_meta.get("source_kind", ""),
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("transcript_id\tcount\n")
        for transcript_id, count_value in rows:
            handle.write(f"{transcript_id}\t{count_value}\n")

    return True, {
        "why": "materialized_transcript_quant_deliverable",
        "output_path": str(output_path),
        "source_path": str(source_path),
        "source_kind": source_kind or parse_meta.get("source_kind", ""),
        "row_count": int(parse_meta.get("row_count", len(rows))),
    }


def _extract_deseq_rows_for_export(source_path: Path) -> list[dict[str, str]]:
    if not source_path.exists() or not source_path.is_file():
        return []
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        first_line = handle.readline()
        handle.seek(0)
        delimiter = "," if first_line.count(",") > first_line.count("\t") else "\t"
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            return []
        fieldnames = {str(field).strip(): field for field in reader.fieldnames if str(field).strip()}
        gene_field = next((fieldnames[key] for key in ("gene_id", "gene", "Geneid") if key in fieldnames), "")
        log2fc_field = next((fieldnames[key] for key in ("log2FoldChange", "log2fc") if key in fieldnames), "")
        pvalue_field = next((fieldnames[key] for key in ("pvalue", "p_value") if key in fieldnames), "")
        padj_field = next((fieldnames[key] for key in ("padj", "adj_pvalue", "adjusted_pvalue") if key in fieldnames), "")
        if not (gene_field and log2fc_field and pvalue_field and padj_field):
            return []
        significant_rows: list[dict[str, str]] = []
        measured_rows: list[dict[str, str]] = []
        for row in reader:
            gene_id = str(row.get(gene_field, "") or "").strip()
            if not gene_id:
                continue
            log2fc = str(row.get(log2fc_field, "") or "").strip()
            pvalue = str(row.get(pvalue_field, "") or "").strip()
            padj = str(row.get(padj_field, "") or "").strip()
            try:
                log2fc_value = float(log2fc)
                padj_value = float(padj)
                float(pvalue)
            except Exception:
                continue
            export_row = {
                "gene_id": gene_id,
                "log2FoldChange": log2fc,
                "pvalue": pvalue,
                "padj": padj,
            }
            measured_rows.append(export_row)
            if abs(log2fc_value) > 2.0 and padj_value < 0.01:
                significant_rows.append(export_row)
        return significant_rows or measured_rows


def _materialize_deseq_deliverable(
    *,
    selected_dir: Path,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any],
    request_text: str = "",
) -> tuple[bool, dict[str, Any]]:
    analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip().lower()
    if analysis_type != "rna_seq_differential_expression":
        return False, {"why": "analysis_type_not_rna_seq_differential_expression"}

    requested_output_path = _preferred_requested_deliverable_path(
        selected_dir=selected_dir,
        analysis_spec=analysis_spec,
        request_text=request_text,
        suffixes=(".csv",),
    )
    output_path = (
        requested_output_path
        if requested_output_path is not None
        else (selected_dir / "final" / "deseq_results.csv").resolve(strict=False)
    )
    if output_path.exists():
        return False, {"why": "deliverable_already_exists", "output_path": str(output_path)}

    source_path: Path | None = None
    for step in (plan.get("plan", []) if isinstance(plan, dict) else []):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "deseq2_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        for candidate in _registered_output_candidates(step, selected_dir=selected_dir):
            if candidate.exists() and candidate.name == "deseq2_results.tsv":
                source_path = candidate
                break
        if source_path is not None:
            break
        output_dir = str(args.get("output_dir", "") or "").strip()
        if not output_dir:
            continue
        candidate = Path(output_dir).expanduser().resolve(strict=False) / "deseq2_results.tsv"
        if candidate.exists():
            source_path = candidate
            break
    if source_path is None:
        matches = sorted(selected_dir.rglob("deseq2_results.tsv"))
        if matches:
            source_path = matches[0].resolve(strict=False)
    if source_path is None:
        return False, {"why": "no_deseq2_results_source_found", "output_path": str(output_path)}

    rows = _extract_deseq_rows_for_export(source_path)
    if not rows:
        return False, {
            "why": "deseq2_results_unreadable_or_empty",
            "output_path": str(output_path),
            "source_path": str(source_path),
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gene_id", "log2FoldChange", "pvalue", "padj"])
        writer.writeheader()
        writer.writerows(rows)

    return True, {
        "why": "materialized_deseq_deliverable",
        "output_path": str(output_path),
        "source_path": str(source_path),
        "row_count": len(rows),
    }


def _materialize_single_cell_deliverable(
    *,
    selected_dir: Path,
    analysis_spec: dict[str, Any],
    plan: dict[str, Any] | None = None,
    request_text: str = "",
) -> tuple[bool, dict[str, Any]]:
    analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip().lower()
    if analysis_type != "single_cell_rna_seq":
        return False, {"why": "analysis_type_not_single_cell_rna_seq"}

    requested_output_path = _preferred_requested_deliverable_path(
        selected_dir=selected_dir,
        analysis_spec=analysis_spec,
        request_text=request_text,
        suffixes=(".csv",),
    )
    output_path = (
        requested_output_path
        if requested_output_path is not None
        else (selected_dir / "final" / "single_cell_results.csv").resolve(strict=False)
    )
    if output_path.exists():
        return False, {"why": "deliverable_already_exists", "output_path": str(output_path)}

    cluster_assignments, marker_genes, raw_counts = _resolve_single_cell_artifacts(
        selected_dir=selected_dir,
        plan=plan,
    )
    if cluster_assignments is None or marker_genes is None or raw_counts is None:
        scanpy_assignments, scanpy_markers = _resolve_scanpy_single_cell_artifacts(
            selected_dir=selected_dir,
            plan=plan,
        )
        if scanpy_assignments is not None and scanpy_markers is not None:
            row_count = _export_scanpy_single_cell_results_csv(
                cluster_assignments_csv=scanpy_assignments,
                marker_genes_csv=scanpy_markers,
                output_csv=output_path,
            )
            return True, {
                "why": "materialized_scanpy_single_cell_deliverable",
                "output_path": str(output_path),
                "cluster_assignments_csv": str(scanpy_assignments),
                "marker_genes_csv": str(scanpy_markers),
                "row_count": row_count,
            }
        return False, {
            "why": "single_cell_artifacts_missing",
            "cluster_assignments": str((selected_dir / "cluster_assignments.json").resolve(strict=False)),
            "marker_genes": str((selected_dir / "marker_genes.json").resolve(strict=False)),
            "raw_counts": str((selected_dir / "raw_counts.json").resolve(strict=False)),
        }

    rows = export_single_cell_results_csv(
        cluster_assignments=cluster_assignments,
        marker_genes=marker_genes,
        raw_counts=raw_counts,
        output_csv=output_path,
    )
    return True, {
        "why": "materialized_single_cell_deliverable",
        "output_path": str(output_path),
        "cluster_assignments": str(cluster_assignments),
        "marker_genes": str(marker_genes),
        "raw_counts": str(raw_counts),
        "row_count": len(rows),
    }


def _export_scanpy_single_cell_results_csv(
    *,
    cluster_assignments_csv: Path,
    marker_genes_csv: Path,
    output_csv: Path,
) -> int:
    """Export one compact single-cell deliverable from Scanpy CSV outputs."""
    cluster_counts: dict[str, int] = {}
    with cluster_assignments_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cluster_id = str(row.get("cluster_id", "") or "").strip()
            if not cluster_id:
                continue
            cluster_counts[cluster_id] = cluster_counts.get(cluster_id, 0) + 1

    rows: list[dict[str, str]] = []
    with marker_genes_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cluster_id = str(row.get("cluster_id", "") or "").strip()
            gene_name = str(row.get("gene_name", "") or "").strip()
            if not cluster_id or not gene_name:
                continue
            rows.append(
                {
                    "cluster_id": cluster_id,
                    "cell_count": str(cluster_counts.get(cluster_id, 0)),
                    "rank": str(row.get("rank", "") or "").strip(),
                    "gene_name": gene_name,
                    "score": str(row.get("score", "") or "").strip(),
                    "logfoldchanges": str(row.get("logfoldchanges", "") or "").strip(),
                    "pvals_adj": str(row.get("pvals_adj", "") or "").strip(),
                }
            )

    if not rows:
        raise ValueError(f"Scanpy marker output is empty or unreadable: {marker_genes_csv}")

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["cluster_id", "cell_count", "rank", "gene_name", "score", "logfoldchanges", "pvals_adj"]
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _resolve_single_cell_artifacts(
    *,
    selected_dir: Path,
    plan: dict[str, Any] | None = None,
) -> tuple[Path | None, Path | None, Path | None]:
    """Return the resolved single-cell JSON artifact paths if available.

    Args:
        selected_dir: Run-selected directory for the deliverable.
        plan: Optional normalized plan used to discover wrapper ``output_dir``
            values for UI-originated runs.

    Returns:
        A tuple of ``(cluster_assignments, marker_genes, raw_counts)`` paths
        when a matching artifact root is found, otherwise ``(None, None, None)``.
    """
    candidate_roots: list[Path] = [
        selected_dir.resolve(strict=False),
        (selected_dir / "sc_output").resolve(strict=False),
    ]
    if isinstance(plan, dict):
        for step in plan.get("plan", []):
            if not isinstance(step, dict) or str(step.get("tool_name", "") or "").strip() != "sc_count_and_cluster":
                continue
            for candidate in _registered_output_candidates(step, selected_dir=selected_dir):
                candidate_roots.append(candidate)
                if candidate.name:
                    candidate_roots.append(candidate.parent.resolve(strict=False))
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            output_dir_raw = str(args.get("output_dir", "") or "").strip()
            if not output_dir_raw:
                continue
            output_dir = Path(output_dir_raw).expanduser().resolve(strict=False)
            candidate_roots.append(output_dir)
            candidate_roots.append((output_dir / "sc_output").resolve(strict=False))

    seen: set[Path] = set()
    for root in candidate_roots:
        if root in seen:
            continue
        seen.add(root)
        cluster_assignments = (root / "cluster_assignments.json").resolve(strict=False)
        marker_genes = (root / "marker_genes.json").resolve(strict=False)
        raw_counts = (root / "raw_counts.json").resolve(strict=False)
        if cluster_assignments.exists() and marker_genes.exists() and raw_counts.exists():
            return cluster_assignments, marker_genes, raw_counts

    return None, None, None


def _resolve_scanpy_single_cell_artifacts(
    *,
    selected_dir: Path,
    plan: dict[str, Any] | None = None,
) -> tuple[Path | None, Path | None]:
    """Return Scanpy CSV artifact paths when one Scanpy wrapper produced them."""
    candidate_roots: list[Path] = [
        selected_dir.resolve(strict=False),
        (selected_dir / "output").resolve(strict=False),
    ]
    if isinstance(plan, dict):
        for step in plan.get("plan", []):
            if not isinstance(step, dict) or str(step.get("tool_name", "") or "").strip() != "scanpy_workflow":
                continue
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            output_dir_raw = str(args.get("output_dir", "") or "").strip()
            if not output_dir_raw:
                continue
            candidate_roots.append(Path(output_dir_raw).expanduser().resolve(strict=False))

    seen: set[Path] = set()
    for root in candidate_roots:
        if root in seen:
            continue
        seen.add(root)
        cluster_assignments = (root / "cluster_assignments.csv").resolve(strict=False)
        marker_genes = (root / "marker_genes.csv").resolve(strict=False)
        if cluster_assignments.exists() and marker_genes.exists():
            return cluster_assignments, marker_genes
    return None, None


def _materialize_cystic_fibrosis_deliverable(
    *,
    selected_dir: Path,
    data_root: Path,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any],
    request_text: str,
) -> tuple[bool, dict[str, Any]]:
    if not _is_cystic_fibrosis_task(analysis_spec, request_text):
        return False, {"why": "not_cystic_fibrosis_task"}

    requested_output_path = _preferred_requested_deliverable_path(
        selected_dir=selected_dir,
        analysis_spec=analysis_spec,
        request_text=request_text,
        suffixes=(".csv",),
    )
    output_path = (
        requested_output_path
        if requested_output_path is not None
        else (selected_dir / "final" / "cf_variants.csv").resolve(strict=False)
    )
    if output_path.exists():
        return False, {"why": "deliverable_already_exists", "output_path": str(output_path)}

    discovered = _discover_cystic_fibrosis_inputs(plan=plan, selected_dir=selected_dir, data_root=data_root)
    input_vcf = str(discovered.get("input_vcf", "") or "").strip()
    family_description = str(discovered.get("family_description", "") or "").strip()
    if not input_vcf or not family_description:
        return False, {
            "why": "missing_cystic_fibrosis_inputs",
            "input_vcf": input_vcf,
            "family_description": family_description,
        }

    meta = export_cystic_fibrosis_csv(
        input_vcf=Path(input_vcf),
        family_description=Path(family_description),
        output_csv=output_path,
        gene_hint="CFTR",
        clinvar_vcf=Path(discovered["clinvar_vcf"]) if str(discovered.get("clinvar_vcf", "")).strip() else None,
    )
    return True, {
        "why": "materialized_cystic_fibrosis_deliverable",
        **meta,
    }


def _materialize_multi_model_dge_pathway_deliverable(
    *,
    selected_dir: Path,
    analysis_spec: dict[str, Any],
    request_text: str = "",
) -> tuple[bool, dict[str, Any]]:
    analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip().lower()
    if analysis_type != "multi_model_dge_pathway":
        return False, {"why": "analysis_type_not_multi_model_dge_pathway"}

    requested_output_path = _preferred_requested_deliverable_path(
        selected_dir=selected_dir,
        analysis_spec=analysis_spec,
        request_text=request_text,
        suffixes=(".csv",),
    )
    output_path = (
        requested_output_path
        if requested_output_path is not None
        else (selected_dir / "final" / "pathway_comparison.csv").resolve(strict=False)
    )
    if output_path.exists():
        return False, {"why": "deliverable_already_exists", "output_path": str(output_path)}

    for candidate in sorted(selected_dir.rglob("pathway_comparison.csv")):
        resolved = candidate.resolve(strict=False)
        if resolved == output_path:
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved, output_path)
        return True, {
            "why": "copied_existing_pathway_comparison",
            "source_path": str(resolved),
            "output_path": str(output_path),
        }

    label_aliases = {
        "5xFAD": ("5xFAD", "5XFAD"),
        "3xTG_AD": ("3xTG_AD", "3XTG_AD", "3xTG-AD", "3XTG-AD"),
        "PS3O1S": ("PS3O1S", "PS301S"),
    }
    label_to_csv: dict[str, Path] = {}
    for candidate in sorted(selected_dir.rglob("*.csv")):
        name = candidate.name
        if not name.lower().startswith("kegg_"):
            continue
        stem = candidate.stem[5:]
        stem_upper = stem.upper()
        for label, aliases in label_aliases.items():
            if any(stem_upper == alias.upper() for alias in aliases):
                label_to_csv[label] = candidate.resolve(strict=False)
                break

    if len(label_to_csv) < 3:
        return False, {
            "why": "missing_model_enrichment_csvs",
            "discovered_labels": sorted(label_to_csv.keys()),
        }

    meta = export_multi_model_pathway_comparison(
        label_to_csv=label_to_csv,
        output_csv=output_path,
    )
    return True, {
        "why": "materialized_multi_model_dge_pathway_deliverable",
        **meta,
    }
