from __future__ import annotations

# ruff: noqa: F403,F405
from tests.core_cases.harness_guard_support import *

def test_materialize_cystic_fibrosis_deliverable_exports_final_csv(tmp_path: Path):
    selected_dir = tmp_path / "run"
    data_root = tmp_path / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "ex1.eff.vcf").write_text(
        "\n".join(
            [
                "##fileformat=VCFv4.2",
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12877\tNA12878\tNA12879\tNA12880\tNA12885\tNA12886",
                "7\t117227832\t.\tG\tT\t.\tPASS\tANN=T|stop_gained|HIGH|CFTR|ENSG00000001626|transcript|ENST00000003084|protein_coding|12/27|c.1624G>T|p.Gly542*\tGT\t0/1\t0/1\t1/1\t0/1\t1/1\t1/1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (data_root / "family_description.txt").write_text(
        "- Father: NA12877 (unaffected male)\n- Mother: NA12878 (unaffected female)\n1. NA12879 (affected female)\n2. NA12880 (unaffected female)\n7. NA12885 (affected female)\n8. NA12886 (affected male)\n",
        encoding="utf-8",
    )
    analysis_spec = {"analysis_type": "variant_annotation", "context_facts": ["clinical relevance filtering"]}

    exported, meta = _materialize_cystic_fibrosis_deliverable(
        selected_dir=selected_dir,
        data_root=data_root,
        plan={"plan": []},
        analysis_spec=analysis_spec,
        request_text="Find the genetic cause of cystic fibrosis in the family VCF",
    )

    assert exported is True
    assert meta["why"] == "materialized_cystic_fibrosis_deliverable"
    assert (selected_dir / "final" / "cf_variants.csv").exists()
def test_repair_multi_model_compare_pathways_command_rebinds_hallucinated_script(tmp_path: Path):
    selected_dir = tmp_path / "run"
    data_root = tmp_path / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "DEA_PS3O1S.csv").write_text("gene_name,log2fc,pval\nAPP,1.0,0.001\n", encoding="utf-8")
    (data_root / "GSE161904_Raw_gene_counts_cortex.txt").write_text("gene\tcase1\tctrl1\nENSMUSG1\t10\t1\n", encoding="utf-8")
    (data_root / "GSE168137_countList.txt").write_text("gene\tcase1\tctrl1\nENSMUSG2\t8\t2\n", encoding="utf-8")

    plan = {
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
        ]
    }

    repaired, meta = _repair_multi_model_compare_pathways_commands(
        plan,
        analysis_spec={"analysis_type": "multi_model_dge_pathway"},
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert "bio_harness/pipeline_scripts/compare_pathways.py" in command
    assert "--run-differential-analysis" in command
    assert "PS3O1S=" in command
    assert "3xTG_AD=" in command
    assert "5xFAD=" in command
    assert str(selected_dir / "final" / "pathway_comparison.csv") in command


def test_repair_multi_model_compare_pathways_command_rebinds_inline_python_workflow(tmp_path: Path):
    selected_dir = tmp_path / "run"
    data_root = tmp_path / "task" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "DEA_PS3O1S.csv").write_text("gene_name,log2fc,pval\nAPP,1.0,0.001\n", encoding="utf-8")
    (data_root / "GSE161904_Raw_gene_counts_cortex.txt").write_text("gene\tcase1\tctrl1\nENSMUSG1\t10\t1\n", encoding="utf-8")
    (data_root / "GSE168137_countList.txt").write_text("gene\tcase1\tctrl1\nENSMUSG2\t8\t2\n", encoding="utf-8")

    plan = {
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
        ]
    }

    repaired, meta = _repair_multi_model_compare_pathways_commands(
        plan,
        analysis_spec={"analysis_type": "multi_model_dge_pathway"},
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert "bio_harness/pipeline_scripts/compare_pathways.py" in command
    assert "--run-differential-analysis" in command
    assert "PS3O1S=" in command
    assert "3xTG_AD=" in command
    assert "5xFAD=" in command
    assert str(selected_dir / "final" / "pathway_comparison.csv") in command
def test_repair_metagenomics_trimmed_read_usage_rebinds_spades_and_kraken2(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    raw_r1 = data_root / "sample_R1.fastq.gz"
    raw_r2 = data_root / "sample_R2.fastq.gz"
    raw_r1.write_text("stub\n", encoding="utf-8")
    raw_r2.write_text("stub\n", encoding="utf-8")
    trimmed_r1 = selected_dir / "preprocessed" / "sample_R1_trimmed.fastq.gz"
    trimmed_r2 = selected_dir / "preprocessed" / "sample_R2_trimmed.fastq.gz"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && mkdir -p preprocessed qc && "
                        f"fastp --in1 {raw_r1} --in2 {raw_r2} "
                        "--out1 preprocessed/sample_R1_trimmed.fastq.gz "
                        "--out2 preprocessed/sample_R2_trimmed.fastq.gz "
                        "--json qc/fastp.json --html qc/fastp.html"
                    )
                },
                "step_id": 1,
            },
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": str(raw_r1),
                    "reads_2": str(raw_r2),
                    "threads": 8,
                    "memory_gb": 32,
                    "careful": True,
                    "output_dir": str(selected_dir / "assembly" / "metaspades"),
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && mkdir -p output && "
                        "kraken2 --db /tmp/db --threads 8 "
                        "--report output/sample_kraken2_report.txt "
                        "--output output/sample_kraken2_output.txt "
                        f"{raw_r1} {raw_r2}"
                    )
                },
                "step_id": 3,
            },
        ]
    }

    repaired, meta = _repair_metagenomics_trimmed_read_usage(
        plan,
        selected_dir=selected_dir,
        analysis_spec={"analysis_type": "metagenomics_classification"},
        request_text="Run metagenomics classification",
    )

    assert meta.get("changed", False) is True
    spades_args = repaired["plan"][1]["arguments"]
    assert spades_args["reads_1"] == str(trimmed_r1)
    assert spades_args["reads_2"] == str(trimmed_r2)
    assert spades_args["meta_mode"] is True
    assert spades_args["careful"] is False
    kraken_cmd = repaired["plan"][2]["arguments"]["command"]
    assert "--paired" in kraken_cmd
    assert str(trimmed_r1) in kraken_cmd
    assert str(trimmed_r2) in kraken_cmd
def test_repair_fastp_cli_flags_rewrites_threads_plural(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && mkdir -p qc && "
                        "fastp --in1 reads_R1.fastq.gz --in2 reads_R2.fastq.gz "
                        "--out1 out_R1.fastq.gz --out2 out_R2.fastq.gz --threads 4 "
                        "--json qc/fastp.json --html qc/fastp.html"
                    )
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_fastp_cli_flags(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert "--thread 4" in command
    assert "--threads 4" not in command
def test_quantification_export_repair_uses_salmon_numreads_column(tmp_path: Path):
    quant_sf = tmp_path / "salmon_out" / "quant.sf"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"awk 'NR>1 {{print $1\"\\t\"$2}}' {quant_sf} > {tmp_path / 'final' / 'counts.tsv'}"
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_quantification_count_exports(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert 'int($5)' in command
    assert str(quant_sf) in command
def test_quantification_export_repair_uses_kallisto_est_counts_column(tmp_path: Path):
    abundance = tmp_path / "kallisto_out" / "abundance.tsv"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"awk 'NR>1 {{print $1\"\\t\"$2}}' {abundance} > {tmp_path / 'final' / 'counts.tsv'}"
                },
                "step_id": 1,
            }
        ]
    }

    repaired, meta = _repair_quantification_count_exports(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert 'int($4)' in command
    assert str(abundance) in command
def test_quantification_export_repair_removes_redundant_salmon_rerun(tmp_path: Path):
    quant_sf = tmp_path / "salmon_out" / "quant.sf"
    final_tsv = tmp_path / "final" / "transcript_counts.tsv"
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {tmp_path} && salmon quant -i salmon_out -l A -1 reads_1.fq.gz -2 reads_2.fq.gz "
                        f"--validateMappings -o salmon_out && awk 'NR==1 || $4 != \"NumReads\" {{print $1\"\\t\"$4}}' "
                        f"{quant_sf} > {final_tsv}"
                    )
                },
                "step_id": 2,
            }
        ]
    }

    repaired, meta = _repair_quantification_count_exports(plan)

    assert meta.get("changed", False) is True
    command = repaired["plan"][0]["arguments"]["command"]
    assert "salmon quant" not in command
    assert "int($5)" in command
    assert str(quant_sf) in command
    assert str(final_tsv) in command
def test_materialize_transcript_quant_deliverable_from_salmon_quant_sf(tmp_path: Path):
    selected_dir = tmp_path / "run"
    salmon_dir = selected_dir / "salmon_out"
    salmon_dir.mkdir(parents=True, exist_ok=True)
    quant_sf = salmon_dir / "quant.sf"
    quant_sf.write_text(
        "Name\tLength\tEffectiveLength\tTPM\tNumReads\n"
        "tx1\t100\t80\t10.5\t12.8\n"
        "tx2\t120\t100\t4.0\t3.1\n",
        encoding="utf-8",
    )
    output_path = selected_dir / "final" / "transcript_counts.tsv"
    plan = {
        "plan": [
            {
                "tool_name": "salmon_quant",
                "arguments": {
                    "output_dir": str(salmon_dir),
                },
                "step_id": 1,
            }
        ]
    }
    analysis_spec = {
        "analysis_type": "transcript_quantification",
        "protocol_grounding": {
            "output_path": str(output_path),
        },
    }

    changed, meta = _materialize_transcript_quant_deliverable(
        selected_dir=selected_dir,
        plan=plan,
        analysis_spec=analysis_spec,
    )

    assert changed is True
    assert meta["source_kind"] == "salmon_quant_sf"
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "transcript_id\tcount",
        "tx1\t12",
        "tx2\t3",
    ]
def test_materialize_transcript_quant_deliverable_noops_when_output_exists(tmp_path: Path):
    selected_dir = tmp_path / "run"
    final_dir = selected_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    output_path = final_dir / "transcript_counts.tsv"
    output_path.write_text("transcript_id\tcount\ntx1\t7\n", encoding="utf-8")

    changed, meta = _materialize_transcript_quant_deliverable(
        selected_dir=selected_dir,
        plan={"plan": []},
        analysis_spec={
            "analysis_type": "transcript_quantification",
            "protocol_grounding": {"output_path": str(output_path)},
        },
    )

    assert changed is False
    assert meta["why"] == "deliverable_already_exists"
def test_materialize_transcript_quant_deliverable_defaults_to_canonical_final_path(tmp_path: Path):
    selected_dir = tmp_path / "run"
    salmon_dir = selected_dir / "salmon_out"
    salmon_dir.mkdir(parents=True, exist_ok=True)
    quant_sf = salmon_dir / "quant.sf"
    quant_sf.write_text(
        "Name\tLength\tEffectiveLength\tTPM\tNumReads\n"
        "tx1\t100\t80\t10.5\t12.8\n"
        "tx2\t120\t100\t4.0\t3.1\n",
        encoding="utf-8",
    )

    changed, meta = _materialize_transcript_quant_deliverable(
        selected_dir=selected_dir,
        plan={
            "plan": [
                {
                    "tool_name": "salmon_quant",
                    "arguments": {"output_dir": str(salmon_dir)},
                    "step_id": 1,
                }
            ]
        },
        analysis_spec={"analysis_type": "transcript_quantification", "protocol_grounding": {}},
    )

    output_path = selected_dir / "final" / "transcript_counts.tsv"
    assert changed is True
    assert meta["why"] == "materialized_transcript_quant_deliverable"
    assert meta["output_path"] == str(output_path.resolve(strict=False))
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "transcript_id\tcount",
        "tx1\t12",
        "tx2\t3",
    ]


def test_materialize_transcript_quant_deliverable_marks_stringtie_abundance_as_nonfatal(tmp_path: Path):
    selected_dir = tmp_path / "run"
    selected_dir.mkdir(parents=True, exist_ok=True)
    abundance_tsv = selected_dir / "gene_abundances.tsv"
    abundance_tsv.write_text(
        "Gene ID\tGene Name\tReference\tStrand\tStart\tEnd\tCoverage\tFPKM\tTPM\n"
        "GENE1\tGeneOne\tchr14\t+\t1\t100\t12.0\t3.0\t5.0\n",
        encoding="utf-8",
    )

    changed, meta = _materialize_transcript_quant_deliverable(
        selected_dir=selected_dir,
        plan={
            "plan": [
                {
                    "tool_name": "stringtie_quant",
                    "arguments": {},
                    "step_id": 1,
                }
            ]
        },
        analysis_spec={"analysis_type": "transcript_quantification", "protocol_grounding": {}},
    )

    assert changed is False
    assert meta["why"] == "stringtie_quant_outputs_present_without_count_export"
    assert meta["nonfatal"] is True
    assert meta["source_kind"] == "stringtie_gene_abundance_tsv"
def test_extract_deliverable_output_path_from_protocol_grounding_postprocess_command():
    protocol_grounding = {
        "postprocess": [
            {
                "tool_name": "bash_run",
                "command": (
                    "mkdir -p /tmp/run/final && "
                    "awk 'NR>1 {print $1\"\\t\"$5}' /tmp/run/salmon_out/quant.sf > /tmp/run/final/transcript_counts.tsv"
                ),
            }
        ]
    }

    output_path = _extract_deliverable_output_path_from_protocol_grounding(protocol_grounding)

    assert output_path == "/tmp/run/final/transcript_counts.tsv"
def test_materialize_deseq_deliverable_from_deseq2_results_tsv(tmp_path: Path):
    selected_dir = tmp_path / "run"
    deseq_dir = selected_dir / "deseq2_results"
    deseq_dir.mkdir(parents=True, exist_ok=True)
    results_tsv = deseq_dir / "deseq2_results.tsv"
    results_tsv.write_text(
        "baseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj\tgene_id\n"
        "10\t2.5\t0.1\t3.0\t0.001\t0.005\tCPAR2_600150\n"
        "8\t1.2\t0.2\t1.0\t0.200\t0.400\tCPAR2_600160\n",
        encoding="utf-8",
    )
    plan = {
        "plan": [
            {
                "tool_name": "deseq2_run",
                "arguments": {
                    "output_dir": str(deseq_dir),
                },
                "step_id": 1,
            }
        ]
    }

    changed, meta = _materialize_deseq_deliverable(
        selected_dir=selected_dir,
        plan=plan,
        analysis_spec={"analysis_type": "rna_seq_differential_expression"},
    )

    output_path = selected_dir / "final" / "deseq_results.csv"
    assert changed is True
    assert meta["why"] == "materialized_deseq_deliverable"
    assert meta["row_count"] == 1
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "gene_id,log2FoldChange,pvalue,padj",
        "CPAR2_600150,2.5,0.001,0.005",
    ]


def test_materialize_deseq_deliverable_falls_back_to_measured_rows(tmp_path: Path):
    """Mini DE fixtures should export real measured rows even without hits."""

    selected_dir = tmp_path / "run"
    deseq_dir = selected_dir / "deseq2_results"
    deseq_dir.mkdir(parents=True, exist_ok=True)
    results_tsv = deseq_dir / "deseq2_results.tsv"
    results_tsv.write_text(
        "gene_id\tbaseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj\n"
        "geneA\t13.0\t-0.65\t0.4\t-1.5\t0.115\t0.115\n"
        "geneB\t26.0\t0.65\t0.2\t2.2\t0.022\t0.044\n",
        encoding="utf-8",
    )
    plan = {
        "plan": [
            {
                "tool_name": "deseq2_run",
                "arguments": {
                    "output_dir": str(deseq_dir),
                },
                "step_id": 1,
            }
        ]
    }

    changed, meta = _materialize_deseq_deliverable(
        selected_dir=selected_dir,
        plan=plan,
        analysis_spec={"analysis_type": "rna_seq_differential_expression"},
    )

    output_path = selected_dir / "final" / "deseq_results.csv"
    assert changed is True
    assert meta["row_count"] == 2
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "gene_id,log2FoldChange,pvalue,padj",
        "geneA,-0.65,0.115,0.115",
        "geneB,0.65,0.022,0.044",
    ]


def test_materialize_deseq_deliverable_respects_requested_final_csv_path(tmp_path: Path):
    selected_dir = tmp_path / "run"
    deseq_dir = selected_dir / "my_analysis" / "de_intermediate"
    deseq_dir.mkdir(parents=True, exist_ok=True)
    results_tsv = deseq_dir / "deseq2_results.tsv"
    results_tsv.write_text(
        "baseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj\tgene_id\n"
        "10\t2.5\t0.1\t3.0\t0.001\t0.005\tCPAR2_600150\n",
        encoding="utf-8",
    )
    requested_csv = selected_dir / "my_analysis" / "final_result.csv"
    plan = {
        "plan": [
            {
                "tool_name": "deseq2_run",
                "arguments": {
                    "output_dir": str(deseq_dir),
                },
                "step_id": 1,
            }
        ]
    }

    changed, meta = _materialize_deseq_deliverable(
        selected_dir=selected_dir,
        plan=plan,
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "required_deliverables": [str(requested_csv)],
        },
    )

    assert changed is True
    assert meta["output_path"] == str(requested_csv.resolve(strict=False))
    assert requested_csv.exists()
def test_materialize_single_cell_deliverable_from_json_outputs(tmp_path: Path):
    selected_dir = tmp_path / "run"
    selected_dir.mkdir(parents=True, exist_ok=True)
    (selected_dir / "cluster_assignments.json").write_text(
        '{"BC1":"0","BC2":"0","BC3":"1","BC4":"1"}',
        encoding="utf-8",
    )
    (selected_dir / "marker_genes.json").write_text(
        '{"0":["Gene0000","Gene0001"],"1":["Gene0015","Gene0016"]}',
        encoding="utf-8",
    )
    (selected_dir / "raw_counts.json").write_text(
        '{"BC1":{"Gene0000":9},"BC2":{"Gene0001":8},"BC3":{"Gene0015":10},"BC4":{"Gene0016":7}}',
        encoding="utf-8",
    )

    changed, meta = _materialize_single_cell_deliverable(
        selected_dir=selected_dir,
        analysis_spec={"analysis_type": "single_cell_rna_seq"},
    )

    output_path = selected_dir / "final" / "single_cell_results.csv"
    assert changed is True
    assert meta["why"] == "materialized_single_cell_deliverable"
    assert output_path.exists()
    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "cluster_id,predicted_cell_type,gene_name,logfoldchanges,pvals,pvals_adj,direction,abs_logfc"
    assert any(",TypeA," in line for line in lines[1:])
    assert any(",TypeB," in line for line in lines[1:])


def test_materialize_single_cell_deliverable_finds_wrapper_output_dir(tmp_path: Path):
    selected_dir = tmp_path / "run"
    output_dir = selected_dir / "sc_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cluster_assignments.json").write_text(
        '{"BC1":"0","BC2":"0","BC3":"1","BC4":"1"}',
        encoding="utf-8",
    )
    (output_dir / "marker_genes.json").write_text(
        '{"0":["Gene0000","Gene0001"],"1":["Gene0015","Gene0016"]}',
        encoding="utf-8",
    )
    (output_dir / "raw_counts.json").write_text(
        '{"BC1":{"Gene0000":9},"BC2":{"Gene0001":8},"BC3":{"Gene0015":10},"BC4":{"Gene0016":7}}',
        encoding="utf-8",
    )

    changed, meta = _materialize_single_cell_deliverable(
        selected_dir=selected_dir,
        analysis_spec={"analysis_type": "single_cell_rna_seq"},
        plan={
            "plan": [
                {
                    "tool_name": "sc_count_and_cluster",
                    "arguments": {"output_dir": str(output_dir)},
                }
            ]
        },
    )

    output_path = selected_dir / "final" / "single_cell_results.csv"
    assert changed is True
    assert meta["why"] == "materialized_single_cell_deliverable"
    assert meta["cluster_assignments"] == str(output_dir / "cluster_assignments.json")
    assert output_path.exists()
def test_repair_single_cell_export_tail_removes_fragile_copy_step(tmp_path: Path):
    selected_dir = tmp_path / "run"
    plan = {
        "plan": [
            {
                "tool_name": "sc_count_and_cluster",
                "arguments": {
                    "r1": "sample_R1.fastq",
                    "r2": "sample_R2.fastq",
                    "output_dir": str(selected_dir),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir / 'final'} && "
                        f"cp {selected_dir / 'differential_expression.csv'} {selected_dir / 'single_cell_results.csv'}"
                    )
                },
                "step_id": 2,
            },
        ]
    }

    repaired, meta = _repair_single_cell_export_tail(
        plan,
        analysis_spec={"analysis_type": "single_cell_rna_seq"},
    )

    assert meta["changed"] is True
    assert meta["why"] == "single_cell_export_tail_removed"
    assert [step["tool_name"] for step in repaired["plan"]] == ["sc_count_and_cluster"]
def test_repair_deseq_bash_run_to_skill_rewrites_inline_r_deseq_step(tmp_path: Path):
    selected_dir = tmp_path / "run"
    selected_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "cd /tmp && Rscript -e '\n"
                        "library(DESeq2);\n"
                        "counts <- read.table(\"counts/gene_counts.txt\", header=TRUE, row.names=1, skip=1);\n"
                        "coldata <- read.table(\"metadata.tsv\", header=TRUE, row.names=1, sep=\"\\t\");\n"
                        "dds <- DESeqDataSetFromMatrix(countData=counts, colData=coldata, design=~condition);\n"
                        "dds <- DESeq(dds);\n"
                        "res <- results(dds, contrast=c(\"condition\", \"biofilm\", \"planktonic\"));\n"
                        "res_df <- as.data.frame(res);\n"
                        "write.csv(res_df, \"final/deseq_results.csv\", row.names=FALSE);\n"
                        "'"
                    )
                },
                "step_id": 5,
            }
        ]
    }

    repaired, meta = _repair_deseq_bash_run_to_skill(
        plan,
        selected_dir=selected_dir,
        analysis_spec={"analysis_type": "rna_seq_differential_expression"},
    )

    assert meta["changed"] is True
    assert meta["replacements"] == [{"step_id": 5, "mode": "bash_run_to_deseq2_run"}]
    step = repaired["plan"][0]
    assert step["tool_name"] == "deseq2_run"
    assert step["step_id"] == 1
    assert step["arguments"]["counts_matrix"] == "counts/gene_counts.txt"
    assert step["arguments"]["metadata_table"] == str((selected_dir / "metadata.tsv").resolve(strict=False))
    assert step["arguments"]["contrast"] == "condition_biofilm_vs_planktonic"
    assert step["arguments"]["output_dir"] == str((selected_dir / "deseq2_results").resolve(strict=False))
    assert step["arguments"]["script_path"].endswith("bio_harness/pipeline_scripts/deseq2_wrapper.R")
