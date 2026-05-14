from __future__ import annotations

from pathlib import Path

from bio_harness.core.hierarchical_planning import (
    assemble_executable_plan,
    normalize_step_execution_spec,
    normalize_workflow_spec,
    should_use_hierarchical_planning,
    workflow_spec_from_plan,
)


def test_workflow_spec_from_plan_preserves_step_ids_and_tools():
    plan = {
        "thought_process": "seed",
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "pwd"}, "step_id": 1},
            {"tool_name": "fastqc_run", "arguments": {"input_file": "reads.fq.gz", "output_dir": "qc"}, "step_id": 2},
        ],
    }

    workflow = workflow_spec_from_plan(plan)

    assert workflow["workflow"][0]["tool_name"] == "bash_run"
    assert workflow["workflow"][1]["step_id"] == 2
    assert workflow["workflow"][1]["depends_on"] == [1]


def test_normalize_workflow_spec_accepts_nested_workflow_steps_with_tool_alias() -> None:
    workflow = normalize_workflow_spec(
        {
            "thought_process": "nested",
            "workflow": {
                "steps": [
                    {
                        "step_id": "salmon_quantification",
                        "tool": "salmon_quant",
                        "objective": "Quantify paired-end RNA-seq reads",
                        "parameter_hints": "validateMappings=True, library_type=A",
                    }
                ]
            },
        }
    )

    assert workflow["workflow"][0]["step_id"] == 1
    assert workflow["workflow"][0]["tool_name"] == "salmon_quant"
    assert workflow["workflow"][0]["parameter_hints"] == {
        "note": "validateMappings=True, library_type=A"
    }


def test_assemble_executable_plan_uses_step_specs_and_seed_fallback():
    workflow_spec = normalize_workflow_spec(
        {
            "thought_process": "assembled",
            "workflow": [
                {"tool_name": "bash_run", "objective": "pwd", "step_id": 1, "depends_on": []},
                {"tool_name": "bash_run", "objective": "ls", "step_id": 2, "depends_on": [1]},
            ],
        }
    )
    seed_plan = {
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "pwd"}, "step_id": 1},
            {"tool_name": "bash_run", "arguments": {"command": "ls"}, "step_id": 2},
        ]
    }
    assembled = assemble_executable_plan(
        workflow_spec,
        [{"tool_name": "bash_run", "arguments": {"command": "pwd -P"}, "step_id": 1}],
        seed_plan=seed_plan,
    )

    assert assembled["plan"][0]["arguments"]["command"] == "pwd -P"
    assert assembled["plan"][1]["arguments"]["command"] == "ls"


def test_normalize_step_execution_spec_promotes_top_level_bash_command() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 2,
            "tool_name": "bash_run",
            "command": "python3 script.py --flag",
        },
        expected_step_id=2,
        expected_tool_name="bash_run",
    )

    assert step["arguments"]["command"] == "python3 script.py --flag"


def test_normalize_step_execution_spec_forces_expected_identity() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 1,
            "tool_name": "bash_run",
            "arguments": {"command": "python3 export_shared.py"},
        },
        expected_step_id=13,
        expected_tool_name="bash_run",
    )

    assert step["step_id"] == 13
    assert step["tool_name"] == "bash_run"
    assert step["arguments"]["command"] == "python3 export_shared.py"


def test_normalize_step_execution_spec_drops_incompatible_args_on_tool_mismatch() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 1,
            "tool_name": "bash_run",
            "arguments": {"command": "pwd", "working_dir": "/tmp/work"},
        },
        expected_step_id=7,
        expected_tool_name="bwa_mem_align",
    )

    assert step["step_id"] == 7
    assert step["tool_name"] == "bwa_mem_align"
    assert step["arguments"] == {}


def test_normalize_step_execution_spec_salvages_bash_script_alias_for_bash_run() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 1,
            "tool_name": "bash",
            "arguments": {"script": "set -euo pipefail\npython3 export_shared_variants_csv.py"},
        },
        expected_step_id=13,
        expected_tool_name="bash_run",
    )

    assert step["step_id"] == 13
    assert step["tool_name"] == "bash_run"
    assert step["arguments"] == {
        "command": "set -euo pipefail\npython3 export_shared_variants_csv.py"
    }


def test_normalize_step_execution_spec_uses_nested_execution_spec_arguments() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 12,
            "tool_name": "snpeff_annotate",
            "arguments": {},
            "execution_spec": {
                "tool": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/tmp/evol1_subtracted_anc.vcf.gz",
                    "output_vcf": "/tmp/evol1_annotated.vcf",
                    "annotation_gff": "/tmp/ancestor_scaffolds.gff",
                    "reference_fasta": "/tmp/ancestor_scaffolds.fasta",
                    "genome_db": "ecoli_k12",
                },
            },
        },
        expected_step_id=12,
        expected_tool_name="snpeff_annotate",
    )

    assert step["step_id"] == 12
    assert step["tool_name"] == "snpeff_annotate"
    assert step["arguments"]["input_vcf"] == "/tmp/evol1_subtracted_anc.vcf.gz"
    assert step["arguments"]["output_vcf"] == "/tmp/evol1_annotated.vcf"
    assert step["arguments"]["genome_db"] == "ecoli_k12"


def test_normalize_step_execution_spec_salvages_freebayes_cli_execution_spec_arguments() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 6,
            "tool_name": "freebayes_call",
            "parameter_hints": {"ploidy": 1},
            "execution_spec": {
                "tool": "freebayes",
                "arguments": [
                    "-p",
                    "1",
                    "-f",
                    "/tmp/ancestor_assembly.fasta",
                    "/tmp/evol1_aligned.bam",
                ],
                "output_redirect": "/tmp/evol1_raw_variants.vcf",
            },
        },
        expected_step_id=6,
        expected_tool_name="freebayes_call",
    )

    assert step["arguments"]["ploidy"] == 1
    assert step["arguments"]["reference_fasta"] == "/tmp/ancestor_assembly.fasta"
    assert step["arguments"]["input_bam"] == "/tmp/evol1_aligned.bam"
    assert step["arguments"]["output_vcf"] == "/tmp/evol1_raw_variants.vcf"


def test_normalize_step_execution_spec_uses_nested_execution_spec_inputs_outputs() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 12,
            "tool_name": "snpeff_annotate",
            "arguments": {},
            "execution_spec": {
                "tool": "snpeff_annotate",
                "inputs": {
                    "input_vcf": "/tmp/evol1_subtracted_anc.vcf.gz",
                    "reference_fasta": "/tmp/ancestor_scaffolds.fasta",
                    "annotation_gff": "/tmp/ancestor_scaffolds.gff",
                },
                "outputs": {
                    "output_vcf": "/tmp/evol1_annotated.vcf",
                },
            },
        },
        expected_step_id=12,
        expected_tool_name="snpeff_annotate",
    )

    assert step["step_id"] == 12
    assert step["tool_name"] == "snpeff_annotate"
    assert step["arguments"]["input_vcf"] == "/tmp/evol1_subtracted_anc.vcf.gz"
    assert step["arguments"]["reference_fasta"] == "/tmp/ancestor_scaffolds.fasta"
    assert step["arguments"]["annotation_gff"] == "/tmp/ancestor_scaffolds.gff"
    assert step["arguments"]["output_vcf"] == "/tmp/evol1_annotated.vcf"


def test_normalize_step_execution_spec_uses_top_level_parameters_alias() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 1,
            "tool_name": "spades_assemble",
            "parameters": {
                "reads_1": "/tmp/anc_R1.fastq.gz",
                "reads_2": "/tmp/anc_R2.fastq.gz",
                "output_dir": "/tmp/ancestor_assembly",
                "careful": True,
                "isolate_mode": False,
                "threads": 16,
                "memory_gb": 64,
            },
            "output_files": {
                "assembly_fasta": "/tmp/ancestor_assembly/contigs.fasta",
            },
        },
        expected_step_id=1,
        expected_tool_name="spades_assemble",
    )

    assert step["arguments"]["reads_1"] == "/tmp/anc_R1.fastq.gz"
    assert step["arguments"]["reads_2"] == "/tmp/anc_R2.fastq.gz"
    assert step["arguments"]["output_dir"] == "/tmp/ancestor_assembly"
    assert step["arguments"]["careful"] is True


def test_normalize_step_execution_spec_uses_parameter_hints_and_aliases_for_snpeff() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 12,
            "tool_name": "snpeff_annotate",
            "parameter_hints": {
                "database": "custom_ancestor_db",
                "reference_fasta": "/tmp/ancestor_assembly.fasta",
                "annotation_gff": "/tmp/ancestor_annotation.gff",
                "input_vcf": "/tmp/evol1_subtracted.vcf.gz",
                "output_vcf": "/tmp/evol1_annotated.vcf.gz",
                "config_dir": "/tmp/snpeff_config",
                "genome_label": "E_coli_ancestor_custom",
                "codon_table": "11",
                "check_protein": False,
                "check_cds": False,
            },
        },
        expected_step_id=12,
        expected_tool_name="snpeff_annotate",
    )

    assert step["arguments"]["genome_db"] == "custom_ancestor_db"
    assert step["arguments"]["reference_fasta"] == "/tmp/ancestor_assembly.fasta"
    assert step["arguments"]["annotation_gff"] == "/tmp/ancestor_annotation.gff"
    assert step["arguments"]["input_vcf"] == "/tmp/evol1_subtracted.vcf.gz"
    assert step["arguments"]["output_vcf"] == "/tmp/evol1_annotated.vcf.gz"
    assert step["arguments"]["config_dir"] == "/tmp/snpeff_config"
    assert step["arguments"]["genome_label"] == "E_coli_ancestor_custom"


def test_normalize_step_execution_spec_uses_top_level_input_files_alias() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 2,
            "tool_name": "prodigal_annotate",
            "input_files": {
                "input_fasta": "/tmp/ancestor_assembly.fasta",
            },
            "output_files": {
                "output_gff": "/tmp/ancestor_genes.gff",
                "output_faa": "/tmp/ancestor_genes.faa",
            },
        },
        expected_step_id=2,
        expected_tool_name="prodigal_annotate",
    )

    assert step["arguments"]["input_fasta"] == "/tmp/ancestor_assembly.fasta"
    assert step["arguments"]["output_gff"] == "/tmp/ancestor_genes.gff"
    assert step["arguments"]["output_faa"] == "/tmp/ancestor_genes.faa"


def test_normalize_step_execution_spec_promotes_execution_command_alias_for_bash_run() -> None:
    step = normalize_step_execution_spec(
        {
            "step_id": 7,
            "tool_name": "bash_run",
            "execution_command": "bcftools view -Oz -o filtered.vcf.gz raw.vcf",
        },
        expected_step_id=7,
        expected_tool_name="bash_run",
    )

    assert step["arguments"]["command"] == "bcftools view -Oz -o filtered.vcf.gz raw.vcf"


def test_assemble_executable_plan_preserves_workflow_slots_when_step_expansion_ids_drift() -> None:
    workflow_spec = normalize_workflow_spec(
        {
                "thought_process": "assembled",
                "workflow": [
                    {"tool_name": "spades_assemble", "objective": "Assemble ancestor", "step_id": 1, "depends_on": []},
                    {"tool_name": "bwa_mem_align", "objective": "Align evol2", "step_id": 2, "depends_on": [1], "branch_id": "evol2"},
                    {"tool_name": "bash_run", "objective": "Export shared variants", "step_id": 3, "depends_on": [2]},
                ],
            }
        )
    step_specs = [
        normalize_step_execution_spec(
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {"reads_1": "anc_R1.fastq.gz", "reads_2": "anc_R2.fastq.gz", "output_dir": "/tmp/assembly"},
            },
            expected_step_id=1,
            expected_tool_name="spades_assemble",
        ),
            normalize_step_execution_spec(
                {
                    "step_id": 1,
                    "tool_name": "bwa_mem_align",
                    "arguments": {},
                },
                expected_step_id=2,
                expected_tool_name="bwa_mem_align",
            ),
            normalize_step_execution_spec(
                {
                    "step_id": 1,
                    "tool_name": "bash_run",
                    "arguments": {"command": "python3 export_shared_variants_csv.py"},
                },
                expected_step_id=3,
                expected_tool_name="bash_run",
            ),
        ]

    assembled = assemble_executable_plan(workflow_spec, step_specs)

    assert [step["step_id"] for step in assembled["plan"]] == [1, 2, 3]
    assert [step["tool_name"] for step in assembled["plan"]] == [
        "spades_assemble",
        "bwa_mem_align",
        "bash_run",
    ]
    assert assembled["plan"][2]["arguments"]["command"] == "python3 export_shared_variants_csv.py"


def test_assemble_executable_plan_rebinds_strict_evolution_paths_from_analysis_spec():
    workflow_spec = normalize_workflow_spec(
        {
            "thought_process": "assembled",
            "workflow": [
                {
                    "tool_name": "prodigal_annotate",
                    "objective": "Annotate the assembled ancestor reference to produce a GFF for downstream variant effect annotation",
                    "step_id": 2,
                    "depends_on": [1],
                    "branch_id": "anc_ref",
                },
                {
                    "tool_name": "bash_run",
                    "objective": "Intersect the ancestor-subtracted annotated evolved callsets and write a comma-separated final CSV with the exact required columns",
                    "step_id": 14,
                    "depends_on": [12, 13],
                    "branch_id": "intersect_final",
                },
            ],
        }
    )
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": "/tmp/official_runs/evolution/attempt1",
    }
    assembled = assemble_executable_plan(
        workflow_spec,
        [
            {
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": "attempt1/assembly/anc_contigs.fasta",
                    "output_gff": "attempt1/annotation/anc_contigs.gff",
                    "output_faa": "attempt1/annotation/anc_contigs.faa",
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {"command": "bcftools isec ..."},
                "step_id": 2,
            },
        ],
        analysis_spec=analysis_spec,
    )

    resolved_dir = str(Path("/tmp/official_runs/evolution/attempt1").resolve(strict=False))
    assert assembled["plan"][0]["arguments"]["input_fasta"] == f"{resolved_dir}/assembly/scaffolds.fasta"
    assert assembled["plan"][0]["arguments"]["output_gff"] == f"{resolved_dir}/annotation/genes.gff"
    export_command = assembled["plan"][1]["arguments"]["command"]
    assert "export_shared_variants_csv.py" in export_command
    assert f"{resolved_dir}/variants/evol1.annotated.normalized.vcf.gz" in export_command
    assert f"{resolved_dir}/final/variants_shared.csv" in export_command


def test_assemble_executable_plan_scientific_harness_rebinds_non_bash_wrappers_only(
    tmp_path: Path,
) -> None:
    workflow_spec = normalize_workflow_spec(
        {
            "thought_process": "assembled",
            "workflow": [
                {
                    "tool_name": "spades_assemble",
                    "objective": "Assemble the ancestor reads into the working bacterial reference",
                    "step_id": 1,
                    "depends_on": [],
                },
                {
                    "tool_name": "bash_run",
                    "objective": "Filter the ancestor and evolved callsets into indexed comparison-ready VCFs",
                    "step_id": 2,
                    "depends_on": [1],
                },
            ],
        }
    )
    selected_dir = tmp_path / "scientific_harness" / "evolution" / "attempt1"
    data_dir = tmp_path / "tasks" / "evolution" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "anc_R1.fastq.gz").write_text("", encoding="utf-8")
    (data_dir / "anc_R2.fastq.gz").write_text("", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "scientific_harness",
        "selected_dir": str(selected_dir),
        "requested_data_root": str(data_dir),
    }

    assembled = assemble_executable_plan(
        workflow_spec,
        [
            {
                "tool_name": "spades_assemble",
                "arguments": {"careful": True},
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {"command": "echo keep-model-command"},
                "step_id": 2,
            },
        ],
        analysis_spec=analysis_spec,
    )

    resolved_dir = str(selected_dir.resolve(strict=False))
    assert assembled["plan"][0]["arguments"]["reads_1"] == str(
        (data_dir / "anc_R1.fastq.gz").resolve(strict=False)
    )
    assert assembled["plan"][0]["arguments"]["reads_2"] == str(
        (data_dir / "anc_R2.fastq.gz").resolve(strict=False)
    )
    assert assembled["plan"][0]["arguments"]["output_dir"] == f"{resolved_dir}/assembly"
    assert assembled["plan"][1]["arguments"]["command"] == "echo keep-model-command"


def test_assemble_executable_plan_rebinds_strict_germline_paths_from_analysis_spec(tmp_path: Path) -> None:
    workflow_spec = normalize_workflow_spec(
        {
            "thought_process": "assembled",
            "workflow": [
                {
                    "tool_name": "bwa_mem_align",
                    "objective": "Align reads to reference",
                    "step_id": 1,
                    "depends_on": [],
                },
                {
                    "tool_name": "gatk_haplotypecaller",
                    "objective": "Call germline variants",
                    "step_id": 2,
                    "depends_on": [1],
                },
            ],
        }
    )
    selected_dir = tmp_path / "official_runs" / "giab" / "attempt1"
    data_dir = tmp_path / "tasks" / "germline-vc" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sample_1.fastq").write_text("", encoding="utf-8")
    (data_dir / "sample_2.fastq").write_text("", encoding="utf-8")
    (data_dir / "ref_genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "germline_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }
    assembled = assemble_executable_plan(
        workflow_spec,
        [
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(data_dir / "ref_genome.fa"),
                    "reads_1": str(data_dir / "sample_1.fastq"),
                    "reads_2": str(data_dir / "sample_2.fastq"),
                    "output_bam": str(selected_dir / "intermediate" / "aligned_reads.bam"),
                    "postprocess_mode": "fixmate_markdup_q20",
                },
                "step_id": 1,
            },
            {
                "tool_name": "gatk_haplotypecaller",
                "arguments": {
                    "reference_fasta": str(data_dir / "ref_genome.fa"),
                    "input_bam": str(selected_dir / "intermediate" / "aligned_reads.bam"),
                    "output_vcf": str(selected_dir / "final" / "variants.vcf"),
                },
                "step_id": 2,
            },
        ],
        analysis_spec=analysis_spec,
    )

    resolved_dir = str(selected_dir.resolve(strict=False))
    expected_bam = f"{resolved_dir}/intermediate/aligned_sorted_markdup.bam"
    assert assembled["plan"][0]["arguments"]["output_bam"] == expected_bam
    assert assembled["plan"][1]["arguments"]["input_bam"] == expected_bam
    assert assembled["plan"][1]["arguments"]["output_vcf"] == f"{resolved_dir}/final/variants.vcf"


def test_normalize_workflow_spec_drops_generic_duplicate_branch_placeholders():
    workflow_spec = normalize_workflow_spec(
        {
            "workflow": [
                {"step_id": 4, "tool_name": "bwa_mem_align", "objective": "Align each evolved line", "branch_id": "evol_align", "depends_on": [1]},
                {"step_id": 4, "tool_name": "bwa_mem_align", "objective": "Align evol1", "branch_id": "evol1_align", "depends_on": [1]},
                {"step_id": 4, "tool_name": "bwa_mem_align", "objective": "Align evol2", "branch_id": "evol2_align", "depends_on": [1]},
                {"step_id": 5, "tool_name": "freebayes_call", "objective": "Call evol1 variants", "branch_id": "evol1_call", "depends_on": [4]},
                {"step_id": 5, "tool_name": "freebayes_call", "objective": "Call evol2 variants", "branch_id": "evol2_call", "depends_on": [4]},
                {"step_id": 6, "tool_name": "bash_run", "objective": "Filter all evolved callsets", "depends_on": [5]},
            ],
        }
    )

    steps = workflow_spec["workflow"]
    assert [step["branch_id"] for step in steps] == [
        "evol1_align",
        "evol2_align",
        "evol1_call",
        "evol2_call",
        "",
    ]
    assert steps[2]["depends_on"] == [1]
    assert steps[3]["depends_on"] == [2]
    assert steps[4]["depends_on"] == [3, 4]


def test_normalize_workflow_spec_expands_generic_evolution_annotation_step() -> None:
    workflow_spec = normalize_workflow_spec(
        {
            "workflow": [
                {"step_id": 5, "tool_name": "bwa_mem_align", "objective": "Align evol1", "branch_id": "evol1", "depends_on": [4]},
                {"step_id": 6, "tool_name": "freebayes_call", "objective": "Call evol1 variants", "branch_id": "evol1", "depends_on": [5]},
                {"step_id": 7, "tool_name": "bwa_mem_align", "objective": "Align evol2", "branch_id": "evol2", "depends_on": [4]},
                {"step_id": 8, "tool_name": "freebayes_call", "objective": "Call evol2 variants", "branch_id": "evol2", "depends_on": [7]},
                {"step_id": 10, "tool_name": "bash_run", "objective": "Subtract the ancestor-supported sites from each evolved callset separately before any evolved-evolved comparison", "depends_on": [6, 8]},
                {"step_id": 11, "tool_name": "snpeff_annotate", "objective": "Annotate the ancestor-subtracted evolved variants with ANN-compatible fields", "depends_on": [10]},
                {"step_id": 12, "tool_name": "bash_run", "objective": "Normalize the annotated evolved callsets, intersect them, and write the final CSV", "depends_on": [11]},
            ],
        }
    )

    steps = workflow_spec["workflow"]
    annotation_steps = [step for step in steps if step["tool_name"] == "snpeff_annotate"]
    assert [step["branch_id"] for step in annotation_steps] == ["evol1", "evol2"]
    assert annotation_steps[0]["depends_on"] == [5]
    assert annotation_steps[1]["depends_on"] == [5]
    export_step = steps[-1]
    assert export_step["tool_name"] == "bash_run"
    assert export_step["depends_on"] == [6, 7]


def test_normalize_workflow_spec_expands_generic_subtracted_variant_annotation_step() -> None:
    workflow_spec = normalize_workflow_spec(
        {
            "workflow": [
                {"step_id": 5, "tool_name": "bwa_mem_align", "objective": "Align evol1", "branch_id": "evol1", "depends_on": [4]},
                {"step_id": 6, "tool_name": "freebayes_call", "objective": "Call evol1 variants", "branch_id": "evol1", "depends_on": [5]},
                {"step_id": 7, "tool_name": "bwa_mem_align", "objective": "Align evol2", "branch_id": "evol2", "depends_on": [4]},
                {"step_id": 8, "tool_name": "freebayes_call", "objective": "Call evol2 variants", "branch_id": "evol2", "depends_on": [7]},
                {"step_id": 10, "tool_name": "bash_run", "objective": "Subtract ancestor-supported sites from each evolved branch", "depends_on": [6, 8]},
                {"step_id": 11, "tool_name": "snpeff_annotate", "objective": "Annotate the subtracted evolved variants with functional effects", "depends_on": [10]},
            ],
        }
    )

    annotation_steps = [step for step in workflow_spec["workflow"] if step["tool_name"] == "snpeff_annotate"]
    assert [step["branch_id"] for step in annotation_steps] == ["evol1", "evol2"]


def test_normalize_workflow_spec_relocates_undocumented_output_hint_to_final_deliverables() -> None:
    workflow_spec = normalize_workflow_spec(
        {
            "workflow": [
                {
                    "step_id": 1,
                    "tool_name": "deseq2_run",
                    "objective": "Run DESeq2 directly from counts and metadata.",
                    "depends_on": [],
                    "parameter_hints": {
                        "design_formula": "~ dex",
                        "contrast": "dex",
                        "output_file": "deseq_results.csv",
                    },
                }
            ],
            "final_deliverables": [],
        }
    )

    step = workflow_spec["workflow"][0]
    assert "output_file" not in step["parameter_hints"]
    assert workflow_spec["final_deliverables"] == ["deseq_results.csv"]


def test_normalize_workflow_spec_accepts_plan_key_fallback_fix_15():
    """Fix #15: LLMs (e.g. Qwen 3.6) sometimes emit the step list under
    `plan` instead of `workflow` even when the schema/seed uses `workflow`.
    The normalizer must fall back so a consistent-but-mis-keyed response is
    not lost to an empty workflow (which would livelock the planner)."""

    workflow_spec = normalize_workflow_spec(
        {
            "thought_process": "evol1 comes next.",
            "plan": [
                {
                    "step_id": 5,
                    "tool_name": "bwa_mem_align",
                    "objective": "Align evolved line 1 reads to the assembled ancestor scaffold reference.",
                    "branch_id": "evol1",
                    "parameter_hints": {"sample_name": "evol1", "threads": 8},
                    "depends_on": [4],
                }
            ],
        }
    )
    steps = workflow_spec["workflow"]
    assert len(steps) == 1
    assert steps[0]["tool_name"] == "bwa_mem_align"
    assert steps[0]["branch_id"] == "evol1"
    assert steps[0]["parameter_hints"].get("sample_name") == "evol1"


def test_normalize_workflow_spec_prefers_workflow_key_over_plan_key_fix_15():
    """Fix #15 regression: if both `workflow` and `plan` are present and
    `workflow` is non-empty, we must use `workflow` (the canonical key).
    Only fall back to `plan` when `workflow` is missing or empty."""

    workflow_spec = normalize_workflow_spec(
        {
            "workflow": [
                {"step_id": 1, "tool_name": "spades_assemble", "branch_id": "anc", "depends_on": []},
            ],
            "plan": [
                {"step_id": 99, "tool_name": "different_tool", "branch_id": "ignored", "depends_on": []},
            ],
        }
    )
    steps = workflow_spec["workflow"]
    assert len(steps) == 1
    assert steps[0]["tool_name"] == "spades_assemble"


def test_normalize_workflow_spec_empty_workflow_with_no_alternates_stays_empty_fix_15():
    """Fix #15 regression: no alternate keys means empty workflow stays empty."""

    workflow_spec = normalize_workflow_spec({"workflow": []})
    assert workflow_spec["workflow"] == []


def test_should_use_hierarchical_planning_for_grounded_repair_and_high_impact():
    assert should_use_hierarchical_planning(
        planner_mode="auto",
        user_query="You are repairing an executable bioinformatics plan to satisfy task-local protocol grounding.",
        analysis_spec={},
    )
    assert should_use_hierarchical_planning(
        planner_mode="auto",
        user_query="Identify and annotate shared variants.",
        analysis_spec={"analysis_type": "bacterial_evolution_variant_calling"},
    )
    assert not should_use_hierarchical_planning(
        planner_mode="off",
        user_query="Identify and annotate shared variants.",
        analysis_spec={"analysis_type": "bacterial_evolution_variant_calling"},
    )
