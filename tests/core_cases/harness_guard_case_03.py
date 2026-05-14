from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_repair_rna_seq_de_plan_with_assay_compiler_rebuilds_malformed_deseq_plan(tmp_path: Path):
    task_dir = tmp_path / "task"
    data_root = task_dir / "data"
    refs = task_dir / "references"
    selected_dir = tmp_path / "run"
    data_root.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    selected_dir.mkdir(parents=True, exist_ok=True)

    for sample in ("SRR1278968", "SRR1278969", "SRR1278970", "SRR1278971"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")
    (data_root / "sample_metadata.tsv").write_text(
        "sample\tcondition\n"
        "SRR1278968\tPlankton\n"
        "SRR1278969\tPlankton\n"
        "SRR1278970\tBiofilm\n"
        "SRR1278971\tBiofilm\n",
        encoding="utf-8",
    )
    (refs / "genome.fasta").write_text(">chr1\nACGTACGTACGT\n", encoding="utf-8")
    (refs / "genes.gff").write_text(
        "chr1\tsrc\tgene\t1\t12\t.\t+\t.\tID=g1;Name=g1\n",
        encoding="utf-8",
    )

    malformed_plan = {
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "genome_dir": str(data_root),
                    "output_prefix": "star_align",
                    "reads_1": "SRR1278968_1.fastq",
                    "reads_2": "SRR1278968_2.fastq",
                    "threads": 1,
                },
                "step_id": 1,
            },
            {
                "tool_name": "featurecounts_run",
                "arguments": {
                    "annotation_gtf": str(data_root / "sample_metadata.tsv"),
                    "input_bams": "star_align.bam",
                    "output_counts": "featurecounts_run.tsv",
                    "threads": 1,
                },
                "step_id": 2,
            },
            {
                "tool_name": "dexseq_run",
                "arguments": {
                    "contrast": "planktonic_vs_biofilm",
                    "counts_matrix": "featurecounts_run.tsv",
                    "design_formula": "~ 1",
                    "metadata_table": "sample_metadata.tsv",
                    "output_dir": str(selected_dir / "final"),
                },
                "step_id": 3,
            },
        ]
    }

    repaired, meta = _repair_rna_seq_de_plan_with_assay_compiler(
        malformed_plan,
        selected_dir=selected_dir,
        data_root=data_root,
        analysis_spec={"analysis_type": "rna_seq_differential_expression"},
    )

    tool_names = [step["tool_name"] for step in repaired["plan"]]
    assert meta["changed"] is True
    assert "uses_dexseq_run" in meta["reasons"]
    assert "missing_deseq2_run" in meta["reasons"]
    assert "featurecounts_annotation_points_to_metadata" in meta["reasons"]
    assert tool_names.count("cutadapt_run") == 4
    assert tool_names.count("bash_run") >= 1
    assert tool_names.count("subread_align") + tool_names.count("star_align") == 4
    assert "featurecounts_run" in tool_names
    assert "deseq2_run" in tool_names
    assert repaired["plan"][-1]["arguments"]["contrast"] == "condition_Biofilm_vs_Plankton"
    first_aligner = next(
        step for step in repaired["plan"] if step["tool_name"] in {"star_align", "subread_align"}
    )
    assert "trimmed" in first_aligner["arguments"]["reads_1"]
    assert "trimmed" in first_aligner["arguments"]["reads_2"]
def test_extract_deseq_rows_for_export_keeps_only_significant_upregulated(tmp_path: Path):
    source = tmp_path / "deseq2_results.tsv"
    source.write_text(
        "gene_id\tlog2FoldChange\tpvalue\tpadj\n"
        "g1\t2.5\t1e-6\t1e-4\n"
        "g2\t1.5\t1e-8\t1e-6\n"
        "g3\t3.0\t0.02\t0.02\n",
        encoding="utf-8",
    )

    rows = _extract_deseq_rows_for_export(source)

    assert rows == [{"gene_id": "g1", "log2FoldChange": "2.5", "pvalue": "1e-6", "padj": "1e-4"}]
def test_repair_rna_seq_de_plan_uses_stranded_pydeseq2_for_official_deseq(tmp_path: Path):
    task_dir = tmp_path / "task"
    data_root = task_dir / "data"
    refs = task_dir / "references"
    selected_dir = tmp_path / "deseq_case"
    data_root.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    selected_dir.mkdir(parents=True, exist_ok=True)

    for sample in ("SRR1278968", "SRR1278969", "SRR1278970", "SRR1278971"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")
    (data_root / "sample_metadata.tsv").write_text(
        "sample\tcondition\n"
        "SRR1278968\tPlankton\n"
        "SRR1278969\tPlankton\n"
        "SRR1278970\tBiofilm\n"
        "SRR1278971\tBiofilm\n",
        encoding="utf-8",
    )
    (refs / "genome.fasta").write_text(">chr1\nACGTACGTACGT\n", encoding="utf-8")
    (refs / "genes.gff").write_text(
        "chr1\tsrc\tgene\t1\t12\t.\t+\t.\tID=g1;Name=g1\n",
        encoding="utf-8",
    )

    repaired, _ = _repair_rna_seq_de_plan_with_assay_compiler(
        {"plan": []},
        selected_dir=selected_dir,
        data_root=data_root,
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        },
    )

    featurecounts_step = next(step for step in repaired["plan"] if step["tool_name"] == "featurecounts_run")
    deseq_step = next(step for step in repaired["plan"] if step["tool_name"] == "deseq2_run")
    assert featurecounts_step["arguments"]["strand_specificity"] == 2
    assert deseq_step["arguments"]["engine"] == "pydeseq2"
def test_missing_input_paths_for_plan_allows_inputs_produced_by_prior_steps(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    gtf = selected_dir / "mouse.gtf"
    fasta = selected_dir / "mouse.fa"
    gtf.write_text("chr1\tsrc\texon\t1\t10\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "subread_align",
                "arguments": {
                    "reads_1": "reads_R1.fastq",
                    "reads_2": "reads_R2.fastq",
                    "reference_fasta": str(fasta),
                    "output_bam": str(selected_dir / "outputs" / "S1.bam"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "rmats_run",
                "arguments": {
                    "group1_bams": [str(selected_dir / "outputs" / "S1.bam")],
                    "group2_bams": [str(selected_dir / "outputs" / "S2.bam")],
                    "annotation_gtf": str(gtf),
                    "output_dir": str(selected_dir / "outputs" / "rmats"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "subread_align",
                "arguments": {
                    "reads_1": "reads2_R1.fastq",
                    "reads_2": "reads2_R2.fastq",
                    "reference_fasta": str(fasta),
                    "output_bam": str(selected_dir / "outputs" / "S2.bam"),
                },
                "step_id": 3,
            },
            {
                "tool_name": "varscan_call",
                "arguments": {
                    "reference_fasta": str(fasta),
                    "input_bam": str(selected_dir / "outputs" / "S1.bam"),
                    "output_vcf": str(selected_dir / "outputs" / "S1.vcf"),
                },
                "step_id": 4,
            },
        ]
    }

    missing = _missing_input_paths_for_plan(plan, selected_dir, data_root)

    assert all("group1_bams" not in item for item in missing)
    assert all("group2_bams" not in item for item in missing)
    assert all("varscan_call.input_bam" not in item for item in missing)
def test_missing_input_paths_for_plan_allows_spades_reference_from_prior_output_dir(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)

    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "anc_R1.fastq.gz",
                    "reads_2": "anc_R2.fastq.gz",
                    "threads": 4,
                    "memory_gb": 16,
                    "output_dir": str(selected_dir / "assembly"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "evol1_R1.fastq.gz",
                    "reads_2": "evol1_R2.fastq.gz",
                    "reference_fasta": str(selected_dir / "assembly" / "contigs.fasta"),
                    "output_bam": str(selected_dir / "alignments" / "evol1.bam"),
                },
                "step_id": 2,
            },
        ]
    }

    missing = _missing_input_paths_for_plan(plan, selected_dir, data_root)

    assert all("bwa_mem_align.reference_fasta" not in item for item in missing)
def test_shared_variant_export_repair_rewrites_brittle_bash_step(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evol1.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol1.snpeff.vcf"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evol2.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol2.snpeff.vcf"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"bcftools isec -p {selected_dir / 'final' / 'isec'} "
                        f"{selected_dir / 'variants' / 'evol1.snpeff.vcf'} "
                        f"{selected_dir / 'variants' / 'evol2.snpeff.vcf'} "
                        f"&& awk ... > {selected_dir / 'final' / 'variants_shared.csv'}"
                    )
                },
                "step_id": 3,
            },
        ]
    }

    repaired, meta = _repair_shared_variant_csv_exports(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][2]["arguments"]["command"]
    assert "export_shared_variants_csv.py" in command
    assert "--input-vcf-a" in command
    assert "--input-vcf-b" in command
    assert "--dedupe-by-gene" in command
def test_shared_variant_export_repair_handles_single_annotated_shared_vcf(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    shared_vcf = selected_dir / "calls" / "shared_annotated.vcf"
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "calls" / "shared_raw.vcf"),
                    "output_vcf": str(shared_vcf),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "mkdir -p "
                        f"{selected_dir / 'final'} && awk -F'[,\\t]' "
                        f"'BEGIN{{OFS=\",\"}} /^#/ {{next}} {{print $1,$2,$3,$4,$5}}' "
                        f"{shared_vcf} > {selected_dir / 'final' / 'variants_shared.csv'}"
                    )
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "mkdir -p "
                        f"{selected_dir / 'annotations'} && bcftools filter -i "
                        f"'IMPACT=\"MODERATE\" || IMPACT=\"HIGH\"' {shared_vcf} "
                        f"| bgzip -c > {selected_dir / 'annotations' / 'shared_highmod.vcf.gz'} "
                        f"&& tabix -f -p vcf {selected_dir / 'annotations' / 'shared_highmod.vcf.gz'}"
                    )
                },
                "step_id": 3,
            },
        ]
    }

    repaired, meta = _repair_shared_variant_csv_exports(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][1]["arguments"]["command"]
    assert "export_shared_variants_csv.py" in command
    assert str(shared_vcf) in command
    assert "--input-vcf-a" in command
    assert "--input-vcf-b" in command
    assert "--dedupe-by-gene" in command
    assert len(repaired["plan"]) == 2
    assert meta.get("diff_summary", {}).get("removed_step_count") == 1


def test_shared_variant_export_repair_detects_inline_snpeff_annotation_outputs(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir} && "
                        f"snpEff annotate -gff3 ancestor_assembly/ancestor_genes.gff shared_raw.vcf "
                        f"> {selected_dir / 'shared_annotated.vcf'}"
                    )
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"bcftools norm -f ancestor_assembly/contigs.fasta {selected_dir / 'shared_annotated.vcf'} "
                        f"-Oz -o {selected_dir / 'shared_normalized.vcf.gz'} && "
                        f"tabix -p vcf {selected_dir / 'shared_normalized.vcf.gz'} && "
                        f"vcftools --gzvcf {selected_dir / 'shared_normalized.vcf.gz'} --remove-indels --recode --stdout | "
                        f"awk 'BEGIN{{OFS=\",\"}} /^#/ {{next}} {{print $1,$2,$4,$5}}' > "
                        f"{selected_dir / 'final_output' / 'shared_variants.csv'}"
                    )
                },
                "step_id": 2,
            },
        ]
    }

    repaired, meta = _repair_shared_variant_csv_exports(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][1]["arguments"]["command"]
    assert "export_shared_variants_csv.py" in command
    assert str(selected_dir / "shared_normalized.vcf.gz") in command
    assert str(selected_dir / "final_output" / "variants_shared.csv") in command


def test_shared_variant_export_repair_handles_bcftools_query_to_arbitrary_csv(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evol1.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol1.annotated.vcf"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evol2.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol2.annotated.vcf"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools query -f '%CHROM,%POS,%REF,%ALT,%INFO/ANN\\n' shared_moderate_high.vcf "
                        f"| awk -F',' 'BEGIN{{OFS=\",\"}} {{print $1,$2,$3,$4,$5}}' > {selected_dir / 'results' / 'variants_shared_moderate_high.csv'}"
                    )
                },
                "step_id": 3,
            },
        ]
    }

    repaired, meta = _repair_shared_variant_csv_exports(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][2]["arguments"]["command"]
    assert "export_shared_variants_csv.py" in command
    assert str(selected_dir / "results" / "variants_shared.csv") in command
    assert "--input-vcf-a" in command
    assert "--input-vcf-b" in command
def test_extract_csv_output_from_command_handles_cli_output_flags():
    command = "vcf2csv --fields CHROM,POS --out shared_moderate_high.csv"

    assert _extract_csv_output_from_command(command) == "shared_moderate_high.csv"
def test_shared_variant_export_repair_handles_vcf2csv_out_flag(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evol1.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol1.annotated.vcf"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evol2.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol2.annotated.vcf"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools isec -C -n=2 evol1.annotated.vcf evol2.annotated.vcf | "
                        "vcf2csv --fields CHROM,POS,REF,ALT,ANN[0].GENE --out shared_moderate_high.csv"
                    )
                },
                "step_id": 3,
            },
        ]
    }

    repaired, meta = _repair_shared_variant_csv_exports(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][2]["arguments"]["command"]
    assert "export_shared_variants_csv.py" in command
    assert "variants_shared.csv" in command
def test_shared_variant_export_repair_uses_benchmark_export_profile(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evol1.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol1.annotated.vcf"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": str(selected_dir / "variants" / "evol2.vcf.gz"),
                    "output_vcf": str(selected_dir / "variants" / "evol2.annotated.vcf"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools isec evol1.annotated.vcf evol2.annotated.vcf "
                        f"| awk '{{print $0}}' > {selected_dir / 'results' / 'variants_shared.csv'}"
                    )
                },
                "step_id": 3,
            },
        ]
    }
    analysis_spec = {
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
    }

    repaired, meta = _repair_shared_variant_csv_exports_with_analysis_spec(plan, analysis_spec=analysis_spec)

    assert meta.get("changed", False) is True
    command = repaired["plan"][2]["arguments"]["command"]
    assert "--header-case upper" in command
    assert "--status shared" in command
    assert "--min-impact MODERATE" in command
    assert "--dedupe-by-gene" in command
