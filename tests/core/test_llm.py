from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bio_harness.core.benchmark_policy import BIOAGENTBENCH_PLANNING_STRICT_POLICY
from bio_harness.core.hierarchical_planning import StepExecutionSpecSchema
from bio_harness.core.llm import BioHarnessError, BioLLM, LLMOutputSchema


# ---------------------------------------------------------------------------
# Helper: create a BioLLM instance without connecting to Ollama
# ---------------------------------------------------------------------------


@pytest.fixture
def llm():
    """BioLLM instance for pure-function tests (no network required)."""
    return BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")


def test_think_raises_clear_timeout_error(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    def _raise_timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(llm, "_request_structured_response", _raise_timeout)

    with pytest.raises(BioHarnessError, match="timed out"):
        llm.think(
            "build a simple plan",
            [
                {
                    "name": "bash_run",
                    "description": "execute shell command",
                    "parameters": {
                        "command": {
                            "type": "string",
                            "description": "shell command",
                            "required": True,
                        }
                    },
                }
            ],
        )


def test_think_propagates_supervisor_timeout(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    def _raise_timeout(*_args, **_kwargs):
        raise TimeoutError("Planner attempt timed out at supervisor wall-clock limit (105s). Falling back to recovery strategy.")

    monkeypatch.setattr(llm, "_request_structured_response", _raise_timeout)

    with pytest.raises(TimeoutError, match="supervisor wall-clock limit"):
        llm.think(
            "build a simple plan",
            [
                {
                    "name": "bash_run",
                    "description": "execute shell command",
                    "parameters": {
                        "command": {
                            "type": "string",
                            "description": "shell command",
                            "required": True,
                        }
                    },
                }
            ],
        )


def test_think_uses_two_stage_primary_mode_when_enabled(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TWO_STAGE_MODE", "always")
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    calls: list[str] = []

    def _fake_request(*, stage, **_kwargs):
        calls.append(stage)
        if stage == "abstract_outline":
            return {
                "thought_process": "Outline first.",
                "plan_outline": [
                    {"tool_name": "bash_run", "objective": "List files", "step_id": 1},
                ],
            }
        if stage == "plan_expansion":
            return {
                "thought_process": "Expand outline.",
                "plan": [
                    {"tool_name": "bash_run", "arguments": {"command": "ls"}, "step_id": 1},
                ],
            }
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(llm, "_request_structured_response", _fake_request)

    plan = llm.think(
        "Build a plan that lists files and then exits.",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
    )

    assert plan["plan"][0]["tool_name"] == "bash_run"
    assert calls == ["abstract_outline", "plan_expansion"]


def test_think_two_stage_always_preempts_hierarchical_mode(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TWO_STAGE_MODE", "always")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_HIERARCHICAL_MODE", "always")
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    calls: list[str] = []

    def _fake_two_stage(*_args, **_kwargs):
        calls.append("two_stage")
        return {
            "thought_process": "Outline first.",
            "plan": [
                {"tool_name": "bash_run", "arguments": {"command": "ls"}, "step_id": 1},
            ],
        }

    def _fake_hierarchical(*_args, **_kwargs):
        calls.append("hierarchical")
        raise AssertionError("hierarchical planner should not run before explicit two-stage mode")

    monkeypatch.setattr(llm, "_should_use_two_stage", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(llm, "_hierarchical_mode_enabled", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(llm, "_think_two_stage", _fake_two_stage)
    monkeypatch.setattr(llm, "_think_hierarchical", _fake_hierarchical)

    plan = llm.think(
        "Build a compact shell plan.",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
    )

    assert plan["plan"][0]["tool_name"] == "bash_run"
    assert calls == ["two_stage"]


def test_think_two_stage_primary_propagates_supervisor_timeout(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TWO_STAGE_MODE", "always")
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    def _raise_timeout(*_args, **_kwargs):
        raise TimeoutError("Planner attempt timed out at supervisor wall-clock limit (105s). Falling back to recovery strategy.")

    monkeypatch.setattr(llm, "_think_two_stage", _raise_timeout)

    with pytest.raises(TimeoutError, match="supervisor wall-clock limit"):
        llm.think(
            "Build a plan that lists files and then exits.",
            [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
        )


def test_plan_expansion_budget_scales_for_grounded_multi_step_outline() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    budget = llm._plan_expansion_predict_budget(
        outline={
            "plan_outline": [
                {"tool_name": "bash_run", "objective": f"step {idx}", "step_id": idx}
                for idx in range(1, 9)
            ]
        },
        analysis_spec={
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            "protocol_grounding": {"grounded": True},
        },
        user_query="x" * 1600,
    )

    assert budget >= 4200


def test_think_uses_two_stage_primary_for_planning_strict_auto_mode(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    calls: list[str] = []

    def _fake_request(*, stage, **_kwargs):
        calls.append(stage)
        if stage == "abstract_outline":
            return {
                "thought_process": "Outline first.",
                "plan_outline": [
                    {"tool_name": "bash_run", "objective": "List files", "step_id": 1},
                ],
            }
        if stage == "plan_expansion":
            return {
                "thought_process": "Expand outline.",
                "plan": [
                    {"tool_name": "bash_run", "arguments": {"command": "ls"}, "step_id": 1},
                ],
            }
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(llm, "_should_use_two_stage", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(llm, "_request_structured_response", _fake_request)

    plan = llm.think(
        "Build a plan that lists files and then exits.",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
        analysis_spec={"benchmark_policy": "official_bioagentbench"},
    )

    assert plan["plan"][0]["tool_name"] == "bash_run"
    assert calls == ["abstract_outline", "plan_expansion"]


def test_think_falls_back_to_two_stage_after_direct_plan_json_failure(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    calls: list[str] = []

    def _fake_request(*, stage, **_kwargs):
        calls.append(stage)
        if stage == "direct_plan":
            raise json.JSONDecodeError("Expecting ',' delimiter", "{", 1)
        if stage == "abstract_outline":
            return {
                "thought_process": "Outline first.",
                "plan_outline": [
                    {"tool_name": "bash_run", "objective": "List files", "step_id": 1},
                ],
            }
        if stage == "plan_expansion":
            return {
                "thought_process": "Expand outline.",
                "plan": [
                    {"tool_name": "bash_run", "arguments": {"command": "ls"}, "step_id": 1},
                ],
            }
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(llm, "_should_use_two_stage", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(llm, "_request_structured_response", _fake_request)

    plan = llm.think(
        "Build a compact shell plan.",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
    )

    assert plan["plan"][0]["tool_name"] == "bash_run"
    assert calls == ["direct_plan", "abstract_outline", "plan_expansion"]


def test_normalize_plan_output_accepts_plan_outline_with_arguments() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    normalized = llm._normalize_plan_output(
        {
            "thought_process": "Expand outline.",
            "plan_outline": [
                {"tool_name": "bash_run", "arguments": {"command": "ls"}, "step_id": 1},
            ],
        }
    )

    assert normalized["plan"][0]["tool_name"] == "bash_run"
    assert normalized["plan"][0]["arguments"]["command"] == "ls"


def test_think_hierarchical_mode_expands_workflow_into_executable_plan(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    def _fake_request(*, stage, **_kwargs):
        if stage == "workflow_skeleton":
            return {
                "thought_process": "Use a compact workflow skeleton first.",
                "workflow": [
                    {"tool_name": "bash_run", "objective": "Print working directory", "step_id": 1, "depends_on": []},
                    {"tool_name": "bash_run", "objective": "List files", "step_id": 2, "depends_on": [1]},
                ],
                "global_constraints": ["Keep the workflow simple."],
                "final_deliverables": [],
            }
        if stage == "step_expansion":
            messages = _kwargs.get("messages", [])
            rendered = "\n".join(str(getattr(msg, "content", "")) for msg in messages)
            workflow_step_block = rendered.split("Workflow step:\n", 1)[1].split("\n\nUpstream context:", 1)[0]
            if '"objective": "Print working directory"' in workflow_step_block:
                return {
                    "step_id": 1,
                    "tool_name": "bash_run",
                    "arguments": {"command": "pwd"},
                }
            return {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {"command": "ls"},
            }
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(llm, "_request_structured_response", _fake_request)

    plan = llm.think(
        "Build a simple two-step shell plan.",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
        planner_mode="hierarchical",
    )

    assert [step["arguments"]["command"] for step in plan["plan"]] == ["pwd", "ls"]


def test_think_hierarchical_mode_uses_adaptive_step_expansion_budget(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    seen_step_budget: list[int] = []

    def _fake_request(*, stage, **kwargs):
        if stage == "workflow_skeleton":
            return {
                "thought_process": "Use a workflow skeleton first.",
                "workflow": [
                    {
                        "tool_name": "bash_run",
                        "objective": f"step {idx}",
                        "step_id": idx,
                        "depends_on": [idx - 1] if idx > 1 else [],
                    }
                    for idx in range(1, 9)
                ],
                "global_constraints": [],
                "final_deliverables": [],
            }
        if stage == "step_expansion":
            seen_step_budget.append(int(kwargs.get("num_predict", 0) or 0))
            normalizer = kwargs.get("normalizer")
            messages = kwargs.get("messages", [])
            rendered = "\n".join(str(getattr(msg, "content", "")) for msg in messages)
            workflow_step_block = rendered.split("Workflow step:\n", 1)[1].split("\n\nUpstream context:", 1)[0]
            step_id = int(workflow_step_block.split('"step_id": ', 1)[1].split(",", 1)[0].strip())
            payload = {
                "step_id": step_id,
                "tool_name": "bash_run",
                "arguments": {"command": f"echo {step_id}"},
            }
            return normalizer(payload) if callable(normalizer) else payload
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(llm, "_request_structured_response", _fake_request)

    plan = llm.think(
        "Create a grounded multi-step plan for a long bacterial evolution workflow.",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
        analysis_spec={
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            "protocol_grounding": {"grounded": True},
        },
        planner_mode="hierarchical",
    )

    assert len(plan["plan"]) == 8
    assert seen_step_budget
    assert min(seen_step_budget) >= 4200


def test_build_workflow_messages_discourages_rule_conflict_rumination(llm):
    messages = llm._build_workflow_messages(
        "Build a benchmark-safe evolution workflow.",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
        {
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            "analysis_type": "bacterial_evolution_variant_calling",
            "plan_skeleton": [("step", "do thing", {})],
        },
        {},
    )

    system_prompt = str(getattr(messages[0], "content", ""))

    assert "Keep `thought_process` to one short sentence." in system_prompt
    assert "Do not narrate rule conflicts" in system_prompt
    assert "prefer the concrete branch-safe workflow" in system_prompt


def test_think_hierarchical_mode_ignores_step_expansion_id_drift(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    def _fake_request(*, stage, **_kwargs):
        normalizer = _kwargs.get("normalizer")
        if stage == "workflow_skeleton":
            payload = {
                "thought_process": "Use the workflow skeleton as the source of truth.",
                "workflow": [
                    {"tool_name": "spades_assemble", "objective": "Assemble ancestor", "step_id": 1, "depends_on": []},
                    {"tool_name": "bwa_mem_align", "objective": "Align evol2", "step_id": 2, "depends_on": [1], "branch_id": "evol2"},
                    {"tool_name": "bash_run", "objective": "Export shared variants", "step_id": 3, "depends_on": [2]},
                ],
                "global_constraints": [],
                "final_deliverables": [],
            }
            return normalizer(payload) if callable(normalizer) else payload
        if stage == "step_expansion":
            messages = _kwargs.get("messages", [])
            rendered = "\n".join(str(getattr(msg, "content", "")) for msg in messages)
            workflow_step_block = rendered.split("Workflow step:\n", 1)[1].split("\n\nUpstream context:", 1)[0]
            if '"tool_name": "spades_assemble"' in workflow_step_block:
                payload = {
                    "step_id": 1,
                    "tool_name": "spades_assemble",
                    "arguments": {"reads_1": "anc_R1.fastq.gz", "reads_2": "anc_R2.fastq.gz", "output_dir": "/tmp/assembly"},
                }
                return normalizer(payload) if callable(normalizer) else payload
            if '"tool_name": "bwa_mem_align"' in workflow_step_block:
                payload = {
                    "step_id": 1,
                    "tool_name": "bwa_mem_align",
                    "arguments": {"output_bam": "/tmp/evol2.bam"},
                }
                return normalizer(payload) if callable(normalizer) else payload
            payload = {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {"command": "python3 export_shared_variants_csv.py"},
            }
            return normalizer(payload) if callable(normalizer) else payload
        raise AssertionError(f"unexpected stage: {stage}")

    monkeypatch.setattr(llm, "_request_structured_response", _fake_request)

    plan = llm.think(
        "Build a compact bacterial evolution export plan.",
        [
            {"name": "spades_assemble", "description": "assemble reads", "parameters": {"reads_1": {}, "reads_2": {}, "output_dir": {}}},
            {"name": "bwa_mem_align", "description": "align reads", "parameters": {"output_bam": {}}},
            {"name": "bash_run", "description": "execute shell command", "parameters": {"command": {}}},
        ],
        planner_mode="hierarchical",
    )

    assert [step["tool_name"] for step in plan["plan"]] == [
        "spades_assemble",
        "bwa_mem_align",
        "bash_run",
    ]
    assert plan["plan"][2]["arguments"]["command"] == "python3 export_shared_variants_csv.py"


def test_normalize_plan_output_preserves_final_deliverables_and_step_contracts(llm):
    normalized = llm._normalize_plan_output(
        {
            "thought_process": "Direct wrapper plan.",
            "final_deliverables": ["/tmp/final/deseq_results.csv"],
            "plan": [
                {
                    "tool_name": "deseq2_run",
                    "arguments": {"output_dir": "/tmp/out"},
                    "step_id": 1,
                    "deliverables": ["/tmp/final/deseq_results.csv"],
                    "expected_files": ["deseq2_results.tsv"],
                    "validation_method": "exists_non_empty",
                }
            ],
        }
    )

    assert normalized["final_deliverables"] == ["/tmp/final/deseq_results.csv"]
    assert normalized["plan"][0]["deliverables"] == ["/tmp/final/deseq_results.csv"]
    assert normalized["plan"][0]["expected_files"] == ["deseq2_results.tsv"]
    assert normalized["plan"][0]["validation_method"] == "exists_non_empty"


def test_think_auto_uses_hierarchical_mode_for_grounded_repair_prompt(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")
    calls = {"hierarchical": 0}

    def _fake_hierarchical(*_args, **_kwargs):
        calls["hierarchical"] += 1
        return {
            "thought_process": "Scoped repair.",
            "plan": [{"tool_name": "bash_run", "arguments": {"command": "pwd"}, "step_id": 1}],
        }

    monkeypatch.setattr(llm, "_think_hierarchical", _fake_hierarchical)

    plan = llm.think(
        "You are repairing an executable bioinformatics plan to satisfy task-local protocol grounding.\nCurrent plan:\n{}",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
        analysis_spec={"protocol_grounding": {"grounded": True}},
    )

    assert calls["hierarchical"] == 1
    assert plan["plan"][0]["tool_name"] == "bash_run"


def test_hierarchical_mode_disabled_for_planning_strict_policy_by_default() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    assert llm._hierarchical_mode_enabled(
        planner_mode="auto",
        user_query="Call germline variants from paired-end reads.",
        analysis_spec={"benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY},
    ) is False


def test_hierarchical_mode_enabled_for_planning_strict_when_forced(monkeypatch) -> None:
    monkeypatch.setenv("BIO_HARNESS_PLANNER_HIERARCHICAL_MODE", "always")
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    assert llm._hierarchical_mode_enabled(
        planner_mode="auto",
        user_query="Call germline variants from paired-end reads.",
        analysis_spec={"benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY},
    ) is True


def test_build_workflow_messages_seed_from_analysis_spec_skeleton() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    messages = llm._build_workflow_messages(
        "Identify shared variants in evolved bacterial isolates relative to an ancestor.",
        [{"name": "spades_assemble"}, {"name": "freebayes_call"}],
        analysis_spec={
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            "plan_skeleton": [
                ("spades_assemble", "Assemble the ancestor genome."),
                ("freebayes_call", "Call variants in the evolved isolate."),
            ],
        },
        seed_plan=None,
    )

    rendered = "\n".join(str(getattr(msg, "content", "")) for msg in messages)
    assert '"tool_name": "spades_assemble"' in rendered
    assert '"tool_name": "freebayes_call"' in rendered


def test_think_direct_plan_prompt_enforces_atomic_bash_and_wrapper_preference(monkeypatch) -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")
    captured: dict[str, str] = {}

    def _fake_request(*, stage, messages, **_kwargs):
        if stage != "direct_plan":
            raise AssertionError(f"unexpected stage: {stage}")
        captured["system_prompt"] = str(messages[0]["content"])
        return {
            "thought_process": "Atomic plan.",
            "plan": [{"tool_name": "bash_run", "arguments": {"command": "pwd"}, "step_id": 1}],
        }

    monkeypatch.setattr(llm, "_should_use_two_stage", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(llm, "_request_structured_response", _fake_request)

    llm.think(
        "Build one shell step.",
        [
            {"name": "bash_run", "description": "execute shell command", "parameters": {}},
            {"name": "bcftools_filter_run", "description": "filter one VCF", "parameters": {}},
            {"name": "bcftools_norm_run", "description": "normalize one VCF", "parameters": {}},
            {"name": "tabix_index_run", "description": "index one bgzipped file", "parameters": {}},
        ],
    )

    system_prompt = captured["system_prompt"]
    assert "Each `bash_run` step must perform exactly one logical operation." in system_prompt
    assert "Prefer typed wrappers over `bash_run` whenever a wrapper exists." in system_prompt
    assert "`bcftools_filter_run`" in system_prompt
    assert "`bcftools_norm_run`" in system_prompt
    assert "`tabix_index_run`" in system_prompt


def test_step_prompt_tool_specific_rules_for_bash_run_forbid_compound_shell() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    rules = llm._step_prompt_tool_specific_rules(
        "bash_run",
        available_skills=[
            {"name": "bcftools_isec_run"},
            {"name": "shared_variants_export_run"},
        ],
    )

    assert "Emit exactly one logical operation" in rules
    assert "Do not use pipes, `&&`, `;`, `||`, loops" in rules
    assert "`bcftools_isec_run`" in rules
    assert "`shared_variants_export_run`" in rules


def test_plan_skeleton_branching_guidance_emits_shared_comparison_note() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    note = llm._plan_skeleton_branching_guidance(
        {
            "plan_skeleton": [
                ("spades_assemble", "Assemble the ancestor genome."),
                ("freebayes_call", "Call variants for each evolved line."),
                ("bash_run", "Export shared variants."),
            ],
            "protocol_grounding": {
                "requires_shared_comparison": True,
                "min_variant_branches": 2,
            },
        }
    )

    assert "logical stage sequence" in note
    assert "one concrete step per branch" in note


def test_think_retries_direct_plan_after_unknown_tool(monkeypatch) -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")
    calls: list[str] = []

    def _fake_request(*, stage, **_kwargs):
        assert stage == "direct_plan"
        calls.append(stage)
        if len(calls) == 1:
            return {
                "thought_process": "Try an execution helper.",
                "plan": [
                    {"tool_name": "execute_bash", "arguments": {"command": "pwd"}, "step_id": 1},
                ],
            }
        return {
            "thought_process": "Use the supported shell tool.",
            "plan": [
                {"tool_name": "bash_run", "arguments": {"command": "pwd"}, "step_id": 1},
            ],
        }

    monkeypatch.setattr(llm, "_should_use_two_stage", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(llm, "_hierarchical_mode_enabled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(llm, "_request_structured_response", _fake_request)

    plan = llm.think(
        "Build a one-step shell plan.",
        [{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
    )

    assert calls == ["direct_plan", "direct_plan"]
    assert plan["plan"][0]["tool_name"] == "bash_run"


def test_build_workflow_messages_compacts_absolute_paths() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    messages = llm._build_workflow_messages(
        "Use /very/long/path/anc_R1.fastq.gz and /very/long/path/anc_R2.fastq.gz to build a workflow.",
        [{"name": "spades_assemble"}],
        analysis_spec={"benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY},
        seed_plan=None,
    )

    rendered = "\n".join(str(getattr(msg, "content", "")) for msg in messages)
    assert "/very/long/path/anc_R1.fastq.gz" not in rendered
    assert "[PATH:anc_R1.fastq.gz]" in rendered


def test_build_workflow_messages_compacts_contract_repair_prompt_for_hierarchical_mode() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    repair_prompt = (
        "You are creating an executable bioinformatics plan.\n"
        "Return ONLY JSON with keys `thought_process` and `plan`.\n\n"
        "Original user request:\n"
        "Identify shared variants in evolved bacterial isolates relative to an ancestor.\n\n"
        "Required contract:\n{}\n\n"
        "Current plan contract gaps:\n"
        "{\"artifact_role_issues\": ["
        "\"bash_run.command:input_in_selected_dir_without_producer:/very/long/path/evol1_subtracted_anc.vcf.gz\""
        "]}\n\n"
        "Rules:\n"
        "- Use concrete file paths and executable tool arguments.\n\n"
        "Current plan summary:\n{}"
    )

    messages = llm._build_workflow_messages(
        repair_prompt,
        [{"name": "spades_assemble"}, {"name": "freebayes_call"}],
        analysis_spec={"benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY},
        seed_plan=None,
    )

    rendered = "\n".join(str(getattr(msg, "content", "")) for msg in messages)
    assert "Identify shared variants in evolved bacterial isolates relative to an ancestor." in rendered
    assert "Workflow repair focus:" in rendered
    assert "Use concrete file paths and executable tool arguments." not in rendered
    assert "Current plan contract gaps:" not in rendered
    assert "/very/long/path/evol1_subtracted_anc.vcf.gz" not in rendered
    assert "[PATH:evol1_subtracted_anc.vcf.gz]" in rendered


def test_build_workflow_messages_compacts_contract_focus_prompt_for_hierarchical_mode() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    repair_prompt = (
        "Create an executable bioinformatics plan as JSON.\n"
        "Output ONLY JSON with keys `thought_process` and `plan`.\n\n"
        "User request:\n"
        "Identify shared variants in evolved bacterial isolates relative to an ancestor.\n\n"
        "Required contract:\n{}\n\n"
        "Latest contract gaps:\n"
        "{\"artifact_role_issues\": ["
        "\"snpeff_annotate.input_vcf:input_in_selected_dir_without_producer:/tmp/evol2_filtered_annotated.vcf.gz\""
        "]}\n\n"
        "Prior plan (if any):\n{}"
    )

    messages = llm._build_workflow_messages(
        repair_prompt,
        [{"name": "spades_assemble"}, {"name": "snpeff_annotate"}],
        analysis_spec={"benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY},
        seed_plan=None,
    )

    rendered = "\n".join(str(getattr(msg, "content", "")) for msg in messages)
    assert "Workflow repair focus:" in rendered
    assert "Latest contract gaps:" not in rendered
    assert "/tmp/evol2_filtered_annotated.vcf.gz" not in rendered
    assert "[PATH:evol2_filtered_annotated.vcf.gz]" in rendered


def test_constrain_step_spec_to_workflow_context_rebinds_branch_specific_bam() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    workflow_spec = {
        "workflow": [
            {
                "step_id": 4,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol1",
                "parameter_hints": {"output_bam": "/tmp/evol1.bam"},
            },
            {
                "step_id": 5,
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "depends_on": [4],
                "parameter_hints": {"output_vcf": "/tmp/evol1.raw.vcf", "ploidy": 1},
            },
        ]
    }
    workflow_step = workflow_spec["workflow"][1]
    raw_step = {
        "step_id": 5,
        "tool_name": "freebayes_call",
        "arguments": {
            "reference_fasta": "/tmp/ref.fa",
            "input_bam": "/tmp/evol2.bam",
            "output_vcf": "/tmp/evol2.raw.vcf",
            "ploidy": 2,
        },
    }

    constrained = llm._constrain_step_spec_to_workflow_context(
        step_spec=raw_step,
        workflow_spec=workflow_spec,
        workflow_step=workflow_step,
    )

    assert constrained["arguments"]["input_bam"] == "/tmp/evol1.bam"
    assert constrained["arguments"]["output_vcf"] == "/tmp/evol1.raw.vcf"
    assert constrained["arguments"]["ploidy"] == 1


def test_constrain_step_spec_to_workflow_context_rebinds_sniffles_to_upstream_bam() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    workflow_spec = {
        "workflow": [
            {
                "step_id": 1,
                "tool_name": "minimap2_align",
                "branch_id": "sample_a",
                "parameter_hints": {"output_bam": "/tmp/sample_a.aligned.bam"},
            },
            {
                "step_id": 2,
                "tool_name": "sniffles_sv_call",
                "branch_id": "sample_a",
                "depends_on": [1],
                "parameter_hints": {"output_vcf": "/tmp/sample_a.variants.vcf"},
            },
        ]
    }
    workflow_step = workflow_spec["workflow"][1]
    raw_step = {
        "step_id": 2,
        "tool_name": "sniffles_sv_call",
        "arguments": {
            "reference_fasta": "/tmp/ref.fa",
            "input_bam": "/tmp/reads.fastq",
            "output_vcf": "/tmp/wrong.vcf",
        },
    }

    constrained = llm._constrain_step_spec_to_workflow_context(
        step_spec=raw_step,
        workflow_spec=workflow_spec,
        workflow_step=workflow_step,
    )

    assert constrained["arguments"]["input_bam"] == "/tmp/sample_a.aligned.bam"
    assert constrained["arguments"]["output_vcf"] == "/tmp/sample_a.variants.vcf"


def test_constrain_step_spec_to_workflow_context_preserves_bam_without_dependencies() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    workflow_spec = {
        "workflow": [
            {
                "step_id": 1,
                "tool_name": "sniffles_sv_call",
                "parameter_hints": {"output_vcf": "/tmp/sample.variants.vcf"},
            }
        ]
    }
    workflow_step = workflow_spec["workflow"][0]
    raw_step = {
        "step_id": 1,
        "tool_name": "sniffles_sv_call",
        "arguments": {
            "reference_fasta": "/tmp/ref.fa",
            "input_bam": "/tmp/sample.aligned.bam",
            "output_vcf": "/tmp/wrong.vcf",
        },
    }

    constrained = llm._constrain_step_spec_to_workflow_context(
        step_spec=raw_step,
        workflow_spec=workflow_spec,
        workflow_step=workflow_step,
    )

    assert constrained["arguments"]["input_bam"] == "/tmp/sample.aligned.bam"
    assert constrained["arguments"]["output_vcf"] == "/tmp/sample.variants.vcf"


def test_constrain_step_spec_to_workflow_context_rebinds_input_bams_from_dependencies() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    workflow_spec = {
        "workflow": [
            {
                "step_id": 1,
                "tool_name": "subread_align",
                "parameter_hints": {"output_bam": "/tmp/group_a.bam"},
            },
            {
                "step_id": 2,
                "tool_name": "subread_align",
                "parameter_hints": {"output_bam": "/tmp/group_b.bam"},
            },
            {
                "step_id": 3,
                "tool_name": "featurecounts_run",
                "depends_on": [1, 2],
                "parameter_hints": {"output_counts": "/tmp/counts.tsv"},
            },
        ]
    }
    workflow_step = workflow_spec["workflow"][2]
    raw_step = {
        "step_id": 3,
        "tool_name": "featurecounts_run",
        "arguments": {
            "annotation_gtf": "/tmp/genes.gtf",
            "input_bams": ["/tmp/stale.bam"],
            "output_counts": "/tmp/wrong.tsv",
        },
    }

    constrained = llm._constrain_step_spec_to_workflow_context(
        step_spec=raw_step,
        workflow_spec=workflow_spec,
        workflow_step=workflow_step,
    )

    assert constrained["arguments"]["input_bams"] == ["/tmp/group_a.bam", "/tmp/group_b.bam"]


def test_constrain_step_spec_to_workflow_context_rewrites_evolution_tail_paths() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    selected_dir = "/tmp/official_runs/evolution/attempt1"
    workflow_step = {
        "step_id": 9,
        "tool_name": "bash_run",
        "branch_id": "evol1_subtract",
        "objective": "Subtract the ancestor-supported sites from evolved line 1 callset",
        "parameter_hints": {"action": "bcftools isec -C -w1"},
    }
    step_spec = {
        "step_id": 9,
        "tool_name": "bash_run",
        "arguments": {
            "command": (
                f"bcftools isec -C -w1 -p {selected_dir}/tmp "
                f"{selected_dir}/evol1_filtered.vcf.gz {selected_dir}/anc_filtered.vcf.gz"
            )
        },
    }

    constrained = llm._constrain_step_spec_to_workflow_context(
        step_spec=step_spec,
        workflow_spec={"workflow": [workflow_step]},
        workflow_step=workflow_step,
        analysis_spec={
            "analysis_type": "bacterial_evolution_variant_calling",
            "selected_dir": selected_dir,
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        },
    )

    command = constrained["arguments"]["command"]
    resolved_dir = str(Path(selected_dir).resolve(strict=False))
    assert f"{resolved_dir}/variants/evol1.ancestor_subtracted.vcf.gz" in command
    assert f"{resolved_dir}/variants/evol1.filtered.vcf.gz" in command
    assert f"{resolved_dir}/variants/anc.filtered.vcf.gz" in command


def test_constrain_step_spec_to_workflow_context_preserves_authoritative_bash_command_hint() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    workflow_step = {
        "step_id": 10,
        "tool_name": "bash_run",
        "branch_id": "evol1",
        "parameter_hints": {
            "command": (
                "bcftools isec -C -n=2 -w1 -Oz -o evol1_subtracted_anc.vcf.gz "
                "evol1_freebayes.vcf ancestor_freebayes.filtered.vcf.gz && "
                "bcftools index evol1_subtracted_anc.vcf.gz"
            )
        },
    }
    step_spec = {
        "step_id": 10,
        "tool_name": "bash_run",
        "arguments": {
            "command": (
                "bcftools isec -n=+2 -w1 filtered_evol1_snps_indels.vcf.gz "
                "filtered_anc_snps_indels.vcf.gz -p isec_evol1_minus_anc"
            )
        },
    }

    constrained = llm._constrain_step_spec_to_workflow_context(
        step_spec=step_spec,
        workflow_spec={"workflow": [workflow_step]},
        workflow_step=workflow_step,
    )

    assert constrained["arguments"]["command"] == workflow_step["parameter_hints"]["command"]


def test_constrain_step_spec_to_workflow_context_preserves_authoritative_snpeff_path_hints() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    workflow_step = {
        "step_id": 12,
        "tool_name": "snpeff_annotate",
        "branch_id": "evol1",
        "parameter_hints": {
            "input_vcf": "evol1_subtracted_anc.vcf.gz",
            "output_vcf": "evol1_annotated.vcf",
            "annotation_gff": "prodigal_ancestor.gff",
            "genome_db": "ecoli",
        },
    }
    step_spec = {
        "step_id": 12,
        "tool_name": "snpeff_annotate",
        "arguments": {
            "input_vcf": "isec_evol1_minus_anc/0000.vcf",
            "output_vcf": "evol1.annotated.filtered.vcf.gz",
            "annotation_gff": "ancestor_assembly.gff",
            "genome_db": "ecoli_custom",
            "reference_fasta": "/tmp/ancestor.fa",
        },
    }

    constrained = llm._constrain_step_spec_to_workflow_context(
        step_spec=step_spec,
        workflow_spec={"workflow": [workflow_step]},
        workflow_step=workflow_step,
    )

    assert constrained["arguments"]["input_vcf"] == "evol1_subtracted_anc.vcf.gz"
    assert constrained["arguments"]["output_vcf"] == "evol1_annotated.vcf"
    assert constrained["arguments"]["annotation_gff"] == "prodigal_ancestor.gff"
    assert constrained["arguments"]["genome_db"] == "ecoli"
    assert constrained["arguments"]["reference_fasta"] == "/tmp/ancestor.fa"


def test_constrain_step_spec_to_workflow_context_rebinds_branch_local_evolution_annotation_paths() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    workflow_step = {
        "step_id": 12,
        "tool_name": "snpeff_annotate",
        "branch_id": "evol2",
        "parameter_hints": {},
    }
    step_spec = {
        "step_id": 12,
        "tool_name": "snpeff_annotate",
        "arguments": {
            "input_vcf": "evol1_subtracted_anc.vcf.gz",
            "output_vcf": "evol1_annotated.vcf",
            "annotation_gff": "ancestor.gff",
            "reference_fasta": "ancestor.fa",
            "genome_db": "ecoli_custom",
        },
    }

    constrained = llm._constrain_step_spec_to_workflow_context(
        step_spec=step_spec,
        workflow_spec={"workflow": [workflow_step]},
        workflow_step=workflow_step,
    )

    assert constrained["arguments"]["input_vcf"] == "evol2_subtracted_anc.vcf.gz"
    assert constrained["arguments"]["output_vcf"] == "evol2_annotated.vcf"
    assert constrained["arguments"]["annotation_gff"] == "ancestor.gff"
    assert constrained["arguments"]["reference_fasta"] == "ancestor.fa"


def test_workflow_seed_from_analysis_spec_preserves_skeleton_metadata() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    seed = llm._workflow_seed_from_analysis_spec(
        {
            "plan_skeleton": [
                (
                    "bash_run",
                    "Subtract the ancestor-supported sites from each evolved callset separately before any evolved-evolved comparison",
                    {
                        "parameter_hints": {"action": "bcftools isec -C -w1"},
                        "downstream_constraints": [
                            "Materialize concrete minus-ancestor outputs before annotation."
                        ],
                    },
                )
            ]
        }
    )

    assert seed["workflow"][0]["parameter_hints"] == {"action": "bcftools isec -C -w1"}
    assert seed["workflow"][0]["downstream_constraints"] == [
        "Materialize concrete minus-ancestor outputs before annotation."
    ]


def test_build_step_messages_adds_concise_bash_run_rules() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    messages = llm._build_step_messages(
        user_query="Export shared variants to CSV.",
        workflow_spec={"workflow": []},
        workflow_step={
            "step_id": 16,
            "tool_name": "bash_run",
            "objective": "Normalize annotated VCFs and export shared variants.",
            "depends_on": [],
        },
        available_skills=[{"name": "bash_run", "description": "execute shell command", "parameters": {}}],
    )

    system_prompt = messages[0].content
    assert "TOOL-SPECIFIC RULES FOR `bash_run`" in system_prompt
    assert "Prefer checked-in helper scripts or single CLI invocations over inline Python, R, awk, or heredoc programs." in system_prompt
    assert "Do not embed long comment blocks, copied field catalogs, or explanatory prose inside the command." in system_prompt


def test_build_step_messages_keeps_bash_specific_rules_off_typed_wrappers() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    messages = llm._build_step_messages(
        user_query="Annotate variants.",
        workflow_spec={"workflow": []},
        workflow_step={
            "step_id": 11,
            "tool_name": "snpeff_annotate",
            "objective": "Annotate variants with snpEff.",
            "depends_on": [],
        },
        available_skills=[{"name": "snpeff_annotate", "description": "annotate VCF", "parameters": {}}],
    )

    system_prompt = messages[0].content
    assert "TOOL-SPECIFIC RULES FOR `bash_run`" not in system_prompt
    assert "Resolve local inconsistencies silently and emit the best executable step JSON directly." in system_prompt
    assert "Do not wrap the answer in markdown fences, repeated drafts, or example JSON blocks." in system_prompt


def test_constrain_step_spec_to_workflow_context_rewrites_evolution_call_and_annotation_paths() -> None:
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    selected_dir = "/tmp/official_runs/evolution/attempt1"
    resolved_dir = str(Path(selected_dir).resolve(strict=False))
    call_workflow_step = {
        "step_id": 5,
        "branch_id": "evol1",
        "objective": "Call haploid evolved-line variants with quality-aware filtering",
    }
    freebayes_step = {
        "step_id": 5,
        "tool_name": "freebayes_call",
        "arguments": {
            "reference_fasta": f"{selected_dir}/assemblies/anc_contigs.fasta",
            "input_bam": f"{selected_dir}/alignments/wrong.bam",
            "output_vcf": f"{selected_dir}/calls/evol1_raw.vcf",
        },
    }

    constrained_call = llm._constrain_step_spec_to_workflow_context(
        step_spec=freebayes_step,
        workflow_spec={"workflow": [call_workflow_step]},
        workflow_step=call_workflow_step,
        analysis_spec={
            "analysis_type": "bacterial_evolution_variant_calling",
            "selected_dir": selected_dir,
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        },
    )

    assert constrained_call["arguments"]["reference_fasta"] == f"{resolved_dir}/assembly/scaffolds.fasta"
    assert constrained_call["arguments"]["input_bam"] == f"{resolved_dir}/alignments/evol1_aligned.bam"
    assert constrained_call["arguments"]["output_vcf"] == f"{resolved_dir}/variants/evol1_raw.vcf"

    annotate_workflow_step = {
        "step_id": 11,
        "branch_id": "evol1",
        "objective": "Annotate the ancestor-subtracted evolved variants with ANN-compatible fields",
    }
    annotate_step = {
        "step_id": 11,
        "tool_name": "snpeff_annotate",
        "arguments": {
            "reference_fasta": f"{selected_dir}/assembly/ancestor/scaffolds.fasta",
            "annotation_gff": f"{selected_dir}/assembly/ancestor/genes.gff",
            "input_vcf": f"{selected_dir}/variants/evol1.ancestor_subtracted.vcf.gz",
            "output_vcf": f"{selected_dir}/variants/evol1.annotated.vcf",
        },
    }

    constrained_annotate = llm._constrain_step_spec_to_workflow_context(
        step_spec=annotate_step,
        workflow_spec={"workflow": [annotate_workflow_step]},
        workflow_step=annotate_workflow_step,
        analysis_spec={
            "analysis_type": "bacterial_evolution_variant_calling",
            "selected_dir": selected_dir,
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        },
    )

    assert constrained_annotate["arguments"]["reference_fasta"] == f"{resolved_dir}/assembly/scaffolds.fasta"
    assert constrained_annotate["arguments"]["annotation_gff"] == f"{resolved_dir}/annotation/genes.gff"
    assert constrained_annotate["arguments"]["config_dir"] == f"{resolved_dir}/annotation/_snpeff"


def test_direct_plan_predict_budget_increases_for_planning_strict_long_skeleton(llm):
    budget = llm._direct_plan_predict_budget(
        attempt_idx=0,
        user_query="Run a long strict evolution benchmark plan. " * 40,
        available_skills=[{"name": "bash_run"}] * 8,
        analysis_spec={
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            "analysis_type": "bacterial_evolution_variant_calling",
            "plan_skeleton": [("a", "b")] * 8,
        },
    )

    assert budget >= 5200


def test_workflow_skeleton_predict_budget_stays_compact_for_initial_request(llm):
    budget = llm._workflow_skeleton_predict_budget(
        user_query="Run a long strict evolution benchmark plan. " * 40,
    )

    assert budget <= 1400


def test_repair_predict_budget_increases_for_truncated_direct_plan(llm):
    budget = llm._repair_predict_budget(
        stage="direct_plan",
        raw_content='{"plan": [' + ("x" * 9000),
        failure_message="Invalid JSON received: Expecting ',' delimiter: line 109 column 6 (char 10083)",
    )

    assert budget >= 3600


def test_request_structured_response_writes_trace_files(monkeypatch, tmp_path):
    llm = BioLLM(
        model_name="qwen3-coder-next",
        host="http://127.0.0.1:11434",
        planner_trace_dir=tmp_path,
        planner_trace_context={"run_id": "test-run", "strategy": "unit"},
    )

    def _fake_chat_json_raw(*_args, **_kwargs):
        return {
            "raw_content": json.dumps(
                {
                    "thought_process": "Short.",
                    "plan": [{"tool_name": "bash_run", "arguments": {"command": "pwd"}, "step_id": 1}],
                }
            ),
            "transport": "unit_test",
            "num_ctx": 8192,
            "num_predict": 512,
        }

    monkeypatch.setattr(llm, "_chat_json_raw", _fake_chat_json_raw)

    payload = llm._request_structured_response(
        stage="direct_plan",
        schema_model=LLMOutputSchema,
        messages=[],
        num_predict=512,
        normalizer=lambda x: x,
        repair_allowed=False,
    )

    assert payload["plan"][0]["tool_name"] == "bash_run"
    trace_files = sorted(tmp_path.glob("*.json"))
    assert trace_files
    joined = "\n".join(path.read_text(encoding="utf-8") for path in trace_files)
    assert "RAW_RESPONSE" in joined
    assert "STRUCTURED_SUCCESS" in joined


def test_request_structured_response_propagates_supervisor_timeout_during_repair(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    calls = {"count": 0}

    def _fake_chat_json_raw(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "raw_content": '{"plan": [',
                "transport": "unit_test",
                "num_ctx": 8192,
                "num_predict": 512,
            }
        raise TimeoutError("Planner attempt timed out at supervisor wall-clock limit (105s). Falling back to recovery strategy.")

    monkeypatch.setattr(llm, "_chat_json_raw", _fake_chat_json_raw)

    with pytest.raises(TimeoutError, match="supervisor wall-clock limit"):
        llm._request_structured_response(
            stage="direct_plan",
            schema_model=LLMOutputSchema,
            messages=[],
            num_predict=512,
            normalizer=lambda x: x,
            repair_allowed=True,
        )


def test_request_structured_response_salvages_fenced_bash_step_expansion(monkeypatch):
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")

    def _fake_chat_json_raw(*_args, **_kwargs):
        return {
            "raw_content": (
                "Here is the final export step.\n\n"
                "```bash\n"
                "set -euo pipefail\n"
                "python3 export_shared_variants_csv.py --help\n"
                "```"
            ),
            "transport": "unit_test",
            "num_ctx": 8192,
            "num_predict": 512,
        }

    monkeypatch.setattr(llm, "_chat_json_raw", _fake_chat_json_raw)

    payload = llm._request_structured_response(
        stage="step_expansion",
        schema_model=StepExecutionSpecSchema,
        messages=[],
        num_predict=512,
        normalizer=lambda raw: llm._normalize_step_output(raw, step_id=13, tool_name="bash_run"),
        repair_allowed=False,
    )

    assert payload["step_id"] == 13
    assert payload["tool_name"] == "bash_run"
    assert payload["arguments"]["command"] == "set -euo pipefail\npython3 export_shared_variants_csv.py --help"


# ===========================================================================
# Pure-function / string-processing tests (no Ollama required)
# ===========================================================================


# ---------------------------------------------------------------------------
# _strip_code_fences (static method)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('```json\n{"plan": []}\n```', '{"plan": []}'),
        ('```\nhello\nworld\n```', "hello\nworld"),
        ('{"plan": []}', '{"plan": []}'),
        ("", ""),
        ("```\nsingle line\n```", "single line"),
        ("no fences at all", "no fences at all"),
    ],
    ids=["json_fence", "plain_fence", "no_fence", "empty", "minimal_fence", "text_only"],
)
def test_strip_code_fences(raw: str, expected: str):
    assert BioLLM._strip_code_fences(raw) == expected


# ---------------------------------------------------------------------------
# _extract_json_candidate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected_start",
    [
        ('{"plan": []}', '{"plan": []}'),
        ('```json\n{"plan": []}\n```', '{"plan": []}'),
        ('Some text before {"plan": []} some after', '{"plan": []}'),
        ("", ""),
        ("no json here", "no json here"),
    ],
    ids=["clean_json", "fenced_json", "embedded_json", "empty", "no_json"],
)
def test_extract_json_candidate(llm, raw: str, expected_start: str):
    result = llm._extract_json_candidate(raw)
    assert result.startswith(expected_start[:10]) or result == expected_start


def test_extract_json_candidate_extracts_braces(llm):
    raw = 'Here is the plan: {"tool": "bash_run", "args": {}} end.'
    result = llm._extract_json_candidate(raw)
    assert result.startswith("{")
    assert result.endswith("}")
    parsed = json.loads(result)
    assert parsed["tool"] == "bash_run"


# ---------------------------------------------------------------------------
# _should_use_two_stage
# ---------------------------------------------------------------------------


def test_should_use_two_stage_off(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TWO_STAGE_MODE", "off")
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")
    assert llm._should_use_two_stage("long query " * 100, [{}] * 20) is False


def test_should_use_two_stage_always(monkeypatch):
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TWO_STAGE_MODE", "always")
    llm = BioLLM(model_name="qwen3-coder-next", host="http://127.0.0.1:11434")
    assert llm._should_use_two_stage("short", []) is True


def test_should_use_two_stage_auto_short_query(llm):
    # Short query + few skills -> single stage
    assert llm._should_use_two_stage("align reads", [{"name": "bash_run"}]) is False


def test_should_use_two_stage_auto_long_query(llm):
    # Long query -> two stage
    long_query = "x" * 300
    assert llm._should_use_two_stage(long_query, [{"name": "bash_run"}]) is True


def test_should_use_two_stage_auto_many_skills(llm):
    # Many skills -> two stage
    skills = [{"name": f"tool_{i}"} for i in range(10)]
    assert llm._should_use_two_stage("short query", skills) is True


# ---------------------------------------------------------------------------
# _normalize_plan_output
# ---------------------------------------------------------------------------


def test_normalize_plan_output_valid(llm):
    raw = {
        "thought_process": "Align reads.",
        "plan": [
            {"tool_name": "star_align", "arguments": {"reads_1": "r1.fq"}, "step_id": 1},
        ],
    }
    result = llm._normalize_plan_output(raw)
    assert result["thought_process"] == "Align reads."
    assert len(result["plan"]) == 1
    assert result["plan"][0]["tool_name"] == "star_align"
    assert result["plan"][0]["step_id"] == 1


def test_normalize_plan_output_missing_thought_process(llm):
    raw = {
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "ls"}, "step_id": 1},
        ],
    }
    result = llm._normalize_plan_output(raw)
    assert "No thought process" in result["thought_process"]


def test_normalize_plan_output_non_dict_input(llm):
    result = llm._normalize_plan_output("not a dict")
    assert result["plan"] == []
    assert "No thought process" in result["thought_process"]


def test_normalize_plan_output_step_id_in_arguments(llm):
    # LLM sometimes puts step_id inside arguments
    raw = {
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "ls", "step_id": 5}},
        ],
    }
    result = llm._normalize_plan_output(raw)
    assert result["plan"][0]["step_id"] == 5
    assert "step_id" not in result["plan"][0]["arguments"]


def test_normalize_plan_output_missing_tool_name(llm):
    raw = {
        "plan": [
            {"arguments": {"command": "ls"}, "step_id": 1},
        ],
    }
    result = llm._normalize_plan_output(raw)
    # Should default to bash_run when command key is present
    assert result["plan"][0]["tool_name"] == "bash_run"


def test_normalize_plan_output_skips_non_dict_steps(llm):
    raw = {
        "plan": [
            "not a step",
            {"tool_name": "bash_run", "arguments": {"command": "ls"}, "step_id": 1},
            42,
        ],
    }
    result = llm._normalize_plan_output(raw)
    assert len(result["plan"]) == 1


def test_normalize_plan_output_non_dict_arguments(llm):
    raw = {
        "plan": [
            {"tool_name": "bash_run", "arguments": "ls -la", "step_id": 1},
        ],
    }
    result = llm._normalize_plan_output(raw)
    assert result["plan"][0]["arguments"] == {}


# ---------------------------------------------------------------------------
# _normalize_outline_output
# ---------------------------------------------------------------------------


def test_normalize_outline_output_valid(llm):
    raw = {
        "thought_process": "Plan outline.",
        "plan_outline": [
            {"tool_name": "star_align", "objective": "Align reads", "step_id": 1},
            {"tool_name": "featurecounts_run", "objective": "Count features", "step_id": 2},
        ],
    }
    result = llm._normalize_outline_output(raw)
    assert len(result["plan_outline"]) == 2
    assert result["plan_outline"][0]["tool_name"] == "star_align"


def test_normalize_outline_output_missing_tool_name_skipped(llm):
    raw = {
        "plan_outline": [
            {"objective": "Do something", "step_id": 1},  # no tool_name
            {"tool_name": "bash_run", "objective": "Run cmd", "step_id": 2},
        ],
    }
    result = llm._normalize_outline_output(raw)
    assert len(result["plan_outline"]) == 1


def test_normalize_outline_output_non_dict(llm):
    result = llm._normalize_outline_output("bad input")
    assert result["plan_outline"] == []


def test_normalize_outline_output_default_objective(llm):
    raw = {
        "plan_outline": [
            {"tool_name": "bash_run", "step_id": 1},  # no objective
        ],
    }
    result = llm._normalize_outline_output(raw)
    assert "Execute the required" in result["plan_outline"][0]["objective"]


# ---------------------------------------------------------------------------
# _format_skills_for_prompt_compact
# ---------------------------------------------------------------------------


def test_format_skills_compact(llm):
    skills = [
        {
            "name": "star_align",
            "description": "Align reads using STAR aligner",
            "parameters": {
                "reads_1": {"type": "string", "description": "R1 FASTQ", "required": True},
                "output_prefix": {"type": "string", "description": "Output prefix", "required": False},
            },
        },
    ]
    result = llm._format_skills_for_prompt_compact(skills)
    assert "star_align" in result
    assert "required_args=[reads_1]" in result
    assert "optional_args=[output_prefix]" in result


def test_format_skills_compact_truncates_long_description(llm):
    skills = [
        {
            "name": "tool_x",
            "description": "A" * 500,
            "parameters": {},
        },
    ]
    result = llm._format_skills_for_prompt_compact(skills)
    assert "..." in result


# ---------------------------------------------------------------------------
# _format_skills_for_prompt_full
# ---------------------------------------------------------------------------


def test_format_skills_full(llm):
    skills = [
        {
            "name": "bash_run",
            "description": "Execute a shell command",
            "parameters": {
                "command": {"type": "string", "description": "Shell command", "required": True},
            },
        },
    ]
    result = llm._format_skills_for_prompt_full(skills)
    assert "### bash_run" in result
    assert "Execute a shell command" in result
    assert "(required)" in result


def test_format_skills_full_no_params(llm):
    skills = [
        {
            "name": "simple_tool",
            "description": "Does something",
            "parameters": {},
        },
    ]
    result = llm._format_skills_for_prompt_full(skills)
    assert "- None" in result
