from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.fallback_skill_builder import (
    choose_repair_action,
    classify_failure_from_artifacts,
    read_run_artifacts,
)


def test_uncommon_missing_tool_marker_routes_to_tool_repair(tmp_path: Path):
    run_dir = tmp_path / "runs" / "run_mixcr_missing"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(
        json.dumps({"status": "failed", "error": ""}, indent=2),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    (run_dir / "execution.log").write_text("[stdout] __MISSING_TOOL__:mixcr\n", encoding="utf-8")
    (run_dir / "stderr.log").write_text("", encoding="utf-8")

    snapshot = read_run_artifacts(run_dir)
    classified = classify_failure_from_artifacts(snapshot)
    assert classified["failure_class"] == "tool_missing"
    assert choose_repair_action(classified["failure_class"], "conservative") == "repair_missing_tool_or_degrade_template"
