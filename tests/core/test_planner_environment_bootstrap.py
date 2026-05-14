from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts.run_agent_e2e_planner_settings import AgentE2EPlannerSettingsMixin
from scripts.run_agent_e2e_planner_supervision import (
    AgentE2EPlannerSupervisionMixin,
    _planner_trace_unproductive_limit_exceeded,
)


class _PlannerHarness(
    AgentE2EPlannerSupervisionMixin,
    AgentE2EPlannerSettingsMixin,
):
    def __init__(self) -> None:
        self.cfg = SimpleNamespace(prompt="Analyze these reads.", quiet=True)
        self.run = {
            "analysis_spec": {},
            "planning_attempts": [],
            "environment_snapshot": {
                "available_tools": {"samtools": "/usr/bin/samtools", "scanpy": ""},
                "tool_groups": {"utilities": ["samtools"]},
                "tool_versions": {"samtools": "samtools 1.20"},
                "pixi_bin_dirs": [],
                "pixi_jvm_bin_dirs": [],
                "jvm_available": False,
                "data_root": "inputs_readonly",
                "data_inventory": [{"name": "reads.fastq.gz", "relative_path": "reads.fastq.gz"}],
                "system_resources": {
                    "platform": "Darwin",
                    "machine": "arm64",
                    "cpu_count": 8,
                    "ram_gb": 32.0,
                    "disk_free_gb": 200.0,
                },
                "known_workarounds": [],
            },
        }
        self.captured_prompt = ""

    def _planner_template_fastpath_candidate(
        self,
        *,
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        del contract
        return None, {"why": "disabled"}

    def _maybe_prewarm_planner(self) -> None:
        return None

    def _planner_max_attempts(self) -> int:
        return 1

    def _planner_attempt_with_heartbeat(
        self,
        *,
        prompt: str,
        strategy: str,
        attempt_num: int,
        planner_mode: str = "auto",
        seed_plan: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> tuple[dict[str, Any], float]:
        del strategy, attempt_num, planner_mode, seed_plan, model_override
        self.captured_prompt = prompt
        return {"plan": [{"tool_name": "bash_run", "arguments": {"command": "echo ok"}}]}, 0.01

    def _normalize_plan_for_execution(
        self,
        plan: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return plan, {}, {}

    def _assess_contract_for_plan(
        self,
        plan: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        del plan, contract
        return {
            "passed": True,
            "missing_capabilities": [],
            "missing_tool_hints": [],
            "direct_wrapper_issues": [],
            "artifact_role_issues": [],
        }

    def _append_event(self, **_kwargs: Any) -> None:
        return None


class _CompilerFastpathHarness(AgentE2EPlannerSettingsMixin):
    def __init__(self, tmp_path: Path) -> None:
        self.cfg = SimpleNamespace(
            prompt="Identify and annotate genome variants.",
            quiet=True,
            selected_dir=tmp_path / "selected",
            data_root=tmp_path / "data",
        )
        self.cfg.selected_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.data_root.mkdir(parents=True, exist_ok=True)
        self.run = {
            "analysis_spec": {
                "analysis_type": "bacterial_evolution_variant_calling",
                "protocol_grounding": {"grounded": True},
            },
            "protocol_repair_enabled": True,
        }
        self.fallback_calls = 0

    def _assess_contract_for_plan(
        self,
        plan: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        del contract
        tool_names = [
            str(step.get("tool_name", "")).strip().lower()
            for step in plan.get("plan", [])
            if isinstance(step, dict)
        ]
        passed = "freebayes_call" in tool_names and "spades_assemble" in tool_names
        return {
            "passed": passed,
            "missing_capabilities": [] if passed else ["annotation", "reference_inputs"],
            "missing_tool_hints": [] if passed else ["freebayes_call"],
            "direct_wrapper_issues": [],
            "artifact_role_issues": [],
        }

    def _build_contract_template_repair(
        self,
        failure_class: str,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        del failure_class
        self.fallback_calls += 1
        return None, "template_not_applicable", {"why": "unit_test_no_fallback"}


class _TraceResumeHarness(
    AgentE2EPlannerSupervisionMixin,
    AgentE2EPlannerSettingsMixin,
):
    def __init__(self, tmp_path: Path) -> None:
        self.cfg = SimpleNamespace(
            prompt="Identify shared evolved variants after subtracting ancestor background.",
            quiet=True,
            selected_dir=tmp_path / "selected",
            data_root=tmp_path / "data",
            benchmark_policy="scientific_harness",
        )
        self.cfg.selected_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.data_root.mkdir(parents=True, exist_ok=True)
        planner_dir = tmp_path / "planner"
        planner_dir.mkdir(parents=True, exist_ok=True)
        self.trace_dir = planner_dir
        self.events: list[dict[str, Any]] = []
        self.expand_calls: list[int] = []
        self.run = {
            "analysis_spec": {
                "analysis_type": "bacterial_evolution_variant_calling",
                "benchmark_policy": "scientific_harness",
            },
            "planning_attempts": [],
            "run_files": {"planner": str(planner_dir)},
        }
        self.orchestrator = SimpleNamespace(
            _available_skill_metadata=lambda: [{"name": "bash_run", "description": "execute shell command"}],
            biollm=SimpleNamespace(_expand_workflow_step=self._expand_missing_step),
        )

    def _expand_missing_step(
        self,
        *,
        workflow_step: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        step_id = int(workflow_step.get("step_id", 0) or 0)
        self.expand_calls.append(step_id)
        return {
            "step_id": step_id,
            "tool_name": str(workflow_step.get("tool_name", "") or "bash_run"),
            "arguments": {"command": "python export_shared.py"},
            "produces": [str(self.cfg.selected_dir / "variants_shared.csv")],
        }

    def _planner_template_fastpath_candidate(
        self,
        *,
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        del contract
        return None, {"why": "disabled"}

    def _maybe_prewarm_planner(self) -> None:
        return None

    def _planner_max_attempts(self) -> int:
        return 1

    def _planner_attempt_with_heartbeat(
        self,
        *,
        prompt: str,
        strategy: str,
        attempt_num: int,
        planner_mode: str = "auto",
        seed_plan: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> tuple[dict[str, Any], float]:
        del prompt, strategy, attempt_num, planner_mode, seed_plan, model_override
        raise TimeoutError("Planner request timed out while waiting for model output.")

    def _normalize_plan_for_execution(
        self,
        plan: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return plan, {}, {}

    def _assess_contract_for_plan(
        self,
        plan: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        del contract
        passed = len(plan.get("plan", [])) == 2
        return {
            "passed": passed,
            "missing_capabilities": [] if passed else ["shared_variant_export"],
            "missing_tool_hints": [],
            "direct_wrapper_issues": [],
            "artifact_role_issues": [],
        }

    def _append_event(self, **kwargs: Any) -> None:
        self.events.append(dict(kwargs))

    def _note_failure_signature(self, _signature: str) -> None:
        return None


def _write_structured_trace(
    planner_dir: Path,
    *,
    base_name: str,
    stage: str,
    payload: dict[str, Any],
    attempt: int = 1,
    pid: int = 111,
) -> None:
    raw_path = planner_dir / f"{base_name}.txt"
    raw_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    event_path = planner_dir / f"{base_name}.json"
    event_path.write_text(
        json.dumps(
            {
                "event_type": "STRUCTURED_SUCCESS",
                "ts": "2026-04-19T11:00:00",
                "pid": pid,
                "trace_context": {"supervisor_attempt": attempt},
                "payload": {"stage": stage, "item_count": 0},
                "raw_content_file": str(raw_path),
                "raw_excerpt": raw_path.read_text(encoding="utf-8"),
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_initial_planning_prompt_includes_environment_snapshot() -> None:
    harness = _PlannerHarness()

    plan, meta = harness._generate_plan_with_supervision({})

    assert plan["plan"][0]["tool_name"] == "bash_run"
    assert meta["strategy"] == "direct_user_prompt"
    assert "## Environment Snapshot" in harness.captured_prompt
    assert "reads.fastq.gz" in harness.captured_prompt
    assert harness.captured_prompt.startswith("Analyze these reads.")


def test_unproductive_structured_trace_limit_blocks_repeated_frontier_rejections() -> None:
    assert _planner_trace_unproductive_limit_exceeded(
        reason="candidate_does_not_advance_branch_frontier",
        count=2,
        limit=2,
    )


def test_unproductive_trace_limit_ignores_raw_trace_noise() -> None:
    assert not _planner_trace_unproductive_limit_exceeded(
        reason="trace_artifact_not_structured_candidate",
        count=20,
        limit=2,
    )


def test_unproductive_structured_trace_limit_waits_for_threshold() -> None:
    assert not _planner_trace_unproductive_limit_exceeded(
        reason="candidate_duplicates_completed_prefix",
        count=1,
        limit=2,
    )


def test_initial_planning_prompt_returns_base_prompt_without_snapshot() -> None:
    harness = _PlannerHarness()
    harness.run["environment_snapshot"] = {}

    assert harness._initial_planning_prompt() == "Analyze these reads."


def test_initial_planning_prompt_respects_env_bootstrap_toggle(
    monkeypatch,
) -> None:
    harness = _PlannerHarness()
    monkeypatch.setenv("BIO_HARNESS_ENV_BOOTSTRAP", "0")

    assert harness._initial_planning_prompt() == "Analyze these reads."


def test_planner_template_fastpath_prefers_protocol_compiler(monkeypatch, tmp_path: Path) -> None:
    harness = _CompilerFastpathHarness(tmp_path)
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "1")

    compiler_plan = {
        "thought_process": "compiled",
        "plan": [
            {"tool_name": "spades_assemble", "arguments": {"reads_1": "a_R1.fastq", "reads_2": "a_R2.fastq"}},
            {"tool_name": "freebayes_call", "arguments": {"input_bam": "aligned.bam", "reference_fasta": "ref.fa"}},
        ],
    }

    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_settings.deterministic_protocol_repair",
        lambda *args, **kwargs: (compiler_plan, {"changed": True, "why": "compiled_bacterial_shared_variant_protocol"}),
    )

    plan, meta = harness._planner_template_fastpath_candidate(
        contract={"must_include_capabilities": ["annotation", "reference_inputs"]}
    )

    assert plan == compiler_plan
    assert meta["why"] == "compiler_fastpath_selected"
    assert meta["analysis_type"] == "bacterial_evolution_variant_calling"
    assert harness.fallback_calls == 0


def test_planner_template_fastpath_skips_protocol_compiler_when_assistance_disabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    harness = _CompilerFastpathHarness(tmp_path)
    harness.run["protocol_repair_enabled"] = False
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "1")

    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_settings.deterministic_protocol_repair",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("compiler should be disabled")),
    )

    plan, meta = harness._planner_template_fastpath_candidate(
        contract={"must_include_capabilities": ["annotation", "reference_inputs"]}
    )

    assert plan is None
    assert meta["why"] == "no_template_candidate"
    assert harness.fallback_calls == 1


def test_planner_timeout_trace_recovery_resumes_missing_hierarchical_step(tmp_path: Path) -> None:
    harness = _TraceResumeHarness(tmp_path)
    _write_structured_trace(
        harness.trace_dir,
        base_name="0001_structured_success",
        stage="workflow_skeleton",
        payload={
            "thought_process": "Split filtering and shared export.",
            "workflow": [
                {"step_id": 1, "tool_name": "bash_run", "objective": "Filter variants", "depends_on": []},
                {"step_id": 2, "tool_name": "bash_run", "objective": "Export shared variants", "depends_on": [1]},
            ],
            "global_constraints": [],
            "final_deliverables": [],
        },
    )
    _write_structured_trace(
        harness.trace_dir,
        base_name="0002_structured_success",
        stage="step_expansion",
        payload={
            "step_id": 1,
            "tool_name": "bash_run",
            "arguments": {"command": "bcftools filter -Oz -o ancestor_filtered.vcf.gz ancestor_raw.vcf.gz"},
        },
    )

    plan, meta = harness._generate_plan_with_supervision({"must_include_capabilities": ["shared_variant_export"]})

    assert isinstance(plan, dict)
    assert len(plan["plan"]) == 2
    assert harness.expand_calls == [2]
    assert meta["strategy"] == "timeout_trace_resume"
    assert meta["trace_recovery"]["recovered_step_ids"] == [2]
    assert any(
        event.get("event_type") == "PLANNER_TIMEOUT_TRACE_RECOVERY_APPLIED"
        for event in harness.events
    )
