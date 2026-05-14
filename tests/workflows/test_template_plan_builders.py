from __future__ import annotations

import json

from bio_harness.workflows.template_plan_builders import (
    build_bootstrap_execution_plan,
    build_splicing_execution_plan,
    export_plan_run_scripts,
)


def test_build_bootstrap_execution_plan_returns_three_step_bash_plan() -> None:
    plan = build_bootstrap_execution_plan("/tmp/data")

    assert [step["tool_name"] for step in plan["plan"]] == ["bash_run", "bash_run", "bash_run"]
    assert plan["plan"][0]["arguments"]["command"].startswith("bash ")


def test_build_splicing_execution_plan_preserves_template_metadata() -> None:
    plan = build_splicing_execution_plan(
        "/tmp/data",
        "/tmp/ref.gtf",
        "/tmp/ref.fa",
        use_test_subset=False,
    )

    assert plan["canonical_template"] == "splicing_execution_v2"
    assert plan["execution_options"]["use_test_subset"] is False
    assert len(plan["plan"]) >= 8


def test_export_plan_run_scripts_writes_manifest_and_step_scripts(tmp_path) -> None:
    plan = {
        "thought_process": "x",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {"command": "echo hi"},
            }
        ],
    }

    exported = export_plan_run_scripts(
        plan,
        run_dir=tmp_path,
        selected_dir=tmp_path / "selected",
        script_set_name="demo",
    )

    manifest = json.loads((tmp_path / "scripts" / "demo" / "scripts_manifest.json").read_text())
    assert exported["run_all"].endswith("run_all.sh")
    assert manifest["script_set"] == "demo"
    assert manifest["steps"][0]["tool_name"] == "bash_run"
