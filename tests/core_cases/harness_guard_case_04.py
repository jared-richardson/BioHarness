from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_signature_repair_shared_variant_export_preserves_completed_prefix(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    variants_dir = selected_dir / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    final_dir = selected_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    annotated_a = variants_dir / "evolved1_annotated.vcf"
    annotated_b = variants_dir / "evolved2_annotated.vcf"
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "##INFO=<ID=ANN,Number=.,Type=String,Description=\"Annotation\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t60\tPASS\tANN=G|missense_variant|MODERATE|gene1|gene1|transcript|tx1|protein_coding|1/1|c.10A>G|p.Lys4Arg|10/100|10/100|4/33||\n"
    )
    annotated_a.write_text(vcf_text, encoding="utf-8")
    annotated_b.write_text(vcf_text, encoding="utf-8")

    harness = AgentE2EHarness.__new__(AgentE2EHarness)
    harness.cfg = SimpleNamespace(selected_dir=selected_dir)
    harness.run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "freebayes_call",
                    "arguments": {"input_bam": "alignments/evolved1.bam", "reference_fasta": "assembly/scaffolds.fasta", "output_vcf_gz": str(variants_dir / "evolved1.vcf.gz")},
                    "step_id": 1,
                },
                {
                    "tool_name": "freebayes_call",
                    "arguments": {"input_bam": "alignments/evolved2.bam", "reference_fasta": "assembly/scaffolds.fasta", "output_vcf_gz": str(variants_dir / "evolved2.vcf.gz")},
                    "step_id": 2,
                },
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {"input_vcf": str(variants_dir / "evolved1.vcf.gz"), "output_vcf": str(annotated_a)},
                    "step_id": 3,
                },
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {"input_vcf": str(variants_dir / "evolved2.vcf.gz"), "output_vcf": str(annotated_b)},
                    "step_id": 4,
                },
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": (
                            f"bcftools view -i 'IMPACT=\"MODERATE\" || IMPACT=\"HIGH\"' {annotated_a} "
                            f"| bgzip -c > {variants_dir / 'evolved1_highmod.vcf.gz'} && "
                            f"bcftools isec -p {final_dir / 'isec'} {annotated_a} {annotated_b} && "
                            f"awk 'BEGIN{{print \"chrom,pos,ref,alt\"}}' > {final_dir / 'variants_shared.csv'}"
                        )
                    },
                    "step_id": 5,
                },
            ]
        },
        "step_statuses": ["completed", "completed", "completed", "completed", "failed"],
        "next_step_idx": 4,
        "failure_signatures": [],
        "analysis_spec": {
            "protocol_grounding": {
                "benchmark_profile": {
                    "export_profile": {
                        "header_case": "upper",
                        "status": "shared",
                        "min_impact": "MODERATE",
                        "dedupe_by_gene": True,
                    }
                }
            }
        },
        "status": "failed",
        "error": "Step 5 failed",
    }

    repaired, meta = harness._apply_vcf_shared_export_signature_repair()

    assert repaired is True
    assert harness.run["next_step_idx"] == 4
    assert harness.run["step_statuses"] == ["completed", "completed", "completed", "completed", "pending"]
    assert harness.run["status"] == "planned"
    command = harness.run["plan"]["plan"][4]["arguments"]["command"]
    assert "export_shared_variants_csv.py" in command
    assert str(annotated_a) in command
    assert str(annotated_b) in command
    assert "--header-case upper" in command
    assert "snpeff_ann_semantics_mismatch" in harness.run["failure_signatures"]
    assert meta["resume"]["preserved_completed_steps"] == 4
def test_repair_scope_marks_local_tail_with_provenance_lock():
    run = {
        "plan": {
            "plan": [
                {"tool_name": "spades_assemble", "arguments": {}, "step_id": 1},
                {"tool_name": "bwa_mem_align", "arguments": {}, "step_id": 2},
                {"tool_name": "freebayes_call", "arguments": {}, "step_id": 3},
                {"tool_name": "snpeff_annotate", "arguments": {}, "step_id": 4},
                {"tool_name": "bash_run", "arguments": {"command": "vcf2csv --out shared.csv"}, "step_id": 5},
            ]
        },
        "step_statuses": ["completed", "completed", "completed", "completed", "failed"],
        "next_step_idx": 4,
    }

    scope = _repair_scope_for_run(run)

    assert scope["scope"] == "step_local"
    assert scope["provenance_locked"] is True
    assert scope["failed_outputs_csv"] is True
def test_output_adapter_tail_repair_handles_validation_blocked_vcf2csv(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    variants_dir = selected_dir / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    annotated_a = variants_dir / "evolved1_annotated.vcf"
    annotated_b = variants_dir / "evolved2_annotated.vcf"
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "##INFO=<ID=ANN,Number=.,Type=String,Description=\"Annotation\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t60\tPASS\tANN=G|missense_variant|MODERATE|gene1|gene1|transcript|tx1|protein_coding|1/1|c.10A>G|p.Lys4Arg|10/100|10/100|4/33||\n"
    )
    annotated_a.write_text(vcf_text, encoding="utf-8")
    annotated_b.write_text(vcf_text, encoding="utf-8")

    harness = AgentE2EHarness.__new__(AgentE2EHarness)
    harness.cfg = SimpleNamespace(selected_dir=selected_dir)
    harness.run = {
        "plan": {
            "plan": [
                {"tool_name": "spades_assemble", "arguments": {}, "step_id": 1},
                {"tool_name": "bwa_mem_align", "arguments": {}, "step_id": 2},
                {"tool_name": "freebayes_call", "arguments": {}, "step_id": 3},
                {"tool_name": "snpeff_annotate", "arguments": {"input_vcf": "a.vcf.gz", "output_vcf": str(annotated_a)}, "step_id": 4},
                {"tool_name": "snpeff_annotate", "arguments": {"input_vcf": "b.vcf.gz", "output_vcf": str(annotated_b)}, "step_id": 5},
                {
                    "tool_name": "bash_run",
                    "arguments": {"command": "vcf2csv --fields CHROM,POS --out shared_moderate_high.csv"},
                    "step_id": 6,
                },
            ]
        },
        "step_statuses": ["completed", "completed", "completed", "completed", "completed", "failed"],
        "next_step_idx": 5,
        "failure_signatures": ["validation_block_missing_tool", "validation_block_missing_tool:vcf2csv"],
        "analysis_spec": {
            "protocol_grounding": {
                "benchmark_profile": {
                    "export_profile": {
                        "header_case": "upper",
                        "status": "shared",
                        "min_impact": "MODERATE",
                        "dedupe_by_gene": True,
                    }
                }
            }
        },
        "status": "failed",
        "error": "blocked by validation agent",
    }

    repaired, meta = harness._apply_output_adapter_tail_repair()

    assert repaired is True
    assert harness.run["next_step_idx"] == 5
    assert harness.run["step_statuses"] == ["completed", "completed", "completed", "completed", "completed", "pending"]
    assert meta["adapter"] == "shared_variant_csv_export"
    assert "export_shared_variants_csv.py" in harness.run["plan"]["plan"][5]["arguments"]["command"]
    assert "--header-case upper" in harness.run["plan"]["plan"][5]["arguments"]["command"]
def test_template_fallback_guard_blocks_local_tail_with_provenance_lock():
    harness = AgentE2EHarness.__new__(AgentE2EHarness)
    harness.run = {
        "plan": {
            "plan": [
                {"tool_name": "spades_assemble", "arguments": {}, "step_id": 1},
                {"tool_name": "bwa_mem_align", "arguments": {}, "step_id": 2},
                {"tool_name": "freebayes_call", "arguments": {}, "step_id": 3},
                {"tool_name": "bash_run", "arguments": {"command": "vcf2csv --out shared.csv"}, "step_id": 4},
            ]
        },
        "step_statuses": ["completed", "completed", "completed", "failed"],
        "next_step_idx": 3,
    }

    guard = harness._template_fallback_guard("validation_block")

    assert guard["allowed"] is False
    assert guard["why"] == "provenance_locked_local_repair_required"
    assert guard["repair_scope"]["scope"] == "step_local"
def test_template_fallback_guard_blocks_generic_fallback_in_official_mode(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "task" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="official benchmark task",
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

    guard = harness._template_fallback_guard("runtime_step_failure")

    assert guard["allowed"] is False
    assert guard["why"] == "official_bioagentbench_disables_generic_template_fallback"
    assert guard["benchmark_policy"] == OFFICIAL_BIOAGENTBENCH_POLICY
def test_template_fallback_guard_blocks_generic_fallback_in_planning_strict_mode(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "task" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="blind benchmark task",
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

    guard = harness._template_fallback_guard("runtime_step_failure")

    assert guard["allowed"] is False
    assert guard["why"] == "bioagentbench_planning_strict_disables_generic_template_fallback"
    assert guard["benchmark_policy"] == BIOAGENTBENCH_PLANNING_STRICT_POLICY
def test_apply_repair_action_planning_strict_skips_runtime_plan_mutation_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "task" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="strict benchmark task",
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
        allow_replan=False,
        allow_canonicalize=True,
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {"command": "python3 /tmp/export.py --output-csv /tmp/out.csv"},
                "step_id": 1,
            }
        ]
    }
    harness.run["failure_signatures"] = ["shared_variant_export_shell_fragility"]

    def _unexpected_call(*_args, **_kwargs):
        pytest.fail("strict runtime mutation repair helper should not be called")

    monkeypatch.setattr(harness, "_apply_output_adapter_tail_repair", _unexpected_call)
    monkeypatch.setattr(harness, "_apply_vcf_shared_export_signature_repair", _unexpected_call)
    monkeypatch.setattr(harness, "_apply_featurecounts_paired_signature_repair", _unexpected_call)
    monkeypatch.setattr(harness, "_apply_deseq2_metadata_signature_repair", _unexpected_call)

    repaired, action, details = harness._apply_repair_action("runtime_step_failure")

    assert repaired is False
    assert action == "replan_disabled"
    assert details["benchmark_policy"] == BIOAGENTBENCH_PLANNING_STRICT_POLICY
    assert details["repair_scope"]["scope"] == "unknown"
    assert details["why"] == "allow_replan=false"
def test_official_template_fallback_resolver_stays_within_task_roots(tmp_path: Path):
    selected_dir = tmp_path / "workspace" / "run"
    data_root = tmp_path / "workspace" / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    task_ref = data_root / "ref_genome.fa"
    task_ref.write_text(">chr1\nACGT\n", encoding="utf-8")
    external_ref = tmp_path / "workspace" / "inputs_readonly" / "mouse_fasta"
    external_ref.parent.mkdir(parents=True, exist_ok=True)
    external_ref.write_text(">chrM\nACGT\n", encoding="utf-8")

    request = (
        f"Use {task_ref} for the staged task data. "
        f"Ignore alias mouse_fasta at {external_ref}."
    )

    gtf, fasta, reason = _resolve_reference_paths_for_template_fallback(
        request,
        data_root=data_root,
        selected_dir=selected_dir,
        official_benchmark_policy=True,
    )

    assert gtf == ""
    assert fasta == str(task_ref)
    assert "official_roots" in reason
def test_build_contract_template_repair_raises_when_official_mode_would_use_generic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "task" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="official benchmark task",
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
    harness.run["user_request"] = "official mode evolution task"
    harness.run["plan_contract"] = {"must_include_capabilities": ["variant_calling"]}

    def _fail_if_called(**_kwargs):
        raise AssertionError("generic fallback selection should not run in official mode")

    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates.select_ranked_fallback_plan",
        _fail_if_called,
    )

    with pytest.raises(RuntimeError, match="Generic template fallback is disabled in official_bioagentbench mode"):
        harness._build_contract_template_repair("runtime_step_failure")

    assert harness.run["generic_template_fallback_blocked"] is True
    assert harness.run["policy_block_detected"] is True
    assert harness.run["generic_template_fallback_block_reason"] == "official_bioagentbench_disables_generic_template_fallback"
def test_prepare_plan_planning_strict_rejects_compiler_deferred_protocol_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "task" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="Quantify transcripts from RNA-seq reads.",
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

    monkeypatch.setenv("BIO_HARNESS_STRICT_LLM_PLANNING", "1")

    def _fake_prepare_analysis_spec(_contract):
        harness.run["analysis_spec"] = {
            "analysis_type": "transcript_quantification",
            "protocol_grounding": {},
            "parameter_profile": [],
        }

    monkeypatch.setattr(harness, "_prepare_analysis_spec", _fake_prepare_analysis_spec)
    monkeypatch.setattr(
        harness,
        "_generate_plan_with_supervision",
        lambda _contract: (
            {"plan": [{"tool_name": "bash_run", "arguments": {"command": "echo broken"}}]},
            {"strategy": "direct"},
        ),
    )
    monkeypatch.setattr(harness, "_normalize_plan_for_execution", lambda plan: (plan, {"changed": False}, {"changed": False}))
    monkeypatch.setattr(
        "scripts.run_agent_e2e_plan_validation.assess_protocol_grounding",
        lambda _plan, _spec: {"passed": False, "required_plan_signals": ["salmon_quant"]},
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_plan_validation.deterministic_protocol_repair",
        lambda *_args, **_kwargs: pytest.fail("compiler normalization should stay disabled in planning_strict mode"),
    )

    with pytest.raises(ValueError, match="Strict LLM planning is enabled and planner output failed protocol grounding"):
        harness._prepare_plan()
def test_repair_workspace_placeholder_paths_rewrites_outputs_and_data_root(tmp_path: Path):
    selected_dir = tmp_path / "workspace" / "run"
    data_root = tmp_path / "workspace" / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/workspace/data/sample_R1.fastq.gz",
                    "reads_2": "/workspace/data/sample_R2.fastq.gz",
                    "output_dir": "/workspace/ancestor_assembly",
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {"command": "python script.py --output /workspace/results/variants_shared.csv"},
                "step_id": 2,
            },
        ]
    }

    repaired, meta = _repair_workspace_placeholder_paths_in_plan(
        plan,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta.get("changed", False) is True
    args = repaired["plan"][0]["arguments"]
    assert args["reads_1"] == str(data_root / "sample_R1.fastq.gz")
    assert args["output_dir"] == str(selected_dir / "ancestor_assembly")
    assert str(selected_dir / "results" / "variants_shared.csv") in repaired["plan"][1]["arguments"]["command"]
def test_assess_plan_semantic_guards_detects_annotation_filter_before_annotation():
    plan = {
        "plan": [
            {
                "tool_name": "freebayes_call",
                "arguments": {"output_vcf": "raw.vcf"},
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {"command": "bcftools filter -i 'IMPACT=\"MODERATE\"' raw.vcf > filtered.vcf"},
                "step_id": 2,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {"input_vcf": "filtered.vcf", "output_vcf": "annotated.vcf"},
                "step_id": 3,
            },
        ]
    }

    validation = _assess_plan_semantic_guards(plan)

    assert validation["passed"] is False
    assert validation["issues"][0]["issue"] == "annotation_filter_before_annotation"
def test_assess_plan_semantic_guards_detects_placeholder_pathway_and_speculative_handoff():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "# Fallback: assume first half control, second half treated\n"
                        "# If step 1 didn't save them, we might need to re-run logic here.\n"
                        "# Mock KEGG pathways for demonstration\n"
                        "kegg_data = {'Pathway_A': ['Gene1'], 'Pathway_B': ['Gene2']}\n"
                        "EOF"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    validation = _assess_plan_semantic_guards(plan)

    assert validation["passed"] is False
    issue_names = {issue["issue"] for issue in validation["issues"]}
    assert "placeholder_pathway_content" in issue_names
    assert "placeholder_scientific_content" in issue_names
    assert "speculative_step_handoff" in issue_names
    assert "guessed_case_control_split" in issue_names
