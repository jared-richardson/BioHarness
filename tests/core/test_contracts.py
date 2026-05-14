from __future__ import annotations

from bio_harness.core.contracts import assess_plan_contract


def test_rmats_script_satisfies_differential_and_group_comparison_contract():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "STAR --runMode alignReads --genomeDir /tmp/star_idx --readFilesIn /tmp/a_R1.fastq /tmp/a_R2.fastq",
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bash /repo/bio_harness/pipeline_scripts/run_rmats_if_needed.sh "
                        "outputs/group_a_bams.txt outputs/group_b_bams.txt "
                        "/refs/mouse.gtf outputs/rmats outputs/rmats_tmp 150 2"
                    ),
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": [
            "alignment",
            "reference_inputs",
            "splicing_analysis",
            "differential_analysis",
            "group_comparison",
        ],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_contract_flags_missing_diff_and_group_when_plan_lacks_two_group_analysis():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "STAR --runMode alignReads --genomeDir /tmp/star_idx --readFilesIn /tmp/a_R1.fastq /tmp/a_R2.fastq",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["differential_analysis", "group_comparison"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is False
    assert "differential_analysis" in validation["missing_capabilities"]
    assert "group_comparison" in validation["missing_capabilities"]


def test_b1_b2_options_count_as_group_comparison_for_splicing_contracts():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "rmats.py --b1 /tmp/groupA.txt --b2 /tmp/groupB.txt --gtf /refs/genes.gtf",
                },
            },
        ]
    }
    contract = {"must_include_capabilities": ["splicing_analysis", "group_comparison"], "explicit_tool_hints": []}

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_deseq2_design_counts_as_group_comparison_without_splicing_markers():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "Rscript run_deseq2.R --counts outputs/counts.tsv "
                        "--metadata outputs/sample_metadata.tsv --design '~ condition' "
                        "--contrast treatment,control"
                    ),
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["differential_analysis", "group_comparison"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_direct_wrapper_group_column_counts_as_group_comparison_signal():
    plan = {
        "plan": [
            {
                "tool_name": "proteomics_diff_abundance",
                "arguments": {
                    "abundance_matrix": "/tmp/abundance_matrix.csv",
                    "metadata_table": "/tmp/metadata.csv",
                    "output_dir": "/tmp/out",
                    "group_column": "condition",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["group_comparison"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_direct_wrapper_tool_capabilities_satisfy_group_comparison_without_explicit_group_args():
    plan = {
        "plan": [
            {
                "tool_name": "proteomics_diff_abundance",
                "arguments": {
                    "abundance_matrix": "/tmp/abundance_matrix.csv",
                    "metadata_table": "/tmp/metadata.csv",
                    "output_dir": "/tmp/out",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["proteomics", "differential_analysis", "group_comparison"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_metabolomics_direct_wrapper_tool_capabilities_satisfy_group_comparison_without_explicit_group_args():
    plan = {
        "plan": [
            {
                "tool_name": "metabolomics_diff_abundance",
                "arguments": {
                    "feature_table": "/tmp/feature_table.csv",
                    "metadata_table": "/tmp/metadata.csv",
                    "output_dir": "/tmp/out",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["metabolomics", "differential_analysis", "group_comparison"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_contract_passes_for_quant_variant_single_cell_signals():
    plan = {
        "plan": [
            {
                "tool_name": "featurecounts_run",
                "arguments": {
                    "annotation_gtf": "/tmp/genes.gtf",
                    "input_bams": "/tmp/sample.bam",
                    "output_counts": "/tmp/counts.tsv",
                    "threads": 2,
                },
            },
            {
                "tool_name": "gatk_haplotypecaller",
                "arguments": {
                    "reference_fasta": "/tmp/ref.fa",
                    "input_bam": "/tmp/sample.bam",
                    "output_vcf": "/tmp/sample.vcf",
                },
            },
            {
                "tool_name": "star_solo_count",
                "arguments": {
                    "reads_1": "/tmp/r2.fq.gz",
                    "reads_2": "/tmp/r1.fq.gz",
                    "whitelist": "/tmp/whitelist.txt",
                    "genome_dir": "/tmp/ref",
                    "output_prefix": "/tmp/solo/sample_",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["quantification", "variant_calling", "single_cell_analysis"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_contract_passes_for_structural_variant_calling_plan():
    plan = {
        "plan": [
            {
                "tool_name": "sniffles_sv_call",
                "arguments": {
                    "input_bam": "/tmp/long_reads.bam",
                    "reference_fasta": "/tmp/reference.fa",
                    "output_vcf": "/tmp/sniffles.vcf",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["structural_variant_calling", "reference_inputs"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_contract_treats_star_solo_count_as_satisfying_starsolo_hint():
    plan = {
        "plan": [
            {
                "tool_name": "star_solo_count",
                "arguments": {
                    "reads_1": "/tmp/sample_R1.fastq.gz",
                    "reads_2": "/tmp/sample_R2.fastq.gz",
                    "whitelist": "/tmp/barcodes_whitelist.txt",
                    "genome_dir": "/tmp/star_index",
                    "output_prefix": "/tmp/solo/sample_",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": [],
        "explicit_tool_hints": ["starsolo", "star_solo_count"],
        "required_tool_hints": ["starsolo", "star_solo_count"],
    }

    validation = assess_plan_contract(plan, contract)

    assert validation["passed"] is True
    assert validation["missing_required_tool_hints"] == []
    assert validation["missing_tool_hints"] == []


def test_reference_inputs_contract_accepts_variant_and_clinvar_inputs():
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/tmp/ex1.eff.vcf",
                    "output_vcf": "/tmp/ex1.annotated.vcf",
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "bcftools annotate -a /refs/clinvar_20250521.vcf.gz -o /tmp/out.vcf /tmp/ex1.annotated.vcf",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["annotation", "reference_inputs"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_reference_inputs_contract_preserves_default_signals_when_capability_specs_override():
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/tmp/ex1.eff.vcf",
                    "output_vcf": "/tmp/ex1.annotated.vcf",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["reference_inputs"],
        "explicit_tool_hints": [],
    }
    capability_specs = {
        "reference_inputs": {
            "plan_signals": ["reference", "genome"],
        }
    }

    validation = assess_plan_contract(plan, contract, capability_specs=capability_specs)

    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_single_cell_end_to_end_tool_satisfies_alignment_contract():
    plan = {
        "plan": [
            {
                "tool_name": "sc_count_and_cluster",
                "arguments": {
                    "r1": "/tmp/sample_R1.fastq.gz",
                    "r2": "/tmp/sample_R2.fastq.gz",
                    "whitelist": "/tmp/barcodes_whitelist.txt",
                    "reference": "/tmp/reference.fa",
                    "gtf": "/tmp/annotation.gtf",
                    "outdir": "/tmp/sc_out",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["alignment", "reference_inputs", "single_cell_analysis"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_single_cell_end_to_end_tool_satisfies_differential_analysis_contract():
    plan = {
        "plan": [
            {
                "tool_name": "sc_count_and_cluster",
                "arguments": {
                    "r1": "/tmp/sample_R1.fastq.gz",
                    "r2": "/tmp/sample_R2.fastq.gz",
                    "whitelist": "/tmp/barcodes_whitelist.txt",
                    "reference": "/tmp/reference.fa",
                    "gtf": "/tmp/annotation.gtf",
                    "output_dir": "/tmp/sc_out",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["single_cell_analysis", "differential_analysis"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_contract_flags_missing_chipseq_when_only_alignment_present():
    plan = {
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "genome_dir": "/tmp/ref",
                    "reads_1": "/tmp/r1.fq.gz",
                    "reads_2": "/tmp/r2.fq.gz",
                    "output_prefix": "/tmp/out/sample_",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["alignment", "chipseq_analysis"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is False
    assert "chipseq_analysis" in validation["missing_capabilities"]


def test_structured_tool_arguments_contribute_contract_signals():
    plan = {
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "genome_dir": "/tmp/refs/mouse_star_index",
                    "reads_1": "/tmp/1_S1_R1_001.fastq",
                    "reads_2": "/tmp/1_S1_R2_001.fastq",
                    "output_prefix": "/tmp/out/S1_",
                    "threads": 4,
                },
            },
            {
                "tool_name": "featurecounts_run",
                "arguments": {
                    "annotation_gtf": "/tmp/refs/mouse_gtf",
                    "input_bams": "/tmp/out/S1.bam /tmp/out/S6.bam",
                    "output_counts": "/tmp/out/counts.tsv",
                    "threads": 2,
                },
            },
            {
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": "/tmp/out/counts.tsv",
                    "metadata_table": "/tmp/out/meta.tsv",
                    "design_formula": "~ condition",
                    "contrast": "condition_S6_vs_S1",
                    "output_dir": "/tmp/out/de",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["alignment", "reference_inputs", "differential_analysis", "group_comparison"],
        "explicit_tool_hints": ["star", "deseq2", "featurecounts"],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_contract_passes_for_uncommon_capability_signals():
    plan = {
        "plan": [
            {
                "tool_name": "methylation_bismark_style",
                "arguments": {
                    "genome_folder": "/tmp/refs/bismark",
                    "reads_1": "/tmp/S1_R1.fastq.gz",
                    "reads_2": "/tmp/S1_R2.fastq.gz",
                    "output_report": "/tmp/out/methylation.tsv",
                },
            },
            {
                "tool_name": "phylogenetics_iqtree_style",
                "arguments": {
                    "alignment_fasta": "/tmp/aln.fasta",
                    "output_tree": "/tmp/out/tree.nwk",
                    "model": "MFP",
                    "seed": 42,
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["methylation_analysis", "phylogenetics"],
        "explicit_tool_hints": ["bismark", "iqtree"],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_required_tool_hints_block_plan_when_explicit_tool_request_is_missing():
    plan = {
        "plan": [
            {
                "tool_name": "star_align",
                "arguments": {
                    "genome_dir": "/tmp/star_idx",
                    "reads_1": "/tmp/S1_R1.fastq.gz",
                    "reads_2": "/tmp/S1_R2.fastq.gz",
                    "output_prefix": "/tmp/out/S1_",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["alignment"],
        "required_tool_hints": ["subread"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is False
    assert validation["missing_required_tool_hints"] == ["subread"]


def test_required_tool_hints_pass_when_plan_uses_requested_tool():
    plan = {
        "plan": [
            {
                "tool_name": "subread_align",
                "arguments": {
                    "index_base": "/tmp/subread/genome",
                    "reference_fasta": "/tmp/ref.fa",
                    "reads_1": "/tmp/S1_R1.fastq.gz",
                    "reads_2": "/tmp/S1_R2.fastq.gz",
                    "output_bam": "/tmp/out/sample.bam",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["alignment", "reference_inputs"],
        "required_tool_hints": ["subread"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_required_tool_hints"] == []


def test_required_tool_hints_ignore_incidental_output_path_matches():
    plan = {
        "plan": [
            {
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": "/tmp/reference.fa",
                    "output_faa": "/tmp/prokka_annotate/sample1.faa",
                    "output_gff": "/tmp/prokka_annotate/sample1.gff",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": [],
        "required_tool_hints": ["prokka_annotate"],
        "explicit_tool_hints": ["prokka_annotate"],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is False
    assert validation["missing_required_tool_hints"] == ["prokka_annotate"]
    assert validation["missing_tool_hints"] == ["prokka_annotate"]


def test_required_tool_hints_still_accept_bash_command_tool_mentions():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "prokka --outdir /tmp/annot --prefix sample1 /tmp/reference.fa",
                    "output_dir": "/tmp/prodigal_annotate",
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": [],
        "required_tool_hints": ["prokka_annotate"],
        "explicit_tool_hints": ["prokka_annotate"],
    }

    validation = assess_plan_contract(plan, contract)
    assert validation["passed"] is True
    assert validation["missing_required_tool_hints"] == []
    assert validation["missing_tool_hints"] == []


def test_phylogeny_helper_bash_run_satisfies_alignment_phylogenetics_contract():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 /repo/bio_harness/pipeline_scripts/infer_phylogeny_biopython.py "
                        "--input /tmp/sequences.fasta --output /tmp/final/phylogeny.treefile"
                    ),
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["alignment", "phylogenetics", "reference_inputs"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)

    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_viral_helper_bash_run_satisfies_alignment_and_metagenomics_contract():
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 /repo/bio_harness/pipeline_scripts/classify_viral_reads_kmer.py "
                        "--reads-1 /tmp/sample_R1.fastq.gz --reads-2 /tmp/sample_R2.fastq.gz "
                        "--references-dir /refs/viral_refs --report /tmp/output/classification_report.tsv "
                        "--detected /tmp/output/detected_viruses.txt"
                    ),
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["alignment", "metagenomics_profiling", "reference_inputs"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)

    assert validation["passed"] is True
    assert validation["missing_capabilities"] == []


def test_metagenomics_output_path_does_not_satisfy_profiling_contract():
    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/tmp/metagenomics/data/sample_R1.fastq.gz",
                    "reads_2": "/tmp/metagenomics/data/sample_R2.fastq.gz",
                    "output_dir": "/tmp/domain_metagenomics/selected/assembly/metaspades",
                    "meta_mode": True,
                    "threads": 8,
                    "memory_gb": 32,
                },
            },
        ]
    }
    contract = {
        "must_include_capabilities": ["metagenomics_profiling"],
        "explicit_tool_hints": [],
    }

    validation = assess_plan_contract(plan, contract)

    assert validation["passed"] is False
    assert validation["missing_capabilities"] == ["metagenomics_profiling"]
