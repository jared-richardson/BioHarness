from __future__ import annotations

from pathlib import Path

from bio_harness.workflows.templates import canonicalize_execution_plan


def test_canonicalization_rewrites_star_index_and_strips_destructive_cleanup():
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "rm -rf outputs/splicing_auto/star_index ; "
                        "STAR --runMode genomeGenerate --genomeDir outputs/splicing_auto/star_index "
                        "--genomeFastaFiles /ref.fa --sjdbGTFfile /ref.gtf "
                        "--runThreadN 4 --sjdbOverhang 151"
                    )
                },
                "step_id": 1,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root="/data")

    assert meta["changed"] is True
    command = normalized["plan"][0]["arguments"]["command"]
    assert "build_star_index.sh" in command
    assert "rm -rf outputs/splicing_auto/star_index" not in command


def test_canonicalization_removes_zcat_for_uncompressed_star_inputs():
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "STAR --runMode alignReads --genomeDir /tmp/index "
                        "--readFilesIn /tmp/a_R1.fastq /tmp/a_R2.fastq "
                        "--readFilesCommand zcat --runThreadN 4"
                    )
                },
                "step_id": 1,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root="/data")

    assert meta["changed"] is True
    command = normalized["plan"][0]["arguments"]["command"]
    assert "--readFilesCommand" not in command


def test_canonicalization_preserves_shell_operators_when_no_rewrite_needed():
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "if [ -f /tmp/x ] && grep -q foo /tmp/x; then "
                        "echo ok | tr o O || echo miss; fi"
                    )
                },
                "step_id": 1,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root="/data")

    assert meta["changed"] is False
    assert normalized["plan"][0]["arguments"]["command"] == raw_plan["plan"][0]["arguments"]["command"]


def test_canonicalization_repairs_structured_read_pairs_from_data_root(tmp_path):
    data_root = tmp_path / "inputs"
    data_root.mkdir(parents=True)
    (data_root / "1_S1_R1_001.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (data_root / "1_S1_R2_001.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "threads": 4,
                    "genome_dir": "/tmp/index",
                    "reads_1": str(data_root / "S1_R1.fastq.gz"),
                    "reads_2": str(data_root / "S1_R2.fastq.gz"),
                    "output_prefix": "/tmp/out/S1_",
                },
                "step_id": 1,
            }
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root=str(data_root))
    args = normalized["plan"][0]["arguments"]

    assert meta["changed"] is True
    assert args["reads_1"].endswith("1_S1_R1_001.fastq")
    assert args["reads_2"].endswith("1_S1_R2_001.fastq")


def test_canonicalization_normalizes_read_aliases_for_subread_align():
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "subread_align",
                "arguments": {
                    "index_base": "/tmp/subread/index",
                    "reference_fasta": "/tmp/ref.fa",
                    "read1": "/tmp/S1_R1.fastq.gz",
                    "read2": "/tmp/S1_R2.fastq.gz",
                    "output_bam": "/tmp/S1.bam",
                },
                "step_id": 1,
            }
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root="/data")
    args = normalized["plan"][0]["arguments"]

    assert meta["changed"] is True
    assert args["reads_1"] == "/tmp/S1_R1.fastq.gz"
    assert args["reads_2"] == "/tmp/S1_R2.fastq.gz"
    assert normalized["plan"][0]["canonicalized_to"] == "structured_io_resolution"


def test_canonicalization_does_not_invent_reads_aliases_for_sc_count_and_cluster():
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "sc_count_and_cluster",
                "arguments": {
                    "r1": "/tmp/S1_R1.fastq.gz",
                    "r2": "/tmp/S1_R2.fastq.gz",
                    "whitelist": "/tmp/whitelist.txt",
                    "reference": "/tmp/reference.fa",
                    "gtf": "/tmp/annotation.gtf",
                    "output_dir": "/tmp/out",
                },
                "step_id": 1,
            }
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root="/data")
    args = normalized["plan"][0]["arguments"]

    assert meta["changed"] is False
    assert args["r1"] == "/tmp/S1_R1.fastq.gz"
    assert args["r2"] == "/tmp/S1_R2.fastq.gz"
    assert "reads_1" not in args
    assert "reads_2" not in args


def test_canonicalization_repairs_missing_star_genome_dir_from_workspace_cache(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly" / "clip_1"
    data_root.mkdir(parents=True)
    cache_dir = workspace / "outputs" / "_cache" / "star_indexes" / "abc123"
    cache_dir.mkdir(parents=True)
    (cache_dir / "genomeParameters.txt").write_text("ok\n", encoding="utf-8")

    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "threads": 4,
                    "genome_dir": str(workspace / "references" / "mouse_star_index"),
                    "reads_1": "/tmp/S1_R1.fastq.gz",
                    "reads_2": "/tmp/S1_R2.fastq.gz",
                    "output_prefix": "/tmp/out/S1_",
                },
                "step_id": 1,
            }
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root=str(data_root))

    assert meta["changed"] is True
    assert normalized["plan"][0]["arguments"]["genome_dir"] == str(cache_dir)


def test_canonicalization_preserves_explicit_planned_star_index_dir(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly" / "clip_1"
    data_root.mkdir(parents=True)
    planned_index = workspace / "benchmarks" / "official_runs" / "deseq" / "run1" / "star_index"
    cache_dir = workspace / "outputs" / "_cache" / "star_indexes" / "abc123"
    cache_dir.mkdir(parents=True)
    (cache_dir / "genomeParameters.txt").write_text("ok\n", encoding="utf-8")

    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "threads": 4,
                    "genome_dir": str(planned_index),
                    "reads_1": "/tmp/S1_R1.fastq.gz",
                    "reads_2": "/tmp/S1_R2.fastq.gz",
                    "output_prefix": "/tmp/out/S1_",
                },
                "step_id": 1,
            }
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root=str(data_root))

    assert normalized["plan"][0]["arguments"]["genome_dir"] == str(planned_index)
    assert meta["changed"] is False


def test_canonicalization_repairs_missing_structured_gtf_reference_from_alias(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly" / "clip_1"
    data_root.mkdir(parents=True)
    gtf_path = workspace / "inputs_readonly" / "mouse_gtf"
    gtf_path.write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")

    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "featurecounts_run",
                "arguments": {
                    "threads": 2,
                    "annotation_gtf": str(workspace / "references" / "mouse_gtf"),
                    "output_counts": "/tmp/counts.txt",
                    "input_bams": "/tmp/a.bam /tmp/b.bam",
                },
                "step_id": 1,
            }
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root=str(data_root))

    assert meta["changed"] is True
    assert normalized["plan"][0]["arguments"]["annotation_gtf"] == str(gtf_path)


def test_canonicalization_preserves_existing_symlinked_gtf_reference(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    data_root = workspace / "extended_test_data" / "splicing_rmats"
    reference_root = workspace / "non_bioagent_real_data" / "ucsc"
    data_root.mkdir(parents=True)
    reference_root.mkdir(parents=True)
    target_gtf = reference_root / "hg19.chr14.knownGene.gtf"
    target_gtf.write_text("chr14\tsource\texon\t1\t10\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    annotation_gtf = data_root / "annotation.gtf"
    annotation_gtf.symlink_to(target_gtf)

    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "rmats_run",
                "arguments": {
                    "group1_bams": "/tmp/treatment1.bam,/tmp/treatment2.bam",
                    "group2_bams": "/tmp/control1.bam,/tmp/control2.bam",
                    "annotation_gtf": str(annotation_gtf),
                    "output_dir": str(workspace / "outputs" / "splicing"),
                    "read_length": 50,
                    "threads": 4,
                },
                "step_id": 1,
            }
        ],
    }

    normalized, _meta = canonicalize_execution_plan(raw_plan, data_root=str(data_root))

    assert normalized["plan"][0]["arguments"]["annotation_gtf"] == str(annotation_gtf)


def test_canonicalization_repairs_featurecounts_star_bam_suffix_mismatch(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)
    expected_bams = [
        out_dir / "S1_Aligned.out.bam",
        out_dir / "S6_Aligned.out.bam",
    ]
    for bam in expected_bams:
        bam.write_text("bam\n", encoding="utf-8")

    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "threads": 2,
                    "genome_dir": "/tmp/index",
                    "reads_1": "/tmp/S1_R1.fastq.gz",
                    "reads_2": "/tmp/S1_R2.fastq.gz",
                    "output_prefix": str(out_dir / "S1_"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "star_align",
                "arguments": {
                    "threads": 2,
                    "genome_dir": "/tmp/index",
                    "reads_1": "/tmp/S6_R1.fastq.gz",
                    "reads_2": "/tmp/S6_R2.fastq.gz",
                    "output_prefix": str(out_dir / "S6_"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "featurecounts_run",
                "arguments": {
                    "annotation_gtf": "/tmp/genes.gtf",
                    "input_bams": f"{out_dir / 'S1.Aligned.out.bam'} {out_dir / 'S6.Aligned.out.bam'}",
                    "output_counts": str(out_dir / "counts.txt"),
                },
                "step_id": 3,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root=str(tmp_path))

    assert meta["changed"] is True
    assert normalized["plan"][2]["arguments"]["input_bams"] == (
        f"{out_dir / 'S1_Aligned.out.bam'} {out_dir / 'S6_Aligned.out.bam'}"
    )


def test_canonicalization_rebinds_mutect2_bams_to_upstream_alignment_outputs() -> None:
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": "/tmp/ref.fa",
                    "reads_1": "/tmp/tumor_R1.fastq",
                    "reads_2": "/tmp/tumor_R2.fastq",
                    "output_bam": "/tmp/run/tumor/tumor_aligned.bam",
                },
                "step_id": 1,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": "/tmp/ref.fa",
                    "reads_1": "/tmp/normal_R1.fastq",
                    "reads_2": "/tmp/normal_R2.fastq",
                    "output_bam": "/tmp/run/normal/normal_sorted.bam",
                },
                "step_id": 2,
            },
            {
                "tool_name": "gatk_mutect2_call",
                "arguments": {
                    "reference_fasta": "/tmp/ref.fa",
                    "tumor_bam": "/tmp/run/tumor/tumor.bam",
                    "tumor_sample": "tumor",
                    "normal_bam": "/tmp/run/normal/normal.bam",
                    "normal_sample": "normal",
                    "output_vcf": "/tmp/run/somatic/variants.vcf",
                },
                "step_id": 3,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root="/data")
    args = normalized["plan"][2]["arguments"]

    assert meta["changed"] is True
    assert args["tumor_bam"] == "/tmp/run/tumor/tumor_aligned.bam"
    assert args["normal_bam"] == "/tmp/run/normal/normal_sorted.bam"


def test_canonicalization_preserves_generated_featurecounts_gff_from_prior_bash_step(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references_dir = data_root.parent / "references"
    run_dir = workspace / "runs" / "attempt"
    references_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    (references_dir / "C_parapsilosis_CDC317_current_features.gff").write_text(
        "##gff-version 3\n",
        encoding="utf-8",
    )
    unrelated_gtf = workspace / "inputs_readonly" / "mouse_gtf"
    unrelated_gtf.parent.mkdir(parents=True)
    unrelated_gtf.write_text(
        'chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id "g1";\n',
        encoding="utf-8",
    )

    generated_gff = run_dir / "references" / "annotation_for_featurecounts.gff"
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 "
                        f"{workspace / 'bio_harness' / 'pipeline_scripts' / 'normalize_gff_for_featurecounts.py'} "
                        f"{references_dir / 'C_parapsilosis_CDC317_current_features.gff'} "
                        f"{generated_gff}"
                    )
                },
                "step_id": 1,
            },
            {
                "tool_name": "featurecounts_run",
                "arguments": {
                    "threads": 2,
                    "annotation_gtf": str(generated_gff),
                    "annotation_format": "GFF",
                    "feature_type": "gene",
                    "attribute_type": "ID",
                    "output_counts": str(run_dir / "counts" / "gene_counts.txt"),
                    "input_bams": "/tmp/a.bam /tmp/b.bam",
                },
                "step_id": 2,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root=str(data_root))

    assert normalized["plan"][1]["arguments"]["annotation_gtf"] == str(generated_gff)
    assert str(unrelated_gtf) != normalized["plan"][1]["arguments"]["annotation_gtf"]
    assert meta["changed"] is False


def test_canonicalization_normalizes_rmats_cli_defaults():
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "rmats.py --b1 a.txt --b2 b.txt --gtf genes.gtf --paired-end --od out --tmp tmp"
                },
                "step_id": 1,
            }
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root="/data")
    command = normalized["plan"][0]["arguments"]["command"]

    assert meta["changed"] is True
    assert "run_rmats_if_needed.sh" in command
    assert "--paired-end" not in command
    assert " a.txt " in command
    assert " b.txt " in command


def test_canonicalization_rewrites_bash_reference_aliases(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly" / "clip_1"
    data_root.mkdir(parents=True)
    fasta_path = workspace / "inputs_readonly" / "mouse_fasta"
    gtf_path = workspace / "inputs_readonly" / "mouse_gtf"
    fasta_path.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf_path.write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")

    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "python tool.py --reference_fasta /bad/mouse_fasta --gtf /bad/mouse_gtf"
                },
                "step_id": 1,
            }
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root=str(data_root))
    command = normalized["plan"][0]["arguments"]["command"]

    assert meta["changed"] is True
    assert str(fasta_path) in command
    assert str(gtf_path) in command


def test_canonicalization_preserves_reference_paths_inside_prior_output_dirs(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly" / "clip_1"
    data_root.mkdir(parents=True)
    alias_fasta = workspace / "inputs_readonly" / "mouse_fasta"
    alias_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    assembly_dir = workspace / "runs" / "attempt" / "assembly"
    contigs = assembly_dir / "contigs.fasta"

    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/tmp/anc_R1.fastq.gz",
                    "reads_2": "/tmp/anc_R2.fastq.gz",
                    "threads": 4,
                    "memory_gb": 16,
                    "output_dir": str(assembly_dir),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(contigs),
                    "reads_1": "/tmp/evol1_R1.fastq.gz",
                    "reads_2": "/tmp/evol1_R2.fastq.gz",
                    "output_bam": str(workspace / "runs" / "attempt" / "alignments" / "evol1.bam"),
                },
                "step_id": 2,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(raw_plan, data_root=str(data_root))

    assert normalized["plan"][1]["arguments"]["reference_fasta"] == str(contigs)
    assert str(alias_fasta) != normalized["plan"][1]["arguments"]["reference_fasta"]
    assert meta["changed"] is False


def test_canonicalization_rewrites_relative_structured_outputs_into_selected_dir(tmp_path):
    selected_dir = tmp_path / "run_dir"
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "scanpy_workflow",
                "arguments": {
                    "input_path": "/tmp/pbmc3k.h5ad",
                    "output_dir": ".",
                },
                "step_id": 1,
            },
            {
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": "/tmp/counts.tsv",
                    "metadata_table": "/tmp/metadata.tsv",
                    "design_formula": "~ dex",
                    "contrast": ["dex", "trt", "untrt"],
                    "output_dir": ".",
                    "script_path": "deseq2_dex.R",
                },
                "step_id": 2,
            },
            {
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/annotation.gtf",
                    "output_gtf": "stringtie_output.gtf",
                },
                "step_id": 3,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(
        raw_plan,
        data_root="/data",
        selected_dir=str(selected_dir),
    )

    assert meta["changed"] is True
    assert normalized["plan"][0]["arguments"]["output_dir"] == str(selected_dir)
    assert normalized["plan"][1]["arguments"]["output_dir"] == str(selected_dir)
    assert normalized["plan"][2]["arguments"]["output_gtf"] == str(selected_dir / "stringtie_output.gtf")


def test_canonicalization_rewrites_output_dependencies_into_selected_dir(tmp_path):
    selected_dir = tmp_path / "run_dir"
    external_output = tmp_path / "inputs" / "malvirus" / "run_20240614_175552"
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "fastqc_run",
                "arguments": {
                    "input_file": "/tmp/sample.fastq.gz",
                    "output_dir": str(external_output),
                },
                "step_id": 1,
            },
            {
                "tool_name": "multiqc_report",
                "arguments": {
                    "run_input": str(external_output),
                    "output_dir": str(external_output),
                },
                "step_id": 2,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(
        raw_plan,
        data_root="/data",
        selected_dir=str(selected_dir),
    )

    relocated = selected_dir / external_output.name
    assert meta["changed"] is True
    assert normalized["plan"][0]["arguments"]["output_dir"] == str(relocated)
    assert normalized["plan"][1]["arguments"]["run_input"] == str(relocated)
    assert normalized["plan"][1]["arguments"]["output_dir"] == str(relocated)


def test_canonicalization_preserves_stringtie_input_bam_while_rewriting_outputs(tmp_path):
    selected_dir = tmp_path / "run_dir"
    input_bam = tmp_path / "inputs" / "ERR127302_chr14.bam"
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": str(input_bam),
                    "annotation_gtf": "/tmp/annotation.gtf",
                    "output_gtf": "stringtie_output.gtf",
                    "gene_abundance_tsv": "./stringtie_abundance.tsv",
                },
                "step_id": 1,
            },
        ],
    }

    normalized, meta = canonicalize_execution_plan(
        raw_plan,
        data_root="/data",
        selected_dir=str(selected_dir),
    )
    args = normalized["plan"][0]["arguments"]

    assert meta["changed"] is True
    assert args["input_bam"] == str(input_bam)
    assert args["output_gtf"] == str(selected_dir / "stringtie_output.gtf")
    assert args["gene_abundance_tsv"] == str(selected_dir / "stringtie_abundance.tsv")


def test_canonicalization_preserves_selected_dir_reference_fasta_for_structured_steps(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    selected_dir = workspace / "runs" / "attempt" / "selected"
    data_root = workspace / "inputs_readonly" / "evolution"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    alias_fasta = workspace / "inputs_readonly" / "mouse_fasta"
    alias_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    plan_owned_reference = selected_dir / "ancestor_scaffolds.fasta"
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_dir": str(selected_dir / "ancestor_assembly"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": str(plan_owned_reference),
                    "reads_1": str(data_root / "evol1_R1.fastq.gz"),
                    "reads_2": str(data_root / "evol1_R2.fastq.gz"),
                    "output_bam": str(selected_dir / "evol1.bam"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": str(plan_owned_reference),
                    "input_bam": str(selected_dir / "evol1.bam"),
                    "output_vcf": str(selected_dir / "evol1.vcf"),
                },
                "step_id": 3,
            },
        ],
    }

    normalized, _meta = canonicalize_execution_plan(
        raw_plan,
        data_root=str(data_root),
        selected_dir=str(selected_dir),
    )

    assert normalized["plan"][1]["arguments"]["reference_fasta"] == str(plan_owned_reference)
    assert normalized["plan"][2]["arguments"]["reference_fasta"] == str(plan_owned_reference)
    assert str(alias_fasta) != normalized["plan"][1]["arguments"]["reference_fasta"]
    assert str(alias_fasta) != normalized["plan"][2]["arguments"]["reference_fasta"]


def test_canonicalization_preserves_selected_dir_reference_flags_in_bash_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    selected_dir = workspace / "runs" / "attempt" / "selected"
    data_root = workspace / "inputs_readonly" / "evolution"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    alias_fasta = workspace / "inputs_readonly" / "mouse_fasta"
    alias_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    plan_owned_reference = selected_dir / "ancestor_scaffolds.fasta"
    raw_plan = {
        "thought_process": "raw model output",
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python compare.py "
                        f"--reference_fasta {plan_owned_reference} "
                        f"--out {selected_dir / 'compare.tsv'}"
                    )
                },
                "step_id": 1,
            },
        ],
    }

    normalized, _meta = canonicalize_execution_plan(
        raw_plan,
        data_root=str(data_root),
        selected_dir=str(selected_dir),
    )
    command = normalized["plan"][0]["arguments"]["command"]

    assert str(plan_owned_reference) in command
    assert str(alias_fasta) not in command
