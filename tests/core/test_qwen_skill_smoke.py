from __future__ import annotations

import json
from pathlib import Path

from bio_harness.analysis.qwen_skill_smoke import (
    build_eighth_tranche_qwen_skill_smoke_cases,
    build_eleventh_tranche_qwen_skill_smoke_cases,
    build_eighteenth_tranche_qwen_skill_smoke_cases,
    build_fifteenth_tranche_qwen_skill_smoke_cases,
    build_fourteenth_tranche_qwen_skill_smoke_cases,
    build_fifth_tranche_qwen_skill_smoke_cases,
    build_fourth_tranche_qwen_skill_smoke_cases,
    build_nineteenth_tranche_qwen_skill_smoke_cases,
    build_ninth_tranche_qwen_skill_smoke_cases,
    build_second_tranche_qwen_skill_smoke_cases,
    build_seventeenth_tranche_qwen_skill_smoke_cases,
    build_seventh_tranche_qwen_skill_smoke_cases,
    build_sixth_tranche_qwen_skill_smoke_cases,
    build_starter_qwen_skill_smoke_cases,
    build_sixteenth_tranche_qwen_skill_smoke_cases,
    build_thirteenth_tranche_qwen_skill_smoke_cases,
    build_tenth_tranche_qwen_skill_smoke_cases,
    build_twentieth_tranche_qwen_skill_smoke_cases,
    build_twentyfirst_tranche_qwen_skill_smoke_cases,
    build_twentysecond_tranche_qwen_skill_smoke_cases,
    build_twentyfourth_tranche_qwen_skill_smoke_cases,
    build_twentythird_tranche_qwen_skill_smoke_cases,
    build_twelfth_tranche_qwen_skill_smoke_cases,
    build_third_tranche_qwen_skill_smoke_cases,
    run_qwen_skill_smoke_matrix,
)


def _write_clean_benchmark_run(selected_dir: Path) -> None:
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "final").mkdir(parents=True, exist_ok=True)
    (selected_dir / "final" / "pathway_comparison.csv").write_text(
        "pathway,score\nA,1\n",
        encoding="utf-8",
    )
    (selected_dir / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "auto_repair_history_count": 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (selected_dir / "validator.log").write_text(
        "BENCHMARK PASSED: True\n",
        encoding="utf-8",
    )


def test_build_starter_qwen_skill_smoke_cases_uses_latest_clean_source(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    old_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "alzheimer-mouse"
        / "20260318_old"
    )
    new_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "alzheimer-mouse"
        / "20260319_new"
    )
    _write_clean_benchmark_run(old_dir)
    _write_clean_benchmark_run(new_dir)

    cases = build_starter_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == [
        "artifact_schema_profile",
        "multiqc_report",
        "quarto_report",
    ]
    assert str(new_dir / "final" / "pathway_comparison.csv") == cases[0].source_input
    assert str(new_dir / "result.json") == cases[1].source_input
    assert "{selected_dir}/report_bundle" in cases[1].prompt_template


def test_build_second_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    evolution_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260319_evolution"
    )
    _write_clean_benchmark_run(evolution_dir)
    (evolution_dir / "assembly").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "assembly" / "scaffolds.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (evolution_dir / "alignments" / "anc_aligned.bam").write_text("bam", encoding="utf-8")

    deseq_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260319_deseq"
    )
    (deseq_dir / "star_index").mkdir(parents=True, exist_ok=True)
    (deseq_dir / "star_index" / "Genome").write_text("index", encoding="utf-8")
    deseq_task_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "data"
    )
    deseq_task_data.mkdir(parents=True, exist_ok=True)
    (deseq_task_data / "SRR1278968_1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (deseq_task_data / "SRR1278968_2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._resolve_star_binary",
        lambda _: "/usr/local/bin/STAR",
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name in {"freebayes", "samtools"},
    )

    cases = build_second_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["star_align", "freebayes_call"]
    assert "genome_dir" in (cases[0].prompt_context or {})
    assert "reference_fasta" in (cases[1].prompt_context or {})


def test_build_third_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    deseq_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260319_deseq"
    )
    _write_clean_benchmark_run(deseq_dir)
    (deseq_dir / "counts").mkdir(parents=True, exist_ok=True)
    (deseq_dir / "counts" / "gene_counts.txt").write_text(
        "Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1\tS2\n"
        "g1\tchr1\t1\t2\t+\t2\t10\t11\n",
        encoding="utf-8",
    )
    deseq_task_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "data"
    )
    deseq_task_data.mkdir(parents=True, exist_ok=True)
    (deseq_task_data / "sample_metadata.tsv").write_text(
        "sample\tcondition\nS1\tPlankton\nS2\tBiofilm\n",
        encoding="utf-8",
    )
    script_path = (
        project_root / "bio_harness" / "pipeline_scripts" / "pydeseq2_wrapper.py"
    )
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("print('ok')\n", encoding="utf-8")

    cystic_fibrosis_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "cystic-fibrosis"
        / "20260319_cf"
    )
    _write_clean_benchmark_run(cystic_fibrosis_dir)
    (cystic_fibrosis_dir / "step1").mkdir(parents=True, exist_ok=True)
    (cystic_fibrosis_dir / "step1" / "snpeff_annotated.vcf").write_text(
        "##INFO=<ID=ANN,Number=.,Type=String,Description=\"ann\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_python_module",
        lambda name: name == "pydeseq2",
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name == "Rscript",
    )

    cases = build_third_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["deseq2_run", "snpeff_annotate"]
    assert "metadata_table" in (cases[0].prompt_context or {})
    assert "script_path" in (cases[0].prompt_context or {})


def test_build_fourth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    deseq_task_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "data"
    )
    deseq_task_data.mkdir(parents=True, exist_ok=True)
    (deseq_task_data / "SRR1278968_1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (deseq_task_data / "SRR1278968_2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")
    deseq_reference_root = deseq_task_data.parent / "references"
    deseq_reference_root.mkdir(parents=True, exist_ok=True)
    (deseq_reference_root / "C_parapsilosis_CDC317_current_chromosomes.fasta").write_text(
        ">chr1\nACGT\n",
        encoding="utf-8",
    )
    (deseq_reference_root / "C_parapsilosis_CDC317_current_features.gff").write_text(
        "chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=g1\n",
        encoding="utf-8",
    )

    viral_task_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "viral-metagenomics"
        / "data"
    )
    viral_task_data.mkdir(parents=True, exist_ok=True)
    (viral_task_data / "sample_R1.fastq.gz").write_text("gz1", encoding="utf-8")
    (viral_task_data / "sample_R2.fastq.gz").write_text("gz2", encoding="utf-8")

    transcript_quant_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "transcript-quant"
        / "data"
    )
    transcript_quant_data.mkdir(parents=True, exist_ok=True)
    (transcript_quant_data / "reads_1.fq.gz").write_text("fq1", encoding="utf-8")
    (transcript_quant_data / "reads_2.fq.gz").write_text("fq2", encoding="utf-8")
    (transcript_quant_data / "transcriptome.fa").write_text(">tx1\nACGT\n", encoding="utf-8")

    phylogenetics_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "phylogenetics"
        / "data"
    )
    phylogenetics_data.mkdir(parents=True, exist_ok=True)
    (phylogenetics_data / "sequences.fasta").write_text(">s1\nACGT\n>s2\nACGT\n", encoding="utf-8")

    deseq_selected_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260319_deseq"
    )
    _write_clean_benchmark_run(deseq_selected_dir)
    (deseq_selected_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (deseq_selected_dir / "alignments" / "SRR1278968Aligned.sortedByCoord.out.bam").write_text(
        "bam",
        encoding="utf-8",
    )

    evolution_selected_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260319_evolution"
    )
    _write_clean_benchmark_run(evolution_selected_dir)
    (evolution_selected_dir / "assembly").mkdir(parents=True, exist_ok=True)
    (evolution_selected_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_selected_dir / "assembly" / "scaffolds.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (evolution_selected_dir / "alignments" / "anc_aligned.bam").write_text("bam", encoding="utf-8")

    viral_reference_root = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "viral-metagenomics"
        / "replicate-1"
    )
    viral_reference_root.mkdir(parents=True, exist_ok=True)
    (viral_reference_root / "viral_ref.fasta").write_text(">virus\nACGT\n", encoding="utf-8")
    (viral_reference_root / "viral_ref.mmi").write_text("mmi", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name
        in {
            "cutadapt",
            "fastp",
            "hisat2",
            "featurecounts",
            "subread",
            "salmon",
            "kallisto",
            "bcftools",
            "samtools",
            "minimap2",
            "mafft",
            "iqtree",
        },
    )

    cases = build_fourth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == [
        "cutadapt_run",
        "fastp_run",
        "hisat2_align",
        "featurecounts_run",
        "salmon_quant",
        "kallisto_quant",
        "bcftools_call",
        "minimap2_align",
        "phylogenetics_workflow",
        "phylogenetics_iqtree_style",
    ]
    assert "reads_2" in (cases[0].prompt_context or {})
    assert "annotation_gtf" in (cases[3].prompt_context or {})
    assert "paired-end" in cases[3].prompt_template
    assert "count read pairs" in cases[3].prompt_template
    assert "transcriptome_fasta" in (cases[4].prompt_context or {})
    assert "cache_index_path" in (cases[7].prompt_context or {})
    assert cases[8].expected_tools == ("mafft_align", "phylogenetics_iqtree_style")
    assert "aligned_sequences.fasta" in cases[8].prompt_template


def test_build_fifth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    deseq_task_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "data"
    )
    deseq_task_data.mkdir(parents=True, exist_ok=True)
    (deseq_task_data / "SRR1278968_1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (deseq_task_data / "SRR1278968_2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")
    deseq_reference_root = deseq_task_data.parent / "references"
    deseq_reference_root.mkdir(parents=True, exist_ok=True)
    (deseq_reference_root / "C_parapsilosis_CDC317_current_chromosomes.fasta").write_text(
        ">chr1\nACGT\n",
        encoding="utf-8",
    )

    deseq_selected_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260320_deseq"
    )
    _write_clean_benchmark_run(deseq_selected_dir)
    (deseq_selected_dir / "star_index").mkdir(parents=True, exist_ok=True)
    (deseq_selected_dir / "star_index" / "Genome").write_text("idx\n", encoding="utf-8")

    evolution_selected_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260320_evolution"
    )
    _write_clean_benchmark_run(evolution_selected_dir)
    (evolution_selected_dir / "assembly").mkdir(parents=True, exist_ok=True)
    (evolution_selected_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_selected_dir / "assembly" / "scaffolds.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (evolution_selected_dir / "alignments" / "anc_aligned.bam").write_text("bam", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name
        in {
            "bwa",
            "bowtie2",
            "bowtie2-build",
            "subread",
            "featurecounts",
            "samtools",
            "gatk",
        },
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._resolve_star_binary",
        lambda _root: "/opt/bin/STAR",
    )

    cases = build_fifth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == [
        "bwa_mem_align",
        "bowtie2_align",
        "subread_align",
        "gatk_haplotypecaller",
        "star_2pass_align",
    ]
    assert "reads_2" in (cases[0].prompt_context or {})
    assert "cached BWA index" in cases[0].prompt_template
    assert "reference_fasta" in (cases[1].prompt_context or {})
    assert "reference_fasta" in (cases[2].prompt_context or {})
    assert "reference_fasta" in (cases[3].prompt_context or {})
    assert "genome_dir" in (cases[4].prompt_context or {})


def test_build_sixth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    single_cell_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "single-cell"
        / "data"
    )
    single_cell_data.mkdir(parents=True, exist_ok=True)
    (single_cell_data / "sample_R1.fastq.gz").write_text("gz1", encoding="utf-8")
    (single_cell_data / "sample_R2.fastq.gz").write_text("gz2", encoding="utf-8")
    (single_cell_data / "barcodes_whitelist.txt").write_text("AAAA\n", encoding="utf-8")
    (single_cell_data / "reference.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (single_cell_data / "annotation.gtf").write_text(
        "chr1\tsrc\texon\t1\t4\t.\t+\t.\tgene_id \"g1\";\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "scanpy",
    )

    cases = build_sixth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["sc_count_and_cluster"]
    assert "reads_2" in (cases[0].prompt_context or {})
    assert "whitelist" in (cases[0].prompt_context or {})
    assert "reference_fasta" in (cases[0].prompt_context or {})
    assert "annotation_gtf" in (cases[0].prompt_context or {})


def test_build_seventh_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    evolution_selected_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260320_evolution"
    )
    _write_clean_benchmark_run(evolution_selected_dir)
    (evolution_selected_dir / "assembly").mkdir(parents=True, exist_ok=True)
    (evolution_selected_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_selected_dir / "assembly" / "scaffolds.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (evolution_selected_dir / "alignments" / "anc_aligned.bam").write_text("bam", encoding="utf-8")
    (evolution_selected_dir / "alignments" / "evol1_aligned.bam").write_text("bam", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name in {"gatk", "samtools"},
    )

    cases = build_seventh_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["gatk_mutect2_call"]
    assert "normal_bam" in (cases[0].prompt_context or {})
    assert "reference_fasta" in (cases[0].prompt_context or {})


def test_build_eighth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    single_cell_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "single-cell"
        / "data"
    )
    single_cell_data.mkdir(parents=True, exist_ok=True)
    (single_cell_data / "sample_R1.fastq.gz").write_text("gz1", encoding="utf-8")
    (single_cell_data / "sample_R2.fastq.gz").write_text("gz2", encoding="utf-8")
    (single_cell_data / "barcodes_whitelist.txt").write_text("AAAA\n", encoding="utf-8")
    (single_cell_data / "reference.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (single_cell_data / "annotation.gtf").write_text(
        "chr1\tsrc\texon\t1\t4\t.\t+\t.\tgene_id \"g1\";\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name == "star",
    )

    cases = build_eighth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["star_solo_count"]
    assert "reads_2" in (cases[0].prompt_context or {})
    assert "whitelist" in (cases[0].prompt_context or {})
    assert "reference_fasta" in (cases[0].prompt_context or {})
    assert "annotation_gtf" in (cases[0].prompt_context or {})


def test_build_ninth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    source_h5ad = (
        project_root
        / "workspace"
        / "skill_smoke"
        / "qwen_skill_smoke_sixth_live_r2"
        / "sc_count_and_cluster"
        / "sc_output"
        / "adata.h5ad"
    )
    source_h5ad.parent.mkdir(parents=True, exist_ok=True)
    source_h5ad.write_text("h5ad", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "scanpy",
    )

    cases = build_ninth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["scanpy_workflow"]
    assert cases[0].expected_outputs == (
        "scanpy_output/processed.h5ad",
        "scanpy_output/cluster_assignments.csv",
        "scanpy_output/marker_genes.csv",
        "scanpy_output/summary.json",
    )


def test_build_tenth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    variant_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "variant-annotation"
        / "data"
    )
    variant_data.mkdir(parents=True, exist_ok=True)
    (variant_data / "input_variants.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    (variant_data / "reference.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (variant_data / "genes.gff").write_text("##gff-version 3\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "vep",
    )

    cases = build_tenth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["vep_annotate"]
    assert "annotation_gff" in (cases[0].prompt_context or {})
    assert "reference_fasta" in (cases[0].prompt_context or {})


def test_build_eleventh_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    variant_annotation_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "variant-annotation"
        / "data"
    )
    variant_annotation_data.mkdir(parents=True, exist_ok=True)
    (variant_annotation_data / "reference.fa").write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "prokka",
    )

    cases = build_eleventh_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["prokka_annotate"]
    assert cases[0].expected_outputs == (
        "annot/sample1.gff",
        "annot/sample1.faa",
        "annot/sample1.gbk",
    )


def test_build_twelfth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    alignments_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260320_deseq"
        / "alignments"
    )
    alignments_dir.mkdir(parents=True, exist_ok=True)
    (alignments_dir / "SRR1278968.bam").write_text("bam", encoding="utf-8")
    (alignments_dir / "SRR1278971.bam").write_text("bam", encoding="utf-8")
    selected_dir = alignments_dir.parent
    _write_clean_benchmark_run(selected_dir)

    gff_path = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "references"
        / "C_parapsilosis_CDC317_current_features.gff"
    )
    gff_path.parent.mkdir(parents=True, exist_ok=True)
    gff_path.write_text(
        "chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=g1;Name=g1\n"
        "chr1\tsrc\tmRNA\t1\t4\t.\t+\t.\tID=t1;Parent=g1\n"
        "chr1\tsrc\texon\t1\t4\t.\t+\t.\tID=e1;Parent=t1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name in {"rmats", "samtools"},
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_bam_subset_alias",
        lambda project_root, source_path, *, alias_parts, alignment_limit=40000: source_path,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_gff_to_gtf_alias",
        lambda project_root, source_path, *, alias_parts: source_path.with_suffix(".gtf"),
    )

    cases = build_twelfth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["rmats_run"]
    assert "group2_bams" in (cases[0].prompt_context or {})
    assert "annotation_gtf" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == (
        "rmats_out/SE.MATS.JC.txt",
        "rmats_out/SE.MATS.JCEC.txt",
        "rmats_out/summary.txt",
    )


def test_build_thirteenth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "dexseq",
    )

    cases = build_thirteenth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["dexseq_run"]
    assert "metadata_table" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == ("dexseq_out/dexseq_results.tsv",)
    counts_path = Path(cases[0].source_input)
    assert counts_path.exists()
    assert "gene_id\texon_id" in counts_path.read_text(encoding="utf-8")


def test_build_fourteenth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "edger",
    )

    cases = build_fourteenth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["edger_run"]
    assert "metadata_table" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == ("edger_out/edger_results.tsv",)
    counts_path = Path(cases[0].source_input)
    assert counts_path.exists()
    assert "gene_id\tcontrol_rep1" in counts_path.read_text(encoding="utf-8")


def test_build_fifteenth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "limma",
    )

    cases = build_fifteenth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["limma_voom_run"]
    assert "metadata_table" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == ("limma_out/limma_voom_results.tsv",)
    counts_path = Path(cases[0].source_input)
    assert counts_path.exists()
    assert "gene_id\tcontrol_rep1" in counts_path.read_text(encoding="utf-8")


def test_build_sixteenth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "seurat",
    )

    cases = build_sixteenth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["seurat_rscript_workflow"]
    assert "metadata_table" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == (
        "seurat_output/seurat_object.rds",
        "seurat_output/pca_embeddings.csv",
        "seurat_output/cell_metadata.csv",
        "seurat_output/summary.json",
    )
    matrix_path = Path(cases[0].source_input)
    assert matrix_path.exists()
    assert "gene_id\tcell1" in matrix_path.read_text(encoding="utf-8")


def test_build_seventeenth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    evolution_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260321_evolution"
    )
    _write_clean_benchmark_run(evolution_dir)
    (evolution_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "alignments" / "evol1_aligned.bam").write_text("bam", encoding="utf-8")
    (evolution_dir / "alignments" / "anc_aligned.bam").write_text("bam", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name in {"macs2", "samtools"},
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_bam_subset_alias",
        lambda _root, source_path, *, alias_parts, alignment_limit=40000: source_path,
    )

    cases = build_seventeenth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["macs2_chipseq_callpeak"]
    assert "control_bam" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == (
        "macs2_chipseq_out/chipseq_smoke_peaks.narrowPeak",
        "macs2_chipseq_out/chipseq_smoke_peaks.xls",
    )


def test_build_eighteenth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    deseq_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260321_deseq"
    )
    _write_clean_benchmark_run(deseq_dir)
    (deseq_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (deseq_dir / "alignments" / "SRR1278968.bam").write_text("bam", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name in {"macs2", "samtools"},
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_bam_subset_alias",
        lambda _root, source_path, *, alias_parts, alignment_limit=40000: source_path,
    )

    cases = build_eighteenth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["macs2_atacseq_callpeak"]
    assert cases[0].expected_outputs == (
        "macs2_atac_out/atac_smoke_peaks.narrowPeak",
        "macs2_atac_out/atac_smoke_peaks.xls",
    )


def test_build_nineteenth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    evolution_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260321_evolution"
    )
    _write_clean_benchmark_run(evolution_dir)
    (evolution_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "assembly").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "alignments" / "evol1_aligned.bam").write_text("bam", encoding="utf-8")
    (evolution_dir / "assembly" / "scaffolds.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "cnvkit.py",
    )

    cases = build_nineteenth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["cnv_cnvkit_style"]
    assert "reference_fasta" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == (
        "cnv/cnv_summary.tsv",
        "cnv/reference.cnn",
    )


def test_build_twentieth_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name == "bismark",
    )

    cases = build_twentieth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["methylation_bismark_style"]
    assert "reads_2" in (cases[0].prompt_context or {})
    assert "genome_folder" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == (
        "methylation/methylation.tsv",
        "methylation/methylation_smoke_pe.bam",
        "methylation/methylation_smoke_PE_report.txt",
    )


def test_build_twentyfirst_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"

    def _fake_requirement_available(name: str) -> bool:
        return name in {"kraken2", "bracken"}

    def _fake_which_with_pixi(name: str) -> str | None:
        mapping = {
            "kraken2-build": "/opt/bin/kraken2-build",
            "count-kmer-abundances.pl": "/opt/bin/count-kmer-abundances.pl",
            "generate_kmer_distribution.py": "/opt/bin/generate_kmer_distribution.py",
        }
        return mapping.get(name)

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        _fake_requirement_available,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.which_with_pixi",
        _fake_which_with_pixi,
    )

    cases = build_twentyfirst_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == ["metagenomics_kraken2_bracken_style"]
    assert "reads_2" in (cases[0].prompt_context or {})
    assert "reference_fasta" in (cases[0].prompt_context or {})
    assert "taxonomy_names" in (cases[0].prompt_context or {})
    assert "taxonomy_nodes" in (cases[0].prompt_context or {})
    assert cases[0].expected_outputs == (
        "metagenomics/bracken.tsv",
        "metagenomics/kraken.report",
        "kraken_db/hash.k2d",
        "kraken_db/database40mers.kmer_distrib",
    )


def test_build_twentysecond_tranche_qwen_skill_smoke_cases_filters_by_machine_support(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    evolution_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260321_evolution"
    )
    _write_clean_benchmark_run(evolution_dir)
    (evolution_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "assembly").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "alignments" / "anc_aligned.bam").write_text("bam", encoding="utf-8")
    (evolution_dir / "assembly" / "scaffolds.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")

    def _fake_requirement_available(name: str) -> bool:
        return name in {"flye", "hmmscan", "trinity", "varscan", "samtools"}

    def _fake_which_with_pixi(name: str) -> str | None:
        mapping = {
            "minimap2": "/opt/bin/minimap2",
            "hmmbuild": "/opt/bin/hmmbuild",
            "hmmpress": "/opt/bin/hmmpress",
            "jellyfish": "/opt/bin/jellyfish",
        }
        return mapping.get(name)

    def _fake_subprocess_run(argv, **_kwargs):  # noqa: ANN001
        if len(argv) < 2:
            raise AssertionError("unexpected subprocess invocation")
        target = Path(argv[1])
        if argv[0] == "/opt/bin/hmmbuild":
            target.write_text("HMMER3/f\n", encoding="utf-8")
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if argv[0] == "/opt/bin/hmmpress":
            for suffix in (".h3f", ".h3i", ".h3m", ".h3p"):
                target.with_suffix(target.suffix + suffix).write_text("", encoding="utf-8")
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        raise AssertionError(f"unexpected subprocess invocation: {argv}")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        _fake_requirement_available,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.which_with_pixi",
        _fake_which_with_pixi,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.subprocess.run",
        _fake_subprocess_run,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_bam_subset_alias",
        lambda _root, source_path, **_kwargs: source_path,
    )

    cases = build_twentysecond_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == [
        "fallback_skill_builder",
        "flye_assemble",
        "hmmscan_search",
        "trinity_assemble",
        "varscan_call",
    ]
    assert "hmm_db" in (cases[2].prompt_context or {})
    assert "reads_2" in (cases[3].prompt_context or {})
    assert "reference_fasta" in (cases[4].prompt_context or {})
    assert cases[0].expected_outputs == ("fallback/fallback_skill_builder_report.json",)
    assert cases[1].expected_outputs == ("flye_out/assembly.fasta",)
    assert cases[2].expected_outputs == ("hmmscan/hmmscan.tbl", "hmmscan/hmmscan.txt")
    assert cases[3].expected_outputs == ("trinity_out/Trinity.fasta",)
    assert cases[4].expected_outputs == ("variants/varscan.vcf",)


def test_build_twentythird_tranche_qwen_skill_smoke_cases_includes_stringtie_and_fusion(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    selected_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260321_stringtie"
    )
    _write_clean_benchmark_run(selected_dir)
    alignments_dir = selected_dir / "alignments"
    alignments_dir.mkdir(parents=True, exist_ok=True)
    bam_path = alignments_dir / "SRR1278968.bam"
    bam_path.write_text("bam", encoding="utf-8")
    gff_path = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "references"
        / "C_parapsilosis_CDC317_current_features.gff"
    )
    gff_path.parent.mkdir(parents=True, exist_ok=True)
    gff_path.write_text(
        "chr1\tsource\tgene\t1\t10\t.\t+\t.\tID=gene1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke.requirement_available",
        lambda name: name in {"stringtie", "samtools", "STAR-Fusion"},
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_bam_subset_alias",
        lambda _root, source_path, **_kwargs: source_path,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_gff_to_gtf_alias",
        lambda _root, source_path, **_kwargs: source_path.with_suffix(".gtf"),
    )

    cases = build_twentythird_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == [
        "stringtie_quant",
        "fusion_star_fusion_style",
    ]
    assert cases[0].expected_outputs == (
        "stringtie/assembled.gtf",
        "stringtie/gene_abundances.tsv",
    )
    assert cases[1].expected_outputs == ("fusion/fusions.tsv",)


def test_build_twentyfourth_tranche_qwen_skill_smoke_cases_includes_blast_family(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    nucleotide_fasta = project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "query.fa"
    protein_fasta = project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "query.faa"
    archive_file = project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "archive" / "blastp.asn"
    protein_db = project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "prot_db" / "query_db"
    profile_list = project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "profiles" / "pssm_list.txt"
    profile_db = project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "profiles" / "domain_db"
    nucleotide_db = project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "nucl_db" / "query_db"
    alias_db = project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "alias_db" / "alias_db"

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_blast_query_aliases",
        lambda _root: (nucleotide_fasta, protein_fasta),
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_blast_database",
        lambda _root, _input, *, alias_parts, dbtype, parse_seqids=False: nucleotide_db if dbtype == "nucl" else protein_db,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_blast_archive",
        lambda _root, **_kwargs: archive_file,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_blast_alias_database",
        lambda _root, **_kwargs: alias_db,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_blast_profile_checkpoint",
        lambda _root, **_kwargs: project_root / "workspace" / "skill_smoke" / "_source_aliases" / "blast_family" / "profiles" / "p1.chk",
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_blast_profile_list",
        lambda _root, **_kwargs: profile_list,
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._ensure_blast_profile_database",
        lambda _root, **_kwargs: profile_db,
    )

    cases = build_twentyfourth_tranche_qwen_skill_smoke_cases(project_root)

    assert [case.name for case in cases] == [
        "makeblastdb_run",
        "blastn_search",
        "blastx_search",
        "tblastn_search",
        "tblastx_search",
        "psiblast_search",
        "blast_formatter_run",
        "blastdbcmd_run",
        "blastdb_aliastool_run",
        "makeprofiledb_run",
        "rpsblast_search",
        "rpstblastn_search",
    ]
    assert cases[0].expected_outputs == ("db/query_db.nsq",)
    assert cases[6].expected_outputs == ("blast_formatter/formatted.tsv",)
    assert cases[9].expected_outputs == ("profile_db/domain_db.rps",)
    assert cases[11].expected_outputs == ("rpstblastn/rpstblastn.tsv",)


def test_run_qwen_skill_smoke_matrix_summarizes_case_outputs(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    source_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "alzheimer-mouse"
        / "20260319_new"
    )
    _write_clean_benchmark_run(source_dir)

    def _fake_run(command, cwd, capture_output, text, timeout, check):  # noqa: ANN001
        selected_dir = Path(command[command.index("--selected-dir") + 1])
        selected_dir.mkdir(parents=True, exist_ok=True)
        case_name = selected_dir.name
        result_payload = {
            "run_id": f"run_{case_name}",
            "status": "completed",
            "error": "",
            "auto_repair_history_count": 0,
        }
        (selected_dir / "result.json").write_text(
            json.dumps(result_payload, indent=2),
            encoding="utf-8",
        )
        if case_name == "artifact_schema_profile":
            (selected_dir / "schema.json").write_text("{}", encoding="utf-8")
            expected_tool = "artifact_schema_profile"
        elif case_name == "multiqc_report":
            report_dir = selected_dir / "report_bundle"
            report_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("summary.json", "summary.md", "tooling_status.json"):
                (report_dir / filename).write_text("{}", encoding="utf-8")
            expected_tool = "multiqc_report"
        else:
            report_dir = selected_dir / "report_bundle"
            report_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("summary.json", "summary.md", "tooling_status.json"):
                (report_dir / filename).write_text("{}", encoding="utf-8")
            expected_tool = "quarto_report"
        run_dir = project_root / "workspace" / "runs" / f"run_{case_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event_type": "PLANNER_ATTEMPT_SUCCEEDED",
                            "payload": {"elapsed_seconds": 12.5},
                        }
                    ),
                    json.dumps(
                        {
                            "event_type": "STEP_FINISHED",
                            "payload": {"tool_name": expected_tool, "exit_code": 0},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        class _Completed:
            returncode = 0
            stdout = "ok\n"
            stderr = ""

        return _Completed()

    monkeypatch.setattr("bio_harness.analysis.qwen_skill_smoke.subprocess.run", _fake_run)

    summary = run_qwen_skill_smoke_matrix(
        project_root,
        label="unit_smoke",
        model_name="qwen3-coder-next:latest",
    )

    assert summary["all_passed"] is True
    assert summary["passed_case_count"] == 3
    assert {case["expected_tool"] for case in summary["cases"]} == {
        "artifact_schema_profile",
        "multiqc_report",
        "quarto_report",
    }


def test_smoke_matrix_can_filter_named_cases(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    source_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "alzheimer-mouse"
        / "20260319_new"
    )
    _write_clean_benchmark_run(source_dir)

    def _fake_run(command, cwd, capture_output, text, timeout, check):  # noqa: ANN001
        selected_dir = Path(command[command.index("--selected-dir") + 1])
        selected_dir.mkdir(parents=True, exist_ok=True)
        (selected_dir / "result.json").write_text(
            json.dumps(
                {
                    "run_id": f"run_{selected_dir.name}",
                    "status": "completed",
                    "error": "",
                    "auto_repair_history_count": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (selected_dir / "schema.json").write_text("{}", encoding="utf-8")
        run_dir = project_root / "workspace" / "runs" / f"run_{selected_dir.name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "event_type": "STEP_FINISHED",
                    "payload": {"tool_name": "artifact_schema_profile", "exit_code": 0},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        class _Completed:
            returncode = 0
            stdout = "ok\n"
            stderr = ""

        return _Completed()

    monkeypatch.setattr("bio_harness.analysis.qwen_skill_smoke.subprocess.run", _fake_run)

    summary = run_qwen_skill_smoke_matrix(
        project_root,
        label="unit_single_case",
        case_names=["artifact_schema_profile"],
    )

    assert summary["case_count"] == 1
    assert summary["cases"][0]["name"] == "artifact_schema_profile"


def test_smoke_matrix_can_fall_back_to_stdout_result_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    source_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "alzheimer-mouse"
        / "20260319_new"
    )
    _write_clean_benchmark_run(source_dir)

    def _fake_run(command, cwd, capture_output, text, timeout, check):  # noqa: ANN001
        selected_dir = Path(command[command.index("--selected-dir") + 1])
        selected_dir.mkdir(parents=True, exist_ok=True)
        (selected_dir / "schema.json").write_text("{}", encoding="utf-8")
        run_dir = project_root / "workspace" / "runs" / "run_stdout_case"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "event_type": "STEP_FINISHED",
                    "payload": {"tool_name": "artifact_schema_profile", "exit_code": 0},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        class _Completed:
            returncode = 0
            stdout = "\n".join(
                [
                    "[19:34:06] Run ID: run_stdout_case",
                    "Plan execution completed.",
                    json.dumps(
                        {
                            "run_id": "run_stdout_case",
                            "status": "completed",
                            "error": "",
                            "auto_repair_history_count": 0,
                        }
                    ),
                ]
            )
            stderr = ""

        return _Completed()

    monkeypatch.setattr("bio_harness.analysis.qwen_skill_smoke.subprocess.run", _fake_run)

    summary = run_qwen_skill_smoke_matrix(
        project_root,
        label="unit_stdout_case",
        case_names=["artifact_schema_profile"],
    )

    assert summary["all_passed"] is True
    assert summary["cases"][0]["run_id"] == "run_stdout_case"


def test_smoke_matrix_can_run_second_tranche(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    evolution_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260319_evolution"
    )
    _write_clean_benchmark_run(evolution_dir)
    (evolution_dir / "assembly").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_dir / "assembly" / "scaffolds.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (evolution_dir / "alignments" / "anc_aligned.bam").write_text("bam", encoding="utf-8")

    deseq_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260319_deseq"
    )
    (deseq_dir / "star_index").mkdir(parents=True, exist_ok=True)
    (deseq_dir / "star_index" / "Genome").write_text("index", encoding="utf-8")
    deseq_task_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "data"
    )
    deseq_task_data.mkdir(parents=True, exist_ok=True)
    (deseq_task_data / "SRR1278968_1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (deseq_task_data / "SRR1278968_2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._resolve_star_binary",
        lambda _: "/usr/local/bin/STAR",
    )
    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name in {"freebayes", "samtools"},
    )

    def _fake_run(command, cwd, capture_output, text, timeout, check):  # noqa: ANN001
        selected_dir = Path(command[command.index("--selected-dir") + 1])
        selected_dir.mkdir(parents=True, exist_ok=True)
        case_name = selected_dir.name
        if case_name == "star_align":
            out_dir = selected_dir / "star_output"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "sample_Aligned.out.bam").write_text("bam", encoding="utf-8")
            (out_dir / "sample_Log.final.out").write_text("log", encoding="utf-8")
            expected_tool = "star_align"
        else:
            out_dir = selected_dir / "variants"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "anc_raw.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
            expected_tool = "freebayes_call"
        run_dir = project_root / "workspace" / "runs" / f"run_{case_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "event_type": "STEP_FINISHED",
                    "payload": {"tool_name": expected_tool, "exit_code": 0},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        class _Completed:
            returncode = 0
            stdout = json.dumps(
                {
                    "run_id": f"run_{case_name}",
                    "status": "completed",
                    "error": "",
                    "auto_repair_history_count": 0,
                }
            )
            stderr = ""

        return _Completed()

    monkeypatch.setattr("bio_harness.analysis.qwen_skill_smoke.subprocess.run", _fake_run)

    summary = run_qwen_skill_smoke_matrix(
        project_root,
        label="unit_second_tranche",
        tranche="second",
    )

    assert summary["all_passed"] is True
    assert [case["name"] for case in summary["cases"]] == ["star_align", "freebayes_call"]


def test_smoke_matrix_can_run_fourth_tranche(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"

    deseq_task_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "data"
    )
    deseq_task_data.mkdir(parents=True, exist_ok=True)
    (deseq_task_data / "SRR1278968_1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (deseq_task_data / "SRR1278968_2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")
    deseq_reference_root = deseq_task_data.parent / "references"
    deseq_reference_root.mkdir(parents=True, exist_ok=True)
    (deseq_reference_root / "C_parapsilosis_CDC317_current_chromosomes.fasta").write_text(
        ">chr1\nACGT\n",
        encoding="utf-8",
    )
    (deseq_reference_root / "C_parapsilosis_CDC317_current_features.gff").write_text(
        "chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=g1\n",
        encoding="utf-8",
    )

    viral_task_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "viral-metagenomics"
        / "data"
    )
    viral_task_data.mkdir(parents=True, exist_ok=True)
    (viral_task_data / "sample_R1.fastq.gz").write_text("gz1", encoding="utf-8")
    (viral_task_data / "sample_R2.fastq.gz").write_text("gz2", encoding="utf-8")

    transcript_quant_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "transcript-quant"
        / "data"
    )
    transcript_quant_data.mkdir(parents=True, exist_ok=True)
    (transcript_quant_data / "reads_1.fq.gz").write_text("fq1", encoding="utf-8")
    (transcript_quant_data / "reads_2.fq.gz").write_text("fq2", encoding="utf-8")
    (transcript_quant_data / "transcriptome.fa").write_text(">tx1\nACGT\n", encoding="utf-8")

    phylogenetics_data = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "phylogenetics"
        / "data"
    )
    phylogenetics_data.mkdir(parents=True, exist_ok=True)
    (phylogenetics_data / "sequences.fasta").write_text(">s1\nACGT\n>s2\nACGT\n", encoding="utf-8")

    deseq_selected_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "deseq"
        / "20260319_deseq"
    )
    _write_clean_benchmark_run(deseq_selected_dir)
    (deseq_selected_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (deseq_selected_dir / "alignments" / "SRR1278968Aligned.sortedByCoord.out.bam").write_text(
        "bam",
        encoding="utf-8",
    )

    evolution_selected_dir = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "evolution"
        / "20260319_evolution"
    )
    _write_clean_benchmark_run(evolution_selected_dir)
    (evolution_selected_dir / "assembly").mkdir(parents=True, exist_ok=True)
    (evolution_selected_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (evolution_selected_dir / "assembly" / "scaffolds.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (evolution_selected_dir / "alignments" / "anc_aligned.bam").write_text("bam", encoding="utf-8")

    viral_reference_root = (
        project_root
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "official_runs"
        / "viral-metagenomics"
        / "replicate-1"
    )
    viral_reference_root.mkdir(parents=True, exist_ok=True)
    (viral_reference_root / "viral_ref.fasta").write_text(">virus\nACGT\n", encoding="utf-8")
    (viral_reference_root / "viral_ref.mmi").write_text("mmi", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.analysis.qwen_skill_smoke._has_executable",
        lambda name: name
        in {
            "cutadapt",
            "fastp",
            "hisat2",
            "featurecounts",
            "subread",
            "salmon",
            "kallisto",
            "bcftools",
            "samtools",
            "minimap2",
            "mafft",
            "iqtree",
        },
    )

    output_files = {
        "cutadapt_run": (
            "trimmed/trimmed_R1.fastq.gz",
            "trimmed/trimmed_R2.fastq.gz",
            "trimmed/cutadapt.json",
        ),
        "fastp_run": (
            "trimmed/trimmed_R1.fastq.gz",
            "trimmed/trimmed_R2.fastq.gz",
            "trimmed/fastp.json",
        ),
        "hisat2_align": ("alignments/sample.sam",),
        "featurecounts_run": ("counts/gene_counts.txt",),
        "salmon_quant": ("salmon_quant/quant.sf",),
        "kallisto_quant": ("kallisto_quant/abundance.tsv",),
        "bcftools_call": ("variants/anc_raw.vcf.gz", "variants/anc_raw.vcf.gz.tbi"),
        "minimap2_align": ("alignments/viral.bam", "alignments/viral.bam.bai"),
        "phylogenetics_workflow": ("aligned_sequences.fasta", "final/phylogeny.treefile"),
        "phylogenetics_iqtree_style": ("phylo/final/tree.nwk",),
    }

    def _fake_run(command, cwd, capture_output, text, timeout, check):  # noqa: ANN001
        selected_dir = Path(command[command.index("--selected-dir") + 1])
        selected_dir.mkdir(parents=True, exist_ok=True)
        case_name = selected_dir.name
        for rel_path in output_files[case_name]:
            output_path = selected_dir / rel_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("ok\n", encoding="utf-8")
        run_dir = project_root / "workspace" / "runs" / f"run_{case_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event_type": "PLANNER_ATTEMPT_SUCCEEDED",
                            "payload": {"elapsed_seconds": 9.1},
                        }
                    ),
                    *(
                        [
                            json.dumps(
                                {
                                    "event_type": "STEP_FINISHED",
                                    "payload": {"tool_name": "mafft_align", "exit_code": 0},
                                }
                            ),
                            json.dumps(
                                {
                                    "event_type": "STEP_FINISHED",
                                    "payload": {"tool_name": "phylogenetics_iqtree_style", "exit_code": 0},
                                }
                            ),
                        ]
                        if case_name == "phylogenetics_workflow"
                        else [
                            json.dumps(
                                {
                                    "event_type": "STEP_FINISHED",
                                    "payload": {"tool_name": case_name, "exit_code": 0},
                                }
                            )
                        ]
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        class _Completed:
            returncode = 0
            stdout = json.dumps(
                {
                    "run_id": f"run_{case_name}",
                    "status": "completed",
                    "error": "",
                    "auto_repair_history_count": 0,
                }
            )
            stderr = ""

        return _Completed()

    monkeypatch.setattr("bio_harness.analysis.qwen_skill_smoke.subprocess.run", _fake_run)

    summary = run_qwen_skill_smoke_matrix(
        project_root,
        label="unit_fourth_tranche",
        tranche="fourth",
    )

    assert summary["all_passed"] is True
    assert {case["name"] for case in summary["cases"]} == set(output_files)
    case_by_name = {case["name"]: case for case in summary["cases"]}
    assert case_by_name["phylogenetics_workflow"]["expected_tools"] == [
        "mafft_align",
        "phylogenetics_iqtree_style",
    ]
    assert case_by_name["phylogenetics_workflow"]["expected_tools_satisfied"] is True
    assert case_by_name["phylogenetics_workflow"]["expected_tools_in_order"] is True
