#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.bioagentbench_official import build_official_scoreboard, render_official_scoreboard_markdown


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an aggregate BioAgentBench official-mode scoreboard from one or more official_summary.json files."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Summary JSON files or directories containing official_summary.json.",
    )
    parser.add_argument("--out-dir", required=True, help="Directory where official_scoreboard.json/md will be written.")
    parser.add_argument(
        "--label",
        default="aggregate",
        help="Human-readable label recorded in the generated scoreboard metadata.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Include only rows for the specified task ID. Repeatable.",
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
    unique_paths: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(path)
    return unique_paths


def _collect_rows(summary_paths: list[Path], *, task_ids: set[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    for path in summary_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if task_ids and str(item.get("task_id", "") or "").strip() not in task_ids:
                continue
            rows.append(item)
    return rows


def main() -> int:
    args = _parse_args()
    summary_paths = _resolve_summary_paths(args.inputs)
    if not summary_paths:
        raise SystemExit("No official_summary.json inputs found.")
    task_ids = {str(task_id).strip() for task_id in (args.task_id or []) if str(task_id).strip()}
    rows = _collect_rows(summary_paths, task_ids=task_ids or None)
    if not rows:
        raise SystemExit("No matching official summary rows found.")
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scoreboard = {
        "created_at": datetime.now().isoformat(),
        "label": str(args.label or "aggregate"),
        "summary_inputs": [str(path) for path in summary_paths],
        "task_filter": sorted(task_ids),
        **build_official_scoreboard(rows),
    }
    scoreboard_json = out_dir / "official_scoreboard.json"
    scoreboard_md = out_dir / "official_scoreboard.md"
    scoreboard_json.write_text(json.dumps(scoreboard, indent=2), encoding="utf-8")
    scoreboard_md.write_text(render_official_scoreboard_markdown(scoreboard), encoding="utf-8")
    print(f"[scoreboard] inputs={len(summary_paths)} rows={len(rows)}")
    print(f"[scoreboard] json={scoreboard_json}")
    print(f"[scoreboard] md={scoreboard_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
