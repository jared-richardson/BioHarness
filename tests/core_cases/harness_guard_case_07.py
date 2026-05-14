from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_normalize_plan_repairs_evolution_sorted_bam_path_bindings(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in ("ancestor_R1.fastq.gz", "ancestor_R2.fastq.gz", "evol1_R1.fastq.gz", "evol1_R2.fastq.gz", "evol2_R1.fastq.gz", "evol2_R2.fastq.gz"):
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
                    "reads_1": str(data_root / "ancestor_R1.fastq.gz"),
                    "reads_2": str(data_root / "ancestor_R2.fastq.gz"),
                    "output_dir": str(selected_dir / "assembly"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "output_gff": str(selected_dir / "annotation" / "prodigal.gff"),
                    "output_faa": str(selected_dir / "annotation" / "prodigal.faa"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "reads_1": str(data_root / "evol1_R1.fastq.gz"),
                    "reads_2": str(data_root / "evol1_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "alignments" / "evol1.sorted.bam"),
                },
                "step_id": 3,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "input_bam": str(selected_dir / "alignments" / "evol1.sorted.bam"),
                    "output_vcf_gz": str(selected_dir / "variants" / "evol1.raw.vcf.gz"),
                    "ploidy": 1,
                },
                "step_id": 4,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "ecoli_custom",
                    "input_vcf": str(selected_dir / "variants" / "evol1.raw.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol1.annotated.vcf"),
                },
                "step_id": 5,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "reads_1": str(data_root / "evol2_R1.fastq.gz"),
                    "reads_2": str(data_root / "evol2_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "alignments" / "evol2.sorted.bam"),
                },
                "step_id": 6,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "input_bam": str(selected_dir / "alignments" / "evol2.sorted.bam"),
                    "output_vcf_gz": str(selected_dir / "variants" / "evol2.raw.vcf.gz"),
                    "ploidy": 1,
                },
                "step_id": 7,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "ecoli_custom",
                    "input_vcf": str(selected_dir / "variants" / "evol2.raw.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol2.annotated.vcf"),
                },
                "step_id": 8,
            },
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert meta.get("changed", False) is True
    assert "evolution_alignment_path_repairs" in meta
    expected_evol1_bam = str(selected_dir / "alignments" / "evol1.bam")
    expected_evol2_bam = str(selected_dir / "alignments" / "evol2.bam")
    align_bams = [
        step["arguments"]["output_bam"]
        for step in normalized["plan"]
        if step["tool_name"] == "bwa_mem_align"
    ]
    assert align_bams == [expected_evol1_bam, expected_evol2_bam]
    freebayes_inputs = [
        step["arguments"]["input_bam"]
        for step in normalized["plan"]
        if step["tool_name"] == "freebayes_call"
    ]
    assert freebayes_inputs == [expected_evol1_bam, expected_evol2_bam]
    assert _missing_input_paths_for_plan(normalized, selected_dir, data_root) == []
def test_normalize_plan_applies_single_cell_analysis_spec_parameter_profile(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in ("sample_R1.fastq.gz", "sample_R2.fastq.gz", "reference.fa", "annotation.gtf", "barcodes_whitelist.txt"):
        (data_root / name).write_text("", encoding="utf-8")

    cfg = HarnessConfig(
        prompt="Analyze the single-cell RNA-seq data to cluster cells and identify marker genes.",
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
        "analysis_type": "single_cell_rna_seq",
        "parameter_profile": [
            {
                "tool_name": "sc_count_and_cluster",
                "settings": {
                    "min_genes": 3,
                    "min_cells": 1,
                    "kmer_size": 25,
                    "leiden_resolution": 0.5,
                },
            },
        ],
    }

    raw_plan = {
        "plan": [
            {
                "tool_name": "sc_count_and_cluster",
                "arguments": {
                    "r1": str(data_root / "sample_R1.fastq.gz"),
                    "r2": str(data_root / "sample_R2.fastq.gz"),
                    "whitelist": str(data_root / "barcodes_whitelist.txt"),
                    "reference": str(data_root / "reference.fa"),
                    "gtf": str(data_root / "annotation.gtf"),
                    "output_dir": str(selected_dir / "sc_output"),
                    "min_genes": 200,
                    "min_cells": 3,
                    "kmer_size": 21,
                    "leiden_resolution": 0.8,
                },
                "step_id": 1,
            },
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert meta.get("changed", False) is True
    assert "analysis_spec_parameter_profile_repairs" in meta
    args = normalized["plan"][0]["arguments"]
    assert args["min_genes"] == 3
    assert args["min_cells"] == 1
    assert args["kmer_size"] == 25
    assert args["leiden_resolution"] == 0.5
def test_normalize_plan_repairs_single_cell_qc_thresholds_in_official_mode(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in ("sample_R1.fastq.gz", "sample_R2.fastq.gz", "reference.fa", "annotation.gtf", "barcodes_whitelist.txt"):
        (data_root / name).write_text("", encoding="utf-8")

    cfg = HarnessConfig(
        prompt="Analyze single-cell RNA-seq data from pre- and post-exercise skeletal muscle samples.",
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
    harness.run["analysis_spec"] = {"analysis_type": "single_cell_rna_seq", "parameter_profile": []}
    harness.run["benchmark_policy"] = OFFICIAL_BIOAGENTBENCH_POLICY

    raw_plan = {
        "plan": [
            {
                "tool_name": "sc_count_and_cluster",
                "arguments": {
                    "r1": str(data_root / "sample_R1.fastq.gz"),
                    "r2": str(data_root / "sample_R2.fastq.gz"),
                    "whitelist": str(data_root / "barcodes_whitelist.txt"),
                    "reference": str(data_root / "reference.fa"),
                    "gtf": str(data_root / "annotation.gtf"),
                    "output_dir": str(selected_dir),
                    "min_genes": 200,
                    "min_cells": 3,
                    "kmer_size": 17,
                    "leiden_resolution": 1.2,
                },
                "step_id": 1,
            }
        ]
    }

    normalized, meta, _ = harness._normalize_plan_for_execution(raw_plan)

    args = normalized["plan"][0]["arguments"]
    assert meta.get("single_cell_qc_threshold_repairs", {}).get("changed") is True
    assert args["min_genes"] == 3
    assert args["min_cells"] == 1
    assert args["kmer_size"] == 25
    assert args["leiden_resolution"] == 0.5
def test_normalize_plan_repairs_variant_annotation_impact_filter_command(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)

    cfg = HarnessConfig(
        prompt="Annotate variants and filter for HIGH or MODERATE impact calls.",
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

    annotated_vcf = selected_dir / "output" / "annotated.vcf"
    filtered_vcf = selected_dir / "output" / "filtered_pathogenic.vcf"
    raw_plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(data_root / "input.vcf"),
                    "output_vcf": str(annotated_vcf),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools filter -i 'INFO/IMPACT=\"HIGH\" || INFO/IMPACT=\"MODERATE\"' "
                        f"{annotated_vcf} > {filtered_vcf}"
                    ),
                },
                "step_id": 2,
            },
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert meta.get("changed", False) is True
    assert "variant_annotation_impact_filter_repairs" in meta
    repaired_command = normalized["plan"][1]["arguments"]["command"]
    assert "SnpSift filter" in repaired_command
    assert str(annotated_vcf) in repaired_command
    assert str(filtered_vcf) in repaired_command
def test_normalize_plan_repairs_variant_annotation_impact_filter_command_with_output_flag(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="variant annotation benchmark",
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

    annotated_vcf = selected_dir / "output" / "annotated.vcf"
    filtered_vcf = selected_dir / "output" / "filtered_pathogenic.vcf"
    raw_plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(data_root / "input.vcf"),
                    "output_vcf": str(annotated_vcf),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools filter -i 'INFO/IMPACT=\"HIGH\" || INFO/IMPACT=\"MODERATE\"' "
                        f"{annotated_vcf} -O v -o {filtered_vcf}"
                    ),
                },
                "step_id": 2,
            },
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert meta.get("changed", False) is True
    assert "variant_annotation_impact_filter_repairs" in meta
    repaired_command = normalized["plan"][1]["arguments"]["command"]
    assert "SnpSift filter" in repaired_command
    assert str(annotated_vcf) in repaired_command
    assert str(filtered_vcf) in repaired_command


def test_normalize_plan_preserves_explicit_scanpy_arguments_in_scientific_mode(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    input_h5ad = data_root / "pbmc3k_processed.h5ad"
    input_h5ad.write_text("h5ad", encoding="utf-8")

    cfg = HarnessConfig(
        prompt=(
            f"Use only the scanpy_workflow tool on {input_h5ad} and write outputs under "
            f"{selected_dir / 'scanpy_output'} using min_genes 3, min_cells 1, "
            "max_mito_pct 100, n_hvgs 48, and leiden_resolution 0.3."
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
        benchmark_policy="scientific_harness",
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    harness.run["analysis_spec"] = {
        "analysis_type": "single_cell_rna_seq",
        "parameter_profile": [
            {
                "tool_name": "scanpy_workflow",
                "settings": {
                    "min_genes": 300,
                    "min_cells": 20,
                    "max_mito_pct": 15,
                    "n_hvgs": 2000,
                    "leiden_resolution": 0.3,
                },
            }
        ],
        "explicit_execution_intent": {
            "locked_tools": ["scanpy_workflow"],
            "preserve_existing_values_for_tools": ["scanpy_workflow"],
        },
    }
    harness.run["benchmark_policy"] = "scientific_harness"

    raw_plan = {
        "plan": [
            {
                "tool_name": "scanpy_workflow",
                "arguments": {
                    "input_path": str(input_h5ad),
                    "output_dir": str(selected_dir / "scanpy_output"),
                    "min_genes": 3,
                    "min_cells": 1,
                    "max_mito_pct": 100,
                    "n_hvgs": 48,
                    "leiden_resolution": 0.3,
                },
                "step_id": 1,
            }
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert normalized["plan"][0]["arguments"] == raw_plan["plan"][0]["arguments"]
    assert "analysis_spec_parameter_profile_repairs" not in meta


def test_normalize_plan_relocates_undocumented_final_output_args_for_direct_wrapper(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    counts_path = data_root / "counts.tsv"
    metadata_path = data_root / "metadata.tsv"
    counts_path.write_text("gene\ts1\nA\t1\n", encoding="utf-8")
    metadata_path.write_text("sample\tcondition\ns1\tcontrol\n", encoding="utf-8")

    cfg = HarnessConfig(
        prompt=(
            "Use only the deseq2_run tool on the provided counts and metadata. "
            "Write intermediate outputs under the selected directory and write the final CSV to the final bundle."
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
        "analysis_type": "rna_seq_differential_expression",
        "required_deliverables": [str(selected_dir / "final" / "deseq_results.csv")],
    }

    raw_plan = {
        "plan": [
            {
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": str(counts_path),
                    "metadata_table": str(metadata_path),
                    "design_formula": "~ condition",
                    "contrast": "condition,treatment,control",
                    "output_dir": str(selected_dir / "deseq_results"),
                    "output_file": str(selected_dir / "final" / "deseq_results.csv"),
                },
                "step_id": 1,
            }
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert "output_file" not in normalized["plan"][0]["arguments"]
    assert normalized["plan"][0]["arguments"]["output_dir"] == str(selected_dir / "deseq_results")
    assert normalized["final_deliverables"] == [str(selected_dir / "final" / "deseq_results.csv")]
    assert meta.get("changed", False) is True
    assert meta["undocumented_output_argument_repairs"]["reason"] == "relocated_undocumented_output_arguments"
def test_normalize_plan_binds_prebuilt_metagenomics_db(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "sample_R1.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "sample_R2.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "truth.json").write_text(
        '{'
        '"species": [{"name": "Escherichia coli", "taxid": 562}, {"name": "Bacillus subtilis", "taxid": 1423}, {"name": "Staphylococcus aureus", "taxid": 1280}], '
        '"expected_top_genus": ["Escherichia", "Bacillus", "Staphylococcus"]'
        '}',
        encoding="utf-8",
    )
    kraken_db = data_root / "kraken2_db"
    kraken_db.mkdir(parents=True, exist_ok=True)
    for token in ("hash.k2d", "opts.k2d", "taxo.k2d"):
        (kraken_db / token).write_text("stub\n", encoding="utf-8")
    (kraken_db / "ktaxonomy.tsv").write_text(
        "\n".join(
            [
                "562\t|\t561\t|\tS\t|\t9\t|\tEscherichia coli",
                "1423\t|\t653685\t|\tS\t|\t10\t|\tBacillus subtilis",
                "1280\t|\t1279\t|\tS\t|\t9\t|\tStaphylococcus aureus",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cfg = HarnessConfig(
        prompt="Profile metagenomic reads with Kraken2 and Bracken.",
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
    harness.run["user_request"] = "Profile metagenomic reads with Kraken2 and Bracken."
    harness.run["analysis_spec"] = {"analysis_type": "metagenomics_classification"}

    report_path = selected_dir / "kraken2" / "sample_kraken2_report.txt"
    bracken_path = selected_dir / "kraken2" / "sample_bracken.tsv"
    raw_plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"kraken2 --paired {data_root / 'sample_R1.fastq.gz'} {data_root / 'sample_R2.fastq.gz'} "
                        f"--report {report_path} --output {selected_dir / 'kraken2' / 'sample_kraken2_output.txt'}"
                    ),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"bracken -i {report_path} -o {bracken_path} -r 150 -l S",
                },
                "step_id": 2,
            },
        ]
    }

    normalized, meta, _fc_meta = harness._normalize_plan_for_execution(raw_plan)

    assert meta.get("changed", False) is True
    assert "metagenomics_prebuilt_db_repairs" in meta
    first_command = normalized["plan"][0]["arguments"]["command"]
    second_command = normalized["plan"][1]["arguments"]["command"]
    assert f"--db {kraken_db}" in first_command
    assert f"-d {kraken_db}" in second_command
