"""Shared post-execution deliverable packaging helpers.

This module centralizes deliverable materialization so CLI and UI execution
paths publish the same final artifacts and benchmark sidecars.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from bio_harness.core.schemas import safe_parse_deliverable_metadata
from bio_harness.harness.deliverables import (
    _materialize_cystic_fibrosis_deliverable,
    _materialize_deseq_deliverable,
    _materialize_multi_model_dge_pathway_deliverable,
    _materialize_single_cell_deliverable,
    _materialize_transcript_quant_deliverable,
)


def _normalized_metadata_row(meta: dict[str, Any], *, analysis_type: str) -> dict[str, Any]:
    """Normalize one deliverable metadata payload.

    Args:
        meta: Raw materializer metadata.
        analysis_type: Canonical analysis type label for the materializer.

    Returns:
        A JSON-serializable metadata mapping.
    """

    row = dict(meta or {})
    row["analysis_type"] = analysis_type
    parsed = safe_parse_deliverable_metadata(row)
    return parsed.model_dump(mode="json") if parsed is not None else row


def _mirror_single_cell_sidecars_into_bundle(
    *,
    selected_dir: Path,
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """Copy single-cell benchmark sidecars into the canonical bundle root.

    Args:
        selected_dir: Run-selected directory.
        meta: Metadata row returned by single-cell deliverable materialization.

    Returns:
        Metadata rows describing any copied sidecars.
    """

    exported_rows: list[dict[str, Any]] = []
    for key, target_name in (
        ("cluster_assignments", "cluster_assignments.json"),
        ("marker_genes", "marker_genes.json"),
        ("raw_counts", "raw_counts.json"),
    ):
        source_raw = str(meta.get(key, "") or "").strip()
        if not source_raw:
            continue
        source_path = Path(source_raw).expanduser().resolve(strict=False)
        if not source_path.exists() or not source_path.is_file():
            continue
        target_path = (selected_dir / target_name).resolve(strict=False)
        if source_path == target_path or target_path.exists():
            continue
        shutil.copy2(source_path, target_path)
        exported_rows.append(
            {
                "why": "mirrored_single_cell_sidecar",
                "analysis_type": "single_cell_rna_seq",
                "sidecar": key,
                "source_path": str(source_path),
                "output_path": str(target_path),
            }
        )
    return exported_rows


def package_deliverables(
    *,
    selected_dir: Path,
    analysis_spec: dict[str, Any],
    plan: dict[str, Any] | None,
    data_root: Path | None = None,
    request_text: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Materialize and publish final deliverables for one run.

    Args:
        selected_dir: Run-selected directory containing step outputs.
        analysis_spec: Normalized analysis specification for the run.
        plan: Normalized execution plan, if one is available.
        data_root: Optional run data root for deliverables that inspect inputs.
        request_text: Optional user request text for task-specific publishers.

    Returns:
        A mapping with exported deliverable metadata rows and non-fatal or hard
        failure rows.
    """

    analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip().lower()
    if not analysis_type:
        return {"exported": [], "failures": []}

    normalized_plan = plan if isinstance(plan, dict) else {}
    exported_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []

    materializers: list[tuple[str, Callable[[], tuple[bool, dict[str, Any]]]]] = []
    if analysis_type == "transcript_quantification":
        materializers.append(
            (
                "transcript_quantification",
                lambda: _materialize_transcript_quant_deliverable(
                    selected_dir=selected_dir,
                    plan=normalized_plan,
                    analysis_spec=analysis_spec,
                    request_text=request_text,
                ),
            )
        )
    if analysis_type == "rna_seq_differential_expression":
        materializers.append(
            (
                "rna_seq_differential_expression",
                lambda: _materialize_deseq_deliverable(
                    selected_dir=selected_dir,
                    plan=normalized_plan,
                    analysis_spec=analysis_spec,
                    request_text=request_text,
                ),
            )
        )
    if analysis_type == "single_cell_rna_seq":
        materializers.append(
            (
                "single_cell_rna_seq",
                lambda: _materialize_single_cell_deliverable(
                    selected_dir=selected_dir,
                    analysis_spec=analysis_spec,
                    plan=normalized_plan,
                    request_text=request_text,
                ),
            )
        )
    if analysis_type == "multi_model_dge_pathway":
        materializers.append(
            (
                "multi_model_dge_pathway",
                lambda: _materialize_multi_model_dge_pathway_deliverable(
                    selected_dir=selected_dir,
                    analysis_spec=analysis_spec,
                    request_text=request_text,
                ),
            )
        )
    if data_root is not None:
        materializers.append(
            (
                "cystic_fibrosis",
                lambda: _materialize_cystic_fibrosis_deliverable(
                    selected_dir=selected_dir,
                    data_root=data_root,
                    plan=normalized_plan,
                    analysis_spec=analysis_spec,
                    request_text=request_text,
                ),
            )
        )

    for label, materialize in materializers:
        changed, meta = materialize()
        meta_row = _normalized_metadata_row(meta, analysis_type=label)
        if changed:
            exported_rows.append(meta_row)
            if label == "single_cell_rna_seq":
                exported_rows.extend(
                    _mirror_single_cell_sidecars_into_bundle(
                        selected_dir=selected_dir,
                        meta=meta_row,
                    )
                )
            continue
        if meta_row.get("why") == "deliverable_already_exists":
            continue
        if bool(meta_row.get("nonfatal", False)):
            continue
        if label == "cystic_fibrosis" and meta_row.get("why") == "not_cystic_fibrosis_task":
            continue
        failure_rows.append(meta_row)

    return {"exported": exported_rows, "failures": failure_rows}
