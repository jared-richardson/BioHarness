from __future__ import annotations

from scripts.run_agent_e2e_postplan_validation_support import (
    apply_fastq_rebinding_if_changed,
    apply_runtime_fallback_if_distinct,
    filter_missing_plan_inputs,
)


def test_filter_missing_plan_inputs_keeps_only_non_intermediate_paths(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    counts_path = workspace / "outputs" / "counts.tsv"

    filtered = filter_missing_plan_inputs(
        [str(counts_path), str(workspace / "missing" / "ref.fa")],
        plan={
            "plan": [
                {
                    "tool_name": "featurecounts_run",
                    "arguments": {"output_counts": str(counts_path)},
                    "step_id": 1,
                }
            ]
        },
        selected_dir=str(workspace),
        quiet=True,
    )

    assert filtered == [str(workspace / "missing" / "ref.fa")]


def test_apply_runtime_fallback_if_distinct_updates_run_and_emits_event() -> None:
    events: list[dict[str, object]] = []
    run = {
        "run_uid": "run-1",
        "plan": {"plan": [{"step_id": 1, "tool_name": "bash_run"}]},
    }
    fallback_plan = {"plan": [{"step_id": 1, "tool_name": "freebayes_call"}]}

    changed = apply_runtime_fallback_if_distinct(
        run=run,
        current_plan=run["plan"],
        contract={"must_include_capabilities": ["variant_calling"]},
        fallback_plan=fallback_plan,
        fallback_action="template_variant",
        fallback_details={"selected_pipeline_id": "variant"},
        message="fallback applied",
        detail_key="missing_plan_tools",
        detail_value=["gatk"],
        quiet=True,
        assess_contract_for_plan=lambda plan, contract: {"passed": True, "tool_name": plan["plan"][0]["tool_name"]},
        append_event=lambda **payload: events.append(payload),
    )

    assert changed is True
    assert run["plan"] == fallback_plan
    assert run["contract_validation"]["tool_name"] == "freebayes_call"
    assert events and events[0]["payload"]["action"] == "preexecution_template_variant"


def test_apply_fastq_rebinding_if_changed_installs_repaired_plan() -> None:
    events: list[dict[str, object]] = []
    run = {"run_uid": "run-2", "plan": {"plan": [{"tool_name": "subread_align"}]}}
    repaired_plan = {"plan": [{"tool_name": "subread_align", "arguments": {"reads_1": "R1.fastq"}}]}

    changed = apply_fastq_rebinding_if_changed(
        run=run,
        repaired_plan=repaired_plan,
        repair_meta={"changed": True, "diff_summary": {"reads_1": "R1.fastq"}},
        quiet=True,
        append_event=lambda **payload: events.append(payload),
    )

    assert changed is True
    assert run["plan"] == repaired_plan
    assert events and events[0]["payload"]["action"] == "rebind_missing_fastq_inputs"
