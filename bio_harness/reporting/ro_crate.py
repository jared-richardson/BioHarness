"""Export completed Bio-Harness runs as lightweight RO-Crate bundles."""

from __future__ import annotations

import csv
import json
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bio_harness.reporting.run_context import (
    build_artifact_inventory,
    final_plan_steps,
    resolve_run_context,
)

_COPYABLE_SUFFIXES = {
    ".csv",
    ".tsv",
    ".txt",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".yaml",
    ".yml",
    ".treefile",
    ".nwk",
    ".vcf",
    ".gff",
    ".gtf",
}
_MAX_COPY_BYTES = 20 * 1024 * 1024


def _safe_name(path: Path) -> str:
    return path.name.replace(" ", "_")


def _copy_if_small(path: Path, destination_dir: Path) -> str | None:
    if not path.is_file():
        return None
    if path.suffix.lower() not in _COPYABLE_SUFFIXES:
        return None
    if path.stat().st_size > _MAX_COPY_BYTES:
        return None
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / _safe_name(path)
    shutil.copy2(path, target)
    return str(target.relative_to(destination_dir.parent))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def export_run_ro_crate(run_input: str | Path, output_dir: str | Path | None = None) -> Path:
    """Export a completed run as a lightweight RO-Crate bundle."""
    context = resolve_run_context(run_input)
    crate_dir = Path(output_dir).expanduser().resolve() if output_dir else (context.selected_dir / "reports" / "ro_crate")
    crate_dir.mkdir(parents=True, exist_ok=True)

    copied_dir = crate_dir / "artifacts"
    copied_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "selected_dir": str(context.selected_dir),
        "run_dir": str(context.run_dir),
        "status": str(context.result.get("status", "") or ""),
        "benchmark_policy": str(context.result.get("benchmark_policy", "") or ""),
        "auto_repair_history_count": int(context.result.get("auto_repair_history_count", 0) or 0),
        "validator_passed": "BENCHMARK PASSED: True" in ((context.validator_log_path.read_text(encoding="utf-8") if context.validator_log_path else "") or ""),
        "final_plan_steps": len(final_plan_steps(context)),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(crate_dir / "run_summary.json", summary)
    _write_json(crate_dir / "workflow_plan.json", context.final_plan if isinstance(context.final_plan, dict) else {})

    inventory = build_artifact_inventory(context)
    _write_csv(crate_dir / "artifact_manifest.csv", inventory)

    copied_entities: list[dict[str, Any]] = []
    for path in [
        context.result_path,
        context.validator_log_path,
        context.harness_log_path,
        context.state_path,
        context.events_path,
        context.execution_log_path,
    ]:
        if path is None:
            continue
        rel = _copy_if_small(path, copied_dir)
        if rel:
            copied_entities.append({"source_path": str(path), "crate_path": rel})

    final_dir = context.selected_dir / "final"
    if final_dir.is_dir():
        for path in sorted(p for p in final_dir.rglob("*") if p.is_file()):
            rel = _copy_if_small(path, copied_dir)
            if rel:
                copied_entities.append({"source_path": str(path), "crate_path": rel})

    _write_csv(crate_dir / "copied_artifacts.csv", copied_entities)

    graph: list[dict[str, Any]] = [
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "about": {"@id": "./"},
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name": f"Bio-Harness run crate: {context.selected_dir.name}",
            "description": "Lightweight RO-Crate bundle for a completed Bio-Harness run.",
            "datePublished": datetime.now(timezone.utc).date().isoformat(),
            "hasPart": [],
        },
    ]

    root_has_part = graph[1]["hasPart"]
    assert isinstance(root_has_part, list)

    for row in copied_entities:
        crate_id = row["crate_path"]
        mime_type = mimetypes.guess_type(crate_id)[0] or "application/octet-stream"
        graph.append(
            {
                "@id": crate_id,
                "@type": "File",
                "name": Path(crate_id).name,
                "encodingFormat": mime_type,
                "contentUrl": row["source_path"],
            }
        )
        root_has_part.append({"@id": crate_id})

    for rel_name in ["run_summary.json", "workflow_plan.json", "artifact_manifest.csv", "copied_artifacts.csv"]:
        mime_type = mimetypes.guess_type(rel_name)[0] or "application/octet-stream"
        graph.append(
            {
                "@id": rel_name,
                "@type": "File",
                "name": rel_name,
                "encodingFormat": mime_type,
            }
        )
        root_has_part.append({"@id": rel_name})

    metadata = {"@context": "https://w3id.org/ro/crate/1.1/context", "@graph": graph}
    _write_json(crate_dir / "ro-crate-metadata.json", metadata)
    return crate_dir
