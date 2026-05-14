from __future__ import annotations

import json

# ruff: noqa: F403,F405
from scripts.run_agent_e2e_preexecution_repairs import (
    _apply_deterministic_preexecution_semantic_repairs,
)
from tests.core_cases.harness_guard_support import *

def test_assess_plan_semantic_guards_detects_index_based_group_guessing():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "# If no clear labels, split by index.\n"
                        "# Assume first half are controls and second half are treated.\n"
                        "ctrl_cols = cols[:n//2]\n"
                        "treat_cols = cols[n//2:]\n"
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
    assert "guessed_case_control_split" in issue_names
def test_assess_plan_semantic_guards_allows_compare_pathways_helper_command():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python /repo/bio_harness/pipeline_scripts/compare_pathways.py "
                        "--output-csv /tmp/final/pathway_comparison.csv "
                        "--run-differential-analysis"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={"analysis_type": "multi_model_dge_pathway"},
    )

    assert validation["passed"] is True
    assert validation["issues"] == []
def test_assess_plan_semantic_guards_allows_lightweight_validation_for_helper_backed_analysis():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "import csv\n"
                        "import os\n"
                        "output_csv = '/tmp/final/pathway_comparison.csv'\n"
                        "if not os.path.exists(output_csv):\n"
                        "    raise SystemExit('Missing pathway comparison output')\n"
                        "with open(output_csv, 'r', encoding='utf-8', newline='') as handle:\n"
                        "    reader = csv.DictReader(handle)\n"
                        "    row_count = sum(1 for _ in reader)\n"
                        "print(f'Validated pathway comparison CSV with {row_count} rows at {output_csv}')\n"
                        "EOF"
                    )
                },
                "step_id": 2,
            }
        ]
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={
            "analysis_type": "multi_model_dge_pathway",
            "plan_skeleton": [
                (
                    "bash_run",
                    "Run compare_pathways helper",
                    {"tool": "python3", "helper_script": "/repo/bio_harness/pipeline_scripts/compare_pathways.py"},
                )
            ],
        },
    )

    assert validation["passed"] is True
    assert validation["issues"] == []
def test_assess_plan_semantic_guards_rejects_inline_multi_model_pathway_workflow():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "from scipy import stats\n"
                        "input_txt_1 = '/tmp/GSE161904_Raw_gene_counts_cortex.txt'\n"
                        "input_txt_2 = '/tmp/GSE168137_countList.txt'\n"
                        "input_csv = '/tmp/DEA_PS3O1S.csv'\n"
                        "stats.ttest_ind([1, 2], [3, 4])\n"
                        "EOF"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={"analysis_type": "multi_model_dge_pathway"},
    )

    assert validation["passed"] is False
    issue_names = {issue["issue"] for issue in validation["issues"]}
    assert "non_helper_multi_model_pathway_workflow" in issue_names
def test_assess_plan_semantic_guards_detects_invented_scientific_output():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "import pandas as pd\n"
                        "rows = [{'chromosome': '7', 'gene_name': 'CFTR'}]\n"
                        "pd.DataFrame(rows).to_csv('/tmp/final/cf_variants.csv', index=False)\n"
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
    assert "invented_scientific_output" in issue_names


def test_assess_plan_semantic_guards_allows_compiled_transcript_quant_plan(tmp_path: Path) -> None:
    from bio_harness.core.protocol_grounding import _compile_transcript_quant_plan

    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "sample_R1.fastq.gz").write_bytes(b"")
    (data_root / "sample_R2.fastq.gz").write_bytes(b"")
    (data_root / "transcriptome.fa").write_text(">tx1\nACGT\n", encoding="utf-8")
    selected_dir = tmp_path / "run"
    selected_dir.mkdir(parents=True, exist_ok=True)

    plan, meta = _compile_transcript_quant_plan(
        plan={"thought_process": "test", "plan": []},
        analysis_spec={"analysis_type": "transcript_quantification"},
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={
            "analysis_type": "transcript_quantification",
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        },
    )

    assert validation["passed"] is True
    assert validation["issues"] == []


def test_assess_plan_semantic_guards_rejects_transcript_quant_with_deseq_tool() -> None:
    plan = {
        "canonical_template": "differential_expression_deseq2",
        "execution_options": {"control_tag": "S1", "treatment_tag": "S6"},
        "plan": [
            {
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": "/tmp/counts.tsv",
                    "metadata_table": "/tmp/metadata.tsv",
                    "design_formula": "~ condition",
                    "contrast": "condition_treatment_vs_control",
                    "output_dir": "/tmp/deseq2_out",
                },
                "step_id": 1,
            }
        ],
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={"analysis_type": "transcript_quantification"},
    )

    assert validation["passed"] is False
    issue_names = {issue["issue"] for issue in validation["issues"]}
    assert "analysis_type_drift" in issue_names


def test_assess_plan_semantic_guards_rejects_group_template_without_distinct_tags() -> None:
    plan = {
        "canonical_template": "differential_expression_deseq2",
        "execution_options": {"control_tag": "S1", "treatment_tag": "S1"},
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {"command": "echo placeholder"},
                "step_id": 1,
            }
        ],
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={"analysis_type": "rna_seq_differential_expression"},
    )

    assert validation["passed"] is False
    issue_names = {issue["issue"] for issue in validation["issues"]}
    assert "missing_distinct_group_evidence" in issue_names


def test_assess_plan_semantic_guards_allows_cystic_fibrosis_strict_scaffold() -> None:
    selected_dir = Path("/tmp/official_runs/cystic-fibrosis/attempt1")
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        "selected_dir": str(selected_dir),
        "biological_objective": "Identify the causal recessive CFTR variant in affected siblings.",
    }
    filter_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "step_id": 2,
            "arguments": {"command": "python3 old_filter.py"},
        },
        workflow_step={"tool_name": "bash_run", "objective": ""},
        analysis_spec=analysis_spec,
    )
    export_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "step_id": 4,
            "arguments": {"command": "python3 old_export.py"},
        },
        workflow_step={"tool_name": "bash_run", "objective": ""},
        analysis_spec=analysis_spec,
    )

    validation = _assess_plan_semantic_guards(
        {"plan": [filter_step, export_step]},
        analysis_spec=analysis_spec,
    )

    assert validation["passed"] is True
    assert validation["issues"] == []


def test_assess_plan_semantic_guards_detects_invalid_bcftools_view_cli() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {
                    "command": (
                        "bcftools view -i 'QUAL>=30' -m -v snps,indels "
                        "-Oz -o ancestor_filtered.vcf.gz ancestor_call/anc_raw.vcf"
                    )
                },
            }
        ]
    }

    validation = _assess_plan_semantic_guards(plan)

    assert validation["passed"] is False
    assert any(issue["issue"] == "invalid_bcftools_view_cli" for issue in validation["issues"])


def test_assess_plan_semantic_guards_detects_stage_dag_issues_for_archived_exp28_plan() -> None:
    path = Path(
        "<BIO_HARNESS_ROOT>/workspace/runs/"
        "20260420_063139_identify_and_annotate_genome_8299/completed_run_context.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    validation = _assess_plan_semantic_guards(
        payload["final_plan"],
        analysis_spec={"analysis_type": "bacterial_evolution_variant_calling"},
    )

    assert validation["passed"] is False
    assert {issue["issue"] for issue in validation["issues"]} == {
        "consumer_before_producer",
        "duplicate_equivalent_step",
        "missing_stage_producer",
    }


def test_preexecution_semantic_stage_repair_records_archived_exp28_repair_sidecar() -> None:
    path = Path(
        "<BIO_HARNESS_ROOT>/workspace/runs/"
        "20260420_063139_identify_and_annotate_genome_8299/completed_run_context.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    candidate, repairs, validation = _apply_deterministic_preexecution_semantic_repairs(
        plan=payload["final_plan"],
        analysis_spec={"analysis_type": "bacterial_evolution_variant_calling"},
        cwd="/tmp",
    )

    assert [step["step_id"] for step in candidate["plan"]].index(10) < [step["step_id"] for step in candidate["plan"]].index(9)
    assert repairs[0]["action"] == "preexecution_semantic_stage_dag_repair"
    assert repairs[0]["repair_meta"]["stage_repairs"]["removed_step_ids"] == [11]
    assert 9 in repairs[0]["repair_meta"]["stage_repairs"]["moved_step_ids"]
    assert validation["passed"] is False
    assert [(issue["issue"], issue.get("identity", ""), issue.get("stage", "")) for issue in validation["issues"]] == [
        ("missing_stage_producer", "evol2", "annotated")
    ]


def test_preexecution_semantic_repair_deterministically_fixes_invalid_bcftools_view_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs"
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
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
    }
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {
                    "command": (
                        "bcftools view -i 'QUAL>=30' -m -v snps,indels "
                        "-Oz -o ancestor_filtered.vcf.gz ancestor_call/anc_raw.vcf"
                    )
                },
            }
        ]
    }
    validation = _assess_plan_semantic_guards(harness.run["plan"])
    assert validation["passed"] is False

    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM replan should not be called")),
    )

    repaired, action, details = harness._attempt_preexecution_semantic_repair(
        analysis_spec=harness.run["analysis_spec"],
        validation=validation,
    )

    assert repaired is True
    assert action == "preexecution_semantic_bcftools_view_cli_repair"
    assert details["semantic_validation_after"]["passed"] is True
    repaired_command = harness.run["plan"]["plan"][0]["arguments"]["command"]
    assert " -m -v " not in repaired_command
    assert "bcftools view -i 'QUAL>=30' -v snps,indels" in repaired_command


def test_preexecution_semantic_repair_deterministically_fixes_fix25_isec_bgzip_pipeline_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs"
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
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
    }
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 10,
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir} && "
                        "bcftools isec -w1 -n=2 -p . "
                        "ancestor_filtered.vcf.gz evol1_call/evol1_raw.vcf "
                        "| bgzip > evol1_ancestor_subtracted.vcf.gz && "
                        "bcftools index evol1_ancestor_subtracted.vcf.gz"
                    )
                },
            }
        ]
    }
    validation = _assess_plan_semantic_guards(harness.run["plan"], cwd=selected_dir)
    assert validation["passed"] is False

    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM replan should not be called")),
    )

    repaired, action, details = harness._attempt_preexecution_semantic_repair(
        analysis_spec=harness.run["analysis_spec"],
        validation=validation,
    )

    assert repaired is True
    assert action == "preexecution_semantic_bcftools_isec_output_repair"
    assert details["semantic_validation_after"]["passed"] is True
    repaired_command = harness.run["plan"]["plan"][0]["arguments"]["command"]
    assert "bgzip -c evol1_call/evol1_raw.vcf > evol1_call/evol1_raw.vcf.gz" in repaired_command
    assert "tabix -f evol1_call/evol1_raw.vcf.gz" in repaired_command
    assert "bcftools isec -w1 -n=2 -p .isec_export_evol1_ancestor_subtracted" in repaired_command
    assert "bgzip -c .isec_export_evol1_ancestor_subtracted/0000.vcf > evol1_ancestor_subtracted.vcf.gz" in repaired_command


def test_assess_plan_semantic_guards_detects_ambiguous_bcftools_expression_namespace(tmp_path: Path) -> None:
    input_vcf = tmp_path / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tDP=9\tDP\t9\n"
        ),
        encoding="utf-8",
    )
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {
                    "command": (
                        f"bcftools filter -e 'QUAL<30 || DP<5' "
                        f"-Oz -o {tmp_path / 'filtered.vcf.gz'} {input_vcf}"
                    )
                },
            }
        ]
    }

    validation = _assess_plan_semantic_guards(plan, cwd=tmp_path)

    assert validation["passed"] is False
    assert any(issue["issue"] == "ambiguous_bcftools_expression_namespace" for issue in validation["issues"])


def test_preexecution_semantic_repair_deterministically_qualifies_ambiguous_bcftools_expression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs"
    data_root.mkdir(parents=True, exist_ok=True)
    input_vcf = selected_dir / "calls.vcf"
    input_vcf.write_text(
        (
            "##fileformat=VCFv4.2\n"
            "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
            "##INFO=<ID=AF,Number=A,Type=Float,Description=\"Allele frequency\">\n"
            "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
            "chr1\t10\t.\tA\tG\t60\tPASS\tDP=9;AF=0.9\tDP\t9\n"
        ),
        encoding="utf-8",
    )
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
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
    }
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {
                    "command": (
                        f"bcftools filter -e 'QUAL<30 || DP<5 || AF<0.8' "
                        f"-Oz -o {selected_dir / 'filtered.vcf.gz'} {input_vcf}"
                    )
                },
            }
        ]
    }
    validation = _assess_plan_semantic_guards(harness.run["plan"], cwd=selected_dir)
    assert validation["passed"] is False

    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM replan should not be called")),
    )

    repaired, action, details = harness._attempt_preexecution_semantic_repair(
        analysis_spec=harness.run["analysis_spec"],
        validation=validation,
    )

    assert repaired is True
    assert action == "preexecution_semantic_bcftools_expression_namespace_repair"
    assert details["semantic_validation_after"]["passed"] is True
    repaired_command = harness.run["plan"]["plan"][0]["arguments"]["command"]
    assert "INFO/DP<5" in repaired_command


def test_preexecution_semantic_repair_chains_isec_and_expression_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs"
    data_root.mkdir(parents=True, exist_ok=True)
    for basename in ("ancestor_raw.vcf", "evol1_raw.vcf", "evol2_raw.vcf"):
        (selected_dir / basename).write_text(
            (
                "##fileformat=VCFv4.2\n"
                "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Depth\">\n"
                "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Sample depth\">\n"
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n"
                "chr1\t10\t.\tA\tG\t60\tPASS\tDP=9\tDP\t9\n"
            ),
            encoding="utf-8",
        )
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
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
    }
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 9,
                "arguments": {
                    "command": (
                        "bcftools view -i 'QUAL>=30 && DP>=5' -Oz -o ancestor_filtered.vcf.gz ancestor_raw.vcf && "
                        "tabix -p vcf ancestor_filtered.vcf.gz && "
                        "bcftools view -i 'QUAL>=30 && DP>=5' -Oz -o evol1_filtered.vcf.gz evol1_raw.vcf && "
                        "tabix -p vcf evol1_filtered.vcf.gz && "
                        "bcftools view -i 'QUAL>=30 && DP>=5' -Oz -o evol2_filtered.vcf.gz evol2_raw.vcf && "
                        "tabix -p vcf evol2_filtered.vcf.gz"
                    )
                },
            },
            {
                "tool_name": "bash_run",
                "step_id": 10,
                "arguments": {
                    "command": (
                        "bcftools isec -C -w1 ancestor_filtered.vcf.gz evol1_filtered.vcf.gz -p . && "
                        "mv evol1_filtered.vcf.gz evol1_subtracted_anc.vcf.gz && "
                        "mv evol1_filtered.vcf.gz.tbi evol1_subtracted_anc.vcf.gz.tbi && "
                        "bcftools isec -C -w1 ancestor_filtered.vcf.gz evol2_filtered.vcf.gz -p . && "
                        "mv evol2_filtered.vcf.gz evol2_subtracted_anc.vcf.gz && "
                        "mv evol2_filtered.vcf.gz.tbi evol2_subtracted_anc.vcf.gz.tbi"
                    )
                },
            },
        ]
    }
    validation = _assess_plan_semantic_guards(harness.run["plan"], cwd=selected_dir)
    assert validation["passed"] is False
    assert any(issue["issue"] == "invalid_bcftools_isec_output_mode" for issue in validation["issues"])

    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM replan should not be called")),
    )

    repaired, action, details = harness._attempt_preexecution_semantic_repair(
        analysis_spec=harness.run["analysis_spec"],
        validation=validation,
    )

    assert repaired is True
    assert action == "preexecution_semantic_deterministic_chain_repair"
    assert details["semantic_validation_after"]["passed"] is True
    assert len(details["deterministic_semantic_repairs"]) == 2
    repaired_commands = "\n".join(
        str(step["arguments"]["command"])
        for step in harness.run["plan"]["plan"]
        if isinstance(step, dict) and isinstance(step.get("arguments"), dict)
    )
    assert "INFO/DP>=5" in repaired_commands
    assert "bgzip -c .isec_export_evol1_subtracted_anc/0000.vcf > evol1_subtracted_anc.vcf.gz" in repaired_commands
    assert "bgzip -c .isec_export_evol2_subtracted_anc/0000.vcf > evol2_subtracted_anc.vcf.gz" in repaired_commands


def test_preexecution_semantic_repair_chains_isec_and_shared_variant_export_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs"
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
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        "protocol_grounding": {
            "benchmark_profile": {
                "export_profile": {
                    "header_case": "lower",
                    "status": "shared",
                    "min_impact": "MODERATE",
                    "dedupe_by_gene": True,
                }
            }
        },
    }
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 7,
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir} && cd {selected_dir} && "
                        "bcftools isec -p shared_intersect evol1_subtracted.vcf evol2_subtracted.vcf && "
                        "cat shared_intersect/0000.vcf shared_intersect/0001.vcf > shared_raw.vcf && "
                        "snpEff annotate -gff3 ancestor_assembly/ancestor_genes.gff shared_raw.vcf > shared_annotated.vcf"
                    )
                },
            },
            {
                "tool_name": "bash_run",
                "step_id": 8,
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir / 'final_output'} && cd {selected_dir} && "
                        "bcftools norm -f ancestor_assembly/contigs.fasta shared_annotated.vcf -Oz -o shared_normalized.vcf.gz && "
                        "tabix -p vcf shared_normalized.vcf.gz && "
                        "vcftools --gzvcf shared_normalized.vcf.gz --remove-indels --recode --stdout | "
                        "awk 'BEGIN{OFS=\",\"} /^#/{next} {split($8,info,\";\"); gene=\"\"; impact=\"\"; effect=\"\"; "
                        "for(i in info){if(info[i]~/^ANN=/){split(info[i],ann,\"=\"); split(ann[2],fields,\"|\"); "
                        "gene=fields[3]; impact=fields[4]; effect=fields[5]}}; "
                        "if(impact==\"MODERATE\"||impact==\"HIGH\"){print $1,$2,$4,$5,gene,impact,effect}}' "
                        "> final_output/shared_variants.csv"
                    )
                },
            },
        ]
    }
    validation = _assess_plan_semantic_guards(harness.run["plan"], analysis_spec=harness.run["analysis_spec"], cwd=selected_dir)
    assert validation["passed"] is False
    issue_names = {issue["issue"] for issue in validation["issues"]}
    assert "invalid_bcftools_isec_output_mode" in issue_names
    assert "annotation_filter_before_annotation" in issue_names

    monkeypatch.setattr(
        harness,
        "_supervised_model_replan",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM replan should not be called")),
    )

    repaired, action, details = harness._attempt_preexecution_semantic_repair(
        analysis_spec=harness.run["analysis_spec"],
        validation=validation,
    )

    assert repaired is True
    assert action == "preexecution_semantic_deterministic_chain_repair"
    assert details["semantic_validation_after"]["passed"] is True
    assert len(details["deterministic_semantic_repairs"]) == 2
    repaired_step7 = harness.run["plan"]["plan"][0]["arguments"]["command"]
    repaired_step8 = harness.run["plan"]["plan"][1]["arguments"]["command"]
    assert "bgzip -c evol1_subtracted.vcf > evol1_subtracted.vcf.gz" in repaired_step7
    assert "bgzip -c evol2_subtracted.vcf > evol2_subtracted.vcf.gz" in repaired_step7
    assert "bcftools isec -p shared_intersect evol1_subtracted.vcf.gz evol2_subtracted.vcf.gz" in repaired_step7
    assert "export_shared_variants_csv.py" in repaired_step8
    assert "--input-vcf-a" in repaired_step8
    assert ("shared_normalized.vcf.gz" in repaired_step8) or ("shared_annotated.vcf" in repaired_step8)
    assert "final_output/shared_variants.csv" not in repaired_step8
    assert "final_output/variants_shared.csv" in repaired_step8


def test_semantic_validation_blocks_planning_strict_execution_even_without_strict_llm_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs"
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
    harness.cfg.benchmark_policy = BIOAGENTBENCH_PLANNING_STRICT_POLICY
    harness._init_run()
    harness.run["benchmark_policy"] = BIOAGENTBENCH_PLANNING_STRICT_POLICY
    harness.run["analysis_spec"] = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    }
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 2,
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "import pandas as pd\n"
                        "rows = [{'chromosome': '7', 'gene_name': 'CFTR'}]\n"
                        "pd.DataFrame(rows).to_csv('/tmp/final/cf_variants.csv', index=False)\n"
                        "EOF"
                    )
                },
            }
        ]
    }
    monkeypatch.setattr(
        harness,
        "_attempt_preexecution_semantic_repair",
        lambda **_kwargs: (False, "", {}),
    )

    with pytest.raises(ValueError, match="Strict semantic validation blocked execution"):
        harness._run_semantic_validation_phase(strict_llm_planning=False)

    assert harness.run["validation_block_detected"] is True
def test_assess_plan_semantic_guards_rejects_inline_metagenomics_for_helper_backed_analysis():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "import pandas as pd\n"
                        "kraken2_report = '/tmp/report.txt'\n"
                        "taxonomy = 'species'\n"
                        "EOF"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={
            "analysis_type": "metagenomics_classification",
            "plan_skeleton": [
                (
                    "bash_run",
                    "Classify reads with helper",
                    {"tool": "python3", "helper_script": "/repo/bio_harness/pipeline_scripts/classify_metagenomics_kmer.py"},
                )
            ],
        },
    )

    assert validation["passed"] is False
    issue_names = {issue["issue"] for issue in validation["issues"]}
    assert "missing_helper_backed_command" in issue_names
def test_assess_plan_semantic_guards_allows_helper_backed_metagenomics_command():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "PYTHONPATH=/repo python3 /repo/bio_harness/pipeline_scripts/classify_metagenomics_kmer.py "
                        "--reads-1 /tmp/r1.fastq --reads-2 /tmp/r2.fastq --output-report /tmp/report.txt"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={
            "analysis_type": "metagenomics_classification",
            "plan_skeleton": [
                (
                    "bash_run",
                    "Classify reads with helper",
                    {"tool": "python3", "helper_script": "/repo/bio_harness/pipeline_scripts/classify_metagenomics_kmer.py"},
                )
            ],
        },
    )

    assert validation["passed"] is True
    assert validation["issues"] == []
def test_assess_plan_semantic_guards_rejects_inline_phylogenetics_for_helper_backed_analysis():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "from Bio import Phylo\n"
                        "newick = '(A:0.1,B:0.2);'\n"
                        "print(newick)\n"
                        "EOF"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={
            "analysis_type": "phylogenetics",
            "plan_skeleton": [
                (
                    "bash_run",
                    "Infer phylogeny with helper",
                    {"tool": "python3", "helper_script": "/repo/bio_harness/pipeline_scripts/infer_phylogeny_biopython.py"},
                )
            ],
        },
    )

    assert validation["passed"] is False
    issue_names = {issue["issue"] for issue in validation["issues"]}
    assert "missing_helper_backed_command" in issue_names
def test_assess_plan_semantic_guards_rejects_inline_viral_for_helper_backed_analysis():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 - <<'EOF'\n"
                        "coverage = {'virusA': 87.0}\n"
                        "detected_viruses = ['virusA']\n"
                        "print(coverage, detected_viruses)\n"
                        "EOF"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    validation = _assess_plan_semantic_guards(
        plan,
        analysis_spec={
            "analysis_type": "viral_metagenomics",
            "plan_skeleton": [
                (
                    "bash_run",
                    "Classify viruses with helper",
                    {"tool": "python3", "helper_script": "/repo/bio_harness/pipeline_scripts/classify_viral_reads_kmer.py"},
                )
            ],
        },
    )

    assert validation["passed"] is False
    issue_names = {issue["issue"] for issue in validation["issues"]}
    assert "missing_helper_backed_command" in issue_names
def test_evolution_spades_repair_prefers_scaffolds_and_sets_isolate_flags(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "anc_R1.fastq.gz",
                    "reads_2": "anc_R2.fastq.gz",
                    "threads": 8,
                    "memory_gb": 32,
                    "output_dir": str(selected_dir / "assembly"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "assembly" / "contigs.fasta"),
                    "output_gff": str(selected_dir / "assembly" / "genes.gff"),
                    "output_faa": str(selected_dir / "assembly" / "genes.faa"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly" / "contigs.fasta"),
                    "reads_1": "evol1_R1.fastq.gz",
                    "reads_2": "evol1_R2.fastq.gz",
                    "output_bam": str(selected_dir / "alignments" / "evol1.bam"),
                },
                "step_id": 3,
            },
        ]
    }

    repaired, meta = _repair_evolution_spades_reference_usage(
        plan,
        "experimental evolution variant calling relative to ancestor",
    )

    assert meta.get("changed", False) is True
    step1 = repaired["plan"][0]["arguments"]
    assert step1["careful"] is True
    assert "isolate_mode" not in step1
    assert repaired["plan"][1]["arguments"]["input_fasta"].endswith("/scaffolds.fasta")
    assert repaired["plan"][2]["arguments"]["reference_fasta"].endswith("/scaffolds.fasta")
def test_evolution_spades_repair_collapses_self_reference_plan_to_ancestor_reference(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "ancestor_R1.fastq.gz",
                    "reads_2": "ancestor_R2.fastq.gz",
                    "output_dir": str(selected_dir / "assemblies"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "evolved1_R1.fastq.gz",
                    "reads_2": "evolved1_R2.fastq.gz",
                    "output_dir": str(selected_dir / "assemblies"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "assemblies" / "evolved1_contigs.fasta"),
                    "output_gff": str(selected_dir / "annotations" / "evolved1.gff"),
                    "output_faa": str(selected_dir / "annotations" / "evolved1.faa"),
                },
                "step_id": 3,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "evolved1_R1.fastq.gz",
                    "reads_2": "evolved1_R2.fastq.gz",
                    "reference_fasta": str(selected_dir / "assemblies" / "evolved1_contigs.fasta"),
                    "output_bam": str(selected_dir / "alignments" / "evolved1_vs_evolved1.bam"),
                },
                "step_id": 4,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments" / "evolved1_vs_evolved1.bam"),
                    "reference_fasta": str(selected_dir / "assemblies" / "evolved1_contigs.fasta"),
                    "output_vcf_gz": str(selected_dir / "variants" / "evolved1.vcf.gz"),
                },
                "step_id": 5,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evolved1.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evolved1_annotated.vcf"),
                    "annotation_gff": str(selected_dir / "annotations" / "evolved1.gff"),
                    "genome_db": "ecoli",
                },
                "step_id": 6,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "ancestor_R1.fastq.gz",
                    "reads_2": "ancestor_R2.fastq.gz",
                    "reference_fasta": str(selected_dir / "assemblies" / "ancestor_contigs.fasta"),
                    "output_bam": str(selected_dir / "alignments" / "ancestor_vs_ancestor.bam"),
                },
                "step_id": 7,
            },
        ]
    }

    repaired, meta = _repair_evolution_spades_reference_usage(
        plan,
        "experimental evolution variant calling relative to ancestor",
    )

    assert meta.get("changed", False) is True
    tool_names = [step["tool_name"] for step in repaired["plan"]]
    assert tool_names.count("spades_assemble") == 1
    assert tool_names.count("prodigal_annotate") == 1
    assert "ancestor_vs_ancestor.bam" not in str(repaired["plan"])
    assert all("evolved1_contigs.fasta" not in str(step) for step in repaired["plan"])
    bwa_step = next(step for step in repaired["plan"] if step["tool_name"] == "bwa_mem_align")
    freebayes_step = next(step for step in repaired["plan"] if step["tool_name"] == "freebayes_call")
    snpeff_step = next(step for step in repaired["plan"] if step["tool_name"] == "snpeff_annotate")
    prodigal_step = next(step for step in repaired["plan"] if step["tool_name"] == "prodigal_annotate")
    assert bwa_step["arguments"]["reference_fasta"].endswith("/assemblies/scaffolds.fasta")
    assert freebayes_step["arguments"]["reference_fasta"].endswith("/assemblies/scaffolds.fasta")
    assert freebayes_step["arguments"]["ploidy"] == 1
    assert prodigal_step["arguments"]["output_gff"].endswith("/assemblies/genes.gff")
    assert snpeff_step["arguments"]["annotation_gff"].endswith("/assemblies/genes.gff")
    assert snpeff_step["arguments"]["genome_db"] == "ecoli_custom"
