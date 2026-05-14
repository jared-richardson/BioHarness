from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bio_harness.core.path_graph_store import PathGraphStore, default_path_graph_db_path
from scripts.run_agent_e2e import AgentE2EHarness, HarnessConfig


def _write_fastq(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")


def _write_refs(workspace: Path) -> None:
    inp = workspace / "inputs_readonly"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "mouse_fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (inp / "mouse_gtf").write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")


def _cfg(
    *,
    prompt: str,
    selected_dir: Path,
    data_root: Path,
    plan_path: Path | None = None,
    graph_user_key: str = "default",
) -> HarnessConfig:
    return HarnessConfig(
        prompt=prompt,
        selected_dir=selected_dir,
        data_root=data_root,
        workspace_root=selected_dir,
        max_repairs=0,
        heartbeat_seconds=15,
        stall_timeout_seconds=45,
        live_process_grace_seconds=900,
        model_name=None,
        host=None,
        auto_install_missing_tools=False,
        allow_replan=True,
        allow_canonicalize=True,
        plan_path=plan_path,
        result_json=None,
        quiet=True,
        print_plan=False,
        path_graph_db=default_path_graph_db_path(selected_dir),
        path_graph_user_key=graph_user_key,
        path_graph_scope="global",
        path_graph_persist_preference_updates=False,
    )


def test_prepare_plan_with_empty_graph_records_selection(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_refs(workspace)
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")

    harness = AgentE2EHarness(
        _cfg(
            prompt="Call somatic variants in tumor vs normal using Mutect2.",
            selected_dir=workspace,
            data_root=data_root,
        )
    )
    harness._init_run()

    def _timeout_planner(_prompt: str, analysis_spec=None):
        raise TimeoutError("Planner request timed out while waiting for model output.")

    harness.orchestrator.think = _timeout_planner  # type: ignore[method-assign]
    harness._prepare_plan()

    selected = str(harness.run.get("selected_path_id", "")).strip()
    assert selected
    assert harness.run.get("plan", {}).get("plan", [])

    db_path = harness.path_graph.db_path
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT path_id, status FROM path_runs WHERE run_id=?",
            (f"{harness.run['run_uid']}:planned",),
        ).fetchone()
    assert row is not None
    assert row[0] == selected
    assert row[1] == "planned"


def test_prepare_plan_prefers_non_blacklisted_tools_when_graph_preferences_exist(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_refs(workspace)
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")

    cfg = _cfg(
        prompt="Call somatic variants in tumor vs normal using Mutect2.",
        selected_dir=workspace,
        data_root=data_root,
        graph_user_key="integration_user",
    )
    store = PathGraphStore(cfg.path_graph_db)
    store.upsert_user_preferences(
        user_key="integration_user",
        scope="global",
        preferences={"tool_blacklist": ["gatk"], "mode": "conservative"},
    )

    harness = AgentE2EHarness(cfg)
    harness._init_run()

    def _timeout_planner(_prompt: str, analysis_spec=None):
        raise TimeoutError("Planner request timed out while waiting for model output.")

    harness.orchestrator.think = _timeout_planner  # type: ignore[method-assign]
    harness._prepare_plan()

    assert harness.run.get("selected_path_id") == "somatic_variant_bcftools_tn_degrade"
    assert bool(harness.run.get("fallback_selection", {}).get("graph_signal_enabled", False)) is True


def test_run_end_to_end_records_final_graph_outcome(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)

    plan_file = tmp_path / "plan.json"
    plan_file.write_text(
        json.dumps(
            {
                "thought_process": "graph outcome smoke",
                "canonical_template": "unit_echo_plan",
                "plan": [
                    {
                        "step_id": 1,
                        "tool_name": "bash_run",
                        "arguments": {"command": "echo __PATH_GRAPH_OUTCOME__"},
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    harness = AgentE2EHarness(
        _cfg(
            prompt="Run a simple smoke command.",
            selected_dir=workspace,
            data_root=data_root,
            plan_path=plan_file,
        )
    )
    result = harness.run_end_to_end()

    assert result["status"] == "completed"
    with sqlite3.connect(str(harness.path_graph.db_path)) as conn:
        row = conn.execute(
            "SELECT path_id, status FROM path_runs WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "unit_echo_plan"
    assert row[1] == "completed"
