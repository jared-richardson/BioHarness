from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_evolution_spades_repair_rebinds_external_reference_to_ancestor_scaffolds(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "anc_R1.fastq.gz",
                    "reads_2": "anc_R2.fastq.gz",
                    "output_dir": str(selected_dir / "assembly_ancestor"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(selected_dir / "inputs_readonly" / "mouse_fasta"),
                    "reads_1": "evol1_R1.fastq.gz",
                    "reads_2": "evol1_R2.fastq.gz",
                    "output_bam": str(selected_dir / "alignments" / "isolate1.bam"),
                },
                "step_id": 2,
            },
        ]
    }
    repaired, meta = _repair_evolution_spades_reference_usage(plan, "experimental evolution variant calling relative to ancestor")
    assert meta.get("changed", False) is True
    assert repaired["plan"][1]["arguments"]["reference_fasta"].endswith("/assembly_ancestor/scaffolds.fasta")
def test_missing_fastq_repair_can_reassign_ancestor_reads_to_evolved_sample(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in ("anc_R1.fastq.gz", "anc_R2.fastq.gz", "evol1_R1.fastq.gz", "evol1_R2.fastq.gz"):
        (data_root / name).write_text("", encoding="utf-8")
    plan = {
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "alignments" / "isolate1_aligned.bam"),
                },
                "step_id": 1,
            }
        ]
    }
    repaired, meta = _repair_missing_fastq_inputs_in_plan(plan, selected_dir, data_root)
    assert meta.get("changed", False) is True
    args = repaired["plan"][0]["arguments"]
    assert args["reads_1"].endswith("evol1_R1.fastq.gz")
    assert args["reads_2"].endswith("evol1_R2.fastq.gz")
def test_missing_fastq_repair_rebinds_spades_ancestor_alias_to_discovered_inputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in ("anc_R1.fastq.gz", "anc_R2.fastq.gz"):
        (data_root / name).write_text("", encoding="utf-8")
    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "ancestor_R1.fastq.gz",
                    "reads_2": "ancestor_R2.fastq.gz",
                    "output_dir": "assemblies",
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_missing_fastq_inputs_in_plan(plan, selected_dir, data_root)

    assert meta.get("changed", False) is True
    args = repaired["plan"][0]["arguments"]
    assert args["reads_1"].endswith("anc_R1.fastq.gz")
    assert args["reads_2"].endswith("anc_R2.fastq.gz")
def test_missing_fastq_repair_preserves_planned_trimmed_pair_outputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "sample_R1.fastq.gz").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (data_root / "sample_R2.fastq.gz").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    trimmed_r1 = selected_dir / "output" / "trimmed_R1.fastq"
    trimmed_r2 = selected_dir / "output" / "trimmed_R2.fastq"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"fastp -i {data_root / 'sample_R1.fastq.gz'} -I {data_root / 'sample_R2.fastq.gz'} "
                        f"-o {trimmed_r1} -O {trimmed_r2}"
                    ),
                },
                "step_id": 1,
            },
            {
                "tool_name": "minimap2_align",
                "arguments": {
                    "reference_fasta": str(selected_dir / "output" / "viral_references_combined.fasta"),
                    "reads_1": str(trimmed_r1),
                    "reads_2": str(trimmed_r2),
                    "output_bam": str(selected_dir / "output" / "aligned_to_viral_refs.bam"),
                },
                "step_id": 2,
            },
        ]
    }

    repaired, meta = _repair_missing_fastq_inputs_in_plan(plan, selected_dir, data_root)

    assert meta.get("changed", False) is False
    args = repaired["plan"][1]["arguments"]
    assert args["reads_1"] == str(trimmed_r1)
    assert args["reads_2"] == str(trimmed_r2)
def test_missing_input_paths_ignore_planned_bash_run_outputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "sample_R1.fastq.gz").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (data_root / "sample_R2.fastq.gz").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")
    ref_dir = selected_dir / "refs_src"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "NC_001416.1.fasta").write_text(">virus\nACGT\n", encoding="utf-8")

    panel_fasta = selected_dir / "output" / "viral_references_combined.fasta"
    trimmed_r1 = selected_dir / "output" / "trimmed_R1.fastq"
    trimmed_r2 = selected_dir / "output" / "trimmed_R2.fastq"
    output_bam = selected_dir / "output" / "aligned_to_viral_refs.bam"

    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"cat {ref_dir}/*.fasta > {panel_fasta}",
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"fastp -i {data_root / 'sample_R1.fastq.gz'} -I {data_root / 'sample_R2.fastq.gz'} "
                        f"-o {trimmed_r1} -O {trimmed_r2}"
                    ),
                },
                "step_id": 2,
            },
            {
                "tool_name": "minimap2_align",
                "arguments": {
                    "reference_fasta": str(panel_fasta),
                    "reads_1": str(trimmed_r1),
                    "reads_2": str(trimmed_r2),
                    "output_bam": str(output_bam),
                },
                "step_id": 3,
            },
        ]
    }

    missing = _missing_input_paths_for_plan(plan, selected_dir, data_root)

    assert missing == []
def test_manifest_zero_marker_sets_no_fastq_flag(tmp_path: Path):
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

    harness._update_run_markers_from_line("[Step 1 Output] [stdout] __FASTQ_MANIFEST_COUNT__:0")
    assert bool(harness.run.get("no_fastq_found", False)) is True
def test_bcftools_mpileup_stderr_marks_run_failed(tmp_path: Path):
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
    harness.run["status"] = "running"

    harness._update_run_markers_from_line("[Step 1 Output] [stderr] [mpileup] failed to read from input file")
    assert harness.run.get("status") == "failed"
    assert "bcftools mpileup failed" in str(harness.run.get("error", "")).lower()
    assert "bcftools_mpileup_input_error" in list(harness.run.get("failure_signatures", []))
def test_auto_recover_data_root_rebuilds_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected_dir = tmp_path / "workspace"
    recovered_root = selected_dir / "inputs_readonly" / "clip_1"
    recovered_root.mkdir(parents=True, exist_ok=True)
    (recovered_root / "S1_R1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
    (recovered_root / "S1_R2.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
    cfg = HarnessConfig(
        prompt="test",
        selected_dir=selected_dir,
        data_root=selected_dir / "inputs_readonly",
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
    harness.run["no_fastq_found"] = True
    fallback_plan = {"thought_process": "t", "plan": [{"tool_name": "bash_run", "arguments": {"command": "echo ok"}}]}
    monkeypatch.setattr(
        harness,
        "_build_contract_template_repair",
        lambda _failure_class: (fallback_plan, "template_unit_test", {"why": "unit_test"}),
    )

    assert harness._maybe_auto_recover_data_root() is True
    assert harness.cfg.data_root == recovered_root
    assert harness.run.get("plan") == fallback_plan
    assert harness.run.get("step_statuses") == ["pending"]
    assert harness.run.get("next_step_idx") == 0
def test_normalize_plan_keeps_evolution_alignment_steps_after_fastq_repair(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in ("anc_R1.fastq.gz", "anc_R2.fastq.gz", "evol1_R1.fastq.gz", "evol1_R2.fastq.gz", "evol2_R1.fastq.gz", "evol2_R2.fastq.gz"):
        (data_root / name).write_text("", encoding="utf-8")

    cfg = HarnessConfig(
        prompt="Identify and annotate genome variants in two evolved lines relative to an ancestor line of E. coli.",
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

    raw_plan = {
        "plan": [
            {"tool_name": "spades_assemble", "arguments": {"reads_1": "ancestor_R1.fastq.gz", "reads_2": "ancestor_R2.fastq.gz", "output_dir": "assemblies"}, "step_id": 1},
            {"tool_name": "spades_assemble", "arguments": {"reads_1": "evolved1_R1.fastq.gz", "reads_2": "evolved1_R2.fastq.gz", "output_dir": "assemblies"}, "step_id": 1},
            {"tool_name": "spades_assemble", "arguments": {"reads_1": "evolved2_R1.fastq.gz", "reads_2": "evolved2_R2.fastq.gz", "output_dir": "assemblies"}, "step_id": 1},
            {"tool_name": "bwa_mem_align", "arguments": {"reads_1": "evolved1_R1.fastq.gz", "reads_2": "evolved1_R2.fastq.gz", "reference_fasta": "assemblies/evolved1_contigs.fasta", "output_bam": "alignments/evolved1_vs_evolved1.bam"}, "step_id": 3},
            {"tool_name": "bwa_mem_align", "arguments": {"reads_1": "evolved2_R1.fastq.gz", "reads_2": "evolved2_R2.fastq.gz", "reference_fasta": "assemblies/evolved2_contigs.fasta", "output_bam": "alignments/evolved2_vs_evolved2.bam"}, "step_id": 3},
            {"tool_name": "freebayes_call", "arguments": {"input_bam": "alignments/evolved1_vs_evolved1.bam", "reference_fasta": "assemblies/evolved1_contigs.fasta", "output_vcf_gz": "variants/evolved1.vcf.gz"}, "step_id": 5},
            {"tool_name": "freebayes_call", "arguments": {"input_bam": "alignments/evolved2_vs_evolved2.bam", "reference_fasta": "assemblies/evolved2_contigs.fasta", "output_vcf_gz": "variants/evolved2.vcf.gz"}, "step_id": 6},
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert meta.get("changed", False) is True
    tool_names = [step["tool_name"] for step in normalized["plan"]]
    assert tool_names.count("bwa_mem_align") == 2
    assert _missing_input_paths_for_plan(normalized, selected_dir, data_root) == []
def test_normalize_plan_repairs_evolution_multi_bam_variant_segment(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in ("anc_R1.fastq.gz", "anc_R2.fastq.gz", "evol1_R1.fastq.gz", "evol1_R2.fastq.gz", "evol2_R1.fastq.gz", "evol2_R2.fastq.gz"):
        (data_root / name).write_text("", encoding="utf-8")

    cfg = HarnessConfig(
        prompt="Identify and annotate genome variants in two evolved lines relative to an ancestor line of E. coli.",
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
    harness.run["analysis_spec"] = {"analysis_type": "bacterial_evolution_variant_calling"}

    raw_plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_dir": str(selected_dir / "assembly_anc"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "assembly" / "spades_contigs.fasta"),
                    "output_gff": str(selected_dir / "annotation" / "prodigal.gff"),
                    "output_faa": str(selected_dir / "annotation" / "prodigal.faa"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(data_root / "rel606.fasta"),
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "alignments" / "anc.bam"),
                },
                "step_id": 3,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": str(data_root / "rel606.fasta"),
                    "input_bam": [
                        str(selected_dir / "alignments" / "anc_sorted.bam"),
                        str(selected_dir / "alignments" / "evol1_sorted.bam"),
                        str(selected_dir / "alignments" / "evol2_sorted.bam"),
                    ],
                    "output_vcf_gz": str(selected_dir / "freebayes" / "raw_variants.vcf.gz"),
                    "ploidy": 1,
                },
                "step_id": 4,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "ecoli_k12_mg1655",
                    "input_vcf": str(selected_dir / "variants_raw.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants_annotated.vcf"),
                },
                "step_id": 5,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"bcftools query -f '%CHROM\\t%POS\\t%REF\\t%ALT\\n' > {selected_dir / 'final' / 'variants_shared.csv'}",
                },
                "step_id": 6,
            },
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert meta.get("changed", False) is True
    assert "evolution_branch_repairs" in meta
    tool_names = [step["tool_name"] for step in normalized["plan"]]
    assert tool_names.count("bwa_mem_align") == 2
    assert tool_names.count("freebayes_call") == 2
    assert tool_names.count("snpeff_annotate") == 2
    assert tool_names.count("bash_run") == 5
    freebayes_inputs = [
        step["arguments"]["input_bam"]
        for step in normalized["plan"]
        if step["tool_name"] == "freebayes_call"
    ]
    assert all(isinstance(value, str) for value in freebayes_inputs)
    assert _missing_input_paths_for_plan(normalized, selected_dir, data_root) == []
    bash_commands = [
        step["arguments"]["command"]
        for step in normalized["plan"]
        if step["tool_name"] == "bash_run"
    ]
    assert any("vcffilter" in command or "bcftools filter" in command for command in bash_commands)
    assert any("bcftools norm" in command for command in bash_commands)
    export_step = normalized["plan"][-1]
    assert export_step["tool_name"] == "bash_run"
    assert "export_shared_variants_csv.py" in export_step["arguments"]["command"]
    assert str(selected_dir / "final" / "variants_shared.csv") in export_step["arguments"]["command"]
def test_normalize_plan_planning_strict_skips_evolution_template_repairs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in ("anc_R1.fastq.gz", "anc_R2.fastq.gz", "evol1_R1.fastq.gz", "evol1_R2.fastq.gz", "evol2_R1.fastq.gz", "evol2_R2.fastq.gz"):
        (data_root / name).write_text("", encoding="utf-8")

    cfg = HarnessConfig(
        prompt="Identify and annotate genome variants in two evolved lines relative to an ancestor line of E. coli.",
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
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    }

    raw_plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_dir": str(selected_dir / "assembly_anc"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": [
                        str(selected_dir / "alignments" / "anc_sorted.bam"),
                        str(selected_dir / "alignments" / "evol1_sorted.bam"),
                        str(selected_dir / "alignments" / "evol2_sorted.bam"),
                    ],
                    "output_vcf_gz": str(selected_dir / "freebayes" / "raw_variants.vcf.gz"),
                    "ploidy": 1,
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"awk '{{print $0}}' > {selected_dir / 'final' / 'variants_shared.csv'}",
                },
                "step_id": 3,
            },
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert "evolution_branch_repairs" not in meta
    assert "shared_variant_csv_repairs" not in meta
    assert "strict_direct_plan_rebinding" in meta
    assert normalized["plan"][1]["tool_name"] == "freebayes_call"
    assert "export_shared_variants_csv.py" in normalized["plan"][2]["arguments"]["command"]
    assert "--header-case upper" in normalized["plan"][2]["arguments"]["command"]
