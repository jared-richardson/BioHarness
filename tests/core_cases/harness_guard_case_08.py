from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_template_repair_composes_missing_capabilities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    harness.run["user_request"] = "alignment and variant calling for sample groups"
    harness.run["plan_contract"] = {
        "must_include_capabilities": ["alignment", "variant_calling"],
        "explicit_tool_hints": [],
    }

    base_plan = {
        "thought_process": "base",
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "reads_1": "/inputs/a_R1.fastq",
                    "reads_2": "/inputs/a_R2.fastq",
                    "reference_fasta": "/refs/ref.fa",
                    "annotation_gtf": "/refs/anno.gtf",
                    "output_bam": "aligned.bam",
                },
            }
        ],
    }
    variant_plan = {
        "thought_process": "variant",
        "plan": [
            {
                "tool_name": "bcftools_call",
                "arguments": {
                    "input_bam": "aligned.bam",
                    "reference_fasta": "/refs/ref.fa",
                    "output_vcf": "calls.vcf",
                },
            }
        ],
    }

    call_counter = {"n": 0}

    def _fake_select(**_kwargs):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return base_plan, {"selection": {"pipeline_id": "base_pipeline"}, "selection_reason": "unit_test"}
        return variant_plan, {"selection": {"pipeline_id": "variant_pipeline"}, "selection_reason": "unit_test"}

    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates.select_ranked_fallback_plan",
        _fake_select,
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates._resolve_reference_paths_for_template_fallback",
        lambda *_args, **_kwargs: ("anno.gtf", "ref.fa", "unit_test"),
    )

    repaired, action, details = harness._build_contract_template_repair("runtime_step_failure")
    assert isinstance(repaired, dict)
    assert action == "template_composed_fallback"
    assert details.get("composition", {}).get("applied", False) is True
    assert details.get("contract_validation", {}).get("passed", False) is True
    assert call_counter["n"] == 2
    tool_names = [str(step.get("tool_name", "")).strip().lower() for step in repaired.get("plan", [])]
    assert "star_align" in tool_names
    assert "bcftools_call" in tool_names


def test_template_repair_passes_forward_excluded_fallback_pipeline_ids(
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
    harness.run["user_request"] = "repair a failed DESeq fallback"
    harness.run["plan_contract"] = {"must_include_capabilities": ["differential_analysis"]}
    harness.run["excluded_fallback_pipeline_ids"] = ["older_failed_pipeline"]
    harness.run["fallback_selection"] = {"selected_pipeline_id": "recent_failed_pipeline"}

    seen: dict[str, list[str]] = {}

    def _fake_select(**kwargs):
        seen["excluded_pipeline_ids"] = list(kwargs.get("excluded_pipeline_ids", []))
        return None, {"why": "no_ranked_fallback_selected"}

    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates.select_ranked_fallback_plan",
        _fake_select,
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates._resolve_reference_paths_for_template_fallback",
        lambda *_args, **_kwargs: ("anno.gtf", "ref.fa", "unit_test"),
    )

    repaired, action, details = harness._build_contract_template_repair("runtime_step_failure")

    assert repaired is None
    assert action == "template_not_applicable"
    assert sorted(seen["excluded_pipeline_ids"]) == [
        "older_failed_pipeline",
        "recent_failed_pipeline",
    ]
    assert harness.run["excluded_fallback_pipeline_ids"] == [
        "older_failed_pipeline",
        "recent_failed_pipeline",
    ]


def test_template_repair_contract_mismatch_uses_enriched_contract_assessment(
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
    harness.run["user_request"] = "repair a broken variant plan"
    harness.run["plan_contract"] = {"must_include_capabilities": ["variant_calling"]}

    artifact_issue = (
        "bash_run.command:input_in_selected_dir_without_producer:"
        f"{(selected_dir / 'ancestor_call' / 'anc_raw.vcf').resolve(strict=False)}"
    )
    candidate = {
        "thought_process": "candidate",
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

    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates.select_ranked_fallback_plan",
        lambda **_kwargs: (
            candidate,
            {"selection": {"pipeline_id": "unit_test_pipeline"}, "selection_reason": "unit_test"},
        ),
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates._resolve_reference_paths_for_template_fallback",
        lambda *_args, **_kwargs: ("anno.gtf", "ref.fa", "unit_test"),
    )
    monkeypatch.setattr(
        harness,
        "_assess_repair_contract_for_plan",
        lambda _plan, _contract: {
            "passed": False,
            "missing_capabilities": [],
            "missing_required_tool_hints": [],
            "missing_tool_hints": [],
            "direct_wrapper_issues": [],
            "artifact_role_issues": [artifact_issue],
        },
    )

    repaired, action, details = harness._build_contract_template_repair("contract_mismatch")

    assert repaired is None
    assert action == "template_contract_failed"
    assert details["contract_validation"]["artifact_role_issues"] == [artifact_issue]


def test_template_composition_rewires_variant_segment_when_bwa_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    harness.run["user_request"] = "alignment and variant calling for paired-end reads"
    harness.run["plan_contract"] = {
        "must_include_capabilities": ["alignment", "variant_calling"],
        "explicit_tool_hints": [],
    }

    base_plan = {
        "thought_process": "base",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "echo /tmp/control.sorted.bam > outputs/splicing_auto/control_bams.txt",
                },
            }
        ],
    }
    variant_plan = {
        "thought_process": "variant",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "a_R1.fastq",
                    "reads_2": "a_R2.fastq",
                    "reference_fasta": "ref.fa",
                    "output_bam": "outputs/fallback/germline_variant_bcftools/alignment.sorted.bam",
                },
            },
            {
                "tool_name": "bcftools_call",
                "arguments": {
                    "reference_fasta": "ref.fa",
                    "input_bam": "/tmp/stale_input.bam",
                    "output_vcf_gz": "outputs/fallback/germline_variant_bcftools/germline.vcf.gz",
                },
            },
        ],
    }

    call_counter = {"n": 0}

    def _fake_select(**_kwargs):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return base_plan, {"selection": {"pipeline_id": "sr_rna_splicing_rmats_star"}, "selection_reason": "unit_test"}
        return variant_plan, {"selection": {"pipeline_id": "germline_variant_bcftools"}, "selection_reason": "unit_test"}

    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates.select_ranked_fallback_plan",
        _fake_select,
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_runtime_repair_templates._resolve_reference_paths_for_template_fallback",
        lambda *_args, **_kwargs: ("anno.gtf", "ref.fa", "unit_test"),
    )
    _orig_which = shutil.which

    def _fake_which(tool: str):
        if str(tool).strip().lower() in {"bwa", "bwa-mem2"}:
            return None
        return _orig_which(tool)

    monkeypatch.setattr("scripts.run_agent_e2e.shutil.which", _fake_which)

    repaired, action, details = harness._build_contract_template_repair("runtime_step_failure")
    assert isinstance(repaired, dict)
    assert action == "template_composed_fallback"
    assert details.get("composition", {}).get("applied", False) is True
    steps = repaired.get("plan", []) if isinstance(repaired, dict) else []
    tool_names = [str(step.get("tool_name", "")).strip().lower() for step in steps if isinstance(step, dict)]
    assert "bcftools_call" in tool_names
    assert "bwa_mem_align" not in tool_names
    resolver_step = next(
        (
            s
            for s in steps
            if str(s.get("tool_name", "")).strip().lower() == "bash_run"
            and "__MISSING_BAM_LIST__" in str((s.get("arguments", {}) if isinstance(s.get("arguments", {}), dict) else {}).get("command", ""))
        ),
        {},
    )
    resolver_cmd = str((resolver_step.get("arguments", {}) if isinstance(resolver_step.get("arguments", {}), dict) else {}).get("command", ""))
    assert 'case "$bam" in /*) ;; *) bam="$PWD/$bam" ;; esac' in resolver_cmd
    assert "head -n1" not in resolver_cmd
    assert "grep -m1 -v '^[[:space:]]*$' \"$bam_list\"" in resolver_cmd
    bc_step = next((s for s in steps if str(s.get("tool_name", "")).strip().lower() == "bcftools_call"), {})
    bc_args = bc_step.get("arguments", {}) if isinstance(bc_step.get("arguments", {}), dict) else {}
    assert str(bc_args.get("input_bam", "")).endswith("outputs/fallback/germline_variant_bcftools/input_from_inplan.bam")
def test_planner_supervision_retries_after_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="build a quick plan",
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
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")
    calls = {"n": 0}

    def _think(_prompt: str, analysis_spec=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("Planner request timed out while waiting for model output.")
        return {"thought_process": "ok", "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo ok"}, "step_id": 1}]}

    harness.orchestrator.think = _think  # type: ignore[method-assign]
    contract = {"must_include_capabilities": [], "explicit_tool_hints": []}
    plan, meta = harness._generate_plan_with_supervision(contract)
    assert isinstance(plan, dict)
    assert meta.get("strategy") in {"contract_focus_prompt", "contract_repair_prompt"}
    assert calls["n"] >= 2
    assert len(harness.run.get("planning_attempts", [])) >= 2
def test_planner_supervision_timeout_failopen_uses_template_for_nonempty_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
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
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TIMEOUT_FAILOPEN", "1")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")
    calls = {"n": 0}

    def _timeout(_prompt: str, analysis_spec=None):
        calls["n"] += 1
        raise TimeoutError("Planner request timed out while waiting for model output.")

    fallback_plan = {
        "thought_process": "fallback",
        "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo fallback"}, "step_id": 1}],
    }

    harness.orchestrator.think = _timeout  # type: ignore[method-assign]
    monkeypatch.setattr(
        harness,
        "_build_contract_template_repair",
        lambda _failure_class: (fallback_plan, "template_fallback_unit", {"why": "unit_test"}),
    )
    monkeypatch.setattr(
        harness,
        "_assess_contract_for_plan",
        lambda _plan, _contract: {"passed": True, "missing_capabilities": [], "missing_tool_hints": []},
    )
    contract = {"must_include_capabilities": ["alignment"], "explicit_tool_hints": []}
    plan, meta = harness._generate_plan_with_supervision(contract)
    assert isinstance(plan, dict)
    assert plan == fallback_plan
    assert meta.get("strategy") == "timeout_failopen_template"
    assert bool(harness.run.get("planner_failopen_used", False)) is True
    assert calls["n"] == 1
def test_planner_supervision_applies_canonicalization_before_contract_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = tmp_path / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "DEA_PS3O1S.csv").write_text("gene_name,log2fc,pval\nAPP,1.0,0.001\n", encoding="utf-8")
    (data_root / "GSE161904_Raw_gene_counts_cortex.txt").write_text("gene\tcase1\tctrl1\nENSMUSG1\t10\t1\n", encoding="utf-8")
    (data_root / "GSE168137_countList.txt").write_text("gene\tcase1\tctrl1\nENSMUSG2\t8\t2\n", encoding="utf-8")

    cfg = HarnessConfig(
        prompt="Compare shared KEGG pathways across Alzheimer's mouse models.",
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
    harness.run["analysis_spec"] = {"analysis_type": "multi_model_dge_pathway"}
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")

    candidate = {
        "thought_process": "t",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"python {tmp_path / 'task' / 'scripts' / 'compare_pathways.py'} "
                        f"--input_csv {data_root / 'DEA_PS3O1S.csv'} "
                        f"--input_txt {data_root / 'GSE161904_Raw_gene_counts_cortex.txt'} "
                        f"--input_txt {data_root / 'GSE168137_countList.txt'} "
                        f"--output_dir {selected_dir / 'final'}"
                    )
                },
                "step_id": 1,
            }
        ],
    }
    monkeypatch.setattr(
        harness,
        "_planner_attempt_with_heartbeat",
        lambda **_kwargs: (candidate, 1.0),
    )

    contract = {"must_include_capabilities": ["differential_analysis", "pathway_enrichment"], "explicit_tool_hints": []}
    plan, meta = harness._generate_plan_with_supervision(contract)

    assert meta["contract_validation"]["passed"] is True
    assert plan["plan"][0]["tool_name"] == "bash_run"
    assert "--run-differential-analysis" in plan["plan"][0]["arguments"]["command"]
    assert "bio_harness/pipeline_scripts/compare_pathways.py" in plan["plan"][0]["arguments"]["command"]


def test_planner_supervision_rebinds_inline_pathway_python_before_semantic_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = tmp_path / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "DEA_PS3O1S.csv").write_text("gene_name,log2fc,pval\nAPP,1.0,0.001\n", encoding="utf-8")
    (data_root / "GSE161904_Raw_gene_counts_cortex.txt").write_text("gene\tcase1\tctrl1\nENSMUSG1\t10\t1\n", encoding="utf-8")
    (data_root / "GSE168137_countList.txt").write_text("gene\tcase1\tctrl1\nENSMUSG2\t8\t2\n", encoding="utf-8")

    cfg = HarnessConfig(
        prompt="Compare shared KEGG pathways across Alzheimer's mouse models.",
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
    harness.run["analysis_spec"] = {"analysis_type": "multi_model_dge_pathway"}
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")

    candidate = {
        "thought_process": "t",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 <<'PYEOF'\n"
                        "import pandas as pd\n"
                        "from scipy.stats import ttest_ind, fisher_exact\n"
                        f"ps = pd.read_csv('{data_root / 'DEA_PS3O1S.csv'}')\n"
                        f"tg = pd.read_csv('{data_root / 'GSE161904_Raw_gene_counts_cortex.txt'}', sep='\\t')\n"
                        f"fad = pd.read_csv('{data_root / 'GSE168137_countList.txt'}', sep='\\t')\n"
                        f"ps.to_csv('{selected_dir / 'final' / 'pathway_comparison.csv'}', index=False)\n"
                        "PYEOF"
                    )
                },
                "step_id": 1,
            }
        ],
    }
    monkeypatch.setattr(
        harness,
        "_planner_attempt_with_heartbeat",
        lambda **_kwargs: (candidate, 1.0),
    )

    contract = {"must_include_capabilities": ["differential_analysis", "pathway_enrichment"], "explicit_tool_hints": []}
    plan, meta = harness._generate_plan_with_supervision(contract)

    assert meta["contract_validation"]["passed"] is True
    assert plan["plan"][0]["tool_name"] == "bash_run"
    assert "--run-differential-analysis" in plan["plan"][0]["arguments"]["command"]
    assert "bio_harness/pipeline_scripts/compare_pathways.py" in plan["plan"][0]["arguments"]["command"]
def test_planner_attempt_with_heartbeat_forces_wall_clock_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test timeout",
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
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", "1")

    def _hang(_prompt: str, analysis_spec=None):
        time.sleep(3)
        return {"thought_process": "late", "plan": []}

    harness.orchestrator.think = _hang  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        harness._planner_attempt_with_heartbeat(prompt="test", strategy="direct_user_prompt", attempt_num=1)


def test_planner_attempt_with_heartbeat_extends_timeout_on_trace_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test timeout extension",
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
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROGRESS_GRACE_SECONDS", "1")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROGRESS_MAX_EXTENSION_SECONDS", "3")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROGRESS_POLL_SECONDS", "0.1")
    monkeypatch.setattr(harness, "_planner_connectivity_wait_seconds", lambda: 0)

    events: list[dict[str, object]] = []
    original_append = harness._append_event

    def _capture_event(**kwargs):
        events.append(kwargs)
        return original_append(**kwargs)

    monkeypatch.setattr(harness, "_append_event", _capture_event)

    planner_dir = Path(str(harness.run.get("run_files", {}).get("planner", "") or ""))

    def _slow_with_progress(_prompt: str, analysis_spec=None, **_kwargs):
        time.sleep(0.3)
        (planner_dir / "unit_progress_1.json").write_text("{}")
        time.sleep(0.55)
        (planner_dir / "unit_progress_2.json").write_text("{}")
        time.sleep(0.3)
        return {"thought_process": "extended", "plan": []}

    harness.orchestrator.think = _slow_with_progress  # type: ignore[method-assign]

    plan, elapsed = harness._planner_attempt_with_heartbeat(
        prompt="test",
        strategy="direct_user_prompt",
        attempt_num=1,
    )

    assert plan["plan"] == []
    assert elapsed >= 1.0
    assert any(event.get("event_type") == "PLANNER_ATTEMPT_TIMEOUT_EXTENDED" for event in events)
    assert not any(event.get("event_type") == "PLANNER_ATTEMPT_TIMEOUT_FORCED" for event in events)


def test_planner_trace_latest_artifact_skips_disappearing_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test trace scan",
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

    class _FakeEntry:
        def __init__(self, name: str, *, mtime: float | None = None, error: OSError | None = None):
            self.name = name
            self._mtime = mtime
            self._error = error

        def is_file(self):
            if self._error is not None:
                raise self._error
            return True

        def stat(self):
            if self._error is not None:
                raise self._error
            return type("_Stat", (), {"st_mtime": self._mtime})()

    class _FakeScandir:
        def __enter__(self):
            return iter(
                [
                    _FakeEntry("deleted.json", error=FileNotFoundError("gone")),
                    _FakeEntry("latest.json", mtime=17.0),
                ]
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_supervision.os.scandir",
        lambda _path: _FakeScandir(),
    )

    latest_mtime, latest_name = harness._planner_trace_latest_artifact(str(tmp_path))

    assert latest_mtime == 17.0
    assert latest_name == "latest.json"


def test_planner_attempt_with_heartbeat_ignores_trace_scan_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test trace scan fallback",
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
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROGRESS_GRACE_SECONDS", "1")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROGRESS_MAX_EXTENSION_SECONDS", "3")
    monkeypatch.setenv("BIO_HARNESS_PLANNER_PROGRESS_POLL_SECONDS", "0.1")
    monkeypatch.setattr(harness, "_planner_connectivity_wait_seconds", lambda: 0)

    events: list[dict[str, object]] = []
    original_append = harness._append_event

    def _capture_event(**kwargs):
        events.append(kwargs)
        return original_append(**kwargs)

    monkeypatch.setattr(harness, "_append_event", _capture_event)
    monkeypatch.setattr(
        harness,
        "_planner_trace_latest_artifact",
        lambda _planner_trace_dir: (_ for _ in ()).throw(FileNotFoundError("trace file removed during scan")),
    )

    harness.orchestrator.think = lambda *_args, **_kwargs: {
        "thought_process": "ok",
        "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo ok"}, "step_id": 1}],
    }  # type: ignore[method-assign]

    plan, _elapsed = harness._planner_attempt_with_heartbeat(
        prompt="test",
        strategy="direct_user_prompt",
        attempt_num=1,
    )

    assert plan["plan"][0]["tool_name"] == "bash_run"
    assert any(event.get("event_type") == "PLANNER_TRACE_PROGRESS_SCAN_SKIPPED" for event in events)


def test_planner_attempt_with_heartbeat_process_isolation_forces_wall_clock_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test process timeout",
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
    monkeypatch.setattr(harness, "_planner_connectivity_wait_seconds", lambda: 0)

    clock = {"t": 0.0}
    events: list[dict[str, object]] = []
    call_order: list[str] = []

    class _FakeConn:
        def poll(self, timeout=None):
            clock["t"] += float(timeout or 0.0)
            return False

        def recv(self):
            raise EOFError("empty")

        def close(self):
            return None

    class _FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 4242
            self.exitcode = None
            self._alive = True

        def start(self):
            return None

        def is_alive(self):
            return self._alive

        def terminate(self):
            call_order.append("terminate")
            self._alive = False
            self.exitcode = -15

        def join(self, timeout=None):
            return None

        def kill(self):
            call_order.append("kill")
            self._alive = False
            self.exitcode = -9

    class _FakeContext:
        def Pipe(self, duplex=False):
            assert duplex is False
            return _FakeConn(), _FakeConn()

        def Process(self, target=None, args=(), daemon=True):
            return _FakeProcess()

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            return None

        def join(self, timeout=None):
            call_order.append("heartbeat_join")
            return None

    monkeypatch.setattr(
        harness.orchestrator.biollm,
        "is_available",
        lambda: (True, "ready"),
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_supervision.threading.Thread",
        _FakeThread,
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_supervision.time.monotonic",
        lambda: clock["t"],
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_supervision.time.sleep",
        lambda seconds: clock.__setitem__("t", clock["t"] + float(seconds or 0.0)),
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_settings.mp.get_all_start_methods",
        lambda: ["spawn"],
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_supervision.mp.get_context",
        lambda _method: _FakeContext(),
    )
    monkeypatch.setattr(
        harness,
        "_append_event",
        lambda **kwargs: events.append(kwargs),
    )

    with pytest.raises(TimeoutError):
        harness._planner_attempt_with_heartbeat(prompt="test", strategy="direct_user_prompt", attempt_num=1)

    timeout_events = [
        event
        for event in events
        if event.get("event_type") == "PLANNER_ATTEMPT_TIMEOUT_FORCED"
    ]
    assert timeout_events
    assert timeout_events[-1]["payload"]["enforcement_mode"] == "process_isolation"
    assert "heartbeat_join" in call_order
    assert "terminate" in call_order
    assert call_order.index("heartbeat_join") < call_order.index("terminate")


def test_planner_process_start_method_prefers_spawn_on_darwin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test start method",
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
    monkeypatch.setattr(
        "scripts.run_agent_e2e_planner_settings.mp.get_all_start_methods",
        lambda: ["fork", "spawn"],
    )
    monkeypatch.setattr("scripts.run_agent_e2e_planner_settings.sys.platform", "darwin")

    assert harness._planner_process_start_method() == "spawn"
def test_planner_process_isolation_connectivity_falls_back_to_inproc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.run_agent_e2e as harness_mod

    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test connectivity fallback",
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

    class _FakeConn:
        def __init__(self):
            self._items = [{"ok": False, "error": "Failed to connect to Ollama: Connection refused"}]
            self.closed = False

        def poll(self, timeout=None):
            return bool(self._items)

        def recv(self):
            if self._items:
                return self._items.pop(0)
            raise EOFError("empty")

        def close(self):
            self.closed = True
            return None

    class _FakeProcess:
        def __init__(self, *args, **kwargs):
            self._alive = False

        def start(self):
            return None

        def is_alive(self):
            return self._alive

        def terminate(self):
            return None

        def join(self, timeout=None):
            return None

        def kill(self):
            return None

    class _FakeContext:
        def Pipe(self, duplex=False):
            assert duplex is False
            return _FakeConn(), _FakeConn()

        def Process(self, target=None, args=(), daemon=True):
            return _FakeProcess()

    monkeypatch.setattr(harness_mod.mp, "get_all_start_methods", lambda: ["fork"])
    monkeypatch.setattr(harness_mod.mp, "get_context", lambda _method: _FakeContext())

    expected_plan = {"thought_process": "inproc", "plan": []}
    calls = {"n": 0}

    def _think(_prompt: str, analysis_spec=None):
        calls["n"] += 1
        return expected_plan

    harness.orchestrator.think = _think  # type: ignore[method-assign]
    plan, _elapsed = harness._planner_attempt_with_heartbeat(
        prompt="test",
        strategy="direct_user_prompt",
        attempt_num=1,
    )
    assert plan == expected_plan
    assert calls["n"] == 1
