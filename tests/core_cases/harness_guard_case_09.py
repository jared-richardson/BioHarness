from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_planner_process_isolation_repeated_timeouts_do_not_overrun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.run_agent_e2e_preexecution_repairs as harness_mod

    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test repeated planner isolation timeouts",
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
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", "1")

    class _HungConn:
        def __init__(self):
            self.closed = False

        def poll(self, timeout=None):
            time.sleep(float(timeout or 0.0))
            return False

        def recv(self):
            raise EOFError("no result")

        def close(self):
            self.closed = True
            return None

    class _HungProcess:
        def __init__(self, *args, **kwargs):
            self._alive = True
            self.terminated = 0
            self.killed = 0

        def start(self):
            return None

        def is_alive(self):
            return self._alive

        def terminate(self):
            self.terminated += 1
            self._alive = False
            return None

        def join(self, timeout=None):
            return None

        def kill(self):
            self.killed += 1
            self._alive = False
            return None

    created_processes: list[_HungProcess] = []

    class _FakeContext:
        def Pipe(self, duplex=False):
            assert duplex is False
            return _HungConn(), _HungConn()

        def Process(self, target=None, args=(), daemon=True):
            proc = _HungProcess()
            created_processes.append(proc)
            return proc

    timeout_events: list[dict[str, object]] = []
    original_append_event = harness._append_event

    def _capture_event(*args, **kwargs):
        payload = kwargs.get("payload", {})
        if kwargs.get("event_type") == "PLANNER_ATTEMPT_TIMEOUT_FORCED":
            timeout_events.append(dict(payload))
        return original_append_event(*args, **kwargs)

    monkeypatch.setattr(harness_mod.mp, "get_all_start_methods", lambda: ["fork"])
    monkeypatch.setattr(harness_mod.mp, "get_context", lambda _method: _FakeContext())
    monkeypatch.setattr(harness, "_append_event", _capture_event)

    start = time.time()
    for attempt_num in (1, 2):
        with pytest.raises(TimeoutError, match="supervisor wall-clock limit"):
            harness._planner_attempt_with_heartbeat(
                prompt="test",
                strategy=f"attempt_{attempt_num}",
                attempt_num=attempt_num,
            )
    elapsed = time.time() - start

    assert elapsed < 5.0
    assert len(timeout_events) == 2
    assert timeout_events[0]["attempt"] == 1
    assert timeout_events[1]["attempt"] == 2
    assert int(timeout_events[0]["elapsed_seconds"]) <= 2
    assert int(timeout_events[1]["elapsed_seconds"]) <= 2
    assert len(created_processes) == 2
    assert all(proc.terminated == 1 for proc in created_processes)
def test_planner_process_isolation_timeout_does_not_depend_on_pipe_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.run_agent_e2e as harness_mod

    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test planner timeout with blocking poll",
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
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", "1")

    class _BlockingConn:
        def __init__(self):
            self.closed = False

        def poll(self, timeout=None):
            time.sleep(10.0)
            return False

        def recv(self):
            raise EOFError("no result")

        def close(self):
            self.closed = True
            return None

    class _JoinDrivenProcess:
        def __init__(self, *args, **kwargs):
            self._alive = True
            self.terminated = 0

        def start(self):
            return None

        def is_alive(self):
            return self._alive

        def terminate(self):
            self.terminated += 1
            self._alive = False
            return None

        def join(self, timeout=None):
            time.sleep(float(timeout or 0.0))
            return None

        def kill(self):
            self._alive = False
            return None

    created_processes: list[_JoinDrivenProcess] = []

    class _FakeContext:
        def Pipe(self, duplex=False):
            assert duplex is False
            return _BlockingConn(), _BlockingConn()

        def Process(self, target=None, args=(), daemon=True):
            proc = _JoinDrivenProcess()
            created_processes.append(proc)
            return proc

    monkeypatch.setattr(harness_mod.mp, "get_all_start_methods", lambda: ["fork"])
    monkeypatch.setattr(harness_mod.mp, "get_context", lambda _method: _FakeContext())

    start = time.monotonic()
    with pytest.raises(TimeoutError, match="supervisor wall-clock limit"):
        harness._planner_attempt_with_heartbeat(
            prompt="test",
            strategy="blocking_poll",
            attempt_num=1,
        )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert len(created_processes) == 1
    assert created_processes[0].terminated == 1
def test_planner_attempt_timeout_scales_with_strategy_and_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test planner timeout budgeting",
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
    harness.run["analysis_spec"] = {"protocol_grounding": {"grounded": True}}
    monkeypatch.delenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("BIO_HARNESS_PLANNER_TIMEOUT_PREEXEC_PROTOCOL_SECONDS", raising=False)
    monkeypatch.setenv("BIO_HARNESS_LLM_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TWO_STAGE_MODE", "always")
    monkeypatch.setenv("BIO_HARNESS_STRICT_LLM_PLANNING", "1")

    short_direct = harness._planner_attempt_timeout_seconds(
        strategy="direct_user_prompt",
        prompt="short prompt",
    )
    long_protocol = harness._planner_attempt_timeout_seconds(
        strategy="preexecution_protocol_repair",
        prompt="x" * 12000,
    )

    assert short_direct >= 180
    assert long_protocol > short_direct

    monkeypatch.setenv("BIO_HARNESS_PLANNER_TIMEOUT_PREEXEC_PROTOCOL_SECONDS", "420")
    overridden = harness._planner_attempt_timeout_seconds(
        strategy="preexecution_protocol_repair",
        prompt="short prompt",
    )
    assert overridden == 420
def test_simple_grounded_planner_shape_uses_shorter_direct_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test simple grounded timeout",
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
        "plan_skeleton": [
            ["sc_count_and_cluster", "Count and cluster single-cell reads", {}],
        ],
        "protocol_grounding": {
            "grounded": True,
            "required_tools": ["sc_count_and_cluster"],
        },
    }
    monkeypatch.delenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("BIO_HARNESS_LLM_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TWO_STAGE_MODE", "always")
    monkeypatch.setenv("BIO_HARNESS_STRICT_LLM_PLANNING", "1")

    timeout_seconds = harness._planner_attempt_timeout_seconds(
        strategy="direct_user_prompt",
        prompt="short prompt",
    )

    assert timeout_seconds >= 180
    assert timeout_seconds < 300
def test_preexecution_protocol_repair_uses_supervised_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.run_agent_e2e_preexecution_repairs as harness_mod

    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test protocol repair supervision",
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
    harness.run["plan"] = {"plan": [{"tool_name": "fastqc_run", "arguments": {"input_file": "reads.fastq.gz", "output_dir": "qc"}, "step_id": 1}]}

    calls: list[tuple[str, str]] = []
    candidate = {"thought_process": "fixed", "plan": [{"tool_name": "fastqc_run", "arguments": {"input_file": "reads.fastq.gz", "output_dir": "qc"}, "step_id": 1}]}

    monkeypatch.setattr(
        harness,
        "_build_repair_prompt_context",
        lambda **_: {"focus_mode": "full_plan", "focus_steps": []},
    )
    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda *, prompt, strategy: calls.append((strategy, prompt)) or candidate,
    )
    monkeypatch.setattr(
        harness,
        "_normalize_plan_for_execution",
        lambda plan: (plan, {"changed": False}, {"changed": False}),
    )
    monkeypatch.setattr(
        harness_mod,
        "assess_protocol_grounding",
        lambda _plan, _analysis_spec: {"passed": True, "missing_required_tools": [], "missing_plan_signals": [], "issues": []},
    )

    repaired, action, details = harness._attempt_preexecution_protocol_repair(
        analysis_spec={"protocol_grounding": {"required_tools": ["freebayes_call"]}},
        validation={"passed": False, "missing_required_tools": ["freebayes_call"], "missing_plan_signals": []},
    )

    assert repaired is True
    assert action == "preexecution_protocol_replan"
    assert calls and calls[0][0] == "preexecution_protocol_repair"
    assert harness.run["plan"] == candidate
    assert details["protocol_validation_after"]["passed"] is True
def test_runtime_failure_replan_uses_supervised_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test runtime repair supervision",
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
    harness.run["plan"] = {"plan": [{"tool_name": "fastqc_run", "arguments": {"input_file": "reads.fastq.gz", "output_dir": "qc"}, "step_id": 1}]}
    harness.run["analysis_spec"] = {"analysis_type": "test"}

    calls: list[str] = []
    candidate = {"thought_process": "repaired", "plan": [{"tool_name": "fastqc_run", "arguments": {"input_file": "reads.fastq.gz", "output_dir": "qc"}, "step_id": 1}]}

    monkeypatch.setattr(
        harness,
        "_build_repair_prompt_context",
        lambda **_: {"focus_mode": "step_local", "focus_steps": []},
    )
    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda *, prompt, strategy: calls.append(strategy) or candidate,
    )
    monkeypatch.setattr(
        harness_mod := __import__("scripts.run_agent_e2e_runtime_repair_actions", fromlist=["canonicalize_execution_plan"]),
        "canonicalize_execution_plan",
        lambda plan, data_root="": (plan, {"changed": False}),
    )
    monkeypatch.setattr(harness, "_prune_and_bound_replan_candidate", lambda plan, failure_class, before_steps: (plan, {"step_growth": 0, "heavy_reintroduced": False}))
    monkeypatch.setattr(harness, "_assess_contract_for_plan", lambda _plan, _contract: {"passed": True})
    monkeypatch.setattr(harness, "_template_fallback_guard", lambda _failure_class: {"allowed": True, "repair_scope": {"scope": "full_replan"}})
    monkeypatch.setattr(harness_mod, "_missing_local_scripts_for_plan", lambda _plan, _selected_dir: [])
    monkeypatch.setattr(harness_mod, "_apply_repaired_plan_with_resume", lambda run, plan: {"resume_idx": 0, "preserved_completed_steps": 0})

    repaired, action, details = harness._maybe_replan_for_failure("runtime_step_failure", "unit test")

    assert repaired is True
    assert calls == ["runtime_repair_runtime_step_failure_step_local_1"]
    assert action == "replan_with_failure_context"
    assert "diff_summary" in details
    assert details["repair_focus_mode"] == "step_local"
def test_runtime_failure_replan_retries_with_progressive_focus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.run_agent_e2e_runtime_repair_actions as harness_mod

    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test runtime repair retry ladder",
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
    harness.run["plan"] = {"plan": [{"tool_name": "fastqc_run", "arguments": {"input_file": "reads.fastq.gz", "output_dir": "qc"}, "step_id": 1}]}
    harness.run["analysis_spec"] = {"analysis_type": "test"}

    prompts: list[tuple[str, str]] = []

    def _fake_replan(*, prompt, strategy):
        prompts.append((strategy, prompt))
        if strategy.endswith("step_local_1"):
            return {}
        if strategy.endswith("subgraph_local_2"):
            return {"thought_process": "still bad", "plan": []}
        return {"thought_process": "fixed", "plan": [{"tool_name": "fastqc_run", "arguments": {"input_file": "reads.fastq.gz", "output_dir": "qc"}, "step_id": 1}]}

    monkeypatch.setattr(
        harness,
        "_build_repair_prompt_context",
        lambda **kwargs: {"focus_mode": kwargs.get("focus_mode", ""), "focus_steps": []},
    )
    monkeypatch.setattr(harness, "_supervised_model_replan", _fake_replan)
    monkeypatch.setattr(harness_mod, "canonicalize_execution_plan", lambda plan, data_root="": (plan, {"changed": False}))
    monkeypatch.setattr(harness, "_prune_and_bound_replan_candidate", lambda plan, failure_class, before_steps: (plan, {"step_growth": 0, "heavy_reintroduced": False}))
    monkeypatch.setattr(harness, "_assess_contract_for_plan", lambda _plan, _contract: {"passed": True})
    monkeypatch.setattr(harness_mod, "_missing_local_scripts_for_plan", lambda _plan, _selected_dir: [])
    monkeypatch.setattr(harness_mod, "_apply_repaired_plan_with_resume", lambda run, plan: {"resume_idx": 0, "preserved_completed_steps": 0})

    repaired, action, details = harness._maybe_replan_for_failure("runtime_step_failure", "unit test")

    assert repaired is True
    assert action == "replan_with_failure_context"
    assert [strategy for strategy, _ in prompts] == [
        "runtime_repair_runtime_step_failure_step_local_1",
        "runtime_repair_runtime_step_failure_subgraph_local_2",
        "runtime_repair_runtime_step_failure_full_plan_3",
    ]
    assert details["repair_focus_mode"] == "full_plan"
    assert "Focused repair context:" in prompts[0][1]


def test_runtime_failure_replan_uses_enriched_contract_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.run_agent_e2e_runtime_repair_actions as harness_mod

    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test runtime repair contract guard",
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
                "tool_name": "fastqc_run",
                "arguments": {"input_file": "reads.fastq.gz", "output_dir": "qc"},
                "step_id": 1,
            }
        ]
    }
    harness.run["analysis_spec"] = {"analysis_type": "test"}
    harness.run["plan_contract"] = {}

    candidate = {
        "thought_process": "repaired",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools view -Oz -o ancestor_call/anc_filtered.vcf.gz "
                        "ancestor_call/anc_raw.vcf"
                    )
                },
                "step_id": 1,
            }
        ],
    }

    resume_calls: list[str] = []
    missing_path = (selected_dir / "ancestor_call" / "anc_raw.vcf").resolve(strict=False)
    artifact_issue = (
        "bash_run.command:input_in_selected_dir_without_producer:"
        f"{missing_path}"
    )

    monkeypatch.setattr(
        harness,
        "_build_repair_prompt_context",
        lambda **_: {"focus_mode": "step_local", "focus_steps": []},
    )
    monkeypatch.setattr(harness, "_runtime_replan_focus_modes", lambda: ["step_local"])
    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda *, prompt, strategy: candidate,
    )
    monkeypatch.setattr(
        harness_mod,
        "canonicalize_execution_plan",
        lambda plan, data_root="": (plan, {"changed": False}),
    )
    monkeypatch.setattr(
        harness,
        "_prune_and_bound_replan_candidate",
        lambda plan, failure_class, before_steps: (
            plan,
            {"step_growth": 0, "heavy_reintroduced": False},
        ),
    )
    monkeypatch.setattr(
        harness,
        "_assess_contract_for_plan",
        lambda _plan, _contract: {
            "passed": False,
            "missing_capabilities": [],
            "missing_required_tool_hints": [],
            "missing_tool_hints": [],
            "direct_wrapper_issues": [],
            "artifact_role_issues": [artifact_issue],
        },
    )
    monkeypatch.setattr(
        harness,
        "_build_contract_template_repair",
        lambda _failure_class: (None, "template_not_applicable", {}),
    )
    monkeypatch.setattr(harness_mod, "_missing_local_scripts_for_plan", lambda _plan, _selected_dir: [])
    monkeypatch.setattr(
        harness_mod,
        "_apply_repaired_plan_with_resume",
        lambda run, plan: resume_calls.append("applied") or {"resume_idx": 0, "preserved_completed_steps": 0},
    )

    repaired, action, details = harness._maybe_replan_for_failure("runtime_step_failure", "unit test")

    assert repaired is False
    assert action == "replan_contract_failed"
    assert resume_calls == []
    assert details["runtime_replan_attempts"][0]["reason"] == "contract_validation_failed"
    assert details["last_validation"]["artifact_role_issues"] == [artifact_issue]


def test_direct_skill_smoke_runtime_failure_disables_replan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test smoke runtime repair block",
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
    harness.run["analysis_spec"] = {"analysis_type": "direct_skill_smoke"}

    called: list[str] = []
    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda **kwargs: called.append("replan") or {},
    )

    repaired, action, details = harness._maybe_replan_for_failure(
        "runtime_step_failure",
        "unit test",
    )

    assert repaired is False
    assert action == "direct_skill_smoke_repair_disabled"
    assert called == []
    assert "direct_skill_smoke" in details["why"]


def test_blind_benchmark_runtime_failure_disables_late_replan_after_execution_started(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test late benchmark runtime replan guard",
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
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["execution_started"] = True

    called: list[str] = []
    monkeypatch.setattr(
        harness,
        "_build_contract_template_repair",
        lambda _failure_class: called.append("template_fallback") or ({}, "fallback", {}),
    )
    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda **kwargs: called.append("replan") or {},
    )

    repaired, action, details = harness._maybe_replan_for_failure(
        "runtime_step_failure",
        "unit test",
    )

    assert repaired is False
    assert action == "benchmark_no_late_replan"
    assert called == []
    assert details["execution_started"] is True
