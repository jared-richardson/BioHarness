from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.fallback_skill_builder import FallbackBuilderRequest, run_fallback_skill_builder


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_builder_executes_uncommon_prompt_batch_with_harness_script(tmp_path: Path):
    workspace = PROJECT_ROOT / "workspace"
    data_root = workspace / "inputs_readonly"
    workspace.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    plan_path = tmp_path / "smoke_plan_uncommon.json"
    plan_path.write_text(
        json.dumps(
            {
                "thought_process": "uncommon e2e smoke",
                "plan": [
                    {
                        "step_id": 1,
                        "tool_name": "bash_run",
                        "arguments": {"command": "echo __UNCOMMON_FALLBACK_BUILDER_E2E__"},
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
        request_text="Run uncommon harness batch smoke prompts.",
        selected_dir=str(workspace),
        data_root=str(data_root),
        batch_prompts=[
            {"name": "methylation", "prompt": "Run bisulfite methylation with Bismark style."},
            {"name": "metagenomics", "prompt": "Profile metagenomics reads with Kraken2 and Bracken."},
            {"name": "fusion", "prompt": "Detect fusions with STAR-Fusion style."},
            {"name": "cnv", "prompt": "Perform CNV analysis with CNVkit style."},
            {"name": "immune", "prompt": "Profile immune repertoire with MiXCR."},
            {"name": "phylo", "prompt": "Infer phylogenetics tree with IQ-TREE style."},
        ],
        run_e2e=True,
        rerun_failures=False,
    )

    report = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=request)
    batch = report.get("batch_report", {})
    summary = batch.get("summary", {}) if isinstance(batch, dict) else {}
    items = summary.get("items", []) if isinstance(summary, dict) else []
    failed_names = {
        str(item.get("name", "")).strip()
        for item in items
        if str(item.get("status", "")).strip() == "failed"
    }
    completed_names = {
        str(item.get("name", "")).strip()
        for item in items
        if str(item.get("status", "")).strip() == "completed"
    }

    assert int(summary.get("count", 0)) == 6
    assert failed_names <= {"cnv", "metagenomics", "methylation"}
    assert {"fusion", "immune", "phylo"} <= completed_names
