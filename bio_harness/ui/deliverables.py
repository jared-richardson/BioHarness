"""UI-specific post-execution deliverable helpers."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from bio_harness.core.schemas import safe_parse_deliverable_metadata
from bio_harness.harness.deliverable_packaging import package_deliverables


def _ui_selected_dir(run: dict[str, Any]) -> Path:
    run_dir = str(run.get("run_dir", "") or "").strip()
    if run_dir:
        return Path(run_dir).expanduser().resolve(strict=False)
    selected_dir = str(run.get("selected_dir", "") or "").strip()
    if selected_dir:
        return Path(selected_dir).expanduser().resolve(strict=False)
    return Path.cwd().resolve()


_EXPLICIT_OUTPUT_FILE_KEYS = (
    "output_tree",
    "output_file",
    "output_gtf",
    "output_tsv",
    "output_csv",
    "output_json",
    "output_jsonl",
    "output_txt",
    "output_md",
    "output_html",
    "output_pdf",
    "output_png",
    "output_svg",
    "output_vcf",
    "output_vcf_gz",
    "output_bed",
    "output_bedgraph",
    "output_bw",
    "gene_abundance_tsv",
)


def _iter_explicit_output_files(plan: dict[str, Any]) -> list[tuple[str, str, Path]]:
    """Collect explicit output-file arguments from a normalized plan.

    Args:
        plan: Normalized execution plan mapping.

    Returns:
        A list of ``(tool_name, argument_name, path)`` rows for existing output
        file arguments that are represented directly in step arguments.
    """
    rows: list[tuple[str, str, Path]] = []
    for step in plan.get("plan", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        for key in _EXPLICIT_OUTPUT_FILE_KEYS:
            raw_path = str(args.get(key, "") or "").strip()
            if not raw_path:
                continue
            rows.append(
                (
                    tool_name,
                    key,
                    Path(raw_path).expanduser().resolve(strict=False),
                )
            )
    return rows


def capture_ui_run_final_outputs(run: dict[str, Any]) -> dict[str, Any]:
    """Mirror explicit output files into the UI run bundle.

    Some wrappers write final outputs to a selected workspace directory rather
    than directly under the run bundle. This helper copies explicit, already
    materialized output files into ``run_dir/final`` so chat previews, archived
    receipts, and benchmark-style validation can all use one stable location.

    Args:
        run: In-memory UI run record.

    Returns:
        A mapping with exported rows and non-fatal skipped rows.
    """
    plan = run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {}
    run_root = _ui_selected_dir(run)
    target_root = (run_root / "final").resolve(strict=False)
    target_root.mkdir(parents=True, exist_ok=True)

    exported_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    seen_targets: set[Path] = set()

    for tool_name, argument_name, source_path in _iter_explicit_output_files(plan):
        if not source_path.exists() or not source_path.is_file():
            skipped_rows.append(
                {
                    "why": "explicit_output_missing",
                    "tool_name": tool_name,
                    "argument_name": argument_name,
                    "source_path": str(source_path),
                }
            )
            continue
        target_path = (target_root / source_path.name).resolve(strict=False)
        if target_path in seen_targets:
            continue
        seen_targets.add(target_path)
        if target_path == source_path:
            continue
        if target_path.exists():
            skipped_rows.append(
                {
                    "why": "explicit_output_already_captured",
                    "tool_name": tool_name,
                    "argument_name": argument_name,
                    "source_path": str(source_path),
                    "output_path": str(target_path),
                }
            )
            continue
        shutil.copy2(source_path, target_path)
        payload = {
            "why": "captured_explicit_output",
            "tool_name": tool_name,
            "argument_name": argument_name,
            "source_path": str(source_path),
            "output_path": str(target_path),
        }
        parsed = safe_parse_deliverable_metadata(payload)
        exported_rows.append(parsed.model_dump(mode="json") if parsed is not None else payload)

    return {"exported": exported_rows, "skipped": skipped_rows}


def materialize_ui_run_deliverables(run: dict[str, Any]) -> dict[str, Any]:
    """Materialize final deliverables for one completed UI run.

    Args:
        run: In-memory UI run record.

    Returns:
        A mapping with exported deliverable metadata and any hard failures.
    """
    selected_dir = _ui_selected_dir(run)
    plan = run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {}
    analysis_spec = run.get("analysis_spec", {}) if isinstance(run.get("analysis_spec", {}), dict) else {}
    return package_deliverables(
        selected_dir=selected_dir,
        analysis_spec=analysis_spec,
        plan=plan,
        request_text=str(run.get("user_request", "") or ""),
    )
