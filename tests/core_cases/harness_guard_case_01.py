from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_first_failed_step_number_avoids_phantom_step_after_completion():
    statuses = ["completed", "completed", "completed"]
    assert _first_failed_step_number(statuses, fallback_next_idx=3) == 0
def test_missing_local_scripts_for_plan_detects_nonexistent_script(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    existing = selected_dir / "ok.sh"
    existing.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")

    missing_abs = selected_dir / "missing.sh"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"bash {existing.as_posix()} ; "
                        f"bash {missing_abs.as_posix()} ; "
                        "python -c 'print(1)'"
                    )
                },
            }
        ]
    }

    missing = _missing_local_scripts_for_plan(plan, selected_dir)
    assert missing == [str(missing_abs.resolve(strict=False))]
def test_repair_missing_fastq_inputs_rebinds_guessed_sample_paths(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly" / "clip_1"
    data_root.mkdir(parents=True, exist_ok=True)
    real_r1 = data_root / "1_S1_R1_001.fastq"
    real_r2 = data_root / "1_S1_R2_001.fastq"
    real_r1.write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
    real_r2.write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "subread_align",
                "arguments": {
                    "reads_1": str(data_root / "S1_1.fastq"),
                    "reads_2": str(data_root / "S1_2.fastq"),
                    "reference_fasta": "mouse_fasta",
                    "output_bam": str(selected_dir / "outputs" / "S1.bam"),
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_missing_fastq_inputs_in_plan(plan, selected_dir, data_root)

    assert meta.get("changed", False) is True
    args = repaired["plan"][0]["arguments"]
    assert args["reads_1"] == str(real_r1)
    assert args["reads_2"] == str(real_r2)
def test_find_workspace_reference_prefers_requested_alias_in_workspace(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly" / "clip_1"
    data_root.mkdir(parents=True, exist_ok=True)
    mouse_fasta = selected_dir / "inputs_readonly" / "mouse_fasta"
    mouse_gtf = selected_dir / "inputs_readonly" / "mouse_gtf"
    mouse_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    mouse_gtf.write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")

    assert _find_workspace_reference("fasta", "references are mouse_fasta and mouse_gtf", selected_dir, data_root) == str(mouse_fasta)
    assert _find_workspace_reference("gtf", "references are mouse_fasta and mouse_gtf", selected_dir, data_root) == str(mouse_gtf)
def test_reference_and_index_repair_rebinds_external_paths_and_stabilizes_subread_cache(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly" / "clip_1"
    data_root.mkdir(parents=True, exist_ok=True)
    mouse_fasta = selected_dir / "inputs_readonly" / "mouse_fasta"
    mouse_gtf = selected_dir / "inputs_readonly" / "mouse_gtf"
    mouse_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    mouse_gtf.write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "subread_align",
                "arguments": {
                    "index_base": "/Users/clip_1",
                    "reads_1": "a_R1.fastq",
                    "reads_2": "a_R2.fastq",
                    "reference_fasta": "/tmp/external/genome.fa",
                    "output_bam": str(selected_dir / "outputs" / "S1.bam"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "featurecounts_run",
                "arguments": {
                    "annotation_gtf": "/tmp/external/genes.gtf",
                    "input_bams": str(selected_dir / "outputs" / "S1.bam"),
                    "output_counts": str(selected_dir / "outputs" / "counts.txt"),
                },
                "step_id": 2,
            },
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "My references are mouse_gtf and mouse_fasta.",
    )

    assert meta.get("changed", False) is True
    subread_args = repaired["plan"][0]["arguments"]
    assert subread_args["reference_fasta"] == str(mouse_fasta)
    assert subread_args["index_base"] == _stable_index_base_for_tool("subread_align", selected_dir, str(mouse_fasta))
    assert repaired["plan"][1]["arguments"]["annotation_gtf"] == str(mouse_gtf)
def test_reference_and_index_repair_injects_transcriptome_for_quantification(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly" / "bench"
    data_root.mkdir(parents=True, exist_ok=True)
    transcriptome = data_root / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "kallisto_quant",
                "arguments": {
                    "index_path": str(transcriptome),
                    "reads_1": str(data_root / "reads_1.fq.gz"),
                    "reads_2": str(data_root / "reads_2.fq.gz"),
                    "output_dir": str(selected_dir / "outputs" / "kallisto_quant"),
                    "threads": 4,
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "Use the provided transcriptome reference for transcript quantification.",
    )

    assert meta.get("changed", False) is True
    args = repaired["plan"][0]["arguments"]
    assert args["transcriptome_fasta"] == str(transcriptome)
def test_filter_missing_plan_inputs_ignores_prior_step_outputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    counts_path = selected_dir / "outputs" / "counts.tsv"
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
        "plan": [
            {
                "tool_name": "featurecounts_run",
                "arguments": {"output_counts": str(counts_path)},
                "step_id": 1,
            }
        ]
    }

    filtered = harness._filter_missing_plan_inputs(
        [str(counts_path), str(selected_dir / "missing" / "reference.fa")]
    )

    assert filtered == [str(selected_dir / "missing" / "reference.fa")]
def test_reference_and_index_repair_uses_planned_gff_conversion_output_for_star(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly" / "bench"
    data_root.mkdir(parents=True, exist_ok=True)
    converted_gtf = selected_dir / "references" / "annotation_from_gff.gtf"

    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 /repo/bio_harness/pipeline_scripts/gff3_to_gtf.py "
                        "/refs/genes.gff "
                        f"{converted_gtf}"
                    )
                },
                "step_id": 1,
            },
            {
                "tool_name": "star_align",
                "arguments": {
                    "annotation_gtf": "/tmp/external/genes.gtf",
                    "genome_dir": str(selected_dir / "star_index"),
                    "reads_1": str(data_root / "S1_1.fastq"),
                    "reads_2": str(data_root / "S1_2.fastq"),
                    "output_prefix": str(selected_dir / "alignments" / "S1"),
                },
                "step_id": 2,
            },
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "Run DESeq on the provided paired-end RNA-seq samples.",
    )

    assert meta.get("changed", False) is True
    assert repaired["plan"][1]["arguments"]["annotation_gtf"] == str(converted_gtf)
def test_collect_planned_output_paths_includes_gff_conversion_output(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    converted_gtf = selected_dir / "references" / "annotation_from_gff.gtf"
    counts_path = selected_dir / "counts" / "gene_counts.txt"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 /repo/bio_harness/pipeline_scripts/gff3_to_gtf.py "
                        "/refs/genes.gff "
                        f"{converted_gtf}"
                    )
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 /repo/bio_harness/pipeline_scripts/build_star_gene_counts_matrix.py "
                        f"{counts_path} "
                        "S1=/alignments/S1ReadsPerGene.out.tab"
                    )
                },
                "step_id": 2,
            },
        ]
    }

    planned = _collect_planned_output_paths(plan, selected_dir)

    assert str(converted_gtf) in planned
    assert str(counts_path) in planned
def test_reference_and_index_repair_rehomes_salmon_index_under_selected_dir(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly" / "bench"
    data_root.mkdir(parents=True, exist_ok=True)
    transcriptome = data_root / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "salmon_quant",
                "arguments": {
                    "index_dir": str(data_root),
                    "reads_1": str(data_root / "reads_1.fq.gz"),
                    "reads_2": str(data_root / "reads_2.fq.gz"),
                    "output_dir": str(selected_dir / "outputs" / "salmon"),
                    "threads": 4,
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "Use the provided transcriptome reference for transcript quantification.",
    )

    assert meta.get("changed", False) is True
    args = repaired["plan"][0]["arguments"]
    assert args["transcriptome_fasta"] == str(transcriptome)
    assert str(selected_dir) in args["index_dir"]
    assert args["library_type"] == "A"
def test_reference_and_index_repair_preserves_task_local_generated_fasta_reference(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly" / "viral"
    data_root.mkdir(parents=True, exist_ok=True)
    mouse_fasta = selected_dir / "inputs_readonly" / "mouse_fasta"
    mouse_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "minimap2_align",
                "arguments": {
                    "reads_1": str(data_root / "sample_R1.fastq.gz"),
                    "reads_2": str(data_root / "sample_R2.fastq.gz"),
                    "reference_fasta": "output/viral_references_combined.fasta",
                    "output_bam": str(selected_dir / "output" / "aligned.bam"),
                    "preset": "sr",
                },
                "step_id": 3,
            }
        ]
    }

    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        "Use the provided local viral panel and do not use the mouse reference.",
    )

    assert repaired["plan"][0]["arguments"]["reference_fasta"] == "output/viral_references_combined.fasta"
    replacements = meta.get("replacements", [])
    assert not any(item.get("argument") == "reference_fasta" for item in replacements)
def test_preflight_skips_group_requirements_for_single_sample_quantification(tmp_path: Path):
    data_root = tmp_path / "inputs"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "reads_1.fq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "reads_2.fq.gz").write_text("stub\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "kallisto_quant",
                "arguments": {
                    "index_path": str(tmp_path / "cache" / "transcripts.idx"),
                    "transcriptome_fasta": str(data_root / "transcriptome.fa"),
                    "reads_1": str(data_root / "reads_1.fq.gz"),
                    "reads_2": str(data_root / "reads_2.fq.gz"),
                    "output_dir": str(tmp_path / "out"),
                    "threads": 4,
                },
            }
        ]
    }
    contract = {"must_include_capabilities": ["quantification", "reference_inputs"]}

    issues = _preflight_execution_issues(plan, data_root, contract, tmp_path / "workspace")

    assert issues["missing_fastq"] is False
    assert issues["missing_groups"] == []
def test_preflight_ignores_bash_generated_fasta_outputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = tmp_path / "inputs"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    transcriptome = data_root / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")
    (data_root / "reads_1.fq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "reads_2.fq.gz").write_text("stub\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "salmon_quant",
                "arguments": {
                    "index_dir": str(selected_dir / "outputs" / "_cache" / "salmon"),
                    "transcriptome_fasta": str(transcriptome),
                    "reads_1": str(data_root / "reads_1.fq.gz"),
                    "reads_2": str(data_root / "reads_2.fq.gz"),
                    "output_dir": str(selected_dir / "salmon_out"),
                    "threads": 4,
                    "library_type": "A",
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"zcat {data_root / 'transcriptome.fa.gz'} | head -n 10 > {selected_dir / 'final' / 'subset.fa'} "
                        f"2>/dev/null || cp {transcriptome} {selected_dir / 'final' / 'transcriptome.fa'}"
                    )
                },
            },
        ]
    }
    contract = {"must_include_capabilities": ["quantification", "reference_inputs"]}

    issues = _preflight_execution_issues(plan, data_root, contract, selected_dir)

    assert issues["missing_references"] == []


def test_preflight_does_not_require_fastq_for_bam_driven_rmats(tmp_path: Path) -> None:
    data_root = tmp_path / "inputs"
    data_root.mkdir(parents=True, exist_ok=True)
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan": [
            {
                "tool_name": "rmats_run",
                "arguments": {
                    "b1": str(data_root / "control_sorted.bam"),
                    "b2": str(data_root / "treatment_sorted.bam"),
                    "gtf": str(data_root / "genes.gtf"),
                    "output_dir": str(selected_dir / "splicing"),
                },
            }
        ]
    }
    contract = {"must_include_capabilities": ["splicing_analysis", "reference_inputs"]}

    issues = _preflight_execution_issues(
        plan,
        data_root,
        contract,
        selected_dir,
        analysis_type="alternative_splicing",
        analysis_spec={
            "execution_contract": {
                "input_mode": "aligned_bam",
                "execution_mode": "direct_wrapper",
            }
        },
    )

    assert issues["missing_fastq"] is False
def test_bash_redirection_repair_creates_parent_dirs_under_selected_dir(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"awk 'NR>1 {{print $1}}' {selected_dir / 'in.tsv'} > {selected_dir / 'final' / 'out.tsv'}"
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_bash_redirection_output_dirs(plan, selected_dir)

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert command.startswith("mkdir -p ")
    assert str(selected_dir / "final") in command
def test_bash_tool_output_parent_dir_repair_handles_fastp_outputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    out1 = selected_dir / "preprocessed" / "sample_R1_trimmed.fastq.gz"
    out2 = selected_dir / "preprocessed" / "sample_R2_trimmed.fastq.gz"
    json_out = selected_dir / "qc" / "fastp.json"
    html_out = selected_dir / "qc" / "fastp.html"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"fastp --in1 reads_R1.fastq.gz --in2 reads_R2.fastq.gz "
                        f"--out1 {out1} --out2 {out2} --json {json_out} --html {html_out}"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_bash_tool_output_parent_dirs(plan, selected_dir)

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert command.startswith("mkdir -p ")
    assert str(selected_dir / "preprocessed") in command
    assert str(selected_dir / "qc") in command
def test_bash_tool_output_parent_dir_repair_handles_kraken2_outputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    report_out = selected_dir / "output" / "sample_kraken2_report.txt"
    stdout_out = selected_dir / "output" / "sample_kraken2_output.txt"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "kraken2 --db /tmp/db --paired reads_R1.fastq.gz reads_R2.fastq.gz "
                        f"--report {report_out} --output {stdout_out}"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_bash_tool_output_parent_dirs(plan, selected_dir)

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert command.startswith("mkdir -p ")
def test_cystic_fibrosis_export_repair_appends_deterministic_export_step(tmp_path: Path):
    selected_dir = tmp_path / "run"
    data_root = tmp_path / "task" / "data"
    references = tmp_path / "task" / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (data_root / "ex1.eff.vcf").write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12877\tNA12878\tNA12879\tNA12880\tNA12885\tNA12886\n",
        encoding="utf-8",
    )
    (data_root / "family_description.txt").write_text(
        "- Father: NA12877 (unaffected male)\n- Mother: NA12878 (unaffected female)\n1. NA12879 (affected female)\n7. NA12885 (affected female)\n8. NA12886 (affected male)\n",
        encoding="utf-8",
    )
    (references / "clinvar_20250521.vcf.gz").write_text("stub\n", encoding="utf-8")
    analysis_spec = {"analysis_type": "variant_annotation", "context_facts": ["clinical relevance filtering"]}
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {"command": "echo existing"},
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_cystic_fibrosis_csv_exports_with_analysis_spec(
        plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
        request_text="Find the causal cystic fibrosis CFTR variant and write cf_variants.csv",
    )

    assert meta.get("changed", False) is True
    export_step = repaired["plan"][-1]
    assert export_step["tool_name"] == "bash_run"
    assert "export_cystic_fibrosis_csv.py" in export_step["arguments"]["command"]
    assert str(selected_dir / "final" / "cf_variants.csv") in export_step["arguments"]["command"]
