"""Summarize real harness skill coverage for Qwen-family runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_INDEX_PATH = PROJECT_ROOT / "bio_harness" / "skills" / "definitions" / "index.json"


def _load_skill_names() -> list[str]:
    payload = json.loads(SKILL_INDEX_PATH.read_text(encoding="utf-8"))
    rows = payload.get("skills", []) if isinstance(payload.get("skills", []), list) else []
    return sorted(
        str(row.get("name", "")).strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("name", "")).strip()
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_tools_from_events(events_path: Path, skill_names: set[str]) -> set[str]:
    tools: set[str] = set()
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        inner = payload.get("payload", {})
        if not isinstance(inner, dict):
            continue
        tool_name = str(inner.get("tool_name", "")).strip()
        if tool_name in skill_names:
            tools.add(tool_name)
    return tools


def build_qwen_skill_coverage(
    workspace_runs_root: str | Path,
    *,
    include_benchmark_runs: bool = False,
    model_token: str = "qwen3-coder-next",
) -> dict[str, Any]:
    """Build a harness skill coverage summary from real run artifacts."""
    root = Path(workspace_runs_root).expanduser().resolve()
    skill_names = _load_skill_names()
    skill_name_set = set(skill_names)
    covered_by_skill: dict[str, list[str]] = {name: [] for name in skill_names}
    scanned_runs = 0

    for manifest_path in sorted(root.glob("*/manifest.json")):
        manifest = _load_manifest(manifest_path)
        model_name = str(manifest.get("model_name", "") or manifest.get("model", "") or "").strip()
        if model_token not in model_name:
            continue
        selected_dir = str(manifest.get("selected_dir", "") or "").strip()
        if (not include_benchmark_runs) and "bioagent-bench" in selected_dir:
            continue
        run_dir = manifest_path.parent
        events_path = run_dir / "events.jsonl"
        if not events_path.exists():
            continue
        scanned_runs += 1
        tool_names = _iter_tools_from_events(events_path, skill_name_set)
        for tool_name in sorted(tool_names):
            covered_by_skill[tool_name].append(run_dir.name)

    covered = sorted([name for name, runs in covered_by_skill.items() if runs])
    missing = [name for name in skill_names if not covered_by_skill[name]]
    return {
        "workspace_runs_root": str(root),
        "model_token": model_token,
        "include_benchmark_runs": bool(include_benchmark_runs),
        "scanned_run_count": scanned_runs,
        "total_skill_count": len(skill_names),
        "covered_skill_count": len(covered),
        "covered_skills": covered,
        "missing_skills": missing,
        "skill_runs": {name: covered_by_skill[name] for name in covered},
    }
