from __future__ import annotations

from bio_harness.core.direct_wrapper_completeness import (
    assess_direct_wrapper_plan_completeness,
    repair_direct_wrapper_plan_bindings,
)


def test_repair_direct_wrapper_plan_bindings_fills_stringtie_arguments_from_request_and_outputs() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
            "preserve_output_paths": True,
            "locked_argument_values": {
                "stringtie_quant": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                }
            },
        },
    }
    contract = {"required_output_paths": ["/tmp/stringtie"]}
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {},
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "Use stringtie_quant on /tmp/hnrnpc/sample.bam with annotation "
            "/tmp/hnrnpc/genes.gtf and write outputs under /tmp/stringtie."
        ),
        selected_dir="/tmp/selected",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["input_bam"] == "/tmp/hnrnpc/sample.bam"
    assert args["annotation_gtf"] == "/tmp/hnrnpc/genes.gtf"
    assert args["output_gtf"] == "/tmp/stringtie/assembled.gtf"
    assert args["gene_abundance_tsv"] == "/tmp/stringtie/gene_abundances.tsv"

    validation = assess_direct_wrapper_plan_completeness(
        repaired,
        analysis_spec=analysis_spec,
    )
    assert validation["passed"] is True
    assert validation["issues"] == []


def test_repair_direct_wrapper_plan_bindings_restores_stringtie_annotation_role_from_explicit_prompt() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
            "preserve_input_paths": True,
            "preserve_output_paths": True,
        },
    }
    contract = {
        "required_output_paths": [
            "/tmp/stringtie/assembled.gtf",
            "/tmp/stringtie/gene_abundances.tsv",
        ]
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/stringtie/assembled.gtf",
                    "output_gtf": "/tmp/stringtie/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/stringtie/gene_abundances.tsv",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "Use only the stringtie_quant tool on the coordinate-sorted BAM at "
            "/tmp/hnrnpc/sample.bam with the annotation GTF at /tmp/refs/genes.gtf. "
            "Write the assembled transcript GTF to /tmp/stringtie/assembled.gtf "
            "and the gene abundance table to /tmp/stringtie/gene_abundances.tsv."
        ),
        selected_dir="/tmp/selected",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["annotation_gtf"] == "/tmp/refs/genes.gtf"
    assert args["output_gtf"] == "/tmp/stringtie/assembled.gtf"
    assert args["gene_abundance_tsv"] == "/tmp/stringtie/gene_abundances.tsv"


def test_repair_direct_wrapper_plan_bindings_fills_scanpy_input_and_output() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["scanpy_workflow"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["scanpy_workflow"],
        },
    }
    contract = {"required_output_paths": ["/tmp/scanpy_output"]}
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "scanpy_workflow",
                "arguments": {},
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "Use only the scanpy_workflow tool on /tmp/pbmc3k_processed.h5ad "
            "and write outputs under /tmp/scanpy_output."
        ),
        selected_dir="/tmp/selected",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["input_path"] == "/tmp/pbmc3k_processed.h5ad"
    assert args["output_dir"] == "/tmp/scanpy_output"


def test_repair_direct_wrapper_plan_bindings_fills_proteomics_inputs_and_output() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["proteomics_diff_abundance"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["proteomics_diff_abundance"],
        },
    }
    contract = {"required_output_paths": ["/tmp/proteomics_output"]}
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "proteomics_diff_abundance",
                "arguments": {},
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "Use proteomics_diff_abundance on /tmp/abundance_matrix.csv with "
            "/tmp/metadata.csv and write outputs under /tmp/proteomics_output."
        ),
        selected_dir="/tmp/selected",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["abundance_matrix"] == "/tmp/abundance_matrix.csv"
    assert args["metadata_table"] == "/tmp/metadata.csv"
    assert args["output_dir"] == "/tmp/proteomics_output"


def test_repair_direct_wrapper_plan_bindings_fills_metabolomics_inputs_and_output() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["metabolomics_diff_abundance"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["metabolomics_diff_abundance"],
        },
    }
    contract = {"required_output_paths": ["/tmp/metabolomics_output"]}
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "metabolomics_diff_abundance",
                "arguments": {},
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "Use metabolomics_diff_abundance on /tmp/feature_table.csv with "
            "/tmp/metadata.csv and write outputs under /tmp/metabolomics_output."
        ),
        selected_dir="/tmp/selected",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["feature_table"] == "/tmp/feature_table.csv"
    assert args["metadata_table"] == "/tmp/metadata.csv"
    assert args["output_dir"] == "/tmp/metabolomics_output"


def test_repair_direct_wrapper_plan_bindings_overrides_wrong_stringtie_output_paths() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
            "preserve_output_paths": True,
            "locked_argument_values": {
                "stringtie_quant": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                }
            },
        },
    }
    contract = {
        "required_output_paths": [
            "/tmp/stringtie/assembled.gtf",
            "/tmp/stringtie/gene_abundances.tsv",
        ]
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                    "output_gtf": "/tmp/run/stringtie_quant/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/run/gene_abundances.tsv",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "Use stringtie_quant on /tmp/hnrnpc/sample.bam with /tmp/refs/genes.gtf and "
            "write outputs to /tmp/stringtie/assembled.gtf and /tmp/stringtie/gene_abundances.tsv."
        ),
        selected_dir="/tmp/run",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["output_gtf"] == "/tmp/stringtie/assembled.gtf"
    assert args["gene_abundance_tsv"] == "/tmp/stringtie/gene_abundances.tsv"


def test_repair_direct_wrapper_plan_bindings_prefers_requested_root_over_locked_stringtie_root() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
            "preserve_output_paths": True,
            "locked_argument_values": {
                "stringtie_quant": {
                    "output_gtf": "/tmp/stringtie",
                }
            },
        },
    }
    contract = {"required_output_paths": ["/tmp/stringtie"]}
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "Use stringtie_quant on /tmp/hnrnpc/sample.bam with /tmp/refs/genes.gtf "
            "and write outputs under /tmp/stringtie."
        ),
        selected_dir="/tmp/run",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["output_gtf"] == "/tmp/stringtie/assembled.gtf"
    assert args["gene_abundance_tsv"] == "/tmp/stringtie/gene_abundances.tsv"


def test_repair_direct_wrapper_plan_bindings_supports_put_outputs_in_stringtie_directory() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
            "preserve_output_paths": True,
            "locked_argument_values": {
                "stringtie_quant": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                }
            },
        },
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={"required_output_paths": ["/tmp/custom_stringtie/output_set"]},
        request_text=(
            "Use stringtie_quant on /tmp/hnrnpc/sample.bam with /tmp/refs/genes.gtf. "
            "Put outputs in /tmp/custom_stringtie/output_set."
        ),
        selected_dir="/tmp/run",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["output_gtf"] == "/tmp/custom_stringtie/output_set/assembled.gtf"
    assert args["gene_abundance_tsv"] == "/tmp/custom_stringtie/output_set/gene_abundances.tsv"


def test_repair_direct_wrapper_plan_bindings_preserves_selected_dir_localized_outputs() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["stringtie_quant"],
            "preserve_output_paths": True,
            "locked_argument_values": {
                "stringtie_quant": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                }
            },
        },
    }
    contract = {
        "required_output_paths": [
            "/tmp/stringtie/assembled.gtf",
            "/tmp/stringtie/gene_abundances.tsv",
        ]
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/hnrnpc/sample.bam",
                    "annotation_gtf": "/tmp/refs/genes.gtf",
                    "output_gtf": "/tmp/run/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/run/gene_abundances.tsv",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text="Use only the stringtie_quant tool on /tmp/hnrnpc/sample.bam with /tmp/refs/genes.gtf.",
        selected_dir="/tmp/run",
    )

    assert meta["changed"] is False
    args = repaired["plan"][0]["arguments"]
    assert args["output_gtf"] == "/tmp/run/assembled.gtf"
    assert args["gene_abundance_tsv"] == "/tmp/run/gene_abundances.tsv"


def test_repair_direct_wrapper_plan_bindings_infers_scanpy_output_dir_from_file_outputs() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["scanpy_workflow"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["scanpy_workflow"],
            "preserve_output_paths": True,
        },
    }
    contract = {
        "required_output_paths": [
            "/tmp/scanpy_output/processed.h5ad",
            "/tmp/scanpy_output/cluster_assignments.csv",
            "/tmp/scanpy_output/marker_genes.csv",
            "/tmp/scanpy_output/summary.json",
        ]
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "scanpy_workflow",
                "arguments": {
                    "input_path": "/tmp/pbmc3k_processed.h5ad",
                    "output_dir": "/tmp/run/scanpy_workflow",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "scanpy_workflow on /tmp/pbmc3k_processed.h5ad output /tmp/scanpy_output only"
        ),
        selected_dir="/tmp/run",
    )

    assert meta["changed"] is True
    assert repaired["plan"][0]["arguments"]["output_dir"] == "/tmp/scanpy_output"


def test_repair_direct_wrapper_plan_bindings_preserves_explicit_minimap2_sam_output() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["minimap2_align"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["minimap2_align"],
        },
    }
    contract = {"required_output_paths": ["/tmp/aligned/orang_vs_human.sam"]}
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "minimap2_align",
                "arguments": {
                    "reference_fasta": "/tmp/MT-human.fa",
                    "reads": "/tmp/MT-orang.fa",
                    "output_bam": "/tmp/aligned/orang_vs_human.bam",
                    "preset": "asm5",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract=contract,
        request_text=(
            "Align /tmp/MT-orang.fa to the reference at /tmp/MT-human.fa with minimap2_align "
            "and write the alignment to /tmp/aligned/orang_vs_human.sam."
        ),
        selected_dir="/tmp/aligned",
    )

    assert meta["changed"] is True
    assert repaired["plan"][0]["arguments"]["output_bam"] == "/tmp/aligned/orang_vs_human.sam"


def test_repair_direct_wrapper_plan_bindings_fills_minimap2_from_discovered_inputs(
    tmp_path,
) -> None:
    data_root = tmp_path / "data"
    selected_dir = tmp_path / "selected"
    data_root.mkdir()
    selected_dir.mkdir()
    reads = data_root / "reads.fastq"
    reference = data_root / "ref.fasta"
    reads.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    reference.write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["minimap2_align"],
        },
        "discovered_data_files": [
            {"name": "reads.fastq", "path": str(reads)},
            {"name": "ref.fasta", "path": str(reference)},
        ],
    }
    plan = {"plan": [{"step_id": 1, "tool_name": "minimap2_align", "arguments": {}}]}

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text="Align Oxford Nanopore reads to the reference genome.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["reference_fasta"] == str(reference)
    assert args["reads"] == str(reads)
    assert args["output_bam"] == str(selected_dir / "aligned.bam")

    validation = assess_direct_wrapper_plan_completeness(
        repaired,
        analysis_spec=analysis_spec,
    )
    assert validation["passed"] is True


def test_repair_direct_wrapper_plan_bindings_repairs_sniffles_bam_alias(
    tmp_path,
) -> None:
    data_root = tmp_path / "data"
    selected_dir = tmp_path / "selected"
    data_root.mkdir()
    selected_dir.mkdir()
    reference = data_root / "ref.fasta"
    aligned_bam = selected_dir / "aligned.bam"
    reference.write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    aligned_bam.write_text("placeholder bam\n", encoding="utf-8")
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["sniffles_sv_call"],
        },
        "parameter_profile": [
            {
                "tool_name": "sniffles_sv_call",
                "settings": {"min_support": 3, "min_sv_length": 50, "threads": 4},
            }
        ],
    }
    plan = {
        "plan": [
            {
                "step_id": 2,
                "tool_name": "sniffles_sv_call",
                "arguments": {
                    "reference_fasta": str(reference),
                    "reads": str(data_root / "reads.fastq"),
                    "output_bam": "aligned.bam",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text="Call structural variants from the aligned long-read BAM.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["input_bam"] == str(aligned_bam)
    assert "output_bam" not in args
    assert args["output_vcf"] == str(selected_dir / "variants.vcf")
    assert args["min_support"] == 3
    assert args["min_sv_length"] == 50
    assert args["threads"] == 4

    validation = assess_direct_wrapper_plan_completeness(
        repaired,
        analysis_spec=analysis_spec,
    )
    assert validation["passed"] is True


def test_repair_direct_wrapper_plan_bindings_fills_flye_inputs_and_profile(
    tmp_path,
) -> None:
    data_root = tmp_path / "data"
    selected_dir = tmp_path / "selected"
    data_root.mkdir()
    selected_dir.mkdir()
    reads = data_root / "reads.fastq"
    reads.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["flye_assemble"],
        },
        "discovered_data_files": [{"name": "reads.fastq", "path": str(reads)}],
        "parameter_profile": [
            {
                "tool_name": "flye_assemble",
                "settings": {"genome_size": "5m", "read_mode": "nano-raw", "threads": 4},
            }
        ],
    }
    plan = {"plan": [{"step_id": 1, "tool_name": "flye_assemble", "arguments": {}}]}

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text="Assemble these Oxford Nanopore reads.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["reads_fastq"] == str(reads)
    assert args["genome_size"] == "5m"
    assert args["threads"] == 4
    assert args["read_mode"] == "nano-raw"
    assert args["output_dir"] == str(selected_dir)

    validation = assess_direct_wrapper_plan_completeness(
        repaired,
        analysis_spec=analysis_spec,
    )
    assert validation["passed"] is True


def test_repair_direct_wrapper_plan_bindings_fills_spatial_input_and_profile(
    tmp_path,
) -> None:
    data_root = tmp_path / "data"
    selected_dir = tmp_path / "selected"
    data_root.mkdir()
    selected_dir.mkdir()
    input_path = data_root / "visium_data.h5ad"
    input_path.write_text("placeholder", encoding="utf-8")
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["spatial_transcriptomics_workflow"],
        },
        "discovered_data_files": [{"name": "visium_data.h5ad", "path": str(input_path)}],
        "parameter_profile": [
            {
                "tool_name": "spatial_transcriptomics_workflow",
                "settings": {"min_cells": 2, "min_genes": 3, "n_hvgs": 50, "n_pcs": 10},
            }
        ],
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spatial_transcriptomics_workflow",
                "arguments": {},
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text="Analyze this Visium spatial transcriptomics dataset.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["input_path"] == str(input_path)
    assert args["output_dir"] == str(selected_dir)
    assert args["min_cells"] == 2
    assert args["n_hvgs"] == 50

    validation = assess_direct_wrapper_plan_completeness(
        repaired,
        analysis_spec=analysis_spec,
    )
    assert validation["passed"] is True


def test_repair_direct_wrapper_plan_bindings_uses_unique_discovered_vcf_for_snpeff() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["snpeff_annotate"],
        },
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "GRCh37.75",
                    "input_vcf": "/tmp/run/ex1.eff.vcf",
                    "output_vcf": "/tmp/run/ex1.snpeff_annotated.vcf",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text="Annotate the supplied family VCF with SnpEff and keep outputs in the run directory.",
        selected_dir="/tmp/run",
        data_root="<BIO_HARNESS_ROOT>/workspace/benchmarks/bioagent-bench/tasks/cystic-fibrosis/data",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["input_vcf"] == (
        "<BIO_HARNESS_ROOT>/workspace/benchmarks/"
        "bioagent-bench/tasks/cystic-fibrosis/data/ex1.eff.vcf"
    )


def test_repair_direct_wrapper_plan_bindings_adds_local_snpeff_references(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    input_vcf = data_root / "input_variants.vcf"
    reference_fasta = data_root / "reference.fa"
    annotation_gff = data_root / "genes.gff"
    input_vcf.write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    reference_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    annotation_gff.write_text("##gff-version 3\n", encoding="utf-8")
    selected_dir = tmp_path / "selected"

    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["snpeff_annotate"],
        },
        "discovered_data_files": [
            {"name": "input_variants.vcf", "path": str(input_vcf)},
            {"name": "reference.fa", "path": str(reference_fasta)},
            {"name": "genes.gff", "path": str(annotation_gff)},
        ],
        "file_manifest": {
            "entries": [
                {
                    "file_type": "vcf",
                    "resolved_path": str(input_vcf),
                    "role": "input_vcf",
                },
                {
                    "file_type": "fasta",
                    "resolved_path": str(reference_fasta),
                    "role": "reference_genome",
                },
                {
                    "file_type": "gff",
                    "resolved_path": str(annotation_gff),
                    "role": "annotation_gff",
                },
            ],
            "output_dir": str(selected_dir),
        },
        "requested_data_root": str(data_root),
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "custom_ref",
                    "input_vcf": str(input_vcf),
                    "output_vcf": str(selected_dir / "output" / "annotated.vcf"),
                    "config_dir": str(selected_dir / "output" / "snpeff_custom_db"),
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text="Annotate the provided VCF with the local reference and GFF.",
        selected_dir=str(selected_dir),
        data_root=str(data_root),
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["reference_fasta"] == str(reference_fasta)
    assert args["annotation_gff"] == str(annotation_gff)


def test_repair_direct_wrapper_plan_bindings_keeps_ambiguous_discovered_vcfs_unbound(
    tmp_path,
) -> None:
    first_vcf = tmp_path / "a.vcf"
    second_vcf = tmp_path / "b.vcf.gz"
    first_vcf.write_text("##fileformat=VCFv4.2\n")
    second_vcf.write_text("##fileformat=VCFv4.2\n")
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["snpeff_annotate"],
        },
        "discovered_data_files": [
            {"name": "a.vcf", "path": str(first_vcf)},
            {"name": "b.vcf.gz", "path": str(second_vcf)},
        ],
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "GRCh37.75",
                    "output_vcf": "/tmp/run/ex1.snpeff_annotated.vcf",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text="Annotate the VCF with SnpEff.",
        selected_dir="/tmp/run",
    )

    assert meta["changed"] is False
    assert "input_vcf" not in repaired["plan"][0]["arguments"]


def test_assess_direct_wrapper_plan_completeness_reports_unbindable_missing_arguments() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["stringtie_quant"],
        }
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {"output_gtf": "/tmp/stringtie/assembled.gtf"},
            }
        ]
    }

    validation = assess_direct_wrapper_plan_completeness(
        plan,
        analysis_spec=analysis_spec,
    )

    assert validation["passed"] is False
    assert validation["issues"] == [
        "incomplete_direct_wrapper:stringtie_quant:annotation_gtf,input_bam"
    ]


def test_repair_direct_wrapper_plan_bindings_uses_locked_deseq_output_dir() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["deseq2_run"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["deseq2_run"],
            "locked_argument_values": {
                "deseq2_run": {
                    "output_dir": "/tmp/deseq_out",
                }
            },
        },
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": "/tmp/airway_counts.tsv",
                    "metadata_table": "/tmp/airway_metadata.tsv",
                    "design_formula": "~ dex",
                    "contrast": "dex_trt_vs_untrt",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text="Use deseq2_run directly on the provided counts and metadata.",
        selected_dir="/tmp/selected",
    )

    assert meta["changed"] is True
    assert repaired["plan"][0]["arguments"]["output_dir"] == "/tmp/deseq_out"


def test_repair_direct_wrapper_plan_bindings_restores_explicit_deseq_inputs_and_outputs() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["deseq2_run"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["deseq2_run"],
            "preserve_input_paths": True,
            "preserve_output_paths": True,
            "locked_argument_values": {
                "deseq2_run": {
                    "output_dir": "/tmp/reports/work",
                }
            },
        },
    }
    corrupted_counts = "/repo//repo/workspace/run/workspace/non_bioagent_real_data/airway/airway_counts.tsv"
    corrupted_metadata = "/repo//repo/workspace/run/workspace/non_bioagent_real_data/airway/airway_metadata_dex.tsv"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": corrupted_counts,
                    "metadata_table": corrupted_metadata,
                    "design_formula": "~ dex",
                    "contrast": "dex",
                    "output_dir": "work",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={"required_output_paths": ["/tmp/reports/work"]},
        request_text=(
            "Run deseq2_run directly on /tmp/airway/airway_counts.tsv with "
            "/tmp/airway/airway_metadata_dex.tsv and keep outputs under "
            "/tmp/reports/work."
        ),
        selected_dir="/tmp/selected",
    )

    assert meta["changed"] is True
    args = repaired["plan"][0]["arguments"]
    assert args["counts_matrix"] == "/tmp/airway/airway_counts.tsv"
    assert args["metadata_table"] == "/tmp/airway/airway_metadata_dex.tsv"
    assert args["output_dir"] == "/tmp/reports/work"


def test_repair_direct_wrapper_plan_bindings_drops_missing_sc_whitelist_for_local_inference() -> None:
    analysis_spec = {
        "execution_contract": {
            "execution_mode": "direct_wrapper",
            "compatible_tools": ["sc_count_and_cluster"],
        },
        "explicit_execution_intent": {
            "locked_tools": ["sc_count_and_cluster"],
        },
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "sc_count_and_cluster",
                "arguments": {
                    "r1": "/tmp/sample_R1.fastq.gz",
                    "r2": "/tmp/sample_R2.fastq.gz",
                    "whitelist": "/tmp/10x_v3_whitelist.txt",
                    "reference": "/tmp/genome.fa",
                    "gtf": "/tmp/genes.gtf",
                    "output_dir": "/tmp/sc_raw",
                },
            }
        ]
    }

    repaired, meta = repair_direct_wrapper_plan_bindings(
        plan,
        analysis_spec=analysis_spec,
        contract={},
        request_text=(
            "Perform single-cell analysis from raw 10X FASTQs at /tmp/sample_R1.fastq.gz "
            "and /tmp/sample_R2.fastq.gz using the genome at /tmp/genome.fa and annotation "
            "/tmp/genes.gtf. Use sc_count_and_cluster and write results to /tmp/sc_raw/."
        ),
        selected_dir="/tmp/sc_raw",
    )

    assert meta["changed"] is True
    assert "whitelist" not in repaired["plan"][0]["arguments"]

    validation = assess_direct_wrapper_plan_completeness(
        repaired,
        analysis_spec=analysis_spec,
    )
    assert validation["passed"] is True
