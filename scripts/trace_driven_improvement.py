#!/usr/bin/env python3
"""Mine failed runs into repair-advisory proposals.

This script is an offline, heuristic-first assistant for turning repeated run
failures into auditable repair-advisory suggestions. It never edits code and
only writes the advisory catalog when ``--write`` is explicitly requested.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.harness.repair_context import (  # noqa: E402
    REPAIR_ADVISORIES_PATH,
    load_repair_advisories,
    save_repair_advisories,
    upsert_repair_advisory,
)

_FAILED_STATUSES = {"failed", "error", "invalid_for_official_reporting"}
_STEP_KEY_CANDIDATES = (
    "failed_step_number",
    "first_failed_step_number",
)
_TOOL_MISSING_PATTERNS = (
    "not found",
    "binary not found",
    "command not found",
    "module not found",
    "no module named",
)


def _collect_failed_runs(runs_root: Path) -> list[dict[str, Any]]:
    """Collect normalized failed-run records under one root directory."""

    root = Path(runs_root).expanduser().resolve(strict=False)
    if not root.exists():
        return []

    failed: list[dict[str, Any]] = []
    for result_path in sorted(root.rglob("result.json")):
        payload = _load_json(result_path)
        status = str(payload.get("status", "") or "").strip().lower()
        if status not in _FAILED_STATUSES:
            continue
        run_dir = result_path.parent
        failed.append(
            {
                "status": status,
                "result_path": str(result_path),
                "run_dir": str(run_dir),
                "failure_class": str(payload.get("failure_class", "") or "").strip(),
                "analysis_type": str(payload.get("analysis_type", "") or "").strip(),
                "failed_step_number": _failed_step_number(payload),
                "failed_tool_name": _failed_tool_name(payload),
                "stderr_tail": _collect_stderr_for_run(run_dir),
                "auto_repair_history": _collect_repair_history(payload),
                "result": payload,
            }
        )
    return failed


def _collect_repair_history(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize repair-history entries from one result payload."""

    raw_history = result.get("auto_repair_history", [])
    if not isinstance(raw_history, list):
        return []

    history: list[dict[str, Any]] = []
    for entry in raw_history:
        if isinstance(entry, Mapping):
            row = dict(entry)
        elif hasattr(entry, "model_dump"):
            try:
                row = dict(entry.model_dump())  # type: ignore[attr-defined]
            except Exception:
                continue
        elif hasattr(entry, "__dict__"):
            row = dict(getattr(entry, "__dict__", {}))
        else:
            continue
        history.append(
            {
                "action": str(row.get("action", "") or "").strip(),
                "failure_class": str(row.get("failure_class", "") or "").strip(),
                "details": row.get("details", {}) if isinstance(row.get("details", {}), Mapping) else {},
            }
        )
    return history


def _heuristic_analysis(failed_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate stable advisory proposals from repeated failures."""

    tool_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    analysis_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failure_class_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in failed_runs:
        tool_name = str(run.get("failed_tool_name", "") or "").strip()
        if tool_name:
            tool_groups[tool_name].append(run)
        analysis_type = str(run.get("analysis_type", "") or "").strip()
        if analysis_type:
            analysis_groups[analysis_type].append(run)
        failure_class = str(run.get("failure_class", "") or "").strip()
        if failure_class:
            failure_class_groups[failure_class].append(run)

    proposals: list[dict[str, Any]] = []
    for tool_name, group in sorted(tool_groups.items()):
        if len(group) < 2:
            continue
        proposals.append(_tool_proposal(tool_name, group))

    for analysis_type, group in sorted(analysis_groups.items()):
        if len(group) < 2:
            continue
        proposal = _analysis_proposal(analysis_type, group)
        if proposal is not None:
            proposals.append(proposal)
            secondary_name = str(proposal.get("secondary_name", "") or "").strip()
            if secondary_name:
                alias_proposal = dict(proposal)
                alias_proposal["name"] = secondary_name
                alias_proposal.pop("secondary_name", None)
                proposals.append(alias_proposal)

    for failure_class, group in sorted(failure_class_groups.items()):
        if len(group) < 3:
            continue
        if any(str(run.get("analysis_type", "") or "").strip() for run in group):
            continue
        proposals.append(_failure_class_proposal(failure_class, group))

    return proposals


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "runs_root",
        nargs="?",
        default=str(PROJECT_ROOT / "workspace"),
        help="Root directory to scan for result.json files.",
    )
    parser.add_argument(
        "--catalog-path",
        default=str(REPAIR_ADVISORIES_PATH),
        help="Repair advisory catalog to update when --write is set.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist generated proposals into the advisory catalog.",
    )
    return parser


def main() -> int:
    """Run the trace-driven advisory proposal CLI."""

    args = build_parser().parse_args()
    runs_root = Path(args.runs_root).expanduser().resolve(strict=False)
    proposals = _heuristic_analysis(_collect_failed_runs(runs_root))
    if not args.write:
        print(json.dumps(proposals, indent=2, sort_keys=True))
        return 0

    catalog_path = Path(args.catalog_path).expanduser().resolve(strict=False)
    catalog = load_repair_advisories(catalog_path)
    for proposal in proposals:
        catalog = upsert_repair_advisory(
            catalog,
            scope=str(proposal.get("scope", "")),
            name=str(proposal.get("name", "")),
            summary=str(proposal.get("summary", "")),
            repair_hints=list(proposal.get("repair_hints", []) or []),
            avoid_patterns=list(proposal.get("avoid_patterns", []) or []),
            source=str(proposal.get("source", "trace_driven_improvement")),
        )
    save_repair_advisories(catalog, catalog_path)
    print(json.dumps(proposals, indent=2, sort_keys=True))
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _failed_step_number(payload: Mapping[str, Any]) -> int:
    for key in _STEP_KEY_CANDIDATES:
        raw = payload.get(key, None)
        if raw is None:
            continue
        try:
            return max(0, int(raw))
        except Exception:
            continue
    return 0


def _failed_tool_name(payload: Mapping[str, Any]) -> str:
    for plan_key in ("final_plan", "plan"):
        plan = payload.get(plan_key, {})
        if not isinstance(plan, Mapping):
            continue
        steps = plan.get("plan", [])
        if not isinstance(steps, list):
            continue
        failed_step = _failed_step_number(payload)
        if 0 <= failed_step < len(steps) and isinstance(steps[failed_step], Mapping):
            return str(steps[failed_step].get("tool_name", "") or "").strip()
    return ""


def _failed_step_stderr(run_dir: Path, failed_step_number: int) -> str:
    step_dir_candidates = (
        run_dir / f"step_{failed_step_number}",
        run_dir / f"step_{failed_step_number + 1}",
    )
    for step_dir in step_dir_candidates:
        stderr_path = step_dir / "stderr.log"
        if stderr_path.is_file():
            try:
                return stderr_path.read_text(encoding="utf-8")[-4000:]
            except Exception:
                return ""
    return ""


def _collect_stderr_for_run(run_dir: Path) -> str:
    """Collect the most relevant stderr text for one failed run directory."""

    result_path = run_dir / "result.json"
    payload = _load_json(result_path)
    failed_step_number = _failed_step_number(payload)
    return _failed_step_stderr(run_dir, failed_step_number)


def _tool_proposal(tool_name: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    failure_classes = Counter(
        str(run.get("failure_class", "") or "").strip() or "unknown_failure"
        for run in runs
    )
    dominant_failure_class, _ = failure_classes.most_common(1)[0]
    repair_hints = _tool_repair_hints(tool_name, runs, dominant_failure_class)
    avoid_patterns = _avoid_patterns_from_history(runs)
    return {
        "scope": "tool",
        "name": tool_name,
        "summary": (
            f"Observed {len(runs)} repeated failures for {tool_name}, most often "
            f"classified as {dominant_failure_class}."
        ),
        "repair_hints": repair_hints,
        "hints": list(repair_hints),
        "avoid_patterns": avoid_patterns,
        "source": "trace_driven_improvement",
        "evidence_count": len(runs),
    }


def _analysis_proposal(analysis_type: str, runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    failure_classes = Counter(
        str(run.get("failure_class", "") or "").strip() or "unknown_failure"
        for run in runs
    )
    dominant_failure_class, count = failure_classes.most_common(1)[0]
    if count < 2:
        return None
    repair_hints = [
        (
            f"Repeated {dominant_failure_class} failures suggest adding a targeted "
            f"{analysis_type} advisory before planner retries."
        )
    ]
    avoid_patterns = _avoid_patterns_from_history(runs)
    shared = {
        "scope": "analysis",
        "name": analysis_type,
        "summary": (
            f"Observed {count} repeated {dominant_failure_class} failures for "
            f"{analysis_type} runs."
        ),
        "repair_hints": repair_hints,
        "hints": list(repair_hints),
        "avoid_patterns": avoid_patterns,
        "source": "trace_driven_improvement",
        "evidence_count": len(runs),
    }
    if dominant_failure_class:
        shared["secondary_name"] = f"{analysis_type}:{dominant_failure_class}"
    return shared


def _failure_class_proposal(
    failure_class: str,
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build an analysis-scope advisory for repeated failure classes."""

    repair_hints = [
        (
            f"Observed repeated {failure_class} failures without stable analysis metadata; "
            "promote a reusable advisory for this failure pattern before replanning."
        )
    ]
    avoid_patterns = _avoid_patterns_from_history(runs)
    return {
        "scope": "analysis",
        "name": failure_class,
        "summary": f"Observed {len(runs)} repeated {failure_class} failures across runs.",
        "repair_hints": repair_hints,
        "hints": list(repair_hints),
        "avoid_patterns": avoid_patterns,
        "source": "trace_driven_improvement",
        "evidence_count": len(runs),
    }


def _tool_repair_hints(
    tool_name: str,
    runs: list[dict[str, Any]],
    dominant_failure_class: str,
) -> list[str]:
    stderr_text = "\n".join(str(run.get("stderr_tail", "") or "") for run in runs).lower()
    hints: list[str] = []
    if any(pattern in stderr_text for pattern in _TOOL_MISSING_PATTERNS) or dominant_failure_class == "tool_missing":
        hints.append(
            f"Verify {tool_name} availability before selection and prefer launcher-backed or equivalent tools when it is missing."
        )
    if "missing" in stderr_text and all("missing" not in hint.lower() for hint in hints):
        hints.append(
            f"Repeated stderr for {tool_name} mentions missing prerequisites or metadata; inspect required inputs and setup before retrying."
        )
    if "no module named 'bio_harness'" in stderr_text:
        hints.append(
            f"Ensure script-style invocations for {tool_name} bootstrap the repository root onto sys.path before package imports."
        )
    if not hints:
        hints.append(
            f"Use the failing stderr trace and recent repair history to build a {tool_name}-specific advisory before another replan."
        )
    return hints


def _avoid_patterns_from_history(runs: list[dict[str, Any]]) -> list[str]:
    action_counts = Counter()
    for run in runs:
        for entry in run.get("auto_repair_history", []) or []:
            if not isinstance(entry, Mapping):
                continue
            action = str(entry.get("action", "") or "").strip()
            if action:
                action_counts[action] += 1
    repeated = [
        f"Repeated failed repair action: {action}"
        for action, count in sorted(action_counts.items())
        if count >= 2
    ]
    return repeated


if __name__ == "__main__":
    raise SystemExit(main())
