from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.fallback_skill_builder import FallbackBuilderRequest, run_fallback_skill_builder


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_builder_executes_harness_batch_prompts_with_plan_file(tmp_path: Path):
    workspace = PROJECT_ROOT / "workspace"
    data_root = workspace / "inputs_readonly"
    workspace.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    plan_path = tmp_path / "smoke_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "thought_process": "e2e smoke",
                "plan": [
                    {
                        "step_id": 1,
                        "tool_name": "bash_run",
                        "arguments": {"command": "echo __FALLBACK_BUILDER_E2E__"},
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    request = FallbackBuilderRequest.from_raw(
        target_capability_set=["alignment"],
        allowed_tools=["bash"],
        data_reference_constraints={"plan_file": str(plan_path)},
        strictness_mode="conservative",
        request_text="Run harness batch smoke prompts.",
        selected_dir=str(workspace),
        data_root=str(data_root),
        batch_prompts=[
            {"name": "smoke_one", "prompt": "smoke one"},
            {"name": "smoke_two", "prompt": "smoke two"},
        ],
        run_e2e=True,
        rerun_failures=False,
    )

    report = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=request)
    batch = report.get("batch_report", {})
    summary = batch.get("summary", {}) if isinstance(batch, dict) else {}

    assert int(batch.get("exit_code", 2)) == 0
    assert int(summary.get("count", 0)) == 2
    assert int(summary.get("failures", 0)) == 0
