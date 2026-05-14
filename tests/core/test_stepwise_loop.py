"""Focused tests for the stepwise harness execution mode."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import scripts.run_agent_e2e_stepwise_loop as stepwise_loop_module

from bio_harness.core.executor_runtime import finish_executor_runtime
from bio_harness.core.file_manifest import FileManifest
from bio_harness.harness.config import HarnessConfig
from scripts.run_agent_e2e_harness import AgentE2EHarness


def _build_harness(tmp_path: Path) -> AgentE2EHarness:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="Assemble a stepwise test workflow.",
        selected_dir=selected_dir,
        data_root=data_root,
        workspace_root=selected_dir,
        max_repairs=0,
        heartbeat_seconds=15,
        stall_timeout_seconds=45,
        live_process_grace_seconds=900,
        model_name=None,
        host=None,
        llm_backend=None,
        auto_install_missing_tools=False,
        allow_replan=True,
        allow_canonicalize=True,
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
        execution_mode="stepwise",
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    return harness


def _stage_rna_seq_de_inputs(harness: AgentE2EHarness) -> None:
    """Stage tiny RNA-seq DE inputs and strict analysis metadata for tests."""

    selected_dir = harness.cfg.selected_dir
    data_root = selected_dir.parent / "inputs_readonly"
    harness.cfg.data_root = data_root
    references_dir = data_root.parent / "references"
    data_root.mkdir(parents=True, exist_ok=True)
    references_dir.mkdir(parents=True, exist_ok=True)
    (references_dir / "C_parapsilosis_CDC317_current_chromosomes.fasta").write_text(
        ">chr1\nACGTACGTACGT\n",
        encoding="utf-8",
    )
    (references_dir / "C_parapsilosis_CDC317_current_features.gff").write_text(
        "chr1\tsrc\tgene\t1\t12\t.\t+\t.\tID=gene1\n",
        encoding="utf-8",
    )
    (data_root / "sample_metadata.tsv").write_text(
        "sample\tcondition\n"
        "plankton1\tPlankton\n"
        "biofilm1\tBiofilm\n",
        encoding="utf-8",
    )
    for sample_id in ("plankton1", "biofilm1"):
        (data_root / f"{sample_id}_1.fastq").write_text("@r1\nACGTA\n+\n!!!!!\n", encoding="utf-8")
        (data_root / f"{sample_id}_2.fastq").write_text("@r2\nTACGT\n+\n!!!!!\n", encoding="utf-8")
    harness.run["analysis_spec"] = {
        "analysis_type": "rna_seq_differential_expression",
        "selected_dir": str(selected_dir),
        "requested_data_root": str(data_root),
    }


def _stage_transcript_quant_inputs(harness: AgentE2EHarness) -> None:
    """Stage tiny transcript-quant inputs and strict analysis metadata."""

    selected_dir = harness.cfg.selected_dir
    data_root = harness.cfg.data_root
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "transcriptome.fa").write_text(
        ">tx1\nACGTACGTACGTACGT\n",
        encoding="utf-8",
    )
    (data_root / "reads_1.fq.gz").write_text(
        "@r1\nACGTACGT\n+\n!!!!!!!!\n",
        encoding="utf-8",
    )
    (data_root / "reads_2.fq.gz").write_text(
        "@r2\nACGTACGT\n+\n!!!!!!!!\n",
        encoding="utf-8",
    )
    harness.run["analysis_spec"] = {
        "analysis_type": "transcript_quantification",
        "benchmark_policy": "scientific_harness",
        "preferred_tools": ["salmon_quant"],
        "protocol_grounding": {
            "grounded": True,
            "analysis_family": "transcript_quantification",
            "execution_mode": "direct_wrapper",
            "required_tools": ["salmon_quant"],
        },
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": [],
        },
        "plan_skeleton": [
            ["salmon_quant", "Quantify transcripts with Salmon"],
        ],
        "selected_dir": str(selected_dir),
        "requested_data_root": str(data_root),
    }


def _set_evolution_prefix_after_evol1_norm(harness: AgentE2EHarness) -> None:
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "protocol_grounding": {
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
        "parameter_profile": [
            {
                "tool_name": "snpeff_annotate",
                "settings": {"annotation_field": "ANN"},
            }
        ],
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bwa_mem_align",
                "branch_id": "ancestor",
                "arguments": {"output_bam": str(selected_dir / "alignments/anc_aligned.bam")},
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "branch_id": "ancestor",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments/anc_aligned.bam"),
                    "output_vcf": str(selected_dir / "variants/anc_raw.vcf"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "bcftools_filter_run",
                "branch_id": "ancestor",
                "arguments": {"output_vcf": str(selected_dir / "variants/anc.filtered.vcf.gz")},
            },
            {
                "step_id": 4,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol1",
                "arguments": {"output_bam": str(selected_dir / "alignments/evol1_aligned.bam")},
            },
            {
                "step_id": 5,
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments/evol1_aligned.bam"),
                    "output_vcf": str(selected_dir / "variants/evol1_raw.vcf"),
                },
            },
            {
                "step_id": 6,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol1",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol1.filtered.vcf.gz")},
            },
            {
                "step_id": 7,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol2",
                "arguments": {"output_bam": str(selected_dir / "alignments/evol2_aligned.bam")},
            },
            {
                "step_id": 8,
                "tool_name": "freebayes_call",
                "branch_id": "evol2",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments/evol2_aligned.bam"),
                    "output_vcf": str(selected_dir / "variants/evol2_raw.vcf"),
                },
            },
            {
                "step_id": 9,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol2",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol2.filtered.vcf.gz")},
            },
            {
                "step_id": 10,
                "tool_name": "bcftools_isec_run",
                "branch_id": "evol1",
                "arguments": {
                    "output_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz")
                },
            },
            {
                "step_id": 11,
                "tool_name": "bcftools_isec_run",
                "branch_id": "evol2",
                "arguments": {
                    "output_vcf": str(selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz")
                },
            },
            {
                "step_id": 12,
                "tool_name": "snpeff_annotate",
                "branch_id": "evol1",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol1.annotated.vcf")},
            },
            {
                "step_id": 13,
                "tool_name": "snpeff_annotate",
                "branch_id": "evol2",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol2.annotated.vcf")},
            },
            {
                "step_id": 14,
                "tool_name": "bcftools_norm_run",
                "branch_id": "evol1",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
                    "output_vcf": str(selected_dir / "variants/evol1.normalized.vcf"),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"] * 14


def _set_evolution_prefix_after_evol1_isec(harness: AgentE2EHarness) -> None:
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "protocol_grounding": {
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bwa_mem_align",
                "branch_id": "ancestor",
                "arguments": {"output_bam": str(selected_dir / "alignments/anc_aligned.bam")},
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "branch_id": "ancestor",
                "arguments": {"output_vcf": str(selected_dir / "variants/anc_raw.vcf")},
            },
            {
                "step_id": 3,
                "tool_name": "bcftools_filter_run",
                "branch_id": "ancestor",
                "arguments": {"output_vcf": str(selected_dir / "variants/anc.filtered.vcf.gz")},
            },
            {
                "step_id": 4,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol1",
                "arguments": {"output_bam": str(selected_dir / "alignments/evol1_aligned.bam")},
            },
            {
                "step_id": 5,
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol1_raw.vcf")},
            },
            {
                "step_id": 6,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol1",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol1.filtered.vcf.gz")},
            },
            {
                "step_id": 7,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol2",
                "arguments": {"output_bam": str(selected_dir / "alignments/evol2_aligned.bam")},
            },
            {
                "step_id": 8,
                "tool_name": "freebayes_call",
                "branch_id": "evol2",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol2_raw.vcf")},
            },
            {
                "step_id": 9,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol2",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol2.filtered.vcf.gz")},
            },
            {
                "step_id": 10,
                "tool_name": "bcftools_isec_run",
                "branch_id": "evol1",
                "arguments": {
                    "output_vcf": str(
                        selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"
                    )
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"] * 10


def _set_evolution_prefix_after_both_isec_without_annotation(
    harness: AgentE2EHarness,
) -> None:
    _set_evolution_prefix_after_evol1_isec(harness)
    selected_dir = harness.cfg.selected_dir
    harness.run["plan"]["plan"].append(
        {
            "step_id": 11,
            "tool_name": "bcftools_isec_run",
            "branch_id": "evol2",
            "arguments": {
                "input_vcfs": [
                    str(selected_dir / "variants/evol2.filtered.vcf.gz"),
                    str(selected_dir / "variants/anc.filtered.vcf.gz"),
                ],
                "output_dir": str(
                    selected_dir / "variants/.isec_evol2.ancestor_subtracted"
                ),
                "output_vcf": str(
                    selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
                ),
            },
        }
    )
    harness.run["step_statuses"].append("completed")


def _allow_synthetic_stepwise_candidate(
    harness: AgentE2EHarness,
    monkeypatch,
) -> None:
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": [],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_args, **_kwargs: [])
    harness._assess_stepwise_protocol_candidate = lambda **_kwargs: {"passed": True}  # type: ignore[method-assign]
    harness._stepwise_required_arg_rejection_reason = lambda **_kwargs: ""  # type: ignore[method-assign]
    harness._stepwise_missing_candidate_inputs = lambda **_kwargs: []  # type: ignore[method-assign]


def test_stepwise_candidate_rejects_mutated_completed_prefix(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {"command": "echo already-ran"},
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]
    harness.run["analysis_spec"] = {}

    def _mutating_normalizer(_plan: dict[str, object]) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        return (
            {
                "thought_process": "",
                "plan": [
                    {
                        "tool_name": "bash_run",
                        "step_id": 1,
                        "arguments": {"command": "echo mutated-prefix"},
                    },
                    {
                        "tool_name": "bash_run",
                        "step_id": 2,
                        "arguments": {"command": "echo next"},
                    },
                ],
            },
            {},
            {},
        )

    harness._normalize_plan_for_execution = _mutating_normalizer  # type: ignore[method-assign]

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "",
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {"command": "echo next"},
                }
            ],
        },
    )

    assert accepted is False
    assert "already executed plan prefix" in reason


def test_stepwise_prompt_includes_analysis_seed_and_recent_step_details(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "chosen_method": "freebayes_call",
        "preferred_tools": [
            "freebayes_call",
            "bcftools_filter_run",
            "bcftools_isec_run",
            "shared_variants_export_run",
        ],
        "plan_skeleton": [
            ("freebayes_call", "Call variants"),
            ("bcftools_filter_run", "Filter one branch-local VCF"),
            ("bcftools_isec_run", "Subtract one branch from another"),
            ("shared_variants_export_run", "Export the final shared CSV"),
        ],
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 1,
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "output_vcf": "/tmp/sample_raw.vcf",
                    "output_vcf_gz": "/tmp/sample_variants.vcf.gz",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]
    available_skills = [
        {"name": "freebayes_call", "description": "Variant caller."},
        {"name": "bcftools_filter_run", "description": "Atomic filter wrapper."},
        {"name": "bcftools_isec_run", "description": "Atomic subtract wrapper."},
        {"name": "shared_variants_export_run", "description": "Shared export wrapper."},
        {"name": "tabix_index_run", "description": "Atomic tabix wrapper."},
        {"name": "bash_run", "description": "Shell fallback."},
    ]
    recommended_skills = [
        {"name": "freebayes_call", "description": "Variant caller."},
        {"name": "bcftools_filter_run", "description": "Atomic filter wrapper."},
        {"name": "bcftools_isec_run", "description": "Atomic subtract wrapper."},
        {"name": "shared_variants_export_run", "description": "Shared export wrapper."},
    ]

    prompt = harness._stepwise_prompt(
        contract={"must_include_capabilities": ["annotation"]},
        contract_progress={"passed": False, "missing_capabilities": ["annotation"]},
        turn_num=2,
        recommended_skills=recommended_skills,
        available_skills=available_skills,
    )

    assert "Analysis brief:" in prompt
    assert "Workflow seed from analysis spec:" in prompt
    assert "Recent executed step details:" in prompt
    assert "bcftools_filter_run" in prompt
    assert "bcftools_isec_run" in prompt
    assert "shared_variants_export_run" in prompt
    assert "output_vcf_gz" in prompt
    assert "Recommended tool names for this turn:" in prompt
    assert "Other installed tool names:" in prompt
    assert "tabix_index_run" in prompt


def test_stepwise_candidate_accepts_prefix_protocol_progress(tmp_path: Path, monkeypatch) -> None:
    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {
        "chosen_method": "freebayes_call",
        "protocol_grounding": {
            "grounded": True,
            "required_tools": [
                "spades_assemble",
                "freebayes_call",
                "snpeff_annotate",
                "prokka_annotate",
            ],
            "required_plan_signals": [
                "spades",
                "freebayes",
                "vcffilter",
                "snpeff",
            ],
            "min_variant_branches": 2,
            "benchmark_profile": {
                "annotation_strategy": {"tool_name": "prokka_annotate"},
            },
        },
    }
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": ["annotation"],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_args, **_kwargs: [])
    # Fix #22b: the candidate's ``reads_1/reads_2`` are synthetic paths under
    # ``tmp_path`` that the test never materializes on disk. The missing-
    # inputs guard would reject the candidate before the protocol-progress
    # assertions run, so short-circuit it for this test.
    harness._stepwise_missing_candidate_inputs = lambda **_kwargs: []  # type: ignore[method-assign]

    accepted, payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "start with assembly",
            "plan": [
                {
                    "tool_name": "spades_assemble",
                    "arguments": {
                        "reads_1": str(tmp_path / "anc_R1.fastq.gz"),
                        "reads_2": str(tmp_path / "anc_R2.fastq.gz"),
                        "output_dir": str(tmp_path / "ancestor_assembly"),
                        # Populate every declared required argument so the new
                        # stepwise required-argument check (which mirrors the
                        # executor preflight) does not reject this candidate.
                        # In live runs these come from normalization/defaults;
                        # the test harness skips that path via a fake
                        # ``_normalize_plan_for_execution``.
                        "memory_gb": 16,
                        "threads": 4,
                        "careful": True,
                    },
                }
            ],
        },
    )

    assert accepted is True, reason
    assert payload["protocol_validation"]["passed"] is True
    assert payload["protocol_validation"]["complete"] is False
    assert payload["protocol_validation"]["validation_mode"] == "stepwise_prefix"
    assert "freebayes_call" in payload["protocol_validation"]["pending_required_tools"]
    assert payload["protocol_validation"]["hard_issues"] == []


def test_stepwise_candidate_rejects_hard_protocol_issue(tmp_path: Path, monkeypatch) -> None:
    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {"protocol_grounding": {"grounded": True}}
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": ["annotation"],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_args, **_kwargs: [])

    def _fake_protocol_grounding(plan: dict[str, object], _analysis_spec: dict[str, object] | None) -> dict[str, object]:
        steps = plan.get("plan", []) if isinstance(plan.get("plan", []), list) else []
        if len(steps) == 0:
            return {
                "passed": False,
                "missing_required_tools": ["later_tool"],
                "missing_plan_signals": ["later_signal"],
                "issues": [],
                "source_files": [],
            }
        return {
            "passed": False,
            "missing_required_tools": ["later_tool"],
            "missing_plan_signals": ["later_signal"],
            "issues": [{"issue": "brittle_structured_variant_export", "step_id": 1}],
            "source_files": [],
        }

    monkeypatch.setattr(stepwise_loop_module, "assess_protocol_grounding", _fake_protocol_grounding)

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "bad export",
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {"command": "echo bad"},
                }
            ],
        },
    )

    assert accepted is False
    assert "brittle_structured_variant_export" in reason


def test_stepwise_turn_retries_empty_plan_instead_of_treating_it_as_done(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    harness.run["plan"] = {"thought_process": "", "plan": []}
    harness.run["step_statuses"] = []
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": ["annotation"],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_args, **_kwargs: [])

    attempts = iter(
        [
            (
                {
                    "thought_process": "not ready",
                    "plan": [],
                    "plan_outline": [{"tool_name": "bash_run", "step_id": 1}],
                },
                0.01,
            ),
            (
                {
                    "thought_process": "run one command",
                    "plan": [
                        {
                            "tool_name": "bash_run",
                            "arguments": {"command": "echo ok"},
                        }
                    ],
                },
                0.02,
            ),
        ]
    )

    harness._planner_attempt_with_heartbeat = lambda **_kwargs: next(attempts)  # type: ignore[method-assign]

    decision = harness._plan_next_step_turn(contract={}, turn_num=1)

    assert decision["status"] == "step"
    assert decision["attempts"][0]["status"] == "invalid_shape"
    assert "empty `plan`" in decision["attempts"][0]["reason"]
    assert decision["attempts"][1]["status"] == "accepted"


def test_stepwise_turn_passes_selected_skill_subset_to_planner_attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {
        "preferred_tools": ["normalize_step", "intersect_step", "shared_export"],
    }
    harness.run["plan"] = {"thought_process": "", "plan": []}
    harness.run["step_statuses"] = []
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": ["annotation"],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_args, **_kwargs: [])

    available_skills = [
        {"name": "bash_run", "description": "Shell fallback."},
        {"name": "normalize_step", "description": "Normalize one VCF."},
        {"name": "intersect_step", "description": "Intersect one pair of VCFs."},
        {"name": "shared_export", "description": "Export one CSV."},
    ]
    selected_skills = [
        {"name": "normalize_step", "description": "Normalize one VCF."},
        {"name": "intersect_step", "description": "Intersect one pair of VCFs."},
        {"name": "shared_export", "description": "Export one CSV."},
    ]
    monkeypatch.setattr(
        harness.orchestrator,
        "_available_skill_metadata",
        lambda: [dict(item) for item in available_skills],
    )
    monkeypatch.setattr(
        harness.orchestrator,
        "_select_planner_skill_metadata",
        lambda *_args, **_kwargs: (
            [dict(item) for item in selected_skills],
            {
                "selected_skill_names": [item["name"] for item in selected_skills],
                "selected_skills": len(selected_skills),
                "budget": len(selected_skills),
            },
        ),
    )

    captured: dict[str, object] = {}

    def _fake_attempt(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        captured["available_skills_metadata_override"] = kwargs["available_skills_metadata_override"]
        return (
            {
                "thought_process": "next",
                "plan": [
                    {
                        "tool_name": "shared_export",
                        "arguments": {"output_csv": str(tmp_path / "out.csv")},
                    }
                ],
            },
            0.01,
        )

    harness._planner_attempt_with_heartbeat = _fake_attempt  # type: ignore[method-assign]

    decision = harness._plan_next_step_turn(contract={}, turn_num=1)

    assert decision["status"] == "step"
    assert [item["name"] for item in captured["available_skills_metadata_override"]] == [
        "normalize_step",
        "intersect_step",
        "shared_export",
    ]
    prompt = str(captured["prompt"])
    assert "Recommended tool names for this turn:" in prompt
    assert '"normalize_step"' in prompt
    assert '"shared_export"' in prompt
    assert "Other installed tool names:" in prompt
    assert '"bash_run"' in prompt


def test_run_end_to_end_stepwise_fails_closed_when_no_usable_next_step(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    harness._record_graph_outcome = lambda: None  # type: ignore[method-assign]
    harness._prepare_stepwise_context = lambda: harness.run.update(  # type: ignore[method-assign]
        {
            "plan": {"thought_process": "", "plan": []},
            "step_statuses": [],
            "next_step_idx": 0,
            "contract_validation": {"passed": False},
            "protocol_validation": {"passed": False},
            "status": "planned",
        }
    ) or {}
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {"passed": False}  # type: ignore[method-assign]
    harness._plan_next_step_turn = lambda **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        ValueError("Stepwise planner did not produce a usable next step. Received an empty `plan`.")
    )

    result = harness._run_end_to_end_stepwise()

    assert result["status"] == "failed"
    assert "usable next step" in str(result["error"])
    assert harness.run["stepwise_turns"][0]["status"] == "failed"


def test_run_end_to_end_stepwise_preserves_pending_tail_across_execution(
    tmp_path: Path,
) -> None:
    """Successful step execution must not drop queued multi-step tails."""

    harness = _build_harness(tmp_path)
    harness._record_graph_outcome = lambda: None  # type: ignore[method-assign]
    harness._stepwise_max_turns = lambda: 2  # type: ignore[method-assign]
    harness._prepare_stepwise_context = lambda: harness.run.update(  # type: ignore[method-assign]
        {
            "plan": {"thought_process": "", "plan": []},
            "step_statuses": [],
            "stepwise_pending_candidate_steps": [],
            "next_step_idx": 0,
            "contract_validation": {"passed": False},
            "protocol_validation": {"passed": False},
            "status": "planned",
        }
    ) or {}
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {"passed": False}  # type: ignore[method-assign]
    monkey_seen_tail = {"value": False}
    tail_step = {
        "tool_name": "bcftools_norm_run",
        "branch_id": "evol2",
        "arguments": {"output_vcf": str(tmp_path / "variants/evol2.normalized.vcf")},
    }

    def _fake_plan_next_step_turn(**_kwargs):
        if len(harness.run.get("stepwise_turns", [])) == 0:
            return {
                "status": "step",
                "attempts": [],
                "accepted_payload": {
                    "plan": {
                        "thought_process": "",
                        "plan": [
                            {
                                "tool_name": "bcftools_norm_run",
                                "branch_id": "evol1",
                                "arguments": {
                                    "output_vcf": str(tmp_path / "variants/evol1.normalized.vcf")
                                },
                            }
                        ],
                    },
                    "contract_validation": {"passed": False},
                    "protocol_validation": {"passed": False},
                    "semantic_validation": {"passed": True},
                    "bash_placeholder_resolutions": [],
                },
                "pending_candidate_steps": [tail_step],
            }
        monkey_seen_tail["value"] = bool(harness.run.get("stepwise_pending_candidate_steps"))
        raise ValueError("stop after observing preserved tail")

    def _fake_execute_once(*_args, **_kwargs) -> None:
        statuses = harness.run.get("step_statuses", [])
        if isinstance(statuses, list) and statuses:
            statuses[-1] = "completed"
        harness.run.pop("stepwise_pending_candidate_steps", None)
        harness.run["stepwise_last_step_failed"] = False

    harness._plan_next_step_turn = _fake_plan_next_step_turn  # type: ignore[method-assign]
    harness._execute_once = _fake_execute_once  # type: ignore[method-assign]

    result = harness._run_end_to_end_stepwise()

    assert result["status"] == "failed"
    assert monkey_seen_tail["value"] is True
    assert harness.run["stepwise_turns"][0]["pending_candidate_steps"] == [tail_step]
    state_path = Path(harness.run["run_files"]["state"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["stepwise_pending_candidate_steps"] == [tail_step]
    assert state["plan"]["plan"][-1]["tool_name"] == "bcftools_norm_run"


def test_execute_once_keeps_stepwise_failures_nonterminal(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {"command": "echo fail"},
            }
        ],
    }
    harness.run["step_statuses"] = ["pending"]

    def _fake_execute_plan(_plan, log_queue, *_args, **kwargs) -> None:
        run_artifacts = kwargs["run_artifacts"]
        finish_executor_runtime(
            run_artifacts,
            run_id=str(run_artifacts["run_id"]),
            status="failed",
            error="Step 1 failed on purpose.",
        )
        log_queue.put("Step 1 failed on purpose.\n")
        log_queue.put(None)

    harness.orchestrator.execute_plan = _fake_execute_plan  # type: ignore[method-assign]
    harness._execute_once(finalize_run=False)

    assert harness.run["status"] == "planned"
    assert bool(harness.run.get("stepwise_last_step_failed", False)) is True
    assert "Step 1 failed on purpose." in str(harness.run.get("error", ""))
    assert not Path(harness.run["run_files"]["completed_run_context"]).exists()
    exit_payload = json.loads(Path(harness.run["run_files"]["exit"]).read_text(encoding="utf-8"))
    assert exit_payload["status"] == "planned"
    assert exit_payload["finished_at"] is None
    assert exit_payload["error"] == ""


def test_run_end_to_end_uses_stepwise_loop(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)

    harness._prepare_analysis_spec = lambda contract: harness.run.update({"analysis_spec": {"analysis_type": "toy"}})  # type: ignore[method-assign]
    harness._refresh_environment_snapshot = lambda: None  # type: ignore[method-assign]
    harness._record_graph_outcome = lambda: None  # type: ignore[method-assign]
    harness._finalize_completed_run = lambda: harness.run.update({"contract_validation": {"passed": True}})  # type: ignore[method-assign]

    def _contract_assessor(plan: dict[str, object], _contract: dict[str, object]) -> dict[str, object]:
        steps = plan.get("plan", []) if isinstance(plan.get("plan", []), list) else []
        passed = len(steps) >= 1
        return {
            "passed": passed,
            "missing_capabilities": [] if passed else ["toy_capability"],
            "missing_required_tool_hints": [],
            "missing_tool_hints": [],
            "direct_wrapper_issues": [],
            "artifact_role_issues": [],
        }

    harness._assess_contract_for_plan = _contract_assessor  # type: ignore[method-assign]
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]

    def _fake_planner_attempt(**_kwargs) -> tuple[dict[str, object], float]:
        return (
            {
                "thought_process": "next",
                "plan": [
                    {
                        "tool_name": "bash_run",
                        "arguments": {"command": "echo ok"},
                    }
                ],
            },
            0.01,
        )

    harness._planner_attempt_with_heartbeat = _fake_planner_attempt  # type: ignore[method-assign]

    def _fake_execute_plan(_plan, log_queue, *_args, **_kwargs) -> None:
        log_queue.put("--- Executing Step 1: bash_run ---\n")
        log_queue.put("[Step 1 Output] [stdout] ok\n")
        log_queue.put("--- Step 1 (bash_run) finished ---\n")
        log_queue.put("Plan execution completed.\n")
        log_queue.put(None)

    harness.orchestrator.execute_plan = _fake_execute_plan  # type: ignore[method-assign]

    result = harness.run_end_to_end()

    assert result["status"] == "completed"
    assert harness.run["status"] == "completed"
    assert harness.run["execution_mode"] == "stepwise"
    assert len(harness.run["stepwise_turns"]) == 1
    assert harness.run["plan"]["plan"][0]["tool_name"] == "bash_run"


def test_stepwise_rejects_candidate_duplicating_completed_step(tmp_path: Path) -> None:
    """A slow planner must not re-run an already-completed step with identical args.

    Observed in exp11: after ``spades_assemble`` finished successfully, the
    planner re-proposed the same call (same tool, same input/output paths)
    ~45 minutes later and the harness happily re-ran it, starving the rest of
    the workflow. The duplicate-step guard blocks this deterministic livelock
    without relying on LLM judgment.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "spades_assemble",
                "step_id": 1,
                "arguments": {
                    "fastq_r1": "/tmp/anc_R1.fastq.gz",
                    "fastq_r2": "/tmp/anc_R2.fastq.gz",
                    "output_dir": "/tmp/assembly",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]
    harness.run["analysis_spec"] = {}

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "",
            "plan": [
                {
                    "tool_name": "spades_assemble",
                    "arguments": {
                        "fastq_r1": "/tmp/anc_R1.fastq.gz",
                        "fastq_r2": "/tmp/anc_R2.fastq.gz",
                        "output_dir": "/tmp/assembly",
                    },
                }
            ],
        },
    )

    assert accepted is False
    assert "duplicates completed step_id=1" in reason
    assert "spades_assemble" in reason
    # Rejection must steer the planner toward a different branch / stage.
    assert "different next step" in reason


def test_stepwise_accepts_candidate_with_different_arguments(tmp_path: Path) -> None:
    """The guard must not block legitimate per-branch repetition.

    In the evolution workflow ``bwa_mem_align`` is called once per evolved
    line — same tool, different FASTQ inputs, different output BAM. The
    duplicate-step check compares (tool_name, arguments), so differing
    arguments pass through to the remaining validators unchanged.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "step_id": 1,
                "arguments": {
                    "reference_fasta": "/tmp/contigs.fasta",
                    "fastq_r1": "/tmp/evol1_R1.fastq.gz",
                    "fastq_r2": "/tmp/evol1_R2.fastq.gz",
                    "output_bam": "/tmp/evol1.bam",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    candidate_step = {
        "tool_name": "bwa_mem_align",
        "arguments": {
            "reference_fasta": "/tmp/contigs.fasta",
            "fastq_r1": "/tmp/evol2_R1.fastq.gz",
            "fastq_r2": "/tmp/evol2_R2.fastq.gz",
            "output_bam": "/tmp/evol2.bam",
        },
    }

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}


def test_stepwise_ignores_uncompleted_prior_step_when_checking_duplicates(
    tmp_path: Path,
) -> None:
    """Only successfully-completed prior steps count as duplicates.

    A step that the planner emitted but that failed (status != completed) is
    fair game for re-proposal — that is the normal recovery path. The guard
    must not starve recovery by rejecting retries of failed steps.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 1,
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "output_vcf": "/tmp/sample_raw.vcf",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["failed"]

    candidate_step = {
        "tool_name": "freebayes_call",
        "arguments": {
            "input_bam": "/tmp/sample.bam",
            "output_vcf": "/tmp/sample_raw.vcf",
        },
    }

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}


def test_stepwise_duplicate_rejection_includes_pending_work_hint(tmp_path: Path) -> None:
    """The duplicate-step rejection must suggest what the planner should try next.

    When the planner duplicates a completed step, the raw guard alone still
    leaves the model guessing. The rejection message reuses existing validation
    state — ``protocol_validation.missing_required_tools``, etc. — to name the
    remaining workflow gaps. This keeps the guard general (no benchmark
    knowledge encoded) while giving the LLM a concrete next candidate to try
    on the retry attempt.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "spades_assemble",
                "step_id": 1,
                "arguments": {"input_fastq": "/tmp/anc.fq"},
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]
    harness.run["protocol_validation"] = {
        "passed": False,
        "missing_required_tools": ["snpeff_annotate", "bcftools_filter_run"],
        "missing_plan_signals": ["per_branch_variant_calls"],
    }
    harness.run["contract_validation"] = {
        "passed": False,
        "missing_capabilities": ["annotation"],
    }

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "",
            "plan": [
                {
                    "tool_name": "spades_assemble",
                    "arguments": {"input_fastq": "/tmp/anc.fq"},
                }
            ],
        },
    )

    assert accepted is False
    assert "duplicates completed step_id=1" in reason
    # Enriched guidance lists each category of pending work.
    assert "snpeff_annotate" in reason
    assert "per_branch_variant_calls" in reason
    assert "annotation" in reason
    # And names a concrete suggested next tool drawn from missing_required_tools.
    assert "Suggested next tool" in reason


def test_stepwise_pending_work_hint_is_empty_when_nothing_pending(tmp_path: Path) -> None:
    """With no missing tools/signals/capabilities the hint collapses to empty."""

    harness = _build_harness(tmp_path)
    harness.run["protocol_validation"] = {
        "passed": True,
        "missing_required_tools": [],
        "missing_plan_signals": [],
    }
    harness.run["contract_validation"] = {"passed": True, "missing_capabilities": []}
    assert harness._stepwise_pending_work_hint() == ""


def test_stepwise_pending_hint_prioritizes_branch_stage_frontier(
    tmp_path: Path,
) -> None:
    """Branch-stage frontier guidance should outrank coarse missing-tool hints."""

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "protocol_grounding": {
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
    }
    harness.run["protocol_validation"] = {
        "missing_required_tools": ["snpeff_annotate"],
        "missing_plan_signals": ["snpeff"],
    }
    harness.run["contract_validation"] = {
        "missing_capabilities": ["shared_variant_export"],
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bwa_mem_align",
                "branch_id": "ancestor",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments" / "anc_aligned.bam"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "branch_id": "ancestor",
                "arguments": {
                    "output_vcf": str(selected_dir / "variants" / "anc_raw.vcf"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol1",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments" / "evol1_aligned.bam"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "arguments": {
                    "output_vcf": str(selected_dir / "variants" / "evol1_raw.vcf"),
                },
            },
            {
                "step_id": 5,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol1",
                "arguments": {
                    "output_vcf": str(selected_dir / "variants" / "evol1.filtered.vcf.gz"),
                },
            },
            {
                "step_id": 6,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol2",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments" / "evol2_aligned.bam"),
                },
            },
            {
                "step_id": 7,
                "tool_name": "freebayes_call",
                "branch_id": "evol2",
                "arguments": {
                    "output_vcf": str(selected_dir / "variants" / "evol2_raw.vcf"),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"] * 7

    hint = harness._stepwise_pending_work_hint()

    assert "Branch-stage progress frontier" in hint
    assert "branch=ancestor" in hint
    assert "stage=filtered_vcf" in hint
    assert "bcftools_filter_run" in hint
    assert "evol2:filtered_vcf" in hint
    assert "Suggested next tool: `snpeff_annotate`" not in hint


def test_stepwise_branch_stage_frontier_blocks_premature_annotation(
    tmp_path: Path,
) -> None:
    """The branch-stage frontier is a hard gate, not just prompt text."""

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "protocol_grounding": {
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bwa_mem_align",
                "branch_id": "ancestor",
                "arguments": {"output_bam": str(selected_dir / "alignments/anc_aligned.bam")},
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "branch_id": "ancestor",
                "arguments": {"output_vcf": str(selected_dir / "variants/anc_raw.vcf")},
            },
            {
                "step_id": 3,
                "tool_name": "bcftools_filter_run",
                "branch_id": "ancestor",
                "arguments": {"output_vcf": str(selected_dir / "variants/anc.filtered.vcf.gz")},
            },
            {
                "step_id": 4,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol1",
                "arguments": {"output_bam": str(selected_dir / "alignments/evol1_aligned.bam")},
            },
            {
                "step_id": 5,
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol1_raw.vcf")},
            },
            {
                "step_id": 6,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol1",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol1.filtered.vcf.gz")},
            },
            {
                "step_id": 7,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol2",
                "arguments": {"output_bam": str(selected_dir / "alignments/evol2_aligned.bam")},
            },
            {
                "step_id": 8,
                "tool_name": "freebayes_call",
                "branch_id": "evol2",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol2_raw.vcf")},
            },
            {
                "step_id": 9,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol2",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol2.filtered.vcf.gz")},
            },
            {
                "step_id": 10,
                "tool_name": "bcftools_isec_run",
                "branch_id": "evol1",
                "arguments": {
                    "input_vcfs": [
                        str(selected_dir / "variants/evol1.filtered.vcf.gz"),
                        str(selected_dir / "variants/anc.filtered.vcf.gz"),
                    ],
                    "output_vcf": str(
                        selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"
                    ),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"] * 10

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "Annotate evol2 next.",
            "plan": [
                {
                    "tool_name": "snpeff_annotate",
                    "branch_id": "evol2",
                    "arguments": {
                        "input_vcf": str(
                            selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
                        ),
                        "output_vcf": str(selected_dir / "variants/evol2.annotated.vcf"),
                    },
                }
            ],
        },
    )

    assert accepted is False
    assert "Candidate does not advance the current branch-stage frontier" in reason
    assert "branch=evol2, stage=ancestor_subtracted_vcf" in reason
    assert "Expected branch-stage tool: `bcftools_isec_run`" in reason
    assert "snpeff_annotate" in reason


def test_stepwise_branch_stage_frontier_accepts_matching_isec_candidate(
    tmp_path: Path,
) -> None:
    """The hard frontier still allows the correct branch-local producer."""

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "protocol_grounding": {
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bwa_mem_align",
                "branch_id": "ancestor",
                "arguments": {"output_bam": str(selected_dir / "alignments/anc_aligned.bam")},
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "branch_id": "ancestor",
                "arguments": {"output_vcf": str(selected_dir / "variants/anc_raw.vcf")},
            },
            {
                "step_id": 3,
                "tool_name": "bcftools_filter_run",
                "branch_id": "ancestor",
                "arguments": {"output_vcf": str(selected_dir / "variants/anc.filtered.vcf.gz")},
            },
            {
                "step_id": 4,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol1",
                "arguments": {"output_bam": str(selected_dir / "alignments/evol1_aligned.bam")},
            },
            {
                "step_id": 5,
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol1_raw.vcf")},
            },
            {
                "step_id": 6,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol1",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol1.filtered.vcf.gz")},
            },
            {
                "step_id": 7,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol2",
                "arguments": {"output_bam": str(selected_dir / "alignments/evol2_aligned.bam")},
            },
            {
                "step_id": 8,
                "tool_name": "freebayes_call",
                "branch_id": "evol2",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol2_raw.vcf")},
            },
            {
                "step_id": 9,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol2",
                "arguments": {"output_vcf": str(selected_dir / "variants/evol2.filtered.vcf.gz")},
            },
            {
                "step_id": 10,
                "tool_name": "bcftools_isec_run",
                "branch_id": "evol1",
                "arguments": {
                    "input_vcfs": [
                        str(selected_dir / "variants/evol1.filtered.vcf.gz"),
                        str(selected_dir / "variants/anc.filtered.vcf.gz"),
                    ],
                    "output_vcf": str(
                        selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"
                    ),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"] * 10

    reason = harness._stepwise_branch_stage_rejection_reason(
        candidate_step={
            "tool_name": "bcftools_isec_run",
            "branch_id": "evol2",
            "objective": "Subtract ancestor-supported variants from evol2.",
        },
    )

    assert reason == ""


def test_stepwise_branch_stage_frontier_blocks_restart_after_norm(
    tmp_path: Path,
) -> None:
    """After evol1 normalization, upstream restarts cannot outrun evol2 norm."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)

    reason = harness._stepwise_branch_stage_rejection_reason(
        candidate_step={
            "tool_name": "freebayes_call",
            "branch_id": "evol1",
            "arguments": {
                "input_bam": str(harness.cfg.selected_dir / "alignments/evol1_aligned.bam"),
                "output_vcf": str(harness.cfg.selected_dir / "variants/evol1_raw_retry.vcf"),
            },
        },
    )

    assert "Candidate does not advance the current branch-stage frontier" in reason
    assert "branch=evol2, stage=normalized_vcf" in reason
    assert "Expected branch-stage tool: `bcftools_norm_run`" in reason


def test_stepwise_branch_stage_frontier_accepts_evol2_norm_after_evol1_norm(
    tmp_path: Path,
) -> None:
    """The post-evol1 frontier still allows the missing evol2 normalization."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)

    reason = harness._stepwise_branch_stage_rejection_reason(
        candidate_step={
            "tool_name": "bcftools_norm_run",
            "branch_id": "evol2",
            "arguments": {
                "input_vcf": str(harness.cfg.selected_dir / "variants/evol2.annotated.vcf"),
                "output_vcf": str(harness.cfg.selected_dir / "variants/evol2.normalized.vcf"),
            },
        },
    )

    assert reason == ""


def test_stepwise_frontier_rebinds_stale_norm_args_to_evol1(
    tmp_path: Path,
) -> None:
    """The current normalization frontier should repair stale wrapper args."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_both_isec_without_annotation(harness)
    selected_dir = harness.cfg.selected_dir
    data_root = harness.cfg.data_root
    harness.run["benchmark_policy"] = "scientific_harness"
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "scientific_harness",
        "selected_dir": str(selected_dir),
        "requested_data_root": str(data_root),
    }
    harness.run["plan"]["plan"].insert(
        0,
        {
            "step_id": 0,
            "tool_name": "spades_assemble",
            "arguments": {
                "reads_1": str(data_root / "anc_R1.fastq.gz"),
                "reads_2": str(data_root / "anc_R2.fastq.gz"),
                "output_dir": str(selected_dir / "assembly"),
            },
        },
    )
    harness.run["step_statuses"].insert(0, "completed")
    harness.run["plan"]["plan"].extend(
        [
            {
                "step_id": 12,
                "tool_name": "snpeff_annotate",
                "branch_id": "evol1",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
                },
            },
            {
                "step_id": 13,
                "tool_name": "snpeff_annotate",
                "branch_id": "evol2",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants/evol2.annotated.vcf"),
                },
            },
        ]
    )
    harness.run["step_statuses"].extend(["completed", "completed"])
    for relative in (
        "assembly/scaffolds.fasta",
        "variants/evol1.annotated.vcf",
        "variants/evol2.annotated.vcf",
    ):
        path = selected_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    for relative in ("anc_R1.fastq.gz", "anc_R2.fastq.gz"):
        path = data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    accepted, payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "The workflow is at the normalization stage for evol1.",
            "plan": [
                {
                    "tool_name": "bcftools_norm_run",
                    "arguments": {
                        "reads_1": str(data_root / "anc_R1.fastq.gz"),
                        "reads_2": str(data_root / "anc_R2.fastq.gz"),
                        "output_dir": str(selected_dir / "assembly"),
                        "careful": True,
                        "threads": 8,
                        "memory_gb": 32,
                    },
                }
            ],
        },
    )

    assert accepted is True, reason
    rebound_step = payload["plan"]["plan"][-1]
    rebound_args = rebound_step["arguments"]
    assert rebound_step["branch_id"] == "evol1"
    assert rebound_args["input_vcf"] == str(
        selected_dir / "variants/evol1.annotated.vcf"
    )
    assert rebound_args["output_vcf"] == str(
        selected_dir / "variants/evol1.annotated.normalized.vcf.gz"
    )
    assert rebound_args["reference_fasta"] == str(
        selected_dir / "assembly/scaffolds.fasta"
    )


def test_stepwise_frontier_rebinds_stale_snpeff_args_to_evol2(
    tmp_path: Path,
) -> None:
    """The annotation frontier should repair stale branch-local wrapper args."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_both_isec_without_annotation(harness)
    selected_dir = harness.cfg.selected_dir
    harness.run["benchmark_policy"] = "scientific_harness"
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "scientific_harness",
        "selected_dir": str(selected_dir),
        "requested_data_root": str(harness.cfg.data_root),
        "protocol_grounding": {
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
    }
    harness.run["plan"]["plan"].insert(
        0,
        {
            "step_id": 0,
            "tool_name": "spades_assemble",
            "arguments": {
                "reads_1": str(harness.cfg.data_root / "anc_R1.fastq.gz"),
                "reads_2": str(harness.cfg.data_root / "anc_R2.fastq.gz"),
                "output_dir": str(selected_dir / "assembly"),
            },
        },
    )
    harness.run["step_statuses"].insert(0, "completed")
    harness.run["plan"]["plan"].extend(
        [
            {
                "step_id": 12,
                "tool_name": "prokka_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "output_dir": str(selected_dir / "annotation"),
                    "sample_prefix": "ancestor",
                },
            },
            {
                "step_id": 13,
                "tool_name": "snpeff_annotate",
                "branch_id": "evol1",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
                },
            },
        ]
    )
    harness.run["step_statuses"].extend(["completed", "completed"])
    for relative in (
        "assembly/scaffolds.fasta",
        "annotation/ancestor.gff",
        "variants/evol1.ancestor_subtracted.vcf.gz",
        "variants/evol1.annotated.vcf",
        "variants/evol2.ancestor_subtracted.vcf.gz",
    ):
        path = selected_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    for relative in ("anc_R1.fastq.gz", "anc_R2.fastq.gz"):
        path = harness.cfg.data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    accepted, payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "Annotate the remaining evolved branch.",
            "plan": [
                {
                    "tool_name": "snpeff_annotate",
                    "branch_id": "evol1",
                    "objective": "Annotate evol1 variants.",
                    "arguments": {
                        "input_vcf": str(
                            selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"
                        ),
                        "output_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
                    },
                }
            ],
        },
    )

    assert accepted is True, reason
    rebound_step = payload["plan"]["plan"][-1]
    rebound_args = rebound_step["arguments"]
    assert rebound_step["branch_id"] == "evol2"
    assert rebound_args["input_vcf"] == str(
        selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
    )
    assert rebound_args["output_vcf"] == str(
        selected_dir / "variants/evol2.annotated.vcf"
    )


def test_stepwise_rejected_candidate_persists_fixture_seed(tmp_path: Path) -> None:
    """Rejected candidates should carry enough state for fixture extraction."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)
    candidate = {
        "thought_process": "Retry a completed evol1 variant call.",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "arguments": {
                    "input_bam": str(
                        harness.cfg.selected_dir / "alignments/evol1_aligned.bam"
                    ),
                    "output_vcf": str(
                        harness.cfg.selected_dir / "variants/evol1_raw.vcf"
                    ),
                },
            }
        ],
    }

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate=candidate,
    )
    record = harness._record_stepwise_candidate_rejection(
        candidate_plan=candidate,
        rejection_reason=reason,
        turn_num=7,
        attempt_num=2,
        strategy="test_strategy",
        source="planner_candidate",
    )
    harness._persist_state()
    state_path = Path(harness.run["run_files"]["state"])
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    persisted_record = persisted["stepwise_rejected_candidates"][0]

    assert accepted is False
    assert "duplicates completed" in reason
    assert record["gate"] == "duplicate_detector"
    assert record["raw_candidate"] == candidate
    assert record["bound_candidate_step"]
    assert record["frontier_state"]["next_cell"]["branch_id"] == "evol2"
    assert record["fixture_seed"]["prefix_state"]["plan"]
    assert persisted_record["rejection_reason"] == reason
    assert persisted_record["fixture_seed"]["candidate"] == candidate


def test_terminal_summary_is_rewritten_after_cli_completion(tmp_path: Path) -> None:
    """Terminal CLI runs should not leave the initial in-progress summary."""

    harness = _build_harness(tmp_path)
    harness.run["status"] = "completed"
    harness.run["finished_at"] = "2026-04-25T12:00:00"
    harness.run["step_statuses"] = ["completed", "completed"]

    harness._write_exit()

    summary = Path(harness.run["run_files"]["summary"]).read_text(encoding="utf-8")
    assert "Run in progress" not in summary
    assert "- Status: completed" in summary
    assert "- Steps completed: 2/2" in summary


def test_stepwise_trace_progress_filter_rejects_completed_prefix_restart(
    tmp_path: Path,
) -> None:
    """Completed-prefix trace emissions should not extend planner timeout."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)
    trace_dir = tmp_path / "planner"
    trace_dir.mkdir()
    raw_path = trace_dir / "candidate.txt"
    raw_path.write_text(
        json.dumps(
            {
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "arguments": {
                    "input_bam": str(harness.cfg.selected_dir / "alignments/evol1_aligned.bam"),
                    "output_vcf": str(harness.cfg.selected_dir / "variants/evol1_raw_retry.vcf"),
                },
            }
        ),
        encoding="utf-8",
    )
    trace_path = trace_dir / "0001_structured_success.json"
    trace_path.write_text(
        json.dumps({"event_type": "STRUCTURED_SUCCESS", "raw_content_file": str(raw_path)}),
        encoding="utf-8",
    )

    assessment = harness._planner_trace_progress_assessment(
        planner_trace_dir=str(trace_dir),
        latest_name=trace_path.name,
    )

    assert assessment["productive"] is False
    assert assessment["reason"] == "candidate_duplicates_completed_prefix"


def test_stepwise_trace_progress_filter_allows_frontier_candidate(
    tmp_path: Path,
) -> None:
    """A structured trace can extend timeout when it names the next frontier."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)
    harness.run["plan"]["plan"] = harness.run["plan"]["plan"][:-1]
    harness.run["step_statuses"] = harness.run["step_statuses"][:-1]
    trace_dir = tmp_path / "planner"
    trace_dir.mkdir()
    raw_path = trace_dir / "candidate.txt"
    raw_path.write_text(
        json.dumps(
            {
                "tool_name": "bcftools_norm_run",
                "branch_id": "evol2",
                "arguments": {
                    "input_vcf": str(harness.cfg.selected_dir / "variants/evol2.annotated.vcf"),
                    "output_vcf": str(harness.cfg.selected_dir / "variants/evol2.normalized.vcf"),
                },
            }
        ),
        encoding="utf-8",
    )
    trace_path = trace_dir / "0002_structured_success.json"
    trace_path.write_text(
        json.dumps({"event_type": "STRUCTURED_SUCCESS", "raw_content_file": str(raw_path)}),
        encoding="utf-8",
    )

    assessment = harness._planner_trace_progress_assessment(
        planner_trace_dir=str(trace_dir),
        latest_name=trace_path.name,
    )

    assert assessment["productive"] is True
    assert assessment["details"]["tool_name"] == "bcftools_norm_run"


def test_stepwise_trace_progress_allows_branchless_expected_frontier_tool(
    tmp_path: Path,
) -> None:
    """Trace liveness is looser than execution validation for partial steps."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)
    harness.run["plan"]["plan"] = harness.run["plan"]["plan"][:-1]
    harness.run["step_statuses"] = harness.run["step_statuses"][:-1]
    trace_dir = tmp_path / "planner"
    trace_dir.mkdir()
    raw_path = trace_dir / "candidate.txt"
    raw_path.write_text(
        json.dumps(
            {
                "tool_name": "bcftools_norm_run",
                "arguments": {},
                "produces": [],
                "assumptions": [],
            }
        ),
        encoding="utf-8",
    )
    trace_path = trace_dir / "0004_structured_success.json"
    trace_path.write_text(
        json.dumps({"event_type": "STRUCTURED_SUCCESS", "raw_content_file": str(raw_path)}),
        encoding="utf-8",
    )

    assessment = harness._planner_trace_progress_assessment(
        planner_trace_dir=str(trace_dir),
        latest_name=trace_path.name,
    )

    assert assessment["productive"] is True
    assert assessment["details"]["tool_name"] == "bcftools_norm_run"


def test_stepwise_trace_progress_filter_skips_premature_step_to_frontier(
    tmp_path: Path,
) -> None:
    """A multi-step trace should credit the first branch-frontier candidate."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_isec(harness)
    trace_dir = tmp_path / "planner"
    trace_dir.mkdir()
    raw_path = trace_dir / "candidate.txt"
    raw_path.write_text(
        json.dumps(
            {
                "plan": [
                    {
                        "tool_name": "snpeff_annotate",
                        "branch_id": "evol2",
                        "arguments": {
                            "input_vcf": str(
                                harness.cfg.selected_dir
                                / "variants/evol2.ancestor_subtracted.vcf.gz"
                            ),
                            "output_vcf": str(
                                harness.cfg.selected_dir / "variants/evol2.annotated.vcf"
                            ),
                        },
                    },
                    {
                        "tool_name": "bcftools_isec_run",
                        "branch_id": "evol2",
                        "arguments": {
                            "input_vcfs": [
                                str(harness.cfg.selected_dir / "variants/evol2.filtered.vcf.gz"),
                                str(harness.cfg.selected_dir / "variants/anc.filtered.vcf.gz"),
                            ],
                            "output_vcf": str(
                                harness.cfg.selected_dir
                                / "variants/evol2.ancestor_subtracted.vcf.gz"
                            ),
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    trace_path = trace_dir / "0003_structured_success.json"
    trace_path.write_text(
        json.dumps({"event_type": "STRUCTURED_SUCCESS", "raw_content_file": str(raw_path)}),
        encoding="utf-8",
    )

    assessment = harness._planner_trace_progress_assessment(
        planner_trace_dir=str(trace_dir),
        latest_name=trace_path.name,
    )

    assert assessment["productive"] is True
    assert assessment["details"]["chosen_index"] == 1
    assert assessment["details"]["tool_name"] == "bcftools_isec_run"


def test_stepwise_first_candidate_selection_skips_premature_annotation(
    tmp_path: Path,
) -> None:
    """The truncation selector should prefer frontier work over later racing."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_isec(harness)

    chosen_index = harness._stepwise_first_nonduplicate_candidate_index(
        [
            {
                "tool_name": "snpeff_annotate",
                "branch_id": "evol2",
                "arguments": {
                    "input_vcf": str(
                        harness.cfg.selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
                    ),
                    "output_vcf": str(harness.cfg.selected_dir / "variants/evol2.annotated.vcf"),
                },
            },
            {
                "tool_name": "bcftools_isec_run",
                "branch_id": "evol2",
                "arguments": {
                    "input_vcfs": [
                        str(harness.cfg.selected_dir / "variants/evol2.filtered.vcf.gz"),
                        str(harness.cfg.selected_dir / "variants/anc.filtered.vcf.gz"),
                    ],
                    "output_vcf": str(
                        harness.cfg.selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
                    ),
                },
            },
        ]
    )

    assert chosen_index == 1


def test_stepwise_pending_work_hint_handles_non_list_values(tmp_path: Path) -> None:
    """Malformed validation payloads must not crash the hint builder."""

    harness = _build_harness(tmp_path)
    harness.run["protocol_validation"] = {
        "missing_required_tools": None,
        "missing_plan_signals": "not-a-list",
    }
    harness.run["contract_validation"] = {"missing_capabilities": 42}
    # Should return an empty hint without raising.
    assert harness._stepwise_pending_work_hint() == ""


def test_stepwise_attempts_per_turn_decoupled_from_batch_ceiling(
    tmp_path: Path, monkeypatch
) -> None:
    """Stepwise retries do not inherit the batch planner's 3-attempt ceiling.

    The batch planner ceiling is for expensive full-plan retries; stepwise
    retries are cheap and validator-informed, so capping them at the batch
    number defeated the whole point of the enriched rejection hint. The
    stepwise ceiling is its own value, defaulting to 6.
    """

    monkeypatch.delenv("BIO_HARNESS_STEPWISE_ATTEMPTS_PER_TURN", raising=False)
    harness = _build_harness(tmp_path)
    # Force the batch ceiling low to prove decoupling.
    monkeypatch.setenv("BIO_HARNESS_PLANNER_MAX_ATTEMPTS", "3")
    assert harness._planner_max_attempts() == 3
    assert harness._stepwise_planner_attempts_per_turn() == 6


def test_stepwise_attempts_per_turn_honours_env_override(tmp_path: Path, monkeypatch) -> None:
    """Operators can tune the attempt budget via the env var."""

    monkeypatch.setenv("BIO_HARNESS_STEPWISE_ATTEMPTS_PER_TURN", "4")
    harness = _build_harness(tmp_path)
    assert harness._stepwise_planner_attempts_per_turn() == 4


def test_stepwise_attempts_per_turn_clamps_to_safe_upper_bound(
    tmp_path: Path, monkeypatch
) -> None:
    """Runaway env values are clamped so a turn cannot spin forever."""

    monkeypatch.setenv("BIO_HARNESS_STEPWISE_ATTEMPTS_PER_TURN", "999")
    harness = _build_harness(tmp_path)
    # Ceiling is 12 — plenty of room, but bounded.
    assert harness._stepwise_planner_attempts_per_turn() == 12


def test_stepwise_step_signature_ignores_step_id_and_depends_on(tmp_path: Path) -> None:
    """Signatures compare only call identity, not bookkeeping fields.

    Two steps that have different ``step_id`` or ``depends_on`` but identical
    ``(tool_name, arguments)`` are the same call and must share a signature.
    """

    harness = _build_harness(tmp_path)
    step_a = {
        "tool_name": "bash_run",
        "step_id": 3,
        "depends_on": [2],
        "arguments": {"command": "ls -la"},
    }
    step_b = {
        "tool_name": "bash_run",
        "step_id": 99,
        "depends_on": [1, 2, 97],
        "arguments": {"command": "ls -la"},
    }
    assert harness._stepwise_step_signature(step_a) == harness._stepwise_step_signature(step_b)

    step_different_args = {
        "tool_name": "bash_run",
        "step_id": 3,
        "arguments": {"command": "ls /other"},
    }
    assert harness._stepwise_step_signature(step_a) != harness._stepwise_step_signature(
        step_different_args
    )
    # Empty or malformed steps yield empty signatures (and are never duplicates).
    assert harness._stepwise_step_signature({}) == ""
    assert harness._stepwise_step_signature({"tool_name": ""}) == ""


def test_stepwise_step_signature_strips_harness_managed_parameters(tmp_path: Path) -> None:
    """Harness-managed parameter names must be excluded from the signature.

    The plan normalizer injects defaults like ``threads``, ``memory_gb``,
    and other infrastructure parameters when a step is accepted into the
    plan prefix. A subsequent LLM candidate (the raw pre-normalization
    form) will not carry those keys, so without stripping them the
    signatures would diverge and duplicate detection would silently miss
    every re-proposed step. The skill registry's
    ``harness_managed_parameters_for`` is the authoritative source and
    must be consulted for both the accepted and candidate sides.
    """

    import bio_harness.core.tool_registry as tool_registry

    class _FakeRegistry:
        def harness_managed_parameters_for(self, tool_name: str) -> list[str]:
            # Return empty here to mirror the real registry for spades_assemble
            # and prove the fallback via parameter_defaults_for covers the gap.
            return []

        def parameter_defaults_for(self, tool_name: str) -> dict[str, object]:
            if tool_name == "spades_assemble":
                return {"threads": 8, "memory_gb": 32, "careful": True}
            return {}

    original = tool_registry.default_tool_registry
    tool_registry.default_tool_registry = lambda: _FakeRegistry()  # type: ignore[assignment]
    try:
        harness = _build_harness(tmp_path)
        # Identity (paths, careful flag) is the same; only harness-managed
        # infrastructure keys differ between accepted-and-normalized and
        # freshly-proposed candidate.
        accepted_after_normalization = {
            "tool_name": "spades_assemble",
            "step_id": 1,
            "arguments": {
                "reads_1": "/data/anc_R1.fastq.gz",
                "reads_2": "/data/anc_R2.fastq.gz",
                "output_dir": "/out/assembly",
                "careful": True,
                "threads": 4,
                "memory_gb": 16,
            },
        }
        candidate_raw_from_llm = {
            "tool_name": "spades_assemble",
            "step_id": 5,
            "arguments": {
                "reads_1": "/data/anc_R1.fastq.gz",
                "reads_2": "/data/anc_R2.fastq.gz",
                "output_dir": "/out/assembly",
                "careful": True,
            },
        }
        assert harness._stepwise_step_signature(
            accepted_after_normalization
        ) == harness._stepwise_step_signature(candidate_raw_from_llm)

        # Genuinely different calls (different input reads) must still
        # produce different signatures — the strip is scoped to managed keys
        # only, not to identity-forming keys like reads_1.
        truly_different = {
            "tool_name": "spades_assemble",
            "arguments": {
                "reads_1": "/data/evol1_R1.fastq.gz",
                "reads_2": "/data/evol1_R2.fastq.gz",
                "output_dir": "/out/assembly",
                "careful": True,
            },
        }
        assert harness._stepwise_step_signature(
            accepted_after_normalization
        ) != harness._stepwise_step_signature(truly_different)
    finally:
        tool_registry.default_tool_registry = original  # type: ignore[assignment]


def test_stepwise_selected_planner_skills_filters_excluded_tools(tmp_path: Path) -> None:
    """Excluded tool names must be stripped from both selected and full skill lists.

    The stepwise attempt loop feeds this set from duplicate-rejected tools so
    later attempts in the same turn cannot re-propose them. The filter is
    applied by name to both the recommended subset and the full available
    list, because the planner prompt also exposes the full list as
    "Other installed tool names" and the LLM will pick from either.
    """

    harness = _build_harness(tmp_path)

    def _fake_available() -> list[dict[str, object]]:
        return [
            {"name": "spades_assemble"},
            {"name": "prodigal_annotate"},
            {"name": "bwa_mem_align"},
            {"name": "snpeff_annotate"},
        ]

    def _fake_select(
        _query: str,
        skills: list[dict[str, object]],
        analysis_spec=None,  # noqa: ARG001
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        return (
            [skill for skill in skills if skill["name"] in {"spades_assemble", "snpeff_annotate"}],
            {"selected_skill_names": ["spades_assemble", "snpeff_annotate"]},
        )

    harness.orchestrator._available_skill_metadata = _fake_available  # type: ignore[method-assign]
    harness.orchestrator._select_planner_skill_metadata = _fake_select  # type: ignore[method-assign]

    selected, _meta, available = harness._stepwise_selected_planner_skills(
        selection_query="plan the next step",
        excluded_tool_names={"spades_assemble"},
    )
    selected_names = {str(skill.get("name")) for skill in selected}
    available_names = {str(skill.get("name")) for skill in available}
    assert "spades_assemble" not in selected_names
    assert "spades_assemble" not in available_names
    # Other tools remain, including the suggested next tool.
    assert "snpeff_annotate" in selected_names
    assert "snpeff_annotate" in available_names
    assert "bwa_mem_align" in available_names


def test_stepwise_selected_planner_skills_no_filter_when_excluded_empty(
    tmp_path: Path,
) -> None:
    """With no exclusion set the filter is a no-op (no silent subsetting)."""

    harness = _build_harness(tmp_path)

    def _fake_available() -> list[dict[str, object]]:
        return [{"name": "a"}, {"name": "b"}]

    def _fake_select(
        _query: str,
        skills: list[dict[str, object]],
        analysis_spec=None,  # noqa: ARG001
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        return (list(skills), {"selected_skill_names": [s["name"] for s in skills]})

    harness.orchestrator._available_skill_metadata = _fake_available  # type: ignore[method-assign]
    harness.orchestrator._select_planner_skill_metadata = _fake_select  # type: ignore[method-assign]

    selected, _meta, available = harness._stepwise_selected_planner_skills(
        selection_query="plan",
    )
    assert {str(s["name"]) for s in selected} == {"a", "b"}
    assert {str(s["name"]) for s in available} == {"a", "b"}


def test_stepwise_prompt_body_surfaces_forbidden_tools_block(tmp_path: Path) -> None:
    """When tools are excluded the prompt body names them as forbidden.

    A soft "Suggested next tool" hint alone did not stop qwen3.6 from
    re-proposing an already-completed tool in exp14. Naming the forbidden
    tools explicitly gives the LLM an unambiguous constraint that
    complements the hint.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {"thought_process": "", "plan": []}
    harness.run["step_statuses"] = []
    harness.run["analysis_spec"] = {}

    body = harness._stepwise_prompt_body(
        contract={},
        contract_progress={},
        turn_num=5,
        retry_reason="",
        excluded_tool_names={"spades_assemble"},
    )
    assert "Forbidden tools for this turn" in body
    assert "spades_assemble" in body
    # The body also normally includes the pending-work hint, both are useful.
    # When nothing is forbidden, the block is absent.
    body_empty = harness._stepwise_prompt_body(
        contract={},
        contract_progress={},
        turn_num=5,
        retry_reason="",
        excluded_tool_names=set(),
    )
    assert "Forbidden tools for this turn" not in body_empty


def test_stepwise_required_arg_check_rejects_missing_required(
    tmp_path: Path, monkeypatch
) -> None:
    """The stepwise validator must reject a candidate missing a required argument.

    Without this check the executor preflight was the only layer catching
    missing required args (observed in exp15: prokka_annotate without
    ``sample_prefix`` accepted by stepwise, rejected by preflight, re-proposed
    in the next turn, loop). Catching it in the stepwise attempt loop lets the
    specific missing-argument name feed straight into ``retry_reason`` so the
    LLM course-corrects within the same turn.
    """

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "passed": True,
        "missing_capabilities": [],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_args, **_kwargs: [])
    # Short-circuit protocol-validation so it never rejects before our check runs.
    harness._assess_stepwise_protocol_candidate = lambda **_kwargs: {  # type: ignore[method-assign]
        "passed": True,
        "complete": False,
        "validation_mode": "stepwise_prefix",
        "hard_issues": [],
        "regressed": False,
        "pending_required_tools": [],
        "pending_plan_signals": [],
        "pending_soft_issues": [],
        "full_validation": {"passed": False, "issues": []},
    }

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "annotate the assembly",
            "plan": [
                {
                    "tool_name": "prokka_annotate",
                    "arguments": {
                        # prokka_annotate requires `sample_prefix` but the
                        # LLM often forgets it. Missing this should trigger
                        # a clear in-turn rejection naming the missing arg.
                        "input_fasta": str(tmp_path / "assembly" / "contigs.fasta"),
                        "output_dir": str(tmp_path / "annotation"),
                    },
                }
            ],
        },
    )

    assert accepted is False
    assert "missing required arguments" in reason
    assert "sample_prefix" in reason
    assert "prokka_annotate" in reason


def test_stepwise_required_arg_check_passes_when_all_args_present(
    tmp_path: Path, monkeypatch
) -> None:
    """With every required argument supplied the candidate must pass the check."""

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "passed": True,
        "missing_capabilities": [],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_args, **_kwargs: [])
    harness._assess_stepwise_protocol_candidate = lambda **_kwargs: {  # type: ignore[method-assign]
        "passed": True,
        "complete": False,
        "validation_mode": "stepwise_prefix",
        "hard_issues": [],
        "regressed": False,
        "pending_required_tools": [],
        "pending_plan_signals": [],
        "pending_soft_issues": [],
        "full_validation": {"passed": False, "issues": []},
    }
    # Fix #22b: the ``input_fasta`` path is synthetic for this test — the
    # file is never materialized on disk. Bypass the missing-inputs guard
    # so this test continues to validate the required-arg coverage path.
    harness._stepwise_missing_candidate_inputs = lambda **_kwargs: []  # type: ignore[method-assign]

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "annotate",
            "plan": [
                {
                    "tool_name": "prokka_annotate",
                    "arguments": {
                        "input_fasta": str(tmp_path / "assembly" / "contigs.fasta"),
                        "output_dir": str(tmp_path / "annotation"),
                        "sample_prefix": "ancestor",
                    },
                }
            ],
        },
    )

    assert accepted is True, reason
    assert reason == ""


def test_stepwise_required_arg_helper_returns_empty_for_valid_plan(
    tmp_path: Path,
) -> None:
    """The helper returns an empty string when the plan has no violations."""

    harness = _build_harness(tmp_path)
    plan = {
        "plan": [
            {
                "tool_name": "prokka_annotate",
                "step_id": 2,
                "arguments": {
                    "input_fasta": "/tmp/c.fa",
                    "output_dir": "/tmp/out",
                    "sample_prefix": "s",
                },
            }
        ]
    }
    assert harness._stepwise_required_arg_rejection_reason(plan=plan) == ""


def test_stepwise_required_arg_helper_only_flags_last_step(tmp_path: Path) -> None:
    """Violations in the immutable prefix must not block every attempt.

    A prior step's missing argument cannot be fixed by re-planning (the
    prefix is immutable history). The helper therefore restricts its
    rejection to findings on the last (candidate) step.
    """

    harness = _build_harness(tmp_path)
    # Prefix step 1 deliberately misses `sample_prefix`; candidate step 2 is OK.
    plan = {
        "plan": [
            {
                "tool_name": "prokka_annotate",
                "step_id": 1,
                "arguments": {
                    "input_fasta": "/tmp/a.fa",
                    "output_dir": "/tmp/out1",
                    # missing sample_prefix
                },
            },
            {
                "tool_name": "prokka_annotate",
                "step_id": 2,
                "arguments": {
                    "input_fasta": "/tmp/b.fa",
                    "output_dir": "/tmp/out2",
                    "sample_prefix": "s2",
                },
            },
        ]
    }
    # No finding on the last step → empty, despite a finding on the prefix.
    assert harness._stepwise_required_arg_rejection_reason(plan=plan) == ""


def test_stepwise_prompt_body_forbidden_block_sorted_and_stable(tmp_path: Path) -> None:
    """Forbidden list ordering must be deterministic so prompt-cache hits stay warm."""

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {"thought_process": "", "plan": []}
    harness.run["step_statuses"] = []
    harness.run["analysis_spec"] = {}

    # Pass the set in different insertion orders; the rendered block must match.
    body_a = harness._stepwise_prompt_body(
        contract={},
        contract_progress={},
        turn_num=1,
        retry_reason="",
        excluded_tool_names={"spades_assemble", "bwa_mem_align"},
    )
    body_b = harness._stepwise_prompt_body(
        contract={},
        contract_progress={},
        turn_num=1,
        retry_reason="",
        excluded_tool_names={"bwa_mem_align", "spades_assemble"},
    )
    # Extract the forbidden line from each and compare it alone, since the
    # full body includes non-deterministic nested JSON serialization of
    # passed dicts in some environments.
    def _forbidden_line(text: str) -> str:
        for line in text.splitlines():
            if line.startswith("Forbidden tools for this turn"):
                return line
        return ""

    assert _forbidden_line(body_a) == _forbidden_line(body_b)
    # And the tools appear in sorted order: `bwa_mem_align` before `spades_assemble`.
    forbidden_line = _forbidden_line(body_a)
    assert forbidden_line.index("bwa_mem_align") < forbidden_line.index("spades_assemble")


def test_stepwise_duplicate_equivalent_step_masks_repeated_tool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """When semantic validation rejects with ``duplicate_equivalent_step``,
    the proposed tool is added to the progressive mask.

    Without this, the LLM keeps re-emitting identical bash_run candidates
    after its first bash attempt fails, the semantic validator blocks
    each retry on the same ``duplicate_equivalent_step`` finding, and the
    turn exhausts its attempts without ever producing a usable step.
    Masking the repeated tool forces the planner to emit a different
    tool (e.g. a protocol step) on the next attempt.
    """

    harness = _build_harness(tmp_path)
    # One completed prefix step, failed bash_run at step 2, candidate bash_run
    # proposed for step 3 with the same canonical command.
    harness.run["analysis_spec"] = {}
    completed = [
        {
            "tool_name": "spades_assemble",
            "step_id": 1,
            "arguments": {"reads_1": "/d/R1.fq.gz", "reads_2": "/d/R2.fq.gz", "output_dir": "/o/a"},
        },
    ]
    harness.run["plan"] = {"thought_process": "", "plan": completed}
    harness.run["step_statuses"] = ["completed"]

    # Stub heavy helpers; we only want to observe that excluded_tool_names
    # grows when the rejection mentions ``duplicate_equivalent_step``.
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_a, **_k: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": [],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    harness._assess_stepwise_protocol_candidate = lambda **_k: {"passed": True}  # type: ignore[method-assign]
    harness._stepwise_required_arg_rejection_reason = lambda **_k: ""  # type: ignore[method-assign]
    # Semantic validation flips passed=False with duplicate_equivalent_step
    # every time a bash_run candidate is evaluated.
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (
            kwargs["plan"],
            {
                "passed": False,
                "issues": [
                    {
                        "issue": "duplicate_equivalent_step",
                        "step_id": 3,
                        "related_step_id": 2,
                    }
                ],
            },
            [],
        ),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_a, **_k: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_a, **_k: [])

    # Track the excluded_tool_names set passed on each attempt.
    observed_excluded_per_attempt: list[list[str]] = []

    available_skills = [
        {"name": "bash_run"},
        {"name": "bwa_mem_align"},
    ]

    monkeypatch.setattr(
        harness.orchestrator,
        "_available_skill_metadata",
        lambda: [dict(item) for item in available_skills],
    )

    def _fake_selected_skills(**kwargs):
        excluded = kwargs.get("excluded_tool_names") or set()
        observed_excluded_per_attempt.append(sorted(str(x) for x in excluded))
        remaining = [s for s in available_skills if s["name"] not in excluded]
        return (
            remaining,
            {"selected_skill_names": [s["name"] for s in remaining], "selected_skills": len(remaining), "budget": len(remaining)},
            remaining,
        )

    harness._stepwise_selected_planner_skills = _fake_selected_skills  # type: ignore[method-assign]

    # Always emit a bash_run candidate. After Fix #9, the excluded_tool_names
    # set should grow to include "bash_run" after the first attempt so later
    # attempts receive a shrunk skill list.
    def _fake_attempt(**_kwargs):
        return (
            {
                "thought_process": "bash fixup",
                "plan": [
                    {
                        "tool_name": "bash_run",
                        "step_id": 3,
                        "arguments": {"command": "echo hello"},
                    }
                ],
            },
            0.01,
        )

    harness._planner_attempt_with_heartbeat = _fake_attempt  # type: ignore[method-assign]
    # Minimum 3 attempts so we see the mask grow across attempts.
    monkeypatch.setenv("BIO_HARNESS_STEPWISE_PLANNER_ATTEMPTS_PER_TURN", "3")

    # The turn should ultimately fail (all attempts rejected), but by the
    # second attempt, bash_run must already be in the excluded set.
    try:
        harness._plan_next_step_turn(contract={}, turn_num=1)
    except ValueError:
        pass

    # Attempt 1 saw an empty excluded set; attempts 2+ must include bash_run.
    assert observed_excluded_per_attempt, "No attempts observed"
    assert observed_excluded_per_attempt[0] == [], (
        "First attempt should start with empty mask; got "
        f"{observed_excluded_per_attempt[0]}"
    )
    assert any("bash_run" in excluded for excluded in observed_excluded_per_attempt[1:]), (
        "bash_run should have been added to the mask after the first "
        "duplicate_equivalent_step rejection, but the observed sets were: "
        f"{observed_excluded_per_attempt}"
    )


def test_stepwise_multistep_truncation_skips_completed_prefix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Multi-step LLM output truncated to first step PAST the completed prefix.

    The LLM often re-emits its original full plan on every turn. If the
    harness always takes ``steps[0]`` from that list, it will pick the
    already-completed first step (e.g. ``spades_assemble``) on every
    attempt. Duplicate detection then rejects each attempt, the tool is
    masked for subsequent attempts, the LLM re-emits the same plan with
    the same first step, and the turn exhausts attempts with no forward
    progress. This test verifies the truncation walks the returned steps
    and selects the first one whose signature is not a completed
    duplicate, which is the LLM's implicit "next" action.
    """

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    # Four steps already completed: spades, prokka, bwa_anc, freebayes_anc.
    completed_plan_steps = [
        {
            "tool_name": "spades_assemble",
            "step_id": 1,
            "arguments": {
                "reads_1": "/d/anc_R1.fq.gz",
                "reads_2": "/d/anc_R2.fq.gz",
                "output_dir": "/o/assembly",
            },
        },
        {
            "tool_name": "prokka_annotate",
            "step_id": 2,
            "arguments": {
                "input_fasta": "/o/assembly/spades.fasta",
                "output_dir": "/o/annotation",
                "sample_prefix": "anc",
            },
        },
        {
            "tool_name": "bwa_mem_align",
            "step_id": 3,
            "arguments": {
                "reads_1": "/d/anc_R1.fq.gz",
                "reads_2": "/d/anc_R2.fq.gz",
                "reference_fasta": "/o/assembly/spades.fasta",
                "output_bam": "/o/align/anc.bam",
            },
        },
        {
            "tool_name": "freebayes_call",
            "step_id": 4,
            "arguments": {
                "input_bam": "/o/align/anc.bam",
                "reference_fasta": "/o/assembly/spades.fasta",
                "output_vcf": "/o/variants/anc.vcf",
                "ploidy": 1,
            },
        },
    ]
    harness.run["plan"] = {"thought_process": "", "plan": completed_plan_steps}
    harness.run["step_statuses"] = ["completed", "completed", "completed", "completed"]

    # Stub orchestrator selection + expensive validation helpers.
    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_a, **_k: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": [],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_a, **_k: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_a, **_k: [])
    # Neutralize protocol + required-arg gates so this test focuses on
    # truncation behavior.
    harness._assess_stepwise_protocol_candidate = lambda **_k: {"passed": True}  # type: ignore[method-assign]
    harness._stepwise_required_arg_rejection_reason = lambda **_k: ""  # type: ignore[method-assign]
    # Fix #22b: the synthetic ``/d/evol1_*.fq.gz`` paths do not exist on
    # disk in this test; bypass the missing-inputs guard to keep the focus
    # on truncation.
    harness._stepwise_missing_candidate_inputs = lambda **_k: []  # type: ignore[method-assign]

    monkeypatch.setattr(
        harness.orchestrator,
        "_available_skill_metadata",
        lambda: [],
    )
    monkeypatch.setattr(
        harness.orchestrator,
        "_select_planner_skill_metadata",
        lambda *_a, **_k: ([], {"selected_skill_names": [], "selected_skills": 0, "budget": 0}),
    )

    # Simulate the LLM returning the full 13-step original plan: first four
    # steps duplicate the completed prefix, step[4] is the new evol1 bwa.
    evol1_next_step = {
        "tool_name": "bwa_mem_align",
        "step_id": 5,
        "arguments": {
            "reads_1": "/d/evol1_R1.fq.gz",
            "reads_2": "/d/evol1_R2.fq.gz",
            "reference_fasta": "/o/assembly/spades.fasta",
            "output_bam": "/o/align/evol1.bam",
        },
    }
    full_plan = completed_plan_steps + [evol1_next_step] + [
        {
            "tool_name": "freebayes_call",
            "step_id": 6,
            "arguments": {"input_bam": "/o/align/evol1.bam", "output_vcf": "/o/variants/evol1.vcf"},
        },
    ]

    def _fake_attempt(**_kwargs):
        return (
            {
                "thought_process": "full plan",
                "plan": [dict(step) for step in full_plan],
            },
            0.01,
        )

    harness._planner_attempt_with_heartbeat = _fake_attempt  # type: ignore[method-assign]

    decision = harness._plan_next_step_turn(contract={}, turn_num=1)

    # Accepted, and the accepted step is evol1's bwa_mem_align — NOT
    # steps[0] (spades, which was completed as step 1).
    assert decision["status"] == "step"
    accepted_plan = decision["candidate_plan"]
    accepted_steps = accepted_plan.get("plan", [])
    assert len(accepted_steps) == 1
    chosen = accepted_steps[0]
    assert chosen["tool_name"] == "bwa_mem_align"
    assert chosen["arguments"]["reads_1"] == "/d/evol1_R1.fq.gz"
    pending_steps = decision.get("pending_candidate_steps", [])
    assert len(pending_steps) == 1
    assert pending_steps[0]["tool_name"] == "freebayes_call"
    assert "step_id" not in pending_steps[0]


def test_stepwise_pending_tail_accepts_evol2_norm_without_planner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A cached tail step is revalidated before asking the LLM again."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)
    _allow_synthetic_stepwise_candidate(harness, monkeypatch)
    selected_dir = harness.cfg.selected_dir
    harness.run["stepwise_pending_candidate_steps"] = [
        {
            "step_id": 99,
            "depends_on": [98],
            "tool_name": "bcftools_norm_run",
            "branch_id": "evol2",
            "arguments": {
                "input_vcf": str(selected_dir / "variants/evol2.annotated.vcf"),
                "output_vcf": str(selected_dir / "variants/evol2.normalized.vcf"),
            },
        },
        {
            "step_id": 100,
            "depends_on": [99],
            "tool_name": "shared_variants_export_run",
            "arguments": {
                "input_vcfs": [
                    str(selected_dir / "variants/evol1.normalized.vcf"),
                    str(selected_dir / "variants/evol2.normalized.vcf"),
                ],
                "output_csv": str(selected_dir / "variants/variants_shared.csv"),
            },
        },
    ]

    def _fail_if_planner_called(**_kwargs):
        raise AssertionError("planner should not run while a valid cached tail exists")

    harness._planner_attempt_with_heartbeat = _fail_if_planner_called  # type: ignore[method-assign]

    decision = harness._plan_next_step_turn(contract={}, turn_num=17)

    assert decision["status"] == "step"
    chosen = decision["candidate_plan"]["plan"][0]
    assert chosen["tool_name"] == "bcftools_norm_run"
    assert chosen["branch_id"] == "evol2"
    assert "step_id" not in chosen
    assert "depends_on" not in chosen
    assert decision["attempts"][0]["source"] == "pending_candidate_tail"
    remaining = decision.get("pending_candidate_steps", [])
    assert len(remaining) == 1
    assert remaining[0]["tool_name"] == "shared_variants_export_run"
    assert "step_id" not in remaining[0]
    assert "depends_on" not in remaining[0]


def test_stepwise_frontier_restricts_planner_tools_to_expected_wrapper(
    tmp_path: Path,
) -> None:
    """A hard branch frontier should narrow the planner's visible tool set."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)
    available = [
        {"name": "spades_assemble"},
        {"name": "freebayes_call"},
        {"name": "bcftools_norm_run"},
        {"name": "shared_variants_export_run"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]
    harness.orchestrator._select_planner_skill_metadata = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (list(available), {"selected_skill_names": [item["name"] for item in available]})
    )

    allowed = harness._stepwise_branch_frontier_allowed_tool_names()
    selected, meta, filtered_available = harness._stepwise_selected_planner_skills(
        selection_query="frontier",
        allowed_tool_names=allowed,
    )
    prompt = harness._stepwise_prompt(
        contract={},
        contract_progress={"passed": False},
        turn_num=17,
        recommended_skills=selected,
        available_skills=filtered_available,
        allowed_tool_names=allowed,
    )

    assert allowed == {"bcftools_norm_run"}
    assert [skill["name"] for skill in selected] == ["bcftools_norm_run"]
    assert [skill["name"] for skill in filtered_available] == ["bcftools_norm_run"]
    assert meta["hard_allowed_tool_names"] == ["bcftools_norm_run"]
    assert "The current unmet harness requirement restricts this turn" in prompt
    assert "Required tools for this turn: `bcftools_norm_run`" in prompt
    assert '"shared_variants_export_run"' not in prompt


def test_stepwise_rna_seq_sample_frontier_blocks_premature_featurecounts(
    tmp_path: Path,
) -> None:
    """RNA-seq sample alignment frontier should be a hard gate before counting."""

    harness = _build_harness(tmp_path)
    _stage_rna_seq_de_inputs(harness)
    selected_dir = harness.cfg.selected_dir
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "subread_align",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments" / "biofilm1.bam"),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "Count reads now.",
            "plan": [
                {
                    "tool_name": "featurecounts_run",
                    "arguments": {},
                }
            ],
        },
    )

    assert accepted is False
    assert "Candidate does not advance the current sample-stage frontier" in reason
    assert "branch=plankton1, stage=aligned_bam, tool=subread_align" in reason
    assert "feature counting" in reason


def test_stepwise_rna_seq_sample_frontier_restricts_planner_to_subread_align(
    tmp_path: Path,
) -> None:
    """After one RNA-seq sample alignment, only missing sample alignment is visible."""

    harness = _build_harness(tmp_path)
    _stage_rna_seq_de_inputs(harness)
    selected_dir = harness.cfg.selected_dir
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "subread_align",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments" / "biofilm1.bam"),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"]
    available = [
        {"name": "subread_align"},
        {"name": "featurecounts_run"},
        {"name": "deseq2_run"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]
    harness.orchestrator._select_planner_skill_metadata = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (list(available), {"selected_skill_names": [item["name"] for item in available]})
    )

    allowed = harness._stepwise_branch_frontier_allowed_tool_names()
    bound = harness._stepwise_rebind_candidate_step_for_gate(
        {"tool_name": "subread_align", "arguments": {}}
    )
    selected, meta, filtered_available = harness._stepwise_selected_planner_skills(
        selection_query="frontier",
        allowed_tool_names=allowed,
    )

    assert allowed == {"subread_align"}
    assert bound["branch_id"] == "plankton1"
    assert bound["arguments"]["output_bam"] == str(selected_dir / "alignments" / "plankton1.bam")
    assert harness._stepwise_branch_stage_rejection_reason(candidate_step=bound) == ""
    assert [skill["name"] for skill in selected] == ["subread_align"]
    assert [skill["name"] for skill in filtered_available] == ["subread_align"]
    assert meta["hard_allowed_tool_names"] == ["subread_align"]

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "Align plankton1 next.",
            "plan": [
                {
                    "tool_name": "subread_align",
                    "arguments": {
                        "reads_1": str(harness.cfg.data_root / "plankton1_1.fastq"),
                        "reads_2": str(harness.cfg.data_root / "plankton1_2.fastq"),
                        "output_bam": str(selected_dir / "alignments" / "plankton1.bam"),
                    },
                }
            ],
        },
    )
    assert accepted is True
    assert reason == ""


def test_stepwise_contract_gap_restricts_tools_after_branch_frontier(
    tmp_path: Path,
) -> None:
    """After branch work is complete, missing contract capabilities drive tools."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)
    selected_dir = harness.cfg.selected_dir
    harness.run["plan"]["plan"].append(
        {
            "step_id": 15,
            "tool_name": "bcftools_norm_run",
            "branch_id": "evol2",
            "arguments": {
                "input_vcf": str(selected_dir / "variants/evol2.annotated.vcf"),
                "output_vcf": str(selected_dir / "variants/evol2.normalized.vcf"),
            },
        }
    )
    harness.run["step_statuses"].append("completed")
    available = [
        {"name": "spades_assemble"},
        {"name": "shared_variants_export_run"},
        {"name": "bcftools_norm_run"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]
    harness.orchestrator._select_planner_skill_metadata = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (list(available), {"selected_skill_names": [item["name"] for item in available]})
    )
    contract_progress = {
        "passed": False,
        "missing_capabilities": ["shared_variant_export"],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
    }

    allowed = harness._stepwise_branch_frontier_allowed_tool_names()
    contract_allowed = harness._stepwise_contract_allowed_tool_names(
        contract_progress=contract_progress,
    )
    selected, meta, filtered_available = harness._stepwise_selected_planner_skills(
        selection_query="contract export",
        allowed_tool_names=contract_allowed,
    )

    assert allowed == set()
    assert contract_allowed == {"shared_variants_export_run"}
    assert [skill["name"] for skill in selected] == ["shared_variants_export_run"]
    assert [skill["name"] for skill in filtered_available] == ["shared_variants_export_run"]
    assert meta["hard_allowed_tool_names"] == ["shared_variants_export_run"]


def test_stepwise_compiled_seed_restricts_tools_before_contract_gap(
    tmp_path: Path,
) -> None:
    """A compiled workflow seed should outrank broad missing-capability tools."""

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {
        "analysis_type": "viral_metagenomics",
        "benchmark_policy": "scientific_harness",
        "preferred_tools": ["fastp_run", "bash_run"],
        "protocol_grounding": {
            "analysis_family": "viral_metagenomics",
            "execution_mode": "compiled_pipeline",
            "input_mode": "raw_fastq",
        },
        "execution_contract": {
            "execution_mode": "compiled_pipeline",
            "compatible_tools": [],
        },
        "plan_skeleton": [
            [
                "fastp_run",
                "Quality trim paired-end reads before viral classification",
                {"length_required": 30, "detect_adapter_for_pe": True},
            ],
            [
                "bash_run",
                "Classify trimmed reads against the staged viral reference panel",
                {
                    "tool": "python3",
                    "helper_script": "/repo/bio_harness/pipeline_scripts/classify_viral_reads_kmer.py",
                },
            ],
        ],
    }
    available = [
        {"name": "fastp_run"},
        {"name": "bash_run"},
        {"name": "minimap2_align"},
        {"name": "bowtie2_align"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]
    harness.orchestrator._select_planner_skill_metadata = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (list(available), {"selected_skill_names": [item["name"] for item in available]})
    )
    contract_progress = {
        "passed": False,
        "missing_capabilities": ["alignment", "reference_inputs"],
        "missing_required_tool_hints": [],
        "missing_tool_hints": ["minimap2"],
    }

    allowed = harness._stepwise_current_allowed_tool_names(
        contract_progress=contract_progress,
    )
    selected, meta, filtered_available = harness._stepwise_selected_planner_skills(
        selection_query="viral seed",
        allowed_tool_names=allowed,
    )

    assert allowed == {"fastp_run"}
    assert [skill["name"] for skill in selected] == ["fastp_run"]
    assert [skill["name"] for skill in filtered_available] == ["fastp_run"]
    assert meta["hard_allowed_tool_names"] == ["fastp_run"]

    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "fastp_run",
                "arguments": {},
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    assert harness._stepwise_current_allowed_tool_names(
        contract_progress=contract_progress,
    ) == {"bash_run"}


def test_stepwise_compiled_seed_accepts_rich_completed_status_records(
    tmp_path: Path,
) -> None:
    """Persisted status records should consume completed skeleton rows."""

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {
        "analysis_type": "viral_metagenomics",
        "benchmark_policy": "scientific_harness",
        "preferred_tools": ["fastp_run", "bash_run"],
        "protocol_grounding": {
            "analysis_family": "viral_metagenomics",
            "execution_mode": "compiled_pipeline",
            "input_mode": "raw_fastq",
        },
        "execution_contract": {
            "execution_mode": "compiled_pipeline",
            "compatible_tools": [],
        },
        "plan_skeleton": [
            [
                "fastp_run",
                "Quality trim paired-end reads before viral classification",
                {"length_required": 30, "detect_adapter_for_pe": True},
            ],
            [
                "bash_run",
                "Classify trimmed reads against the staged viral reference panel",
                {
                    "tool": "python3",
                    "helper_script": "/repo/bio_harness/pipeline_scripts/classify_viral_reads_kmer.py",
                },
            ],
        ],
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "fastp_run",
                "arguments": {},
            }
        ],
    }
    harness.run["step_statuses"] = [
        {
            "exit_code": 0,
            "status": "completed",
            "step_id": 1,
            "tool_name": "fastp_run",
        }
    ]
    contract_progress = {
        "passed": False,
        "missing_capabilities": ["alignment", "reference_inputs"],
        "missing_required_tool_hints": [],
        "missing_tool_hints": ["minimap2"],
    }

    assert harness._stepwise_current_allowed_tool_names(
        contract_progress=contract_progress,
    ) == {"bash_run"}
    assert (
        harness._stepwise_workflow_seed_tool_rejection_reason(
            candidate_step={"tool_name": "bash_run", "arguments": {}},
        )
        == ""
    )


def test_stepwise_compiled_sv_raw_reads_allow_minimap2_before_sniffles(
    tmp_path: Path,
) -> None:
    """Raw-read SV seeds should permit alignment before Sniffles calling."""

    harness = _build_harness(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    reads_path = data_dir / "reads.fastq"
    reference_path = data_dir / "ref.fasta"
    reads_path.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    reference_path.write_text(">ref\nACGT\n", encoding="utf-8")
    harness.run["analysis_spec"] = {
        "analysis_type": "structural_variant_calling",
        "benchmark_policy": "scientific_harness",
        "preferred_tools": ["sniffles_sv_call"],
        "protocol_grounding": {
            "grounded": True,
            "analysis_family": "structural_variant_calling",
            "execution_mode": "compiled_pipeline",
            "required_tools": ["sniffles_sv_call"],
        },
        "execution_contract": {
            "execution_mode": "compiled_pipeline",
            "compatible_tools": [],
        },
        "plan_skeleton": [
            [
                "sniffles_sv_call",
                "Call structural variants from the aligned long-read BAM",
                {"min_support": 3, "min_sv_length": 50, "threads": 4},
            ],
        ],
        "discovered_data_files": [
            {"name": reads_path.name, "path": str(reads_path)},
            {"name": reference_path.name, "path": str(reference_path)},
        ],
        "file_manifest": {
            "entries": [
                {
                    "role": "input_fastq_r1",
                    "resolved_path": str(reads_path),
                    "file_type": "fastq",
                },
                {
                    "role": "input_fasta",
                    "resolved_path": str(reference_path),
                    "file_type": "fasta",
                },
            ],
        },
    }
    available = [{"name": "sniffles_sv_call"}, {"name": "minimap2_align"}]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]
    contract_progress = {
        "passed": False,
        "missing_capabilities": [
            "alignment",
            "reference_inputs",
            "structural_variant_calling",
        ],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
    }

    assert harness._stepwise_current_allowed_tool_names(
        contract_progress=contract_progress,
    ) == {"minimap2_align", "sniffles_sv_call"}
    assert harness._stepwise_workflow_seed_tool_rejection_reason(
        candidate_step={"tool_name": "minimap2_align", "arguments": {}},
    ) == ""
    assert harness._stepwise_workflow_seed_tool_rejection_reason(
        candidate_step={"tool_name": "sniffles_sv_call", "arguments": {}},
    ) == ""


def test_stepwise_single_step_compiler_fills_empty_salmon_candidate(
    tmp_path: Path,
) -> None:
    """A same-tool compiler may fill missing direct-wrapper arguments."""

    harness = _build_harness(tmp_path)
    _stage_transcript_quant_inputs(harness)

    rebound = harness._stepwise_rebind_candidate_step_for_gate(
        {"tool_name": "salmon_quant", "arguments": {}}
    )
    args = rebound["arguments"]

    assert rebound["tool_name"] == "salmon_quant"
    assert args["transcriptome_fasta"] == str(harness.cfg.data_root / "transcriptome.fa")
    assert args["reads_1"] == str(harness.cfg.data_root / "reads_1.fq.gz")
    assert args["reads_2"] == str(harness.cfg.data_root / "reads_2.fq.gz")
    assert args["index_dir"] == str(harness.cfg.selected_dir / "salmon_index")
    assert args["output_dir"] == str(harness.cfg.selected_dir / "salmon_quant")
    assert args["threads"] == 4


def test_stepwise_single_step_compiler_preserves_explicit_salmon_values(
    tmp_path: Path,
) -> None:
    """Compiler rebinding should fill gaps without overwriting explicit args."""

    harness = _build_harness(tmp_path)
    _stage_transcript_quant_inputs(harness)
    explicit_output = str(harness.cfg.selected_dir / "custom_salmon_out")

    rebound = harness._stepwise_rebind_candidate_step_for_gate(
        {
            "tool_name": "salmon_quant",
            "arguments": {
                "output_dir": explicit_output,
            },
        }
    )
    args = rebound["arguments"]

    assert args["output_dir"] == explicit_output
    assert args["reads_1"] == str(harness.cfg.data_root / "reads_1.fq.gz")
    assert args["reads_2"] == str(harness.cfg.data_root / "reads_2.fq.gz")
    assert args["index_dir"] == str(harness.cfg.selected_dir / "salmon_index")
    assert args["threads"] == 4


def test_stepwise_fanout_seed_allows_post_branch_producer_before_contract_gap(
    tmp_path: Path,
) -> None:
    """Fan-out branch steps should not skip required downstream producers."""

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    sample_ids = [
        "SRR1278968",
        "SRR1278969",
        "SRR1278970",
        "SRR1278971",
        "SRR1278972",
        "SRR1278973",
    ]
    harness.run["analysis_spec"] = {
        "analysis_type": "rna_seq_differential_expression",
        "protocol_grounding": {
            "grounded": True,
            "analysis_family": "rna_seq_differential_expression",
            "execution_mode": "direct_wrapper",
            "required_tools": ["featurecounts_run", "deseq2_run"],
        },
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": [],
        },
        "plan_skeleton": [
            ["subread_align", "Align RNA-seq samples"],
            ["featurecounts_run", "Generate the gene count matrix"],
            ["deseq2_run", "Run differential expression analysis"],
        ],
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": index,
                "tool_name": "subread_align",
                "branch_id": sample_id,
                "arguments": {
                    "output_bam": str(selected_dir / "alignments" / f"{sample_id}.bam"),
                },
            }
            for index, sample_id in enumerate(sample_ids, start=1)
        ],
    }
    harness.run["step_statuses"] = ["completed"] * len(sample_ids)
    available = [
        {"name": "subread_align"},
        {"name": "featurecounts_run"},
        {"name": "deseq2_run"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]
    contract_progress = {
        "passed": False,
        "missing_capabilities": ["differential_analysis"],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
    }

    assert harness._stepwise_current_allowed_tool_names(
        contract_progress=contract_progress,
    ) == {"featurecounts_run"}
    assert harness._stepwise_workflow_seed_tool_rejection_reason(
        candidate_step={"tool_name": "featurecounts_run", "arguments": {}},
    ) == ""
    assert (
        "Expected next workflow seed tool is `featurecounts_run`"
        in harness._stepwise_workflow_seed_tool_rejection_reason(
            candidate_step={"tool_name": "deseq2_run", "arguments": {}},
        )
    )


def test_stepwise_grounded_direct_wrapper_uses_plan_skeleton_frontier(
    tmp_path: Path,
) -> None:
    """Grounded direct-wrapper specs should not skip support workflow stages."""

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "preferred_tools": [
            "freebayes_call",
            "snpeff_annotate",
            "shared_variants_export_run",
            "bwa_mem_align",
        ],
        "protocol_grounding": {
            "grounded": True,
            "analysis_family": "bacterial_evolution_variant_calling",
            "execution_mode": "direct_wrapper",
            "required_tools": [
                "freebayes_call",
                "snpeff_annotate",
                "shared_variants_export_run",
            ],
        },
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": [],
        },
        "plan_skeleton": [
            ["spades_assemble", "Assemble ancestor reads"],
            ["prokka_annotate", "Annotate assembled scaffolds"],
            ["bwa_mem_align", "Align ancestor reads to assembled scaffolds"],
            ["freebayes_call", "Call ancestor variants"],
        ],
    }
    harness.run["protocol_validation"] = {
        "missing_required_tools": [
            "freebayes_call",
            "snpeff_annotate",
            "shared_variants_export_run",
        ],
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {"step_id": 1, "tool_name": "spades_assemble", "arguments": {}},
            {"step_id": 2, "tool_name": "prokka_annotate", "arguments": {}},
        ],
    }
    harness.run["step_statuses"] = ["completed", "completed"]
    available = [
        {"name": "spades_assemble"},
        {"name": "prokka_annotate"},
        {"name": "bwa_mem_align"},
        {"name": "freebayes_call"},
        {"name": "snpeff_annotate"},
        {"name": "shared_variants_export_run"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]

    assert harness._stepwise_current_allowed_tool_names(
        contract_progress={},
    ) == {"bwa_mem_align"}


def test_stepwise_branch_frontier_outranks_repeated_seed_stage(
    tmp_path: Path,
) -> None:
    """Branch-local frontier work may fan out beyond compact skeleton rows."""

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "protocol_grounding": {
            "grounded": True,
            "analysis_family": "bacterial_evolution_variant_calling",
            "execution_mode": "direct_wrapper",
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": [],
        },
        "plan_skeleton": [
            ["spades_assemble", "Assemble ancestor reads"],
            ["prodigal_annotate", "Annotate assembled scaffolds"],
            ["bwa_mem_align", "Align ancestor reads"],
            ["freebayes_call", "Call ancestor variants"],
            ["bwa_mem_align", "Align evolved lines"],
            ["freebayes_call", "Call evolved variants"],
            ["bcftools_filter_run", "Filter callsets"],
            ["bcftools_isec_run", "Subtract ancestor-supported sites"],
            ["snpeff_annotate", "Annotate evolved variants"],
            ["bcftools_norm_run", "Normalize annotated variants"],
            ["shared_variants_export_run", "Export shared variants"],
        ],
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {"step_id": 1, "tool_name": "spades_assemble", "arguments": {}},
            {"step_id": 2, "tool_name": "prodigal_annotate", "arguments": {}},
            {
                "step_id": 3,
                "tool_name": "bwa_mem_align",
                "branch_id": "ancestor",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments/anc_aligned.bam"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol1",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments/evol1_aligned.bam"),
                },
            },
            {
                "step_id": 5,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol2",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments/evol2_aligned.bam"),
                },
            },
            {
                "step_id": 6,
                "tool_name": "freebayes_call",
                "branch_id": "ancestor",
                "arguments": {
                    "output_vcf": str(selected_dir / "variants/anc_raw.vcf"),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"] * 6
    available = [
        {"name": "freebayes_call"},
        {"name": "bcftools_filter_run"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]

    candidate_step = {
        "tool_name": "freebayes_call",
        "branch_id": "evol1",
        "arguments": {
            "input_bam": str(selected_dir / "alignments/evol1_aligned.bam"),
            "output_vcf": str(selected_dir / "variants/evol1_raw.vcf"),
        },
    }

    assert harness._stepwise_current_allowed_tool_names(contract_progress={}) == {
        "freebayes_call",
    }
    assert harness._stepwise_branch_stage_rejection_reason(
        candidate_step=candidate_step,
    ) == ""
    assert harness._stepwise_workflow_seed_tool_rejection_reason(
        candidate_step=candidate_step,
    ) == ""


def test_stepwise_annotation_frontier_requires_gff_producer(
    tmp_path: Path,
) -> None:
    """Variant annotation should wait for a concrete reference GFF."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_both_isec_without_annotation(harness)
    selected_dir = harness.cfg.selected_dir
    for relative in (
        "assembly/scaffolds.fasta",
        "variants/evol1.ancestor_subtracted.vcf.gz",
        "variants/evol2.ancestor_subtracted.vcf.gz",
    ):
        path = selected_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    available = [
        {"name": "snpeff_annotate"},
        {"name": "prodigal_annotate"},
        {"name": "prokka_annotate"},
        {"name": "bcftools_norm_run"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]
    harness.orchestrator._select_planner_skill_metadata = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (list(available), {"selected_skill_names": [item["name"] for item in available]})
    )

    frontier_allowed = harness._stepwise_branch_frontier_allowed_tool_names()
    prerequisite_allowed = harness._stepwise_annotation_prerequisite_allowed_tool_names(
        frontier_allowed_tool_names=frontier_allowed,
    )
    selected, meta, filtered_available = harness._stepwise_selected_planner_skills(
        selection_query="annotation prerequisite",
        allowed_tool_names=prerequisite_allowed,
    )
    snpeff_step = {
        "tool_name": "snpeff_annotate",
        "branch_id": "evol1",
        "arguments": {
            "input_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"),
            "output_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
        },
    }
    prodigal_step = {
        "tool_name": "prodigal_annotate",
        "arguments": {
            "input_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
            "output_gff": str(selected_dir / "annotation/genes.gff"),
            "output_faa": str(selected_dir / "annotation/proteins.faa"),
        },
    }

    assert frontier_allowed == {"snpeff_annotate"}
    assert prerequisite_allowed == {"prodigal_annotate", "prokka_annotate"}
    assert [skill["name"] for skill in selected] == [
        "prodigal_annotate",
        "prokka_annotate",
    ]
    assert [skill["name"] for skill in filtered_available] == [
        "prodigal_annotate",
        "prokka_annotate",
    ]
    assert meta["hard_allowed_tool_names"] == [
        "prodigal_annotate",
        "prokka_annotate",
    ]
    assert "reference gene annotation GFF" in harness._stepwise_branch_stage_rejection_reason(
        candidate_step=snpeff_step,
    )
    assert "annotation/genes.gff" in "\n".join(
        harness._stepwise_missing_candidate_inputs(candidate_step=snpeff_step)
    )
    assert harness._stepwise_branch_stage_rejection_reason(
        candidate_step=prodigal_step,
    ) == ""


def test_stepwise_snpeff_uses_existing_prokka_gff(
    tmp_path: Path,
) -> None:
    """A completed Prokka GFF should satisfy the annotation frontier."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_both_isec_without_annotation(harness)
    selected_dir = harness.cfg.selected_dir
    harness.run["plan"]["plan"].append(
        {
            "step_id": 12,
            "tool_name": "prokka_annotate",
            "arguments": {
                "input_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                "output_dir": str(selected_dir / "annotation"),
                "sample_prefix": "ancestor",
            },
        }
    )
    harness.run["step_statuses"].append("completed")
    for relative in (
        "assembly/scaffolds.fasta",
        "variants/evol1.ancestor_subtracted.vcf.gz",
        "variants/evol2.ancestor_subtracted.vcf.gz",
        "annotation/ancestor.gff",
    ):
        path = selected_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    snpeff_step = {
        "tool_name": "snpeff_annotate",
        "branch_id": "evol1",
        "objective": "Annotate evol1 ancestor-subtracted variants.",
        "arguments": {
            "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
            "annotation_gff": str(selected_dir / "annotation/genes.gff"),
            "input_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"),
            "output_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
        },
    }

    assert harness._stepwise_reference_annotation_available() is True
    assert harness._stepwise_missing_candidate_inputs(
        candidate_step=snpeff_step,
    ) == []
    assert harness._stepwise_branch_stage_rejection_reason(
        candidate_step=snpeff_step,
    ) == ""


def test_stepwise_appended_binding_recovers_missing_analysis_spec(
    tmp_path: Path,
) -> None:
    """Runtime binding should survive state payloads without analysis_spec."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_both_isec_without_annotation(harness)
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {}
    harness.run["user_request"] = (
        "Identify shared variants in evolved E. coli lineages relative to the "
        "ancestor and annotate them."
    )
    harness.run["plan_contract"] = {
        "must_include_capabilities": [
            "genome_assembly",
            "alignment",
            "variant_calling",
            "annotation",
            "shared_variant_export",
        ]
    }
    harness.run["plan"]["plan"].append(
        {
            "step_id": 12,
            "tool_name": "prokka_annotate",
            "arguments": {
                "input_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                "output_dir": str(selected_dir / "annotation"),
                "sample_prefix": "ancestor",
            },
        }
    )
    harness.run["step_statuses"].append("completed")
    for relative in (
        "assembly/scaffolds.fasta",
        "annotation/ancestor.gff",
        "variants/evol1.ancestor_subtracted.vcf.gz",
    ):
        path = selected_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    plan = deepcopy(harness.run["plan"])
    plan["plan"].append(
        {
            "step_id": 13,
            "tool_name": "snpeff_annotate",
            "branch_id": "evol1",
            "objective": "Annotate evol1 ancestor-subtracted variants.",
            "arguments": {
                "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                "annotation_gff": str(selected_dir / "assembly/genes.gff"),
                "config_dir": str(selected_dir / "annotation/_snpeff"),
                "genome_db": "ancestor",
                "input_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"),
                "output_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
            },
        }
    )

    rebound_plan, meta = harness._stepwise_rebind_appended_candidate_step(
        plan=plan,
        existing_step_count=len(harness._stepwise_plan_steps()),
    )
    rebound_args = rebound_plan["plan"][-1]["arguments"]

    assert meta["changed"] is True
    assert harness._runtime_binding_analysis_spec()["analysis_type"] == (
        "bacterial_evolution_variant_calling"
    )
    assert rebound_args["annotation_gff"] == str(
        selected_dir / "annotation/ancestor.gff"
    )


def test_stepwise_candidate_accepts_live_file_manifest_without_prefix_mutation(
    tmp_path: Path,
) -> None:
    """Live file manifests must not rewrite the executed stepwise prefix."""

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    data_root = harness.cfg.data_root
    for path in (
        data_root / "anc_R1.fastq.gz",
        data_root / "anc_R2.fastq.gz",
        selected_dir / "assembly/scaffolds.fasta",
        selected_dir / "annotation/genes.gff",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "scientific_harness",
        "protocol_grounding": {
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
        "file_manifest": FileManifest.from_data_root(
            data_root,
            "bacterial_evolution_variant_calling",
            output_dir=str(selected_dir),
        ),
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_dir": str(selected_dir / "assembly"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "output_gff": str(selected_dir / "annotation/genes.gff"),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed", "completed"]

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "Align ancestor reads to the assembled reference.",
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "bwa_mem_align",
                    "branch_id": "ancestor",
                    "arguments": {
                        "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                        "reads_1": str(data_root / "anc_R1.fastq.gz"),
                        "reads_2": str(data_root / "anc_R2.fastq.gz"),
                        "output_bam": str(selected_dir / "alignments/anc_aligned.bam"),
                    },
                }
            ],
        },
    )

    assert accepted is True, reason


def test_stepwise_candidate_freezes_completed_prokka_prefix_during_snpeff(
    tmp_path: Path,
) -> None:
    """Stepwise SnpEff validation must not add late Prodigal prefix repairs."""

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    data_root = harness.cfg.data_root
    harness.run["benchmark_policy"] = "scientific_harness"
    harness.run["user_request"] = (
        "Identify and annotate genome variants in two evolved lines relative "
        "to an ancestor line of E. coli; report only variants shared by both "
        "evolved lines with moderate or higher predicted severity."
    )
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "scientific_harness",
        "protocol_grounding": {
            "min_variant_branches": 2,
            "requires_shared_comparison": True,
        },
    }
    for relative in (
        "anc_R1.fastq.gz",
        "anc_R2.fastq.gz",
        "evol1_R1.fastq.gz",
        "evol1_R2.fastq.gz",
        "evol2_R1.fastq.gz",
        "evol2_R2.fastq.gz",
    ):
        path = data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    for relative in (
        "assembly/scaffolds.fasta",
        "annotation/ancestor.gff",
        "alignments/anc_aligned.bam",
        "alignments/evol1_aligned.bam",
        "alignments/evol2_aligned.bam",
        "variants/anc_raw.vcf",
        "variants/evol1_raw.vcf",
        "variants/evol2_raw.vcf",
        "variants/anc.filtered.vcf.gz",
        "variants/evol1.filtered.vcf.gz",
        "variants/evol2.filtered.vcf.gz",
        "variants/evol1.ancestor_subtracted.vcf.gz",
        "variants/evol2.ancestor_subtracted.vcf.gz",
        "variants/evol1.annotated.vcf",
    ):
        path = selected_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_dir": str(selected_dir / "assembly"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "prokka_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "output_dir": str(selected_dir / "annotation"),
                    "sample_prefix": "ancestor",
                },
            },
            {
                "step_id": 3,
                "tool_name": "bwa_mem_align",
                "branch_id": "ancestor",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "alignments/anc_aligned.bam"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol1",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "reads_1": str(data_root / "evol1_R1.fastq.gz"),
                    "reads_2": str(data_root / "evol1_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "alignments/evol1_aligned.bam"),
                },
            },
            {
                "step_id": 5,
                "tool_name": "bwa_mem_align",
                "branch_id": "evol2",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "reads_1": str(data_root / "evol2_R1.fastq.gz"),
                    "reads_2": str(data_root / "evol2_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "alignments/evol2_aligned.bam"),
                },
            },
            {
                "step_id": 6,
                "tool_name": "freebayes_call",
                "branch_id": "ancestor",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "input_bam": str(selected_dir / "alignments/anc_aligned.bam"),
                    "output_vcf": str(selected_dir / "variants/anc_raw.vcf"),
                },
            },
            {
                "step_id": 7,
                "tool_name": "freebayes_call",
                "branch_id": "evol1",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "input_bam": str(selected_dir / "alignments/evol1_aligned.bam"),
                    "output_vcf": str(selected_dir / "variants/evol1_raw.vcf"),
                },
            },
            {
                "step_id": 8,
                "tool_name": "freebayes_call",
                "branch_id": "evol2",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "input_bam": str(selected_dir / "alignments/evol2_aligned.bam"),
                    "output_vcf": str(selected_dir / "variants/evol2_raw.vcf"),
                },
            },
            {
                "step_id": 9,
                "tool_name": "bcftools_filter_run",
                "branch_id": "ancestor",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants/anc_raw.vcf"),
                    "output_vcf": str(selected_dir / "variants/anc.filtered.vcf.gz"),
                },
            },
            {
                "step_id": 10,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol1",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants/evol1_raw.vcf"),
                    "output_vcf": str(selected_dir / "variants/evol1.filtered.vcf.gz"),
                },
            },
            {
                "step_id": 11,
                "tool_name": "bcftools_filter_run",
                "branch_id": "evol2",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants/evol2_raw.vcf"),
                    "output_vcf": str(selected_dir / "variants/evol2.filtered.vcf.gz"),
                },
            },
            {
                "step_id": 12,
                "tool_name": "bcftools_isec_run",
                "branch_id": "evol1",
                "arguments": {
                    "input_vcfs": [
                        str(selected_dir / "variants/evol1.filtered.vcf.gz"),
                        str(selected_dir / "variants/anc.filtered.vcf.gz"),
                    ],
                    "output_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"),
                },
            },
            {
                "step_id": 13,
                "tool_name": "bcftools_isec_run",
                "branch_id": "evol2",
                "arguments": {
                    "input_vcfs": [
                        str(selected_dir / "variants/evol2.filtered.vcf.gz"),
                        str(selected_dir / "variants/anc.filtered.vcf.gz"),
                    ],
                    "output_vcf": str(selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"),
                },
            },
            {
                "step_id": 14,
                "tool_name": "snpeff_annotate",
                "branch_id": "evol1",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                    "annotation_gff": str(selected_dir / "annotation/ancestor.gff"),
                    "config_dir": str(selected_dir / "annotation/_snpeff"),
                    "genome_db": "ancestor",
                    "input_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
                },
            },
        ],
    }
    harness.run["step_statuses"] = ["completed"] * 14

    accepted, payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "Annotate the remaining evolved branch.",
            "plan": [
                {
                    "step_id": 15,
                    "tool_name": "snpeff_annotate",
                    "branch_id": "evol2",
                    "arguments": {
                        "reference_fasta": str(selected_dir / "assembly/scaffolds.fasta"),
                        "annotation_gff": str(selected_dir / "annotation/ancestor.gff"),
                        "config_dir": str(selected_dir / "annotation/_snpeff"),
                        "genome_db": "ancestor",
                        "input_vcf": str(
                            selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
                        ),
                        "output_vcf": str(selected_dir / "variants/evol2.annotated.vcf"),
                    },
                }
            ],
        },
    )

    assert accepted is True, reason
    accepted_steps = payload["plan"]["plan"]
    assert "annotation_field" not in accepted_steps[13]["arguments"]
    assert "annotation_field" not in accepted_steps[14]["arguments"]


def test_stepwise_frontier_rebinds_wrong_branch_retry_to_expected_cell(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A wrong-branch retry should become the expected sibling frontier step."""

    harness = _build_harness(tmp_path)
    _set_evolution_prefix_after_evol1_norm(harness)
    _allow_synthetic_stepwise_candidate(harness, monkeypatch)
    available = [
        {"name": "bcftools_norm_run"},
        {"name": "shared_variants_export_run"},
    ]
    harness.orchestrator._available_skill_metadata = lambda: list(available)  # type: ignore[method-assign]
    harness.orchestrator._select_planner_skill_metadata = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (list(available), {"selected_skill_names": [item["name"] for item in available]})
    )
    selected_dir = harness.cfg.selected_dir
    seen_tool_sets: list[list[str]] = []

    def _fake_attempt(**kwargs):
        skills = kwargs.get("available_skills_metadata_override") or []
        seen_tool_sets.append([skill["name"] for skill in skills])
        if len(seen_tool_sets) == 1:
            return (
                {
                    "thought_process": "retry wrong branch",
                    "plan": [
                        {
                            "tool_name": "bcftools_norm_run",
                            "branch_id": "evol1",
                            "arguments": {
                                "input_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
                                "output_vcf": str(selected_dir / "variants/evol1.normalized.vcf"),
                            },
                        }
                    ],
                },
                0.01,
            )
        return (
            {
                "thought_process": "advance sibling branch",
                "plan": [
                    {
                        "tool_name": "bcftools_norm_run",
                        "branch_id": "evol2",
                        "arguments": {
                            "input_vcf": str(selected_dir / "variants/evol2.annotated.vcf"),
                            "output_vcf": str(selected_dir / "variants/evol2.normalized.vcf"),
                        },
                    }
                ],
            },
            0.01,
        )

    harness._planner_attempt_with_heartbeat = _fake_attempt  # type: ignore[method-assign]

    decision = harness._plan_next_step_turn(contract={}, turn_num=17)

    assert decision["status"] == "step"
    assert seen_tool_sets == [["bcftools_norm_run"]]
    assert decision["attempts"][0]["status"] == "accepted"
    accepted_step = decision["accepted_payload"]["plan"]["plan"][-1]
    assert accepted_step["branch_id"] == "evol2"
    assert accepted_step["arguments"]["input_vcf"] == str(
        selected_dir / "variants/evol2.annotated.vcf"
    )


def test_stepwise_multistep_truncation_skips_excluded_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Truncation must also skip steps whose tool is in the exclusion mask.

    Fix #9 masks ``bash_run`` after a ``duplicate_equivalent_step``
    rejection, but Fix #8's truncation only honored the completed-prefix
    signature set. If the LLM's multi-step response still contained
    ``bash_run`` earlier than the next valid tool, Fix #8 would select
    it, the semantic validator would reject on the same grounds, and the
    turn livelocks. This test verifies that truncation walks past
    masked-tool steps when picking the next candidate.
    """

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    # One completed spades step; bash_run#5 succeeded previously; bash_run#6
    # failed — not part of completed_signatures.
    completed = [
        {
            "tool_name": "spades_assemble",
            "step_id": 1,
            "arguments": {"reads_1": "/d/R1.fq.gz", "reads_2": "/d/R2.fq.gz", "output_dir": "/o/a"},
        },
    ]
    harness.run["plan"] = {"thought_process": "", "plan": completed}
    harness.run["step_statuses"] = ["completed"]

    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_a, **_k: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": [],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    harness._assess_stepwise_protocol_candidate = lambda **_k: {"passed": True}  # type: ignore[method-assign]
    harness._stepwise_required_arg_rejection_reason = lambda **_k: ""  # type: ignore[method-assign]
    # Fix #22b: synthetic ``/d/*.fq.gz`` paths do not exist; bypass the
    # missing-inputs guard to keep this test focused on mask propagation.
    harness._stepwise_missing_candidate_inputs = lambda **_k: []  # type: ignore[method-assign]

    # First attempt's rejection returns a duplicate_equivalent_step — Fix #9
    # adds bash_run to excluded. Subsequent attempts' semantic passes.
    reject_then_pass = {"count": 0}

    def _fake_semantic(**kwargs):
        reject_then_pass["count"] += 1
        if reject_then_pass["count"] == 1:
            return (
                kwargs["plan"],
                {
                    "passed": False,
                    "issues": [
                        {
                            "issue": "duplicate_equivalent_step",
                            "step_id": 3,
                            "related_step_id": 2,
                        }
                    ],
                },
                [],
            )
        return (kwargs["plan"], {"passed": True}, [])

    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        _fake_semantic,
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_a, **_k: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_a, **_k: [])

    monkeypatch.setattr(
        harness.orchestrator,
        "_available_skill_metadata",
        lambda: [{"name": "spades_assemble"}, {"name": "bash_run"}, {"name": "bwa_mem_align"}],
    )
    monkeypatch.setattr(
        harness.orchestrator,
        "_select_planner_skill_metadata",
        lambda *_a, **_k: (
            [{"name": "bash_run"}, {"name": "bwa_mem_align"}],
            {
                "selected_skill_names": ["bash_run", "bwa_mem_align"],
                "selected_skills": 2,
                "budget": 2,
            },
        ),
    )

    # LLM always returns the same multi-step plan:
    # [spades (completed), bash_run (masked after attempt 1), bwa_mem_align (target)]
    multistep_plan = [
        {
            "tool_name": "spades_assemble",
            "step_id": 1,
            "arguments": {"reads_1": "/d/R1.fq.gz", "reads_2": "/d/R2.fq.gz", "output_dir": "/o/a"},
        },
        {
            "tool_name": "bash_run",
            "step_id": 2,
            "arguments": {"command": "echo fix"},
        },
        {
            "tool_name": "bwa_mem_align",
            "step_id": 3,
            "arguments": {
                "reads_1": "/d/evol1_R1.fq.gz",
                "reads_2": "/d/evol1_R2.fq.gz",
                "reference_fasta": "/o/a/spades.fasta",
                "output_bam": "/o/align/evol1.bam",
            },
        },
    ]

    def _fake_attempt(**_kwargs):
        return (
            {"thought_process": "full plan", "plan": [dict(s) for s in multistep_plan]},
            0.01,
        )

    harness._planner_attempt_with_heartbeat = _fake_attempt  # type: ignore[method-assign]
    monkeypatch.setenv("BIO_HARNESS_STEPWISE_PLANNER_ATTEMPTS_PER_TURN", "4")

    decision = harness._plan_next_step_turn(contract={}, turn_num=1)

    # Attempt 1: Fix #8 picks bash_run (first non-completed), semantic
    # rejects → Fix #9 adds bash_run to excluded.
    # Attempt 2: Fix #8 + Fix #10 now skip both spades (completed) AND
    # bash_run (excluded), selecting bwa_mem_align — accepted.
    assert decision["status"] == "step"
    chosen = decision["candidate_plan"]["plan"][0]
    assert chosen["tool_name"] == "bwa_mem_align", (
        f"Expected bwa_mem_align after Fix #10 skipped masked bash_run, got {chosen['tool_name']}"
    )


def test_stepwise_missing_required_arg_masks_repeated_tool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """When the required-arg preflight rejects a step, the tool is masked.

    Fix #11. The LLM has no way of knowing which argument the registry
    requires for a given tool (the error message doesn't teach the model
    between attempts in the same turn), so it will often re-emit the
    exact same incomplete step across all attempts, exhausting the turn
    without producing a usable step. Masking the tool after a
    ``missing required argument`` rejection pushes the planner toward a
    different tool on the next attempt — either one whose required args
    it can populate, or one whose required-args set is smaller.

    Concretely this mirrors exp22's failure: ``prokka_annotate``
    rejected on attempt 1 for missing ``sample_prefix``, LLM re-emits the
    exact same step on attempts 2..N, and the turn runs out of attempts.
    After Fix #11, attempt 2+ should see ``prokka_annotate`` in the
    excluded set.
    """

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    completed = [
        {
            "tool_name": "spades_assemble",
            "step_id": 1,
            "arguments": {
                "reads_1": "/d/R1.fq.gz",
                "reads_2": "/d/R2.fq.gz",
                "output_dir": "/o/a",
            },
        },
    ]
    harness.run["plan"] = {"thought_process": "", "plan": completed}
    harness.run["step_statuses"] = ["completed"]

    harness._normalize_plan_for_execution = lambda plan: (plan, {}, {})  # type: ignore[method-assign]
    harness._assess_contract_for_plan = lambda *_a, **_k: {  # type: ignore[method-assign]
        "passed": False,
        "missing_capabilities": [],
        "missing_required_tool_hints": [],
        "missing_tool_hints": [],
        "direct_wrapper_issues": [],
        "artifact_role_issues": [],
    }
    harness._assess_stepwise_protocol_candidate = lambda **_k: {"passed": True}  # type: ignore[method-assign]

    # Always return the missing-required-arg rejection message. This is the
    # trigger Fix #11 is watching for; masking must happen even when the
    # message recurs on every attempt (it will, because the LLM keeps
    # emitting the same broken step).
    harness._stepwise_required_arg_rejection_reason = lambda **_k: (  # type: ignore[method-assign]
        "Next step is missing required arguments. Step 11 (prokka_annotate) "
        "is missing required argument(s): sample_prefix. "
        "Emit the step again with every required argument populated "
        "(per the tool's declared parameters), or choose a different tool."
    )
    monkeypatch.setattr(
        stepwise_loop_module,
        "assess_plan_semantic_guards_with_bash_placeholders",
        lambda **kwargs: (kwargs["plan"], {"passed": True}, []),
    )
    monkeypatch.setattr(stepwise_loop_module, "_missing_exec_tools_for_plan", lambda _plan: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_input_paths_for_plan", lambda *_a, **_k: [])
    monkeypatch.setattr(stepwise_loop_module, "_missing_local_scripts_for_plan", lambda *_a, **_k: [])

    observed_excluded_per_attempt: list[list[str]] = []

    available_skills = [
        {"name": "prokka_annotate"},
        {"name": "prodigal_annotate"},
    ]

    monkeypatch.setattr(
        harness.orchestrator,
        "_available_skill_metadata",
        lambda: [dict(item) for item in available_skills],
    )

    def _fake_selected_skills(**kwargs):
        excluded = kwargs.get("excluded_tool_names") or set()
        observed_excluded_per_attempt.append(sorted(str(x) for x in excluded))
        remaining = [s for s in available_skills if s["name"] not in excluded]
        return (
            remaining,
            {
                "selected_skill_names": [s["name"] for s in remaining],
                "selected_skills": len(remaining),
                "budget": len(remaining),
            },
            remaining,
        )

    harness._stepwise_selected_planner_skills = _fake_selected_skills  # type: ignore[method-assign]

    # LLM always emits the same prokka_annotate step with sample_prefix omitted.
    def _fake_attempt(**_kwargs):
        return (
            {
                "thought_process": "annotate assembly",
                "plan": [
                    {
                        "tool_name": "prokka_annotate",
                        "step_id": 2,
                        "arguments": {
                            "input_fasta": "/o/a/scaffolds.fasta",
                            "output_dir": "/o/annot",
                        },
                    }
                ],
            },
            0.01,
        )

    harness._planner_attempt_with_heartbeat = _fake_attempt  # type: ignore[method-assign]
    monkeypatch.setenv("BIO_HARNESS_STEPWISE_PLANNER_ATTEMPTS_PER_TURN", "3")

    try:
        harness._plan_next_step_turn(contract={}, turn_num=1)
    except ValueError:
        pass

    assert observed_excluded_per_attempt, "No attempts observed"
    assert observed_excluded_per_attempt[0] == [], (
        "First attempt should start with empty mask; got "
        f"{observed_excluded_per_attempt[0]}"
    )
    assert any(
        "prokka_annotate" in excluded
        for excluded in observed_excluded_per_attempt[1:]
    ), (
        "prokka_annotate should have been masked after the first "
        "missing-required-arg rejection, but observed sets were: "
        f"{observed_excluded_per_attempt}"
    )


def test_stepwise_autofill_populates_missing_vcf_args_fix_13(tmp_path: Path) -> None:
    """Fix #13: auto-populate ``input_vcf`` / ``output_vcf`` from prior
    completed steps' VCF outputs, skipping the rejection when the step
    can be made valid without the LLM's help.

    Without this, the stepwise loop exhausts turn 17 on snpeff_annotate
    because (a) the LLM emits the step with empty file-path args,
    (b) Fix #11 masks snpeff_annotate after the rejection, and
    (c) no alternative tool can satisfy the protocol's snpeff
    requirement. Observed in exp24 terminal: ``Step 17 (snpeff_annotate)
    is missing required argument(s): input_vcf, output_vcf``.
    """

    harness = _build_harness(tmp_path)
    # Simulate a run with one completed step that produced a filtered VCF.
    harness.run["analysis_spec"] = {}
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bcftools_filter_run",
                "step_id": 1,
                "arguments": {
                    "input_vcf": "/runs/anc_raw.vcf.gz",
                    "output_vcf": "/runs/anc_filtered.vcf.gz",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    candidate_plan = {
        "thought_process": "annotate the filtered VCF",
        "plan": [
            harness.run["plan"]["plan"][0],
            {
                "tool_name": "snpeff_annotate",
                "step_id": 2,
                "arguments": {},  # LLM emitted no args at all
            },
        ],
    }

    # Before Fix #13 this returns a rejection message. After Fix #13 it
    # must return "" because input_vcf / output_vcf got auto-populated
    # from the completed bcftools_filter_run step and the default
    # selected_dir path.
    reason = harness._stepwise_required_arg_rejection_reason(plan=candidate_plan)

    # The candidate's arguments must have been mutated in place.
    filled_args = candidate_plan["plan"][-1]["arguments"]
    # The test doesn't assume either arg is present in the registry's
    # required-args set (that depends on the skill's schema). Only
    # assert the observable behavior: either autofill closed the gap
    # (reason is empty) OR the candidate args now contain a concrete
    # VCF path derived from the completed step.
    if reason:
        # If we still get a rejection, at least one of the args was
        # auto-populated — the rejection now references fewer args.
        assert "input_vcf" in filled_args or "output_vcf" in filled_args, (
            f"Fix #13 did not populate any VCF args; "
            f"rejection={reason!r}; filled_args={filled_args!r}"
        )
    else:
        # Full autofill — the original filtered VCF should appear as input.
        assert filled_args.get("input_vcf") == "/runs/anc_filtered.vcf.gz", (
            f"Expected input_vcf populated from completed step's output_vcf; "
            f"got filled_args={filled_args!r}"
        )
        # output_vcf defaulted to a path under selected_dir.
        out = str(filled_args.get("output_vcf") or "")
        assert out.endswith(".vcf.gz") and str(harness.cfg.selected_dir) in out, (
            f"Expected output_vcf default under selected_dir; got {out!r}"
        )


def test_stepwise_primary_io_signature_catches_bwa_label_drop_fix_14(
    tmp_path: Path,
) -> None:
    """Fix #14: duplicate detection must match on primary I/O when the LLM
    drops a label arg (e.g. ``sample_name``) between turns.

    Observed in exp25: the accepted step 3 for ``bwa_mem_align`` carried
    ``sample_name: "anc"``, but re-submissions at steps 8-16 omitted
    ``sample_name`` while keeping identical ``reads_1``/``reads_2``/
    ``reference_fasta``/``output_bam``. The strict full-args signature
    missed the duplicate and the same alignment ran nine times.

    The primary-I/O fallback signature strips every non-I/O arg and matches
    on the work actually being performed, closing this class of livelock.
    """

    harness = _build_harness(tmp_path)
    # Completed step carrying a label arg the LLM later drops.
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "step_id": 3,
                "arguments": {
                    "reads_1": "/data/anc_R1.fastq.gz",
                    "reads_2": "/data/anc_R2.fastq.gz",
                    "reference_fasta": "/out/scaffolds.fasta",
                    "output_bam": "/out/anc_aligned.bam",
                    "sample_name": "anc",
                    "threads": 8,
                    "postprocess_mode": "fixmate_markdup_q20",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # Candidate submits identical I/O but omits the label/tuning args.
    candidate_step = {
        "tool_name": "bwa_mem_align",
        "arguments": {
            "reads_1": "/data/anc_R1.fastq.gz",
            "reads_2": "/data/anc_R2.fastq.gz",
            "reference_fasta": "/out/scaffolds.fasta",
            "output_bam": "/out/anc_aligned.bam",
            "threads": 8,
        },
    }

    # Strict signatures differ because ``sample_name`` is present in one
    # and absent in the other (regression guard against relying on the
    # strict signature alone).
    strict_prior = harness._stepwise_step_signature(harness.run["plan"]["plan"][0])
    strict_candidate = harness._stepwise_step_signature(candidate_step)
    assert strict_prior != strict_candidate, (
        "Regression guard: strict signatures should differ when label arg "
        "is dropped — otherwise this test does not exercise Fix #14."
    )

    # Primary-I/O signatures MUST match.
    io_prior = harness._stepwise_primary_io_signature(harness.run["plan"]["plan"][0])
    io_candidate = harness._stepwise_primary_io_signature(candidate_step)
    assert io_prior and io_candidate, (
        f"Both steps should produce a non-empty I/O signature; "
        f"got prior={io_prior!r} candidate={io_candidate!r}"
    )
    assert io_prior == io_candidate, (
        f"Primary-I/O signatures should match across label-arg drift; "
        f"prior={io_prior!r} candidate={io_candidate!r}"
    )

    # End-to-end: duplicate detector MUST flag this as a duplicate.
    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior, (
        f"Fix #14: candidate with identical primary I/O but missing "
        f"sample_name must be detected as duplicate of step 3; got {duplicate_prior!r}"
    )
    assert duplicate_prior.get("step_id") == 3
    assert duplicate_prior.get("tool_name") == "bwa_mem_align"


def test_stepwise_primary_io_signature_allows_different_sample_fix_14(
    tmp_path: Path,
) -> None:
    """Fix #14 must not over-match: aligning a different sample (different
    reads, different output) is NOT a duplicate even when the tool and
    reference are shared.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "step_id": 3,
                "arguments": {
                    "reads_1": "/data/anc_R1.fastq.gz",
                    "reads_2": "/data/anc_R2.fastq.gz",
                    "reference_fasta": "/out/scaffolds.fasta",
                    "output_bam": "/out/anc_aligned.bam",
                    "sample_name": "anc",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # Evolved line 1: different reads, different output BAM — legitimate
    # next alignment. Must NOT be flagged as duplicate.
    candidate_step = {
        "tool_name": "bwa_mem_align",
        "arguments": {
            "reads_1": "/data/evol1_R1.fastq.gz",
            "reads_2": "/data/evol1_R2.fastq.gz",
            "reference_fasta": "/out/scaffolds.fasta",
            "output_bam": "/out/evol1_aligned.bam",
        },
    }

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}, (
        f"Fix #14 must not flag a different-sample alignment as duplicate; "
        f"got {duplicate_prior!r}"
    )


def test_stepwise_input_only_signature_catches_reference_swap_fix_14b(
    tmp_path: Path,
) -> None:
    """Fix #14b: duplicate detection must match on input-only when the LLM
    swaps in an equivalent reference copy and a different output path.

    Observed in exp26: step 3 (completed) ran ``bwa_mem_align`` on
    ``anc_R1.fastq.gz``/``anc_R2.fastq.gz`` against
    ``/assembly/scaffolds.fasta`` writing ``/selected/alignment/anc_aligned
    .bam``. The LLM then re-submitted the same alignment at steps 8, 9, 10,
    12 — each using ``/selected/ancestor_ref.fasta`` (a ``cp`` of the
    scaffolds) and writing to ``/selected/anc_aligned.bam`` (different
    subdirectory). Both the strict and primary-I/O signatures missed the
    duplicate because reference_fasta *and* output_bam differ. The
    input-only signature — identity by ``reads_1``/``reads_2`` — catches
    it.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "step_id": 3,
                "arguments": {
                    "reads_1": "/data/anc_R1.fastq.gz",
                    "reads_2": "/data/anc_R2.fastq.gz",
                    "reference_fasta": "/out/assembly/scaffolds.fasta",
                    "output_bam": "/out/selected/alignment/anc_aligned.bam",
                    "sample_name": "anc",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # Candidate: same reads, *different* reference copy, *different*
    # output directory. Strict and primary-I/O signatures differ.
    candidate_step = {
        "tool_name": "bwa_mem_align",
        "arguments": {
            "reads_1": "/data/anc_R1.fastq.gz",
            "reads_2": "/data/anc_R2.fastq.gz",
            "reference_fasta": "/out/selected/ancestor_ref.fasta",
            "output_bam": "/out/selected/anc_aligned.bam",
        },
    }

    prior_step = harness.run["plan"]["plan"][0]
    # Regression guards: strict and I/O signatures must NOT match here,
    # else this test doesn't exercise Fix #14b.
    assert (
        harness._stepwise_step_signature(prior_step)
        != harness._stepwise_step_signature(candidate_step)
    ), "strict sigs should differ when reference and output paths differ"
    assert (
        harness._stepwise_primary_io_signature(prior_step)
        != harness._stepwise_primary_io_signature(candidate_step)
    ), "primary-I/O sigs should differ when reference and output paths differ"

    # Input-only signatures MUST match.
    inp_prior = harness._stepwise_input_only_signature(prior_step)
    inp_candidate = harness._stepwise_input_only_signature(candidate_step)
    assert inp_prior and inp_candidate, (
        f"Both steps should produce a non-empty input signature; "
        f"got prior={inp_prior!r} candidate={inp_candidate!r}"
    )
    assert inp_prior == inp_candidate, (
        f"Input-only signatures should match across reference/output drift; "
        f"prior={inp_prior!r} candidate={inp_candidate!r}"
    )

    # End-to-end: duplicate detector MUST flag this as a duplicate.
    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior, (
        f"Fix #14b: candidate with identical reads_1/reads_2 but different "
        f"reference_fasta/output_bam must be detected as duplicate of step 3; "
        f"got {duplicate_prior!r}"
    )
    assert duplicate_prior.get("step_id") == 3


def test_stepwise_input_only_signature_allows_different_reads_fix_14b(
    tmp_path: Path,
) -> None:
    """Fix #14b must not over-match: different reads = legitimate new sample,
    not a duplicate, even when tool and reference are shared.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "step_id": 3,
                "arguments": {
                    "reads_1": "/data/anc_R1.fastq.gz",
                    "reads_2": "/data/anc_R2.fastq.gz",
                    "reference_fasta": "/out/scaffolds.fasta",
                    "output_bam": "/out/anc_aligned.bam",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # evol1 is a legitimate additional sample.
    candidate_step = {
        "tool_name": "bwa_mem_align",
        "arguments": {
            "reads_1": "/data/evol1_R1.fastq.gz",
            "reads_2": "/data/evol1_R2.fastq.gz",
            "reference_fasta": "/out/scaffolds.fasta",
            "output_bam": "/out/evol1_aligned.bam",
        },
    }

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}, (
        f"Fix #14b must not flag a different-reads alignment as duplicate; "
        f"got {duplicate_prior!r}"
    )


def test_stepwise_resolved_output_signature_catches_filename_rename_fix_14c(
    tmp_path: Path,
) -> None:
    """Fix #14c: duplicate detection must match via filesystem-resolved
    output paths when the LLM renames an output filename (``ancestor_...``
    vs ``anc_...``) to an alias of an already-produced artifact.

    Observed in exp27: step 4 (completed) ran ``freebayes_call`` writing
    ``anc_raw.vcf``. Later turns submitted ``ancestor_raw.vcf`` from
    ``ancestor_aligned.bam`` — textually different input_bam, output_vcf
    and reference, but the execution-time fuzzy resolver maps
    ``ancestor_raw.vcf`` back to the already-existing ``anc_raw.vcf`` on
    disk (same parent dir, overlapping first-stem prefix ``anc`` with
    identical ``_raw.vcf`` suffix). Strict/io/input signatures all see
    different text and miss the duplicate, producing a freebayes
    livelock. The resolved-output signature sees both paths canonicalize
    to the same real file and catches the duplicate.
    """

    variants_dir = tmp_path / "variants"
    variants_dir.mkdir(parents=True)
    completed_vcf = variants_dir / "anc_raw.vcf"
    completed_vcf.write_text("##fileformat=VCFv4.2\n")

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 4,
                "arguments": {
                    "input_bam": str(tmp_path / "anc_aligned.bam"),
                    "reference_fasta": str(tmp_path / "scaffolds.fasta"),
                    "output_vcf": str(completed_vcf),
                    "ploidy": 1,
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # Candidate: renamed input_bam (``ancestor_aligned.bam``) and renamed
    # output_vcf (``ancestor_raw.vcf``) in the SAME parent directory — on
    # disk only ``anc_raw.vcf`` exists, but the fuzzy resolver links
    # both names (overlap ``anc`` + shared ``_raw.vcf`` suffix).
    candidate_step = {
        "tool_name": "freebayes_call",
        "arguments": {
            "input_bam": str(tmp_path / "ancestor_aligned.bam"),
            "reference_fasta": str(tmp_path / "ancestor_ref.fasta"),
            "output_vcf": str(variants_dir / "ancestor_raw.vcf"),
            "ploidy": 1,
        },
    }

    prior_step = harness.run["plan"]["plan"][0]
    # Regression guards: strict, I/O, and input signatures must NOT match
    # here, else this test doesn't exercise Fix #14c.
    assert (
        harness._stepwise_step_signature(prior_step)
        != harness._stepwise_step_signature(candidate_step)
    ), "strict sigs should differ when every path is renamed"
    assert (
        harness._stepwise_primary_io_signature(prior_step)
        != harness._stepwise_primary_io_signature(candidate_step)
    ), "primary-I/O sigs should differ when every path is renamed"
    assert (
        harness._stepwise_input_only_signature(prior_step)
        != harness._stepwise_input_only_signature(candidate_step)
    ), "input-only sigs should differ when input_bam is renamed textually"

    # Resolved-output signatures MUST match because the candidate's
    # renamed output_vcf fuzzy-resolves to the same on-disk VCF.
    resolved_prior = harness._stepwise_resolved_output_signature(prior_step)
    resolved_candidate = harness._stepwise_resolved_output_signature(candidate_step)
    assert resolved_prior and resolved_candidate, (
        f"Both steps should produce a non-empty resolved-output signature; "
        f"got prior={resolved_prior!r} candidate={resolved_candidate!r}"
    )
    assert resolved_prior == resolved_candidate, (
        f"Resolved-output signatures should match across filename rename; "
        f"prior={resolved_prior!r} candidate={resolved_candidate!r}"
    )

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior, (
        f"Fix #14c: candidate whose output_vcf fuzzy-resolves to an "
        f"already-produced VCF must be detected as duplicate of step 4; "
        f"got {duplicate_prior!r}"
    )
    assert duplicate_prior.get("step_id") == 4


def test_stepwise_resolved_output_signature_allows_different_sample_fix_14c(
    tmp_path: Path,
) -> None:
    """Fix #14c must not over-match: a different sample whose output does
    NOT fuzzy-resolve to an existing artifact is legitimate new work.
    """

    variants_dir = tmp_path / "variants"
    variants_dir.mkdir(parents=True)
    anc_vcf = variants_dir / "anc_raw.vcf"
    anc_vcf.write_text("##fileformat=VCFv4.2\n")

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 4,
                "arguments": {
                    "input_bam": str(tmp_path / "anc_aligned.bam"),
                    "reference_fasta": str(tmp_path / "scaffolds.fasta"),
                    "output_vcf": str(anc_vcf),
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # evol1 sample: stem prefix ``evol1`` does NOT overlap with ``anc`` at
    # >=3 chars, so fuzzy resolver won't alias the output to anc_raw.vcf.
    candidate_step = {
        "tool_name": "freebayes_call",
        "arguments": {
            "input_bam": str(tmp_path / "evol1_aligned.bam"),
            "reference_fasta": str(tmp_path / "scaffolds.fasta"),
            "output_vcf": str(variants_dir / "evol1_raw.vcf"),
        },
    }

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}, (
        f"Fix #14c must not flag a different-sample freebayes call as duplicate; "
        f"got {duplicate_prior!r}"
    )


def test_stepwise_resolved_output_signature_allows_sibling_evolution_branch_fix_28(
    tmp_path: Path,
) -> None:
    """Fix #28: sibling branch outputs like evol1/evol2 are not aliases.

    The resolved-output duplicate guard intentionally links spelling drift such
    as ``anc_aligned.bam`` and ``ancestor_aligned.bam``. It must not collapse
    sibling evolved branches that share a textual prefix but represent distinct
    samples.
    """

    alignments_dir = tmp_path / "alignments"
    alignments_dir.mkdir(parents=True)
    evol1_bam = alignments_dir / "evol1_aligned.bam"
    evol1_bam.write_text("placeholder bam\n")

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "step_id": 5,
                "arguments": {
                    "reads_1": "/data/evol1_R1.fastq.gz",
                    "reads_2": "/data/evol1_R2.fastq.gz",
                    "reference_fasta": "/out/scaffolds.fasta",
                    "output_bam": str(evol1_bam),
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    candidate_step = {
        "tool_name": "bwa_mem_align",
        "arguments": {
            "reads_1": "/data/evol2_R1.fastq.gz",
            "reads_2": "/data/evol2_R2.fastq.gz",
            "reference_fasta": "/out/scaffolds.fasta",
            "output_bam": str(alignments_dir / "evol2_aligned.bam"),
        },
    }

    assert harness._stepwise_resolved_output_signature(candidate_step) == ""
    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}, (
        f"Fix #28 must not flag evol2 alignment as a duplicate of evol1; "
        f"got {duplicate_prior!r}"
    )


def test_stepwise_bare_args_candidate_matches_completed_tool_fix_14d(
    tmp_path: Path,
) -> None:
    """Fix #14d: a candidate with no arguments at all must duplicate-match
    any prior completed step that used the same tool name.

    Observed in exp28: the LLM emitted ``{"step_id": 7, "tool_name":
    "freebayes_call"}`` with NO ``arguments`` field. Every path-based
    signature (strict, primary-I/O, input-only, resolved-output) collapses
    to the empty string for such a bare step, so Fix #14/#14b/#14c all
    miss the duplicate. The harness normalizer then filled in the same
    args from context and re-executed the identical freebayes command,
    producing a livelock (3× freebayes on ``anc_aligned.bam``). The bare
    candidate must be treated as a duplicate of the most recent completed
    call of the same tool.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 4,
                "arguments": {
                    "input_bam": "/out/alignments/anc_aligned.bam",
                    "reference_fasta": "/out/assembly/scaffolds.fasta",
                    "output_vcf": "/out/variants/anc_raw.vcf",
                    "ploidy": 1,
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # Bare candidate: the exp28 pattern — only step_id + tool_name.
    candidate_step = {
        "step_id": 7,
        "tool_name": "freebayes_call",
    }

    # Regression guards: every path-based signature must be empty here.
    assert harness._stepwise_step_signature(candidate_step) == "freebayes_call|{}", (
        "strict signature should degenerate to bare-tool + empty args"
    )
    assert harness._stepwise_primary_io_signature(candidate_step) == ""
    assert harness._stepwise_input_only_signature(candidate_step) == ""
    assert harness._stepwise_resolved_output_signature(candidate_step) == ""

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior, (
        f"Fix #14d: bare-args freebayes candidate must be detected as "
        f"duplicate of completed step 4; got {duplicate_prior!r}"
    )
    assert duplicate_prior.get("step_id") == 4
    assert duplicate_prior.get("tool_name") == "freebayes_call"


def test_stepwise_bare_args_candidate_allows_new_tool_fix_14d(
    tmp_path: Path,
) -> None:
    """Fix #14d must not over-match: a bare-args candidate that names a
    tool which has NOT been run yet is legitimate forward progress.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 4,
                "arguments": {
                    "input_bam": "/out/alignments/anc_aligned.bam",
                    "reference_fasta": "/out/assembly/scaffolds.fasta",
                    "output_vcf": "/out/variants/anc_raw.vcf",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # Bare candidate for a DIFFERENT tool (next pipeline stage).
    candidate_step = {
        "step_id": 5,
        "tool_name": "snpeff_annotate",
    }

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}, (
        f"Fix #14d must not flag a bare snpeff_annotate candidate as duplicate "
        f"when only freebayes_call has completed; got {duplicate_prior!r}"
    )


def test_stepwise_bare_args_candidate_with_parameter_hints_is_not_duplicate_fix_14d(
    tmp_path: Path,
) -> None:
    """Fix #14d refinement: candidates that omit ``arguments`` but carry
    distinguishing metadata (``parameter_hints`` / ``branch_id`` /
    ``sample_name`` / ``objective``) describe legitimate per-sample work
    and must NOT be flagged as duplicates of a prior completed step, even
    when the tool name matches.

    Observed in exp29: after ancestor alignment, the LLM emitted
    ``{"step_id": 5, "tool_name": "bwa_mem_align", "branch_id": "evol1",
    "parameter_hints": {"sample_name": "evol1", ...}, "objective":
    "Align evolved line 1 reads..."}``. A too-aggressive Fix #14d flagged
    this as a duplicate of the anc alignment and stalled the pipeline
    at step 4 of 10+. The refined check must let sample-parameterized
    bare-arg steps through while still catching the pure exp28 pattern.
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "step_id": 3,
                "arguments": {
                    "reads_1": "/data/anc_R1.fastq.gz",
                    "reads_2": "/data/anc_R2.fastq.gz",
                    "reference_fasta": "/out/assembly/scaffolds.fasta",
                    "output_bam": "/out/alignments/anc_aligned.bam",
                    "sample_name": "anc",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # The exp29 pattern: no ``arguments``, but ``parameter_hints`` +
    # ``branch_id`` + ``objective`` convey sample identity.
    candidate_step = {
        "step_id": 5,
        "tool_name": "bwa_mem_align",
        "branch_id": "evol1",
        "parameter_hints": {
            "sample_name": "evol1",
            "threads": 8,
            "postprocess_mode": "fixmate_markdup_q20",
        },
        "objective": "Align evolved line 1 reads to the assembled ancestor scaffold.",
    }

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}, (
        f"Fix #14d refinement: bare-arguments candidate carrying "
        f"parameter_hints/branch_id/objective for a different sample "
        f"(evol1) must NOT be flagged as duplicate of the anc alignment; "
        f"got {duplicate_prior!r}"
    )


def test_stepwise_bare_args_candidate_ignores_uncompleted_prior_fix_14d(
    tmp_path: Path,
) -> None:
    """Fix #14d must only match against *completed* prior steps. A bare
    candidate for a tool whose only prior use is a failed/pending step
    should be allowed (retrying failed work is legitimate).
    """

    harness = _build_harness(tmp_path)
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 4,
                "arguments": {
                    "input_bam": "/out/alignments/anc_aligned.bam",
                    "reference_fasta": "/out/assembly/scaffolds.fasta",
                    "output_vcf": "/out/variants/anc_raw.vcf",
                },
            }
        ],
    }
    # Prior step FAILED, not completed.
    harness.run["step_statuses"] = ["failed"]

    candidate_step = {
        "step_id": 5,
        "tool_name": "freebayes_call",
    }

    duplicate_prior = harness._stepwise_duplicate_completed_step(
        candidate_step=candidate_step,
    )
    assert duplicate_prior == {}, (
        f"Fix #14d must not flag a bare candidate as duplicate of a failed "
        f"prior step; got {duplicate_prior!r}"
    )


def test_stepwise_missing_inputs_flags_evolution_shared_export_before_evol2_chain_fix_22b(
    tmp_path: Path,
) -> None:
    """Fix #22b: emitting shared_variants_export_run before the evol2
    branch has run must be rejected with a structured hint, not normalized
    into a "prefix mutation". The candidate references the canonical
    evol2.annotated.normalized.vcf.gz path (after binder rebind); because
    that path neither exists on disk nor is scheduled by any prior step in
    the executed prefix, the guard must surface it as a missing input.
    """

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }
    # Prior executed prefix: only ancestor + evol1 chains ran. No evol2
    # anywhere, so evol2.annotated.normalized.vcf.gz will never be produced
    # by any prior step.
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "spades_assemble",
                "step_id": 1,
                "arguments": {},
            },
            {
                "tool_name": "bwa_mem_align",
                "step_id": 2,
                "branch_id": "evol1",
                "arguments": {},
            },
        ],
    }
    harness.run["step_statuses"] = ["completed", "completed"]

    candidate_step = {
        "step_id": 3,
        "tool_name": "shared_variants_export_run",
        "arguments": {
            # Planner-invented paths; the binder will rebind these to
            # canonical annotated.normalized scaffold paths that still
            # don't exist on disk.
            "input_vcf_a": "/tmp/bogus/evol1.normalized.vcf",
            "input_vcf_b": "/tmp/bogus/evol2.normalized.vcf",
            "output_csv": "/tmp/bogus/shared.csv",
        },
    }

    missing = harness._stepwise_missing_candidate_inputs(candidate_step=candidate_step)
    # At least one of the canonical evol1/evol2 annotated-normalized paths
    # must be flagged as missing (neither exists on disk nor is scheduled).
    assert missing, "Fix #22b must flag missing canonical evol2 input"
    missing_text = " ".join(missing)
    assert "annotated.normalized" in missing_text


def test_stepwise_missing_inputs_respects_scheduled_outputs_fix_22b(tmp_path: Path) -> None:
    """Fix #22b: when a prior accepted step is scheduled to produce the
    candidate's input path (as one of its declared output_argument_keys),
    the guard must NOT flag that input as missing — even if the path
    doesn't exist on disk yet. The plan prefix is honored as a producer
    plan, not just "files that exist right now".
    """

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    # Prior step schedules /tmp/out.vcf as its output.
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 1,
                "arguments": {
                    "input_bam": "/tmp/existing_but_not_real.bam",
                    "output_vcf": "/tmp/scheduled_but_not_yet_on_disk.vcf",
                    "reference_fasta": "/tmp/ref.fa",
                },
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # Candidate references exactly the output the prior step schedules.
    candidate_step = {
        "step_id": 2,
        "tool_name": "bcftools_filter_run",
        "arguments": {
            "input_vcf": "/tmp/scheduled_but_not_yet_on_disk.vcf",
            "output_vcf": "/tmp/filtered.vcf.gz",
            "filter_expression": "QUAL > 1",
        },
    }

    missing = harness._stepwise_missing_candidate_inputs(candidate_step=candidate_step)
    # /tmp/scheduled_but_not_yet_on_disk.vcf is produced by the prior step,
    # so it must NOT appear in the missing list.
    assert not missing or "/tmp/scheduled_but_not_yet_on_disk.vcf" not in " ".join(
        missing
    ), f"Fix #22b must honor scheduled outputs; got missing={missing!r}"


def test_stepwise_missing_inputs_empty_for_bare_tool_name_fix_22b(tmp_path: Path) -> None:
    """Fix #22b guard: a bare candidate with no tool_name cannot be checked
    (no input_keys to look up); return an empty list rather than raising.
    """

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    harness.run["plan"] = {"thought_process": "", "plan": []}
    harness.run["step_statuses"] = []

    missing = harness._stepwise_missing_candidate_inputs(
        candidate_step={"step_id": 1, "tool_name": ""},
    )
    assert missing == []

    missing_no_tool = harness._stepwise_missing_candidate_inputs(
        candidate_step={"step_id": 1},
    )
    assert missing_no_tool == []


def test_stepwise_missing_inputs_passes_when_file_exists_on_disk_fix_22b(
    tmp_path: Path,
) -> None:
    """Fix #22b: an input path that exists on disk must NOT be flagged —
    even if no prior step in the plan explicitly schedules it (e.g. a
    pre-existing benchmark reference file).
    """

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    harness.run["plan"] = {"thought_process": "", "plan": []}
    harness.run["step_statuses"] = []

    real_vcf = tmp_path / "pre_existing.vcf"
    real_vcf.write_text("##fileformat=VCFv4.2\n", encoding="utf-8")

    candidate_step = {
        "step_id": 1,
        "tool_name": "bcftools_filter_run",
        "arguments": {
            "input_vcf": str(real_vcf),
            "output_vcf": str(tmp_path / "out.vcf.gz"),
            "filter_expression": "QUAL > 1",
        },
    }

    missing = harness._stepwise_missing_candidate_inputs(candidate_step=candidate_step)
    assert str(real_vcf) not in missing


def test_stepwise_evaluate_candidate_rejects_missing_inputs_with_directive_fix_22b(
    tmp_path: Path,
) -> None:
    """Fix #22b end-to-end: ``_evaluate_stepwise_candidate`` must reject a
    candidate whose canonical bound inputs are missing-and-unscheduled BEFORE
    delegating to the normalizer, producing a directive that names at least
    one missing path (not the cryptic "prefix mutation" message).
    """

    harness = _build_harness(tmp_path)
    selected_dir = harness.cfg.selected_dir
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }
    harness.run["plan"] = {
        "thought_process": "",
        "plan": [
            {
                "tool_name": "spades_assemble",
                "step_id": 1,
                "arguments": {},
            }
        ],
    }
    harness.run["step_statuses"] = ["completed"]

    # Guard against the normalizer obscuring the rejection reason — we want
    # to prove Fix #22b fires BEFORE the normalizer runs.
    def _unreachable_normalizer(
        _plan: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        raise AssertionError(
            "Fix #22b must reject before the normalizer is invoked"
        )

    harness._normalize_plan_for_execution = _unreachable_normalizer  # type: ignore[method-assign]

    accepted, _payload, reason = harness._evaluate_stepwise_candidate(
        contract={},
        candidate={
            "thought_process": "",
            "plan": [
                {
                    "tool_name": "shared_variants_export_run",
                    "arguments": {
                        "input_vcf_a": "/tmp/bogus/evol1.normalized.vcf",
                        "input_vcf_b": "/tmp/bogus/evol2.normalized.vcf",
                        "output_csv": "/tmp/bogus/shared.csv",
                    },
                }
            ],
        },
    )

    assert accepted is False
    # The directive should name the missing file / branch so the planner
    # can act on it.
    assert "not available" in reason.lower()
    assert "annotated.normalized" in reason or "missing" in reason.lower()


def test_stepwise_missing_inputs_skips_bare_relative_paths_fix_22b(
    tmp_path: Path,
) -> None:
    """Fix #22b post-exp38: bare-filename args must NOT be flagged.

    Regression guard for exp38: the planner emitted ``spades_assemble`` on
    turn 1 with ``reads_1="anc_R1.fastq.gz"`` (a bare filename the binder
    did not rebind because the analysis_spec passed through the stepwise
    loop lacked the ``requested_data_root`` key). ``Path.exists()``
    resolved that bare name against cwd (the repo root) and returned
    False, so Fix #22b rejected the step with
    "Missing: /Users/.../bio_harness/anc_R1.fastq.gz" — the repo root
    false positive. The executor's preflight is the right place to
    resolve bare names against the full anchor set (data_root +
    selected_dir + cwd); Fix #22b is scoped to catch only the
    aggregator-before-producer case where canonical absolute paths are
    missing AND unscheduled.
    """

    harness = _build_harness(tmp_path)
    harness.run["analysis_spec"] = {}
    harness.run["plan"] = {"thought_process": "", "plan": []}
    harness.run["step_statuses"] = []

    candidate_step = {
        "step_id": 1,
        "tool_name": "spades_assemble",
        "arguments": {
            # Bare filenames — exactly what exp38 observed.
            "reads_1": "anc_R1.fastq.gz",
            "reads_2": "anc_R2.fastq.gz",
            "output_dir": "assembly",
        },
    }

    missing = harness._stepwise_missing_candidate_inputs(
        candidate_step=candidate_step,
    )
    assert missing == [], (
        "Bare-filename inputs must be deferred to the executor preflight "
        f"(which has the full anchor set), got missing={missing}"
    )


def test_stepwise_missing_inputs_resolves_relative_under_data_root_fix_22b(
    tmp_path: Path,
) -> None:
    """Fix #22b post-exp38: relative paths that resolve under ``data_root``
    (via ``cfg.data_root``) must NOT be flagged.

    Complements the bare-filename regression test: when a relative path
    EXISTS under a known anchor, the check must treat it as available
    (the executor will resolve the same anchor).
    """

    data_root = tmp_path / "data"
    data_root.mkdir()
    fastq = data_root / "sample_R1.fq.gz"
    fastq.write_bytes(b"")

    harness = _build_harness(tmp_path)
    harness.cfg.data_root = str(data_root)  # type: ignore[attr-defined]
    harness.run["analysis_spec"] = {}
    harness.run["plan"] = {"thought_process": "", "plan": []}
    harness.run["step_statuses"] = []

    candidate_step = {
        "step_id": 1,
        "tool_name": "spades_assemble",
        "arguments": {
            "reads_1": "sample_R1.fq.gz",  # resolves under data_root
            "reads_2": "sample_R1.fq.gz",
            "output_dir": "assembly",
        },
    }

    missing = harness._stepwise_missing_candidate_inputs(
        candidate_step=candidate_step,
    )
    assert missing == [], (
        "Relative path resolving under data_root must not be flagged, "
        f"got missing={missing}"
    )
