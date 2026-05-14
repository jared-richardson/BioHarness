#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "benchmark_data" / "bioagentbench_official_manifest.json"
DEFAULT_TASK_METADATA = PROJECT_ROOT / "external" / "bioagent-bench" / "src" / "task_metadata.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.bioagentbench_official import resolve_manifest_entries


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export local official-mode benchmark runs into the RUN_LOGS layout expected by "
            "bioagent-experiments/src/eval.py."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="official_summary.json files or directories containing official_summary.json.",
    )
    parser.add_argument("--out-dir", required=True, help="RUN_LOGS-style export directory.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--task-metadata", default=str(DEFAULT_TASK_METADATA))
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Export only the specified task IDs. Repeatable.",
    )
    parser.add_argument(
        "--experiment-name",
        default="bio_harness_local_official_export",
        help="Experiment name recorded in exported run metadata.",
    )
    parser.add_argument(
        "--copy-results",
        action="store_true",
        help="Copy result files instead of symlinking them where possible.",
    )
    parser.add_argument(
        "--allow-unsupported",
        action="store_true",
        help="Skip unsupported tasks instead of exiting with an error.",
    )
    return parser.parse_args()


def _resolve_summary_paths(inputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            candidate = path / "official_summary.json"
            if candidate.exists():
                paths.append(candidate)
            continue
        if path.is_file():
            paths.append(path)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _load_rows(summary_paths: list[Path], *, wanted_tasks: set[str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in summary_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "") or "").strip()
            if wanted_tasks and task_id not in wanted_tasks:
                continue
            rows.append(item)
    return rows


def _load_task_prompts(task_metadata_path: Path) -> dict[str, str]:
    payload = json.loads(task_metadata_path.read_text(encoding="utf-8"))
    prompts: dict[str, str] = {}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "") or "").strip()
            task_prompt = str(item.get("task_prompt", "") or "").strip()
            if task_id and task_prompt:
                prompts[task_id] = task_prompt
    return prompts


def _stable_run_hash(task_id: str, selected_dir: Path) -> str:
    digest = hashlib.sha1(f"{task_id}:{selected_dir}".encode("utf-8")).hexdigest()
    return digest[:16]


def _safe_unlink(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _mirror_processing_tree(selected_dir: Path, outputs_root: Path) -> None:
    outputs_root.mkdir(parents=True, exist_ok=True)
    for source in sorted(selected_dir.rglob("*")):
        rel = source.relative_to(selected_dir)
        if len(rel.parts) > 2:
            continue
        if rel.parts and rel.parts[0] in {"harness.log", "validator.log"}:
            continue
        target = outputs_root / rel
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        _ensure_parent(target)
        _safe_unlink(target)
        try:
            os.symlink(source, target)
        except Exception:
            shutil.copy2(source, target)


def _link_or_copy(source: Path, target: Path, *, copy_mode: bool = False) -> None:
    _ensure_parent(target)
    _safe_unlink(target)
    if copy_mode:
        shutil.copy2(source, target)
        return
    try:
        os.symlink(source, target)
    except Exception:
        shutil.copy2(source, target)


def _gzip_copy(source: Path, target: Path) -> None:
    _ensure_parent(target)
    _safe_unlink(target)
    with source.open("rb") as src, gzip.open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)


def _export_results(task_id: str, selected_dir: Path, results_root: Path, *, copy_mode: bool) -> list[str]:
    results_root.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []

    def link(rel_source: str, rel_target: str | None = None) -> None:
        source = selected_dir / rel_source
        if not source.exists():
            raise FileNotFoundError(f"Missing expected result artifact: {source}")
        target = results_root / (rel_target or Path(rel_source).name)
        _link_or_copy(source, target, copy_mode=copy_mode)
        exported.append(str(target))

    if task_id == "alzheimer-mouse":
        link("final/pathway_comparison.csv")
    elif task_id == "cystic-fibrosis":
        link("final/cf_variants.csv")
    elif task_id == "deseq":
        link("final/deseq_results.csv")
    elif task_id == "evolution":
        link("final/variants_shared.csv")
    elif task_id == "giab":
        source = selected_dir / "final" / "variants.vcf"
        if not source.exists():
            raise FileNotFoundError(f"Missing expected GIAB VCF: {source}")
        target = results_root / "variants.vcf.gz"
        _gzip_copy(source, target)
        exported.append(str(target))
    elif task_id == "transcript-quant":
        link("final/transcript_counts.tsv")
    elif task_id == "single-cell":
        link("final/single_cell_results.csv")
    else:
        raise ValueError(f"Task is not supported for official-evaluator export: {task_id}")
    return exported


def _build_metadata(
    *,
    run_hash: str,
    metadata_path: Path,
    run_dir_path: Path,
    data_path: Path,
    task_id: str,
    task_prompt: str,
    model_name: str,
    experiment_name: str,
) -> dict[str, Any]:
    return {
        "run_hash": run_hash,
        "metadata_path": str(metadata_path),
        "use_reference_data": True,
        "timestamp": datetime.now().isoformat(),
        "task_id": task_id,
        "task_prompt": task_prompt,
        "num_tools": 0,
        "tool_names": [],
        "tools": [],
        "system_prompt_name": "bio_harness_export",
        "experiment_name": experiment_name,
        "input_tokens": 0,
        "output_tokens": 0,
        "model": model_name,
        "duration": 0.0,
        "error_type": None,
        "error_message": None,
        "eval_results": None,
        "run_dir_path": str(run_dir_path),
        "data_path": str(data_path),
        "otel_sink_host": "127.0.0.1:4317",
        "otel_sink_path": str(run_dir_path.parent.parent.parent / "otel" / f"otlp-{run_hash}.ndjson"),
    }


def main() -> int:
    args = _parse_args()
    summary_paths = _resolve_summary_paths(args.inputs)
    if not summary_paths:
        raise SystemExit("No official_summary.json inputs found.")

    wanted_tasks = {str(task_id).strip() for task_id in (args.task_id or []) if str(task_id).strip()} or None
    rows = _load_rows(summary_paths, wanted_tasks=wanted_tasks)
    if not rows:
        raise SystemExit("No matching official summary rows found.")

    manifest_entries = {
        str(entry.get("task_id", "") or "").strip(): entry
        for entry in resolve_manifest_entries(Path(args.manifest).expanduser().resolve())
    }
    task_prompts = _load_task_prompts(Path(args.task_metadata).expanduser().resolve())

    out_dir = Path(args.out_dir).expanduser().resolve()
    runs_dir = out_dir / "runs"
    exported_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for row in rows:
        task_id = str(row.get("task_id", "") or "").strip()
        selected_dir = Path(str(row.get("selected_dir", "") or "")).resolve(strict=False)
        if row.get("official_report_bucket") != "official_blind_clean" or row.get("validation_passed") is not True:
            skipped.append({"task_id": task_id, "reason": "not_clean_validated_run"})
            continue
        entry = manifest_entries.get(task_id)
        if not entry:
            skipped.append({"task_id": task_id, "reason": "missing_manifest_entry"})
            continue
        if task_id not in task_prompts:
            skipped.append({"task_id": task_id, "reason": "missing_upstream_task_prompt"})
            continue

        run_hash = _stable_run_hash(task_id, selected_dir)
        run_dir = out_dir / str(args.experiment_name) / task_id / run_hash
        outputs_root = run_dir / "outputs"
        results_root = run_dir / "results"
        metadata_path = runs_dir / f"{run_hash}.json"

        try:
            _mirror_processing_tree(selected_dir, outputs_root)
            exported_results = _export_results(
                task_id,
                selected_dir,
                results_root,
                copy_mode=bool(args.copy_results),
            )
        except Exception as exc:
            if args.allow_unsupported:
                skipped.append({"task_id": task_id, "reason": str(exc)})
                continue
            raise

        metadata = _build_metadata(
            run_hash=run_hash,
            metadata_path=metadata_path,
            run_dir_path=run_dir,
            data_path=Path(str(entry.get("task_dir", "") or "")).resolve(strict=False),
            task_id=task_id,
            task_prompt=task_prompts[task_id],
            model_name=str(row.get("executor_model_name", "") or "bio_harness"),
            experiment_name=str(args.experiment_name),
        )
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        exported_rows.append(
            {
                "task_id": task_id,
                "selected_dir": str(selected_dir),
                "run_hash": run_hash,
                "run_dir": str(run_dir),
                "metadata_path": str(metadata_path),
                "results": exported_results,
            }
        )

    export_manifest = {
        "created_at": datetime.now().isoformat(),
        "summary_inputs": [str(path) for path in summary_paths],
        "task_filter": sorted(wanted_tasks or []),
        "experiment_name": str(args.experiment_name),
        "exported_count": len(exported_rows),
        "skipped_count": len(skipped),
        "exported": exported_rows,
        "skipped": skipped,
    }
    manifest_path = out_dir / "bioagent_experiments_export_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(export_manifest, indent=2), encoding="utf-8")
    print(f"[export] manifest={manifest_path}")
    print(f"[export] exported={len(exported_rows)} skipped={len(skipped)}")
    for row in exported_rows:
        print(f"[export] task={row['task_id']} run_dir={row['run_dir']}")
    for item in skipped:
        print(f"[skip] task={item['task_id']} reason={item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
