from __future__ import annotations

# ruff: noqa: F403,F405
import scripts.run_agent_e2e_execution as execution_mod
from bio_harness.core.executor_runtime import (
    finish_executor_runtime,
    load_executor_runtime,
)

from tests.core_cases.harness_guard_support import *

def test_supervised_model_replan_prefers_auto_mode_and_preserves_seed_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test hierarchical repair",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    seed_plan = {
        "thought_process": "seed",
        "plan": [{"tool_name": "bash_run", "arguments": {"command": "touch repaired.txt"}, "step_id": 1}],
    }
    harness.run["plan"] = seed_plan

    captured: dict[str, object] = {}

    planner_modes: list[str] = []

    def _fake_attempt(*, prompt, strategy, attempt_num, planner_mode, seed_plan, model_override=None):
        captured["prompt"] = prompt
        captured["strategy"] = strategy
        captured["attempt_num"] = attempt_num
        captured["seed_plan"] = seed_plan
        captured["model_override"] = model_override
        planner_modes.append(str(planner_mode))
        return ({"thought_process": "fixed", "plan": seed_plan["plan"]}, 1.0)

    monkeypatch.setattr(harness, "_planner_attempt_with_heartbeat", _fake_attempt)

    candidate = harness._supervised_model_replan(
        prompt="repair this plan",
        strategy="preexecution_protocol_repair",
    )

    assert candidate["plan"][0]["tool_name"] == "bash_run"
    assert captured["strategy"] == "preexecution_protocol_repair"
    assert captured["attempt_num"] == 0
    assert captured["seed_plan"] == seed_plan
    assert planner_modes == ["auto"]


def test_supervised_model_replan_falls_back_to_direct_mode_after_non_actionable_auto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test repair fallback",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["plan"] = {
        "thought_process": "seed",
        "plan": [{"tool_name": "bash_run", "arguments": {"command": "pwd"}, "step_id": 1}],
    }

    planner_modes: list[str] = []

    def _fake_attempt(*, prompt, strategy, attempt_num, planner_mode, seed_plan, model_override=None):
        del prompt, strategy, attempt_num, seed_plan, model_override
        planner_modes.append(str(planner_mode))
        if planner_mode == "auto":
            return ({"thought_process": "workflow only", "plan": []}, 1.0)
        return (
            {
                "thought_process": "fixed",
                "plan": [{"tool_name": "bash_run", "arguments": {"command": "pwd -P"}, "step_id": 1}],
            },
            1.0,
        )

    monkeypatch.setattr(harness, "_planner_attempt_with_heartbeat", _fake_attempt)

    candidate = harness._supervised_model_replan(
        prompt="repair this plan",
        strategy="preexecution_semantic_repair",
    )

    assert planner_modes == ["auto", "direct"]
    assert candidate["plan"][0]["arguments"]["command"] == "pwd -P"
def test_planner_template_fastpath_skips_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="run alignment and variant calling",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "1")

    fallback_plan = {
        "thought_process": "fallback",
        "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo fallback"}, "step_id": 1}],
    }
    monkeypatch.setattr(
        harness,
        "_build_contract_template_repair",
        lambda _failure_class: (fallback_plan, "template_fastpath", {"why": "unit_test"}),
    )
    monkeypatch.setattr(
        harness,
        "_assess_contract_for_plan",
        lambda _plan, _contract: {"passed": True, "missing_capabilities": [], "missing_tool_hints": []},
    )
    harness.orchestrator.think = lambda _prompt, analysis_spec=None: (_ for _ in ()).throw(RuntimeError("should_not_call_model"))  # type: ignore[method-assign]

    contract = {"must_include_capabilities": ["alignment"], "explicit_tool_hints": []}
    plan, meta = harness._generate_plan_with_supervision(contract)
    assert plan == fallback_plan
    assert meta.get("strategy") == "template_fastpath"
    assert str(harness.run.get("planner_strategy_used", "")) == "template_fastpath"


def test_planner_process_isolation_skips_instance_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test planner override",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "1")

    def _override(_prompt: str, analysis_spec=None, planner_mode="auto", seed_plan=None, model_override=None):
        return {"thought_process": "override", "plan": []}

    harness.orchestrator.think = _override  # type: ignore[method-assign]

    assert harness._planner_isolate_process_enabled() is True
    assert harness._planner_process_isolation_allowed() is False


def test_adaptive_live_process_grace_uses_tool_and_command_hints(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    v1 = harness._adaptive_live_process_grace_seconds(active_tool_name="bcftools_call", active_command="")
    v2 = harness._adaptive_live_process_grace_seconds(
        active_tool_name="bash_run",
        active_command="bcftools mpileup -f ref.fa in.bam | bcftools call -mv -Oz -o out.vcf.gz",
    )
    v3 = harness._adaptive_live_process_grace_seconds(
        active_tool_name="spades_assemble",
        active_command="spades.py -1 reads_1.fastq.gz -2 reads_2.fastq.gz -o assembly",
    )
    v4 = harness._adaptive_live_process_grace_seconds(
        active_tool_name="bash_run",
        active_command=(
            "env PYTHONPATH=<BIO_HARNESS_ROOT> "
            "<BIO_HARNESS_ROOT>/.pixi/envs/default/bin/python3.10 "
            "<BIO_HARNESS_ROOT>/bio_harness/pipeline_scripts/compare_pathways.py "
            "--output_dir output"
        ),
    )
    v5 = harness._adaptive_live_process_grace_seconds(
        active_tool_name="deseq2_run",
        active_command="Rscript /tmp/deseq2_wrapper.R --counts counts.tsv --metadata metadata.tsv",
    )
    assert v1 >= 1800
    assert v2 >= 1800
    assert v3 >= 3600
    assert v4 >= 3600
    assert v5 >= 600


def test_stall_monitor_uses_full_idle_grace_for_spades_and_pipeline_scripts(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    assert harness._stall_monitor_uses_full_idle_grace(
        active_tool_name="spades_assemble",
        active_command="spades.py -1 reads_1.fastq.gz -2 reads_2.fastq.gz -o assembly",
    )
    assert harness._stall_monitor_uses_full_idle_grace(
        active_tool_name="bash_run",
        active_command=(
            "env PYTHONPATH=<BIO_HARNESS_ROOT> "
            "<BIO_HARNESS_ROOT>/.pixi/envs/default/bin/python3.10 "
            "<BIO_HARNESS_ROOT>/bio_harness/pipeline_scripts/compare_pathways.py "
            "--output_dir output"
        ),
    )
    assert harness._stall_monitor_uses_full_idle_grace(
        active_tool_name="deseq2_run",
        active_command="Rscript /tmp/deseq2_wrapper.R --counts counts.tsv --metadata metadata.tsv",
    )


def test_execution_monitor_tracks_pre_execution_phase_and_first_pid(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    state = harness._new_execution_monitor_state()
    harness._update_active_execution_context(
        "[Step 1 Output] [status] phase=validating tool=deseq2_run\n",
        state,
    )
    assert state.active_step_id == 1
    assert state.active_tool_name == "deseq2_run"
    assert state.active_phase == "validating"
    assert state.first_pid_observed is False

    harness._update_active_execution_context(
        "[Step 1 Output] [status] starting command in cwd=/tmp\n",
        state,
    )
    assert state.active_phase == "spawning_process"

    harness._update_active_execution_context(
        "[Step 1 Output] [status] spawned pid=4242\n",
        state,
    )
    assert state.active_pid == 4242
    assert state.first_pid_observed is True
    assert state.active_phase == "running_process"


def test_execution_monitor_tracks_executor_level_phase_before_step_start(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    state = harness._new_execution_monitor_state()
    harness._update_active_execution_context("[status] phase=executor_preflight\n", state)
    assert state.active_phase == "executor_preflight"
    assert state.active_step_id is None
    assert state.first_pid_observed is False

    harness._update_active_execution_context("[status] phase=executor_dispatch\n", state)
    assert state.active_phase == "executor_dispatch"
    assert state.active_step_id is None


def test_startup_phase_grace_uses_short_budget_for_executor_prestep(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    state = harness._new_execution_monitor_state()
    state.active_phase = "executor_preflight"
    state.first_pid_observed = False

    assert harness._startup_phase_grace_seconds(state) == 120


def test_execution_heartbeat_ignores_artifact_progress_before_step_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    calls = {"artifacts": 0}

    monkeypatch.setattr(
        execution_mod,
        "collect_process_snapshot",
        lambda *_args, **_kwargs: {
            "tree_cpu_seconds": 0.0,
            "inferred_tool": "unknown",
            "live_process_count": 0,
        },
    )
    monkeypatch.setattr(
        execution_mod,
        "_stream_evidence",
        lambda *_args, **_kwargs: {"tier": "quiet"},
    )

    def _fake_collect_recent_outputs(*_args, **_kwargs):
        calls["artifacts"] += 1
        return {"recent_files": ["dummy.txt"], "latest_mtime": 250.0}

    monkeypatch.setattr(execution_mod, "collect_recent_outputs", _fake_collect_recent_outputs)

    state = harness._new_execution_monitor_state()
    state.last_progress_ts = 100.0
    state.active_step_started_ts = 50.0
    state.active_phase = "executor_preflight"
    state.active_step_id = None

    harness._emit_execution_heartbeat(state, now_ts=260.0)

    assert calls["artifacts"] == 0
    assert state.last_progress_ts == 100.0
    assert harness.run["last_artifact_probe"]["recent_files"] == []


def test_active_step_completion_evidence_detects_materialized_outputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test stall suppression from expected outputs",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["plan"] = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {"command": "printf 'done\\n' > outputs/example.tsv"},
            }
        ]
    }
    (selected_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (selected_dir / "outputs" / "example.tsv").write_text("done\n", encoding="utf-8")
    state = type("State", (), {"active_step_id": 1})()

    evidence = harness._active_step_completion_evidence(state)

    assert evidence["has_evidence"] is True
    assert evidence["source"] == "expected_outputs"

def test_update_active_execution_context_tracks_step_command_and_pid(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    state = harness._new_execution_monitor_state()
    started_before = state.active_step_started_ts

    harness._update_active_execution_context("--- Executing Step 2: bash_run ---", state)
    assert state.active_pid is None
    assert state.active_step_id == 2
    assert state.active_tool_name == "bash_run"
    assert state.active_step_started_ts >= started_before

    harness._update_active_execution_context("[Step 2 Output] [command] python task.py", state)
    assert state.active_step_id == 2
    assert state.active_command == "python task.py"

    harness._update_active_execution_context("[status] pid=12345 still running", state)
    assert state.active_pid == 12345
def test_should_drain_completed_execution_returns_true_after_idle_period(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["step_statuses"] = ["completed"]

    state = harness._new_execution_monitor_state()
    state.last_progress_ts = time.time() - 300.0

    assert harness._should_drain_completed_execution(
        state,
        now_ts=time.time(),
        has_live_process=False,
    ) is True
def test_execute_once_refuses_completed_status_when_fastq_manifest_is_empty(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["plan"] = {"thought_process": "t", "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo ok"}, "step_id": 1}]}
    harness.run["step_statuses"] = ["pending"]

    def _fake_execute_plan(_plan, log_queue, *_args, **_kwargs):
        log_queue.put("--- Executing Step 1: bash_run ---\n")
        log_queue.put("[Step 1 Output] [stdout] __FASTQ_MANIFEST_COUNT__:0\n")
        log_queue.put("[Step 1 Output] [stdout] __BAM_LIST_COUNT__:control:1\n")
        log_queue.put("--- Step 1 (bash_run) finished ---\n")
        log_queue.put("Plan execution completed.\n")
        log_queue.put(None)

    harness.orchestrator.execute_plan = _fake_execute_plan  # type: ignore[method-assign]
    harness._execute_once()

    assert harness.run.get("status") == "failed"
    assert "FASTQ discovery produced zero files" in str(harness.run.get("error", ""))
    assert harness.run.get("observed_sample_groups", []) == []


def test_execute_once_preserves_executor_prevalidation_failure(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["plan"] = {
        "thought_process": "t",
        "plan": [{"tool_name": "snpeff_annotate", "arguments": {}, "step_id": 12}],
    }
    harness.run["step_statuses"] = ["pending"]

    def _fake_execute_plan(_plan, log_queue, *_args, **kwargs):
        run_artifacts = kwargs["run_artifacts"]
        finish_executor_runtime(
            run_artifacts,
            run_id=str(run_artifacts["run_id"]),
            status="failed",
            error="Step 12 (snpeff_annotate) is missing required argument(s): input_vcf, output_vcf.",
        )
        log_queue.put(
            "Pre-execution validation failed: Step 12 (snpeff_annotate) is missing required argument(s): input_vcf, output_vcf.\n"
        )
        log_queue.put(None)

    harness.orchestrator.execute_plan = _fake_execute_plan  # type: ignore[method-assign]
    harness._execute_once()

    runtime = load_executor_runtime(harness.run["run_files"])
    assert runtime["status"] == "failed"
    assert harness.run.get("status") == "failed"
    assert "Step 12 (snpeff_annotate) is missing required argument(s)" in str(harness.run.get("error", ""))
    assert "Planned outputs were not produced" not in str(harness.run.get("error", ""))
def test_execute_once_preserves_planner_timeout_signal(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["planner_timeout_detected"] = True
    harness.run["plan"] = {"thought_process": "t", "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo ok"}, "step_id": 1}]}
    harness.run["step_statuses"] = ["pending"]

    def _fake_execute_plan(_plan, log_queue, *_args, **_kwargs):
        log_queue.put("--- Executing Step 1: bash_run ---\n")
        log_queue.put("[Step 1 Output] [stdout] ok\n")
        log_queue.put("--- Step 1 (bash_run) finished ---\n")
        log_queue.put("Plan execution completed.\n")
        log_queue.put(None)

    harness.orchestrator.execute_plan = _fake_execute_plan  # type: ignore[method-assign]
    harness._execute_once()

    assert bool(harness.run.get("planner_timeout_detected", False)) is True
def test_fastq_manifest_script_follows_symlinked_input_roots(tmp_path: Path):
    data_real = tmp_path / "real_data"
    data_real.mkdir(parents=True, exist_ok=True)
    (data_real / "S1_R1.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_real / "S1_R2.fastq.gz").write_text("stub\n", encoding="utf-8")
    symlink_root = tmp_path / "inputs_readonly"
    symlink_root.mkdir(parents=True, exist_ok=True)
    link_path = symlink_root / "clip_1"
    link_path.symlink_to(data_real, target_is_directory=True)
    manifest = tmp_path / "manifest.txt"

    script = Path("bio_harness/pipeline_scripts/fastq_manifest.sh").resolve()
    result = subprocess.run(
        ["bash", str(script), str(link_path), str(manifest)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "__FASTQ_MANIFEST_COUNT__:2" in result.stdout
    lines = [ln.strip() for ln in manifest.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
def test_create_test_subset_script_uses_cache_when_inputs_unchanged(tmp_path: Path):
    subset_dir = tmp_path / "subset"
    control_r1 = tmp_path / "control_R1_001.fastq"
    control_r2 = tmp_path / "control_R2_001.fastq"
    treatment_r1 = tmp_path / "treat_R1_001.fastq"
    treatment_r2 = tmp_path / "treat_R2_001.fastq"
    for fp in (control_r1, control_r2, treatment_r1, treatment_r2):
        fp.write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")

    control_list = tmp_path / "control.txt"
    treatment_list = tmp_path / "treatment.txt"
    control_out = tmp_path / "control_out.txt"
    treatment_out = tmp_path / "treatment_out.txt"
    control_list.write_text(f"{control_r1}\n", encoding="utf-8")
    treatment_list.write_text(f"{treatment_r1}\n", encoding="utf-8")

    script = Path("bio_harness/pipeline_scripts/create_test_subset_from_r1_lists.sh").resolve()
    args = [
        "bash",
        str(script),
        str(control_list),
        str(treatment_list),
        str(subset_dir),
        str(control_out),
        str(treatment_out),
        "1",
        "control",
        "treatment",
    ]

    first = subprocess.run(args, capture_output=True, text=True, check=False)
    second = subprocess.run(args, capture_output=True, text=True, check=False)

    assert first.returncode == 0
    assert second.returncode == 0
    assert "__TEST_SUBSET_DONE__:reads_per_fastq:1" in first.stdout
    assert "__TEST_SUBSET_SKIPPED__:cached" in second.stdout
    assert "__TEST_SUBSET_GROUP_COUNT__:control:1" in second.stdout
    assert "__TEST_SUBSET_GROUP_COUNT__:treatment:1" in second.stdout


def test_create_test_subset_script_fails_when_group_lists_are_missing(tmp_path: Path):
    subset_dir = tmp_path / "subset"
    control_list = tmp_path / "control.txt"
    treatment_list = tmp_path / "treatment.txt"
    control_out = tmp_path / "control_out.txt"
    treatment_out = tmp_path / "treatment_out.txt"
    control_list.write_text("", encoding="utf-8")
    treatment_list.write_text("", encoding="utf-8")

    script = Path("bio_harness/pipeline_scripts/create_test_subset_from_r1_lists.sh").resolve()
    result = subprocess.run(
        [
            "bash",
            str(script),
            str(control_list),
            str(treatment_list),
            str(subset_dir),
            str(control_out),
            str(treatment_out),
            "1",
            "control",
            "treatment",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "__TEST_SUBSET_SKIPPED__:missing_inputs" in result.stdout
def test_extract_sample_tags_from_plan_reads_select_script_tags():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bash /tmp/select_sample_r1.sh outputs/manifest.txt S2 outputs/control_r1.txt CONTROL; "
                        "bash /tmp/select_sample_r1.sh outputs/manifest.txt S5 outputs/treatment_r1.txt TREATMENT"
                    )
                },
                "step_id": 1,
            }
        ]
    }
    control, treatment = _extract_sample_tags_from_plan(plan)
    assert control == "S2"
    assert treatment == "S5"
def test_extract_sample_tags_from_plan_reads_fastq_basename_tags():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "echo /data/1_S1_R1_001.fastq /data/1_S1_R2_001.fastq ; "
                        "echo /data/6_S6_R1_001.fastq /data/6_S6_R2_001.fastq"
                    )
                },
                "step_id": 1,
            }
        ]
    }
    control, treatment = _extract_sample_tags_from_plan(plan)
    assert control == "S1"
    assert treatment == "S6"
def test_extract_group_tags_from_request_text_control_treatment_groups():
    prompt = (
        "Can you take the S1 S2, S3 paired end sample as the control and "
        "the S4, S5, S6 sample as the treatment for my paired-end reads?"
    )
    control_tags, treatment_tags = _extract_group_tags_from_request_text(prompt)
    assert control_tags == ["S1", "S2", "S3"]
    assert treatment_tags == ["S4", "S5", "S6"]
def test_strict_llm_planning_disables_template_fastpath_and_failopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    monkeypatch.setenv("BIO_HARNESS_STRICT_LLM_PLANNING", "1")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "1")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TIMEOUT_FAILOPEN", "1")

    assert harness._planner_template_fastpath_enabled() is False
    assert harness._planner_timeout_failopen_enabled() is False
def test_strict_protocol_grounding_must_raise_for_planning_strict_policy(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    assert harness._strict_protocol_grounding_must_raise(
        "bacterial_evolution_variant_calling",
        planning_strict_benchmark_policy=True,
    ) is True
    assert harness._strict_protocol_grounding_must_raise(
        "unknown_analysis",
        planning_strict_benchmark_policy=False,
    ) is True
    assert harness._strict_protocol_grounding_must_raise(
        "bacterial_evolution_variant_calling",
        planning_strict_benchmark_policy=False,
    ) is False
def test_protocol_normalization_policy_disables_blind_planning_strict_mode(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    enabled, meta = harness._protocol_normalization_policy(
        blind_benchmark_policy=True,
        has_compiler=True,
        planning_strict_benchmark_policy=True,
        protocol_source_files=[],
    )

    assert enabled is False
    assert meta == {
        "changed": False,
        "why": "disabled_for_bioagentbench_planning_strict_policy",
    }


def test_assess_contract_for_plan_scopes_direct_wrapper_tool_hints(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test direct-wrapper contract scoping",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
        },
    }
    plan = {
        "plan": [
            {
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/genes.gtf",
                    "output_gtf": "/tmp/stringtie/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/stringtie/gene_abundances.tsv",
                },
            }
        ]
    }
    contract = {
        "must_include_capabilities": ["alignment", "reference_inputs"],
        "explicit_tool_hints": ["salmon", "stringtie_quant"],
        "required_tool_hints": ["salmon", "stringtie_quant"],
        "blocked_tool_hints": ["salmon"],
    }

    validation = harness._assess_contract_for_plan(plan, contract)

    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []
    assert validation["missing_required_tool_hints"] == []
    assert validation["missing_tool_hints"] == []


def test_assess_contract_for_plan_scopes_direct_wrapper_capabilities(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test direct-wrapper capability scoping",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
        },
    }
    plan = {
        "plan": [
            {
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/genes.gtf",
                    "output_gtf": "/tmp/stringtie/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/stringtie/gene_abundances.tsv",
                },
            }
        ]
    }
    contract = {
        "must_include_capabilities": [
            "annotation",
            "differential_analysis",
            "quantification",
            "reference_inputs",
        ],
        "explicit_tool_hints": ["stringtie_quant"],
        "required_tool_hints": ["stringtie_quant"],
        "blocked_tool_hints": [],
    }

    validation = harness._assess_contract_for_plan(plan, contract)

    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []
    assert validation["missing_required_tool_hints"] == []
    assert validation["missing_tool_hints"] == []


def test_assess_contract_for_plan_blocks_incomplete_direct_wrapper_steps(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="Use stringtie_quant on /tmp/sample.bam with annotation /tmp/genes.gtf.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
        },
    }
    plan = {
        "plan": [
            {
                "tool_name": "stringtie_quant",
                "arguments": {},
            }
        ]
    }
    contract = {
        "must_include_capabilities": ["quantification", "reference_inputs"],
        "explicit_tool_hints": ["stringtie_quant"],
        "required_tool_hints": ["stringtie_quant"],
        "blocked_tool_hints": [],
    }

    validation = harness._assess_contract_for_plan(plan, contract)

    assert validation["passed"] is False
    assert validation["missing_capabilities"] == ["reference_inputs"]
    assert validation["missing_required_tool_hints"] == []
    assert validation["direct_wrapper_issues"] == [
        "incomplete_direct_wrapper:stringtie_quant:annotation_gtf,input_bam,output_gtf"
    ]


def test_assess_contract_for_plan_rejects_artifact_role_collisions(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="Use stringtie_quant directly.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
        },
    }
    plan = {
        "plan": [
            {
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/stringtie/assembled.gtf",
                    "output_gtf": "/tmp/stringtie/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/stringtie/gene_abundances.tsv",
                },
            }
        ]
    }
    contract = {
        "must_include_capabilities": ["quantification", "reference_inputs"],
        "explicit_tool_hints": ["stringtie_quant"],
        "required_tool_hints": ["stringtie_quant"],
        "blocked_tool_hints": [],
    }

    validation = harness._assess_contract_for_plan(plan, contract)

    assert validation["passed"] is False
    assert validation["artifact_role_issues"] == [
        (
            "stringtie_quant.annotation_gtf:input_equals_output:"
            f"{Path('/tmp/stringtie/assembled.gtf').resolve(strict=False)}"
        )
    ]


def test_assess_contract_for_plan_allows_upstream_bash_output_for_artifact_profile(
    tmp_path: Path,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="Profile the final pathway comparison CSV after generating it.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    final_csv = selected_dir / "final" / "pathway_comparison.csv"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python compare_pathways.py "
                        f"--output-csv {final_csv} "
                        f"--output_dir {selected_dir / 'output'}"
                    )
                },
            },
            {
                "tool_name": "artifact_schema_profile",
                "arguments": {
                    "input_path": str(final_csv),
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["artifact_schema_profiling", "pathway_enrichment"],
        "explicit_tool_hints": [],
        "required_tool_hints": [],
        "blocked_tool_hints": [],
    }

    validation = harness._assess_contract_for_plan(plan, contract)

    assert validation["passed"] is True
    assert validation["artifact_role_issues"] == []


def test_assess_contract_for_plan_allows_upstream_bash_output_prefix_for_snpeff(
    tmp_path: Path,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="Annotate the intersected VCF after subtracting ancestor variants.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    intersect_root = selected_dir / "filtered" / "intersected"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o calls/evol1_raw.vcf /refs/evol1_raw.vcf.gz"
                    )
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o filtered/anc_filtered.vcf.gz /refs/anc_filtered.vcf.gz"
                    )
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o filtered/evol1_filtered.vcf.gz calls/evol1_raw.vcf && "
                        "bcftools isec -w1 -p filtered/intersected "
                        "filtered/evol1_filtered.vcf.gz filtered/anc_filtered.vcf.gz"
                    )
                },
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "ecoli_custom",
                    "input_vcf": str(intersect_root / "0000.vcf"),
                    "output_vcf": str(selected_dir / "annotated" / "evol1.vcf"),
                    "annotation_gff": "/tmp/refs/genes.gff",
                    "reference_fasta": "/tmp/refs/contigs.fasta",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["variant_calling", "annotation"],
        "explicit_tool_hints": [],
        "required_tool_hints": [],
        "blocked_tool_hints": [],
    }

    validation = harness._assess_contract_for_plan(plan, contract)

    assert validation["passed"] is True
    assert validation["artifact_role_issues"] == []


def test_assess_contract_for_plan_allows_upstream_normalized_featurecounts_gff(
    tmp_path: Path,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="Run featureCounts after normalizing the provided GFF for Subread compatibility.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    normalized_gff = selected_dir / "references" / "annotation_for_featurecounts.gff"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 pipeline_scripts/normalize_gff_for_featurecounts.py "
                        "/tmp/refs/genes.gff "
                        f"{normalized_gff}"
                    )
                },
            },
            {
                "tool_name": "subread_align",
                "arguments": {
                    "index_base": str(selected_dir / "subread_index" / "genome"),
                    "reference_fasta": "/tmp/refs/genome.fa",
                    "reads_1": "/tmp/inputs/sample_R1.fastq.gz",
                    "reads_2": "/tmp/inputs/sample_R2.fastq.gz",
                    "output_bam": str(selected_dir / "alignments" / "sample.bam"),
                },
            },
            {
                "tool_name": "featurecounts_run",
                "arguments": {
                    "input_bams": [str(selected_dir / "alignments" / "sample.bam")],
                    "annotation_gtf": str(normalized_gff),
                    "annotation_format": "GFF",
                    "feature_type": "gene",
                    "attribute_type": "ID",
                    "output_counts": str(selected_dir / "counts" / "gene_counts.txt"),
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["quantification", "differential_analysis"],
        "explicit_tool_hints": [],
        "required_tool_hints": [],
        "blocked_tool_hints": [],
    }

    validation = harness._assess_contract_for_plan(plan, contract)

    assert validation["passed"] is True
    assert validation["artifact_role_issues"] == []


def test_normalize_plan_for_execution_binds_empty_stringtie_wrapper_from_request(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt=(
            "Use stringtie_quant on /tmp/sample.bam with annotation /tmp/genes.gtf "
            "and write outputs under /tmp/stringtie."
        ),
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
        },
    }
    harness.run["plan_contract"] = {
        "required_output_paths": ["/tmp/stringtie"],
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(
        {
            "plan": [
                {
                    "tool_name": "stringtie_quant",
                    "arguments": {},
                }
            ]
        }
    )

    assert meta["changed"] is True
    args = normalized["plan"][0]["arguments"]
    assert args["input_bam"] == "/tmp/sample.bam"
    assert args["annotation_gtf"] == "/tmp/genes.gtf"
    assert args["output_gtf"] == str(selected_dir.resolve(strict=False) / "assembled.gtf")


def test_normalize_plan_for_execution_restores_stringtie_annotation_gtf_after_output_collision(
    tmp_path: Path,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt=(
            "Use only the stringtie_quant tool on the coordinate-sorted BAM at "
            "/tmp/sample.bam with the annotation GTF at /tmp/refs/genes.gtf. "
            "Write the assembled transcript GTF to /tmp/stringtie/assembled.gtf "
            "and the gene abundance table to /tmp/stringtie/gene_abundances.tsv."
        ),
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
            "preserve_input_paths": True,
            "preserve_output_paths": True,
        },
    }
    harness.run["plan_contract"] = {
        "required_output_paths": [
            "/tmp/stringtie/assembled.gtf",
            "/tmp/stringtie/gene_abundances.tsv",
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(
        {
            "plan": [
                {
                    "tool_name": "stringtie_quant",
                    "arguments": {
                        "input_bam": "/tmp/sample.bam",
                        "annotation_gtf": "/tmp/stringtie/assembled.gtf",
                        "output_gtf": "/tmp/stringtie/assembled.gtf",
                        "gene_abundance_tsv": "/tmp/stringtie/gene_abundances.tsv",
                    },
                }
            ]
        }
    )

    args = normalized["plan"][0]["arguments"]
    assert args["annotation_gtf"] == "/tmp/refs/genes.gtf"
    assert args["output_gtf"] == str(selected_dir.resolve(strict=False) / "assembled.gtf")
    assert meta.get("artifact_role_issues", []) == []


def test_normalize_plan_for_execution_rebinds_stringtie_inspection_bash_run_to_bound_artifact(
    tmp_path: Path,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    legacy_output_dir = tmp_path / "legacy_output"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt=(
            "Proceed with execution now. Run stringtie_quant on /tmp/sample.bam with "
            "annotation /tmp/refs/genes.gtf, then inspect the gene abundance table "
            "and explain what it contains."
        ),
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
        },
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(
        {
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "stringtie_quant",
                    "arguments": {
                        "input_bam": "/tmp/sample.bam",
                        "annotation_gtf": "/tmp/refs/genes.gtf",
                        "output_gtf": str(legacy_output_dir / "assembled.gtf"),
                        "gene_abundance_tsv": str(legacy_output_dir / "gene_abundances.tsv"),
                    },
                },
                {
                    "step_id": 2,
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": f"head -n 20 {legacy_output_dir / 'ERR127302_chr14_gene_abundance.tsv'}",
                    },
                },
            ]
        }
    )

    step1_args = normalized["plan"][0]["arguments"]
    step2_command = normalized["plan"][1]["arguments"]["command"]
    assert step1_args["gene_abundance_tsv"] == str(selected_dir.resolve(strict=False) / "gene_abundances.tsv")
    assert step2_command == f"head -n 20 {selected_dir.resolve(strict=False) / 'gene_abundances.tsv'}"
    assert (
        "direct_wrapper_inspection_bash_run_repairs" in meta
        or "artifact_role_repairs_after_output_redirect" in meta
    )


def test_normalize_plan_for_execution_repairs_missing_snpeff_input_from_data_root(
    tmp_path: Path,
):
    selected_dir = tmp_path / "workspace"
    data_root = tmp_path / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    input_vcf = data_root / "ex1.eff.vcf"
    input_vcf.write_text("##fileformat=VCFv4.2\n")
    cfg = HarnessConfig(
        prompt="Annotate the supplied family VCF with SnpEff and keep outputs under the run directory.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "analysis_type": "variant_annotation",
        "execution_contract": {
            "analysis_family": "variant_annotation",
            "input_mode": "vcf",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["snpeff_annotate"],
        },
    }
    harness.run["plan_contract"] = {}

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(
        {
            "plan": [
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {
                        "genome_db": "GRCh37.75",
                        "input_vcf": str(selected_dir / "ex1.eff.vcf"),
                        "output_vcf": str(selected_dir / "ex1.snpeff_annotated.vcf"),
                    },
                }
            ]
        }
    )

    args = normalized["plan"][0]["arguments"]
    assert args["input_vcf"] == str(input_vcf.resolve(strict=False))
    assert args["output_vcf"] == str((selected_dir / "ex1.snpeff_annotated.vcf").resolve(strict=False))
    assert meta.get("artifact_role_issues", []) == []


def test_normalize_plan_for_execution_preserves_explicit_deseq_paths_after_output_redirect(
    tmp_path: Path,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt=(
            "Run deseq2_run directly on /tmp/airway/airway_counts.tsv with "
            "/tmp/airway/airway_metadata_dex.tsv for dex, keep intermediate "
            "outputs under /tmp/reports/work, and write the final CSV to "
            "/tmp/reports/final_table.csv."
        ),
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "rna_seq_differential_expression",
            "input_mode": "count_matrix",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["deseq2_run"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["deseq2_run"],
            "preserve_input_paths": True,
            "preserve_output_paths": True,
            "locked_argument_values": {
                "deseq2_run": {
                    "output_dir": "/tmp/reports/work",
                }
            },
        },
        "parameter_profile": [
            {
                "tool_name": "deseq2_run",
                "settings": {},
                "rationale": "Keep the run on the direct count-matrix DESeq2 path.",
            }
        ],
    }
    harness.run["plan_contract"] = {
        "required_output_paths": ["/tmp/reports/work", "/tmp/reports/final_table.csv"],
    }

    corrupted_counts = (
        f"{Path.cwd()}/"
        f"{Path.cwd()}/workspace/non_bioagent_real_data/airway/airway_counts.tsv"
    )
    corrupted_metadata = (
        f"{Path.cwd()}/"
        f"{Path.cwd()}/workspace/non_bioagent_real_data/airway/airway_metadata_dex.tsv"
    )
    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(
        {
            "plan": [
                {
                    "tool_name": "deseq2_run",
                    "arguments": {
                        "counts_matrix": corrupted_counts,
                        "metadata_table": corrupted_metadata,
                        "design_formula": "~ dex",
                        "contrast": "dex",
                        "output_dir": "work",
                    },
                }
            ]
        }
    )

    assert meta["changed"] is True
    args = normalized["plan"][0]["arguments"]
    assert args["counts_matrix"] == "/tmp/airway/airway_counts.tsv"
    assert args["metadata_table"] == "/tmp/airway/airway_metadata_dex.tsv"
    assert args["output_dir"] == "/tmp/reports/work"


def test_generate_plan_with_supervision_rejects_incomplete_direct_wrapper_after_max_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="Use stringtie_quant directly.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
        },
    }
    monkeypatch.setattr(harness, "_planner_template_fastpath_candidate", lambda contract: (None, {}))
    monkeypatch.setattr(harness, "_planner_max_attempts", lambda: 1)
    monkeypatch.setattr(
        harness,
        "_planner_attempt_with_heartbeat",
        lambda **kwargs: (
            {"thought_process": "bad", "plan": [{"tool_name": "stringtie_quant", "arguments": {}}]},
            0.1,
        ),
    )

    contract = {
        "must_include_capabilities": ["quantification"],
        "explicit_tool_hints": ["stringtie_quant"],
        "required_tool_hints": ["stringtie_quant"],
    }

    with pytest.raises(RuntimeError, match="Planner did not produce a usable plan"):
        harness._generate_plan_with_supervision(contract)


def test_generate_plan_with_supervision_protocol_normalizes_official_viral_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = tmp_path / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    cfg = HarnessConfig(
        prompt="Classify viruses from paired-end reads.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "analysis_type": "viral_metagenomics",
        "protocol_grounding": {"grounded": True, "source_files": []},
    }
    monkeypatch.setattr(harness, "_planner_template_fastpath_candidate", lambda contract: (None, {}))
    monkeypatch.setattr(harness, "_planner_max_attempts", lambda: 1)
    monkeypatch.setattr(
        harness,
        "_planner_attempt_with_heartbeat",
        lambda **kwargs: (
            {
                "thought_process": "gemma-style viral skeleton",
                "plan": [
                    {"tool_name": "fastp_run", "arguments": {}, "step_id": 1},
                    {"tool_name": "bash_run", "arguments": {}, "step_id": 2},
                ],
            },
            0.1,
        ),
    )

    captured: dict[str, object] = {}
    repaired_plan = {
        "thought_process": "rescued via compiler",
        "plan": [
            {
                "tool_name": "fastp_run",
                "arguments": {"reads_1": "sample_R1.fastq.gz"},
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "python3 bio_harness/pipeline_scripts/classify_viral_reads_kmer.py"
                },
                "step_id": 2,
            },
        ],
    }

    def _fake_protocol_repair(plan, *, analysis_spec, selected_dir, data_root):
        captured["plan"] = plan
        captured["analysis_spec"] = analysis_spec
        captured["selected_dir"] = selected_dir
        captured["data_root"] = data_root
        return repaired_plan, {"changed": True, "why": "deterministic_protocol_repair_applied"}

    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_supervision.deterministic_protocol_repair",
        _fake_protocol_repair,
    )

    def _assess(plan, _contract):
        command = str(
            (
                (plan.get("plan", [{}])[-1].get("arguments", {}))
                if isinstance(plan.get("plan", [{}])[-1], dict)
                else {}
            ).get("command", "")
        )
        if "classify_viral_reads_kmer.py" in command:
            return {
                "passed": True,
                "missing_capabilities": [],
                "missing_tool_hints": [],
                "direct_wrapper_issues": [],
                "artifact_role_issues": [],
            }
        return {
            "passed": False,
            "missing_capabilities": ["alignment", "metagenomics_profiling", "reference_inputs"],
            "missing_tool_hints": ["minimap2"],
            "direct_wrapper_issues": [],
            "artifact_role_issues": [],
        }

    monkeypatch.setattr(harness, "_assess_contract_for_plan", _assess)

    contract = {
        "must_include_capabilities": ["alignment", "metagenomics_profiling", "reference_inputs"],
        "explicit_tool_hints": ["minimap2"],
    }
    plan, meta = harness._generate_plan_with_supervision(contract)

    assert plan == repaired_plan
    assert meta["strategy"] == "direct_user_prompt"
    assert meta["contract_validation"]["passed"] is True
    assert harness.run["planning_attempts"][0]["contract_passed"] is True
    assert harness.run["planning_attempts"][0]["protocol_normalized_before_contract_check"] is True
    assert captured["analysis_spec"] == harness.run["analysis_spec"]
    assert captured["selected_dir"] == selected_dir
    assert captured["data_root"] == data_root


def test_generate_plan_with_supervision_preserves_passing_official_viral_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = tmp_path / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    cfg = HarnessConfig(
        prompt="Classify viruses from paired-end reads.",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "analysis_type": "viral_metagenomics",
        "protocol_grounding": {"grounded": True, "source_files": []},
    }
    monkeypatch.setattr(harness, "_planner_template_fastpath_candidate", lambda contract: (None, {}))
    monkeypatch.setattr(harness, "_planner_max_attempts", lambda: 1)
    passing_plan = {
        "thought_process": "qwen-style viral plan",
        "plan": [
            {
                "tool_name": "fastp_run",
                "arguments": {"reads_1": "sample_R1.fastq.gz"},
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "python3 bio_harness/pipeline_scripts/classify_viral_reads_kmer.py"
                },
                "step_id": 2,
            },
        ],
    }
    monkeypatch.setattr(
        harness,
        "_planner_attempt_with_heartbeat",
        lambda **kwargs: (passing_plan, 0.1),
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_supervision.deterministic_protocol_repair",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("passing attempts should not be protocol-normalized")
        ),
    )
    monkeypatch.setattr(
        harness,
        "_assess_contract_for_plan",
        lambda _plan, _contract: {
            "passed": True,
            "missing_capabilities": [],
            "missing_tool_hints": [],
            "direct_wrapper_issues": [],
            "artifact_role_issues": [],
        },
    )

    contract = {
        "must_include_capabilities": ["alignment", "metagenomics_profiling", "reference_inputs"],
        "explicit_tool_hints": ["minimap2"],
    }
    plan, meta = harness._generate_plan_with_supervision(contract)

    assert plan == passing_plan
    assert meta["strategy"] == "direct_user_prompt"
    assert meta["contract_validation"]["passed"] is True
    assert "protocol_normalized_before_contract_check" not in harness.run["planning_attempts"][0]


def test_completed_run_contract_uses_execution_scoped_capabilities(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test completed-run contract scoping",
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
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "execution_contract": {
            "analysis_family": "transcript_quantification",
            "input_mode": "aligned_bam",
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
        },
    }
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/genes.gtf",
                    "output_gtf": "/tmp/stringtie/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/stringtie/gene_abundances.tsv",
                },
            }
        ]
    }
    harness.run["plan_contract"] = {
        "must_include_capabilities": [
            "annotation",
            "differential_analysis",
            "quantification",
            "reference_inputs",
        ],
        "explicit_tool_hints": ["stringtie_quant"],
        "required_tool_hints": ["stringtie_quant"],
        "blocked_tool_hints": [],
    }

    validation = harness._assess_completed_run_contract()

    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []
    assert validation["missing_required_tool_hints"] == []
    assert validation["missing_tool_hints"] == []
