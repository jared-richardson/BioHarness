from __future__ import annotations

from scripts.run_agent_e2e import (
    _infer_observed_groups_from_plan_artifacts,
    _mark_group_missing_signal,
    _mark_group_observed,
    _normalize_group_label,
    _reconcile_missing_sample_groups,
)


def test_group_label_normalization_handles_common_aliases():
    assert _normalize_group_label("CONTROL") == "control"
    assert _normalize_group_label("ctrl") == "control"
    assert _normalize_group_label("trt") == "treatment"
    assert _normalize_group_label("Group-1") == "group_1"


def test_missing_groups_reconcile_with_positive_observation():
    run = {
        "missing_sample_groups": [],
        "missing_sample_group_signals": [],
        "observed_sample_groups": [],
    }
    _mark_group_missing_signal(run, "control")
    _mark_group_missing_signal(run, "treatment")
    assert run["missing_sample_groups"] == ["control", "treatment"]

    _mark_group_observed(run, "CONTROL")
    assert run["missing_sample_groups"] == ["treatment"]
    assert run["observed_sample_groups"] == ["control"]


def test_reconcile_ignores_resolved_alias_forms():
    run = {
        "missing_sample_groups": [],
        "missing_sample_group_signals": ["treatment"],
        "observed_sample_groups": ["trt"],
    }
    _reconcile_missing_sample_groups(run)
    assert run["missing_sample_groups"] == []


def test_mark_group_observed_records_source_history():
    run = {
        "missing_sample_groups": [],
        "missing_sample_group_signals": [],
        "observed_sample_groups": [],
        "observed_sample_group_sources": {},
    }
    _mark_group_observed(run, "control", source="stream_marker:selected_r1")
    _mark_group_observed(run, "CONTROL", source="plan_artifact_inference")
    assert run["observed_sample_groups"] == ["control"]
    assert run["observed_sample_group_sources"]["control"] == [
        "plan_artifact_inference",
        "stream_marker:selected_r1",
    ]


def test_infers_observed_groups_from_nonempty_group_artifact_paths(tmp_path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True)
    out_dir = selected_dir / "outputs" / "demo"
    out_dir.mkdir(parents=True)
    control_file = out_dir / "control_bams.txt"
    treatment_file = out_dir / "treatment_bams.txt"
    control_file.write_text("/tmp/c1.bam\n", encoding="utf-8")
    treatment_file.write_text("/tmp/t1.bam\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"echo ok > {control_file.as_posix()} ; "
                        f"echo ok > {treatment_file.as_posix()}"
                    )
                },
            }
        ]
    }
    observed = _infer_observed_groups_from_plan_artifacts(
        plan,
        selected_dir,
        ["control", "treatment"],
    )
    assert observed == {"control", "treatment"}


def test_does_not_infer_group_from_empty_artifact_path(tmp_path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True)
    out_dir = selected_dir / "outputs" / "demo"
    out_dir.mkdir(parents=True)
    empty_control = out_dir / "control_samples.txt"
    empty_control.write_text("", encoding="utf-8")
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"cat {empty_control.as_posix()}",
                },
            }
        ]
    }
    observed = _infer_observed_groups_from_plan_artifacts(plan, selected_dir, ["control"])
    assert observed == set()


def test_infers_group_from_group_labeled_argument_key(tmp_path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True)
    out_dir = selected_dir / "outputs" / "demo"
    out_dir.mkdir(parents=True)
    generic_list = out_dir / "samples.list"
    generic_list.write_text("S1\nS2\n", encoding="utf-8")
    plan = {
        "plan": [
            {
                "tool_name": "rmats_run",
                "arguments": {
                    "control_bam_list": generic_list.as_posix(),
                },
            }
        ]
    }
    observed = _infer_observed_groups_from_plan_artifacts(plan, selected_dir, ["control"])
    assert observed == {"control"}


def test_infers_group_from_metadata_file_content(tmp_path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True)
    out_dir = selected_dir / "outputs" / "demo"
    out_dir.mkdir(parents=True)
    metadata = out_dir / "sample_sheet.tsv"
    metadata.write_text("sample\tcondition\nS1\tcontrol\nS2\ttreatment\n", encoding="utf-8")
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"cat {metadata.as_posix()}",
                },
            }
        ]
    }
    observed = _infer_observed_groups_from_plan_artifacts(
        plan,
        selected_dir,
        ["control", "treatment"],
    )
    assert observed == {"control", "treatment"}
