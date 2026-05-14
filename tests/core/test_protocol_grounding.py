from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_harness.agents.orchestrator import Orchestrator
from bio_harness.core.benchmark_policy import (
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    OFFICIAL_BIOAGENTBENCH_POLICY,
    SCIENTIFIC_HARNESS_POLICY,
)
from bio_harness.core.analysis_spec import (
    build_analysis_brief,
    deterministic_analysis_spec,
    normalize_analysis_spec,
)
from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.protocol_grounding import (
    _compile_rna_seq_de_plan,
    analysis_patch_from_protocol,
    assess_protocol_grounding,
    discover_protocol_files,
    deterministic_protocol_repair,
    extract_protocol_grounding,
)
from bio_harness.core.strict_artifact_binding import bind_step_spec_for_strict_mode
from bio_harness.harness.config import SKILLS_DEFINITIONS, SKILLS_LIBRARY


def test_extract_protocol_grounding_from_local_recipe(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "evolution" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "evolution" / "data"
    recipe_root = project_root / "external" / "bioagent-bench" / "tasks" / "evolution"
    results_root = data_root.parent / "results"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    recipe_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)

    (recipe_root / "run_script.sh").write_text(
        "\n".join(
            [
                "spades.py --careful -o ./outputs/assembly",
                "freebayes -p 1 -f ./outputs/assembly/scaffolds.fasta ./outputs/mappings/evol1.bam > evol1.vcf",
                "vcffilter -f \"QUAL > 1\" evol1.vcf > evol1.filtered.vcf",
                "snpEff -c ./outputs/voi/snpEff.config mygenome evol1.filtered.vcf > evol1.anno.vcf",
            ]
        ),
        encoding="utf-8",
    )
    (results_root / "variants_shared.csv").write_text(
        "CHROM,POS,REF,ALT,GENE,IMPACT,EFFECT,STATUS\n",
        encoding="utf-8",
    )

    grounding = extract_protocol_grounding(
        user_query="Identify and annotate shared variants in evolved E. coli lines.",
        analysis_type="bacterial_evolution_variant_calling",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=[
            "spades_assemble",
            "freebayes_call",
            "snpeff_annotate",
            "prodigal_annotate",
            "shared_variants_export_run",
        ],
    )

    assert grounding["grounded"] is True
    assert "freebayes_call" in grounding["required_tools"]
    assert "snpeff_annotate" in grounding["required_tools"]
    assert "shared_variants_export_run" in grounding["required_tools"]
    assert "vcffilter" in grounding["required_plan_signals"]
    assert "shared_variants_export_run" in grounding["required_plan_signals"]
    assert "spades_assemble" in grounding["preferred_tools"]
    assert grounding["output_columns"] == ["CHROM", "POS", "REF", "ALT", "GENE", "IMPACT", "EFFECT", "STATUS"]
    assert grounding["requires_shared_comparison"] is True
    assert grounding["min_variant_branches"] == 2
    assert grounding["analytical_method"] == "freebayes_call"
    assert grounding["benchmark_profile"]["profile_id"] == "bioagent_bench_evolution_shared_v1"
    freebayes_hint = next(item for item in grounding["parameter_profile"] if item["tool_name"] == "freebayes_call")
    assert freebayes_hint["settings"]["ploidy"] == 1


def test_extract_protocol_grounding_direct_skill_smoke_ignores_repo_readme(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "skill_smoke" / "case" / "rmats_run"
    data_root = project_root / "workspace" / "inputs_readonly"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (project_root / "README.md").write_text(
        "Use prokka for bacterial annotation.\n",
        encoding="utf-8",
    )

    grounding = extract_protocol_grounding(
        user_query=(
            "This is a direct one-step skill smoke test. Use only the rmats_run tool "
            "to compare the grouped BAMs."
        ),
        analysis_type="direct_skill_smoke",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["rmats_run", "prokka_annotate"],
    )

    assert grounding == {}


def test_assess_protocol_grounding_detects_missing_required_signal():
    analysis_spec = {
        "protocol_grounding": {
            "required_tools": ["freebayes_call", "snpeff_annotate"],
            "required_plan_signals": ["vcffilter"],
            "source_files": ["/tmp/run_script.sh"],
        }
    }
    plan = {
        "plan": [
            {"tool_name": "freebayes_call", "arguments": {}, "step_id": 1},
            {"tool_name": "snpeff_annotate", "arguments": {}, "step_id": 2},
            {"tool_name": "bash_run", "arguments": {"command": "python export_shared_variants_csv.py"}, "step_id": 3},
        ]
    }

    validation = assess_protocol_grounding(plan, analysis_spec)

    assert validation["passed"] is False
    assert validation["missing_plan_signals"] == ["vcffilter"]
    assert validation["missing_required_tools"] == []


def test_analysis_patch_from_protocol_preserves_fallback_when_grounding_is_sparse():
    patch = analysis_patch_from_protocol(
        {
            "grounded": True,
            "required_tools": ["sc_count_and_cluster"],
            "required_plan_signals": ["sc_count_and_cluster"],
            "source_files": [],
            "parameter_profile": [],
            "notes": [],
        },
        available_skill_names=["sc_count_and_cluster", "scanpy_workflow"],
    )

    assert patch["protocol_grounding"]["grounded"] is True
    assert "parameter_profile" not in patch
    assert "acceptance_checks" not in patch
    assert "source_provenance" not in patch


def test_analysis_patch_from_protocol_preserves_seeded_preferences_and_profiles():
    patch = analysis_patch_from_protocol(
        {
            "grounded": True,
            "required_tools": ["spades_assemble", "freebayes_call", "snpeff_annotate"],
            "preferred_tools": ["spades_assemble", "freebayes_call", "snpeff_annotate", "prokka_annotate"],
            "required_plan_signals": ["spades", "freebayes", "snpeff"],
            "parameter_profile": [
                {"tool_name": "spades_assemble", "settings": {"careful": True}},
                {"tool_name": "freebayes_call", "settings": {"ploidy": 1}},
            ],
            "source_files": [],
            "notes": [],
        },
        available_skill_names=[
            "spades_assemble",
            "freebayes_call",
            "snpeff_annotate",
            "prokka_annotate",
            "bcftools_filter_run",
            "bcftools_isec_run",
            "bcftools_norm_run",
            "shared_variants_export_run",
        ],
        analysis_spec={
            "preferred_tools": [
                "spades_assemble",
                "freebayes_call",
                "bcftools_filter_run",
                "bcftools_isec_run",
                "bcftools_norm_run",
                "shared_variants_export_run",
            ],
            "parameter_profile": [
                {"tool_name": "bcftools_filter_run", "settings": {"output_type": "z"}},
                {"tool_name": "bcftools_isec_run", "settings": {"mode": "complement"}},
                {"tool_name": "shared_variants_export_run", "settings": {"min_impact": "MODERATE"}},
            ],
        },
    )

    assert "bcftools_filter_run" in patch["preferred_tools"]
    assert "bcftools_isec_run" in patch["preferred_tools"]
    assert "bcftools_norm_run" in patch["preferred_tools"]
    assert "shared_variants_export_run" in patch["preferred_tools"]
    assert any(entry["tool_name"] == "bcftools_filter_run" for entry in patch["parameter_profile"])
    assert any(entry["tool_name"] == "bcftools_isec_run" for entry in patch["parameter_profile"])
    assert any(entry["tool_name"] == "shared_variants_export_run" for entry in patch["parameter_profile"])


def test_assess_protocol_grounding_accepts_atomic_filter_wrapper_for_vcffilter_signal():
    analysis_spec = {
        "protocol_grounding": {
            "required_tools": ["freebayes_call", "snpeff_annotate"],
            "required_plan_signals": ["vcffilter"],
            "source_files": ["/tmp/run_script.sh"],
        }
    }
    plan = {
        "plan": [
            {"tool_name": "freebayes_call", "arguments": {}, "step_id": 1},
            {
                "tool_name": "bcftools_filter_run",
                "arguments": {
                    "input_vcf": "/tmp/sample_raw.vcf.gz",
                    "output_vcf": "/tmp/sample_filtered.vcf.gz",
                    "filter_expression": "QUAL > 1",
                },
                "step_id": 2,
            },
            {"tool_name": "snpeff_annotate", "arguments": {}, "step_id": 3},
        ]
    }

    validation = assess_protocol_grounding(plan, analysis_spec)

    assert validation["passed"] is True
    assert validation["missing_plan_signals"] == []


def test_extract_protocol_grounding_uses_explicit_scanpy_wrapper_seed(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "runs" / "scanpy_case"
    data_root = project_root / "workspace" / "inputs"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query=(
            "Use only the scanpy_workflow tool on /tmp/pbmc3k.h5ad and write outputs "
            "under /tmp/scanpy_out."
        ),
        analysis_type="single_cell_rna_seq",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["scanpy_workflow", "sc_count_and_cluster", "bash_run"],
    )

    assert grounding["required_tools"] == ["scanpy_workflow"]
    assert grounding["required_plan_signals"] == ["scanpy_workflow"]
    assert grounding["execution_mode"] == "direct_wrapper"
    assert grounding["compatible_tools"] == ["scanpy_workflow"]


def test_extract_protocol_grounding_uses_explicit_deseq_wrapper_seed(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "runs" / "deseq_case"
    data_root = project_root / "workspace" / "inputs"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query=(
            "Use only the deseq2_run tool on the counts matrix /tmp/airway_counts.tsv "
            "with metadata /tmp/airway_metadata.tsv and write final CSV under /tmp/final."
        ),
        analysis_type="rna_seq_differential_expression",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["deseq2_run", "edger_run", "limma_voom_run", "star_align", "featurecounts_run"],
    )

    assert grounding["required_tools"] == ["deseq2_run"]
    assert grounding["required_plan_signals"] == ["deseq2_run"]
    assert grounding["execution_mode"] == "direct_wrapper"
    assert grounding["compatible_tools"] == ["deseq2_run"]
    assert "star_align" not in grounding["required_plan_signals"]
    assert "featurecounts_run" not in grounding["required_plan_signals"]


def test_extract_protocol_grounding_uses_explicit_edger_wrapper_seed(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "runs" / "edger_case"
    data_root = project_root / "workspace" / "inputs"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    user_query = (
        "Run edgeR and not DESeq2 on the counts matrix /tmp/airway_counts.tsv "
        "with metadata /tmp/airway_metadata.tsv."
    )
    contract = {
        "must_include_capabilities": ["differential_analysis"],
        "required_tool_hints": ["edger_run"],
        "blocked_tool_hints": ["deseq2_run"],
    }
    analysis_spec = deterministic_analysis_spec(
        user_query,
        contract=contract,
        available_skill_names=["deseq2_run", "edger_run", "limma_voom_run", "star_align", "featurecounts_run"],
    )

    grounding = extract_protocol_grounding(
        user_query=user_query,
        analysis_type="rna_seq_differential_expression",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["deseq2_run", "edger_run", "limma_voom_run", "star_align", "featurecounts_run"],
        contract=contract,
        analysis_spec=analysis_spec,
    )

    assert grounding["required_tools"] == ["edger_run"]
    assert grounding["required_plan_signals"] == ["edger_run"]
    assert grounding["execution_mode"] == "direct_wrapper"
    assert grounding["compatible_tools"] == ["edger_run"]
    assert "deseq2_run" not in grounding["required_plan_signals"]
    assert "star_align" not in grounding["required_plan_signals"]


def test_extract_protocol_grounding_uses_analysis_spec_execution_contract_for_count_matrix_de(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "runs" / "count_matrix_de"
    data_root = project_root / "workspace" / "inputs"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    analysis_spec = deterministic_analysis_spec(
        (
            "Run differential expression on the counts matrix /tmp/airway_counts.tsv "
            "with metadata /tmp/airway_metadata.tsv and design ~ dex."
        ),
        available_skill_names=["deseq2_run", "edger_run", "limma_voom_run", "star_align", "featurecounts_run"],
    )

    grounding = extract_protocol_grounding(
        user_query=(
            "Run differential expression on the counts matrix /tmp/airway_counts.tsv "
            "with metadata /tmp/airway_metadata.tsv and design ~ dex."
        ),
        analysis_type="rna_seq_differential_expression",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["deseq2_run", "edger_run", "limma_voom_run", "star_align", "featurecounts_run"],
        analysis_spec=analysis_spec,
    )

    assert grounding["execution_mode"] == "direct_wrapper"
    assert grounding["required_tools"] == [analysis_spec["chosen_method"]]
    assert grounding["required_plan_signals"] == [analysis_spec["chosen_method"]]
    assert "star_align" not in grounding["required_plan_signals"]
    assert "featurecounts_run" not in grounding["required_plan_signals"]


def test_analysis_patch_from_protocol_keeps_locked_explicit_tool_choice() -> None:
    base_spec = deterministic_analysis_spec(
        "Use only the scanpy_workflow tool on /tmp/pbmc3k.h5ad and write outputs under /tmp/scanpy_out.",
        available_skill_names=["scanpy_workflow", "sc_count_and_cluster", "bash_run"],
    )

    patch = analysis_patch_from_protocol(
        {
            "grounded": True,
            "required_tools": ["sc_count_and_cluster"],
            "required_plan_signals": ["sc_count_and_cluster"],
            "source_files": [],
        },
        available_skill_names=["scanpy_workflow", "sc_count_and_cluster", "bash_run"],
        analysis_spec=base_spec,
    )

    assert patch["chosen_method"] == "scanpy_workflow"
    assert patch["preferred_tools"][0] == "scanpy_workflow"


def test_deterministic_protocol_repair_preserves_locked_scanpy_plan(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    input_h5ad = data_root / "pbmc3k_processed.h5ad"
    input_h5ad.write_text("h5ad", encoding="utf-8")

    user_query = (
        f"Use only the scanpy_workflow tool on {input_h5ad} and write outputs under "
        f"{selected_dir / 'scanpy_output'} using min_genes 3, min_cells 1, max_mito_pct 100, "
        "n_hvgs 48, and leiden_resolution 0.3."
    )
    analysis_spec = deterministic_analysis_spec(
        user_query,
        available_skill_names=["scanpy_workflow", "sc_count_and_cluster", "bash_run"],
    )
    plan = {
        "thought_process": "direct wrapper request",
        "plan": [
            {
                "step_id": 1,
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
            }
        ],
    }

    repaired, meta = deterministic_protocol_repair(
        plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert repaired["plan"][0]["tool_name"] == "scanpy_workflow"
    assert repaired["plan"][0]["arguments"] == plan["plan"][0]["arguments"]
    assert meta["changed"] is False


def test_orchestrator_built_analysis_spec_preserves_explicit_scanpy_plan(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    project_root = tmp_path / "project"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)
    input_h5ad = data_root / "pbmc3k_processed.h5ad"
    input_h5ad.write_text("h5ad", encoding="utf-8")

    user_query = (
        f"Use only the scanpy_workflow tool on the processed AnnData file at {input_h5ad}. "
        f"Write outputs under {selected_dir / 'scanpy_output'} using min_genes 3, min_cells 1, "
        "max_mito_pct 100, n_hvgs 48, and leiden_resolution 0.3. "
        "Do not add FASTQ processing, count matrix generation, or bash_run."
    )
    plan = {
        "thought_process": "direct wrapper request",
        "plan": [
            {
                "step_id": 1,
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
            }
        ],
    }
    orchestrator = Orchestrator(SKILLS_DEFINITIONS, SKILLS_LIBRARY)
    analysis_spec = orchestrator.build_analysis_spec(
        user_query,
        contract={
            "explicit_tool_hints": ["scanpy_workflow"],
            "must_include_capabilities": ["single_cell_analysis"],
            "required_tool_hints": [],
            "required_output_paths": [],
            "blocked_tool_hints": [],
        },
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        project_root=str(project_root),
        benchmark_policy=SCIENTIFIC_HARNESS_POLICY,
    )

    repaired, meta = deterministic_protocol_repair(
        plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert analysis_spec["chosen_method"] == "scanpy_workflow"
    assert analysis_spec["explicit_execution_intent"]["locked_tools"] == ["scanpy_workflow"]
    assert repaired["plan"][0]["arguments"] == plan["plan"][0]["arguments"]
    assert meta["changed"] is False


def test_deterministic_protocol_repair_still_replaces_wrong_single_cell_tool(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    input_h5ad = data_root / "pbmc3k_processed.h5ad"
    input_h5ad.write_text("h5ad", encoding="utf-8")

    analysis_spec = deterministic_analysis_spec(
        (
            f"Use only the scanpy_workflow tool on {input_h5ad} and write outputs under "
            f"{selected_dir / 'scanpy_output'}."
        ),
        available_skill_names=["scanpy_workflow", "sc_count_and_cluster", "bash_run"],
    )
    wrong_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {"command": "echo wrong"},
            }
        ]
    }

    repaired, meta = deterministic_protocol_repair(
        wrong_plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert repaired["plan"][0]["tool_name"] == "scanpy_workflow"
    assert repaired["plan"][0]["arguments"]["output_dir"] == str(selected_dir / "scanpy_output")
    assert meta["changed"] is True


def test_deterministic_protocol_repair_normalizes_stringtie_output_basenames(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    input_bam = data_root / "ERR127302_chr14.bam"
    annotation_gtf = data_root / "genes.gtf"
    input_bam.write_text("bam", encoding="utf-8")
    annotation_gtf.write_text("gtf", encoding="utf-8")

    analysis_spec = deterministic_analysis_spec(
        (
            f"Do not realign anything. Use the existing aligned BAM at {input_bam} with the "
            f"external GTF at {annotation_gtf}, keep the workflow on stringtie_quant, and "
            f"write outputs under {selected_dir / 'stringtie'}."
        ),
        available_skill_names=["stringtie_quant", "salmon_quant", "kallisto_quant"],
    )
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": str(input_bam),
                    "annotation_gtf": str(annotation_gtf),
                    "output_gtf": str(selected_dir / "stringtie" / "quantified.gtf"),
                    "gene_abundance_tsv": str(selected_dir / "stringtie" / "gene_abundance.tsv"),
                },
            }
        ]
    }

    repaired, meta = deterministic_protocol_repair(
        plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    args = repaired["plan"][0]["arguments"]
    assert args["output_gtf"] == str(selected_dir / "stringtie" / "assembled.gtf")
    assert args["gene_abundance_tsv"] == str(selected_dir / "stringtie" / "gene_abundances.tsv")
    assert any(
        row.get("why") == "canonical_output_filenames"
        for row in meta["repairs"]
    )


def test_deterministic_protocol_repair_preserves_locked_stringtie_output_filenames(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    input_bam = data_root / "ERR127302_chr14.bam"
    annotation_gtf = data_root / "genes.gtf"
    input_bam.write_text("bam", encoding="utf-8")
    annotation_gtf.write_text("gtf", encoding="utf-8")

    analysis_spec = deterministic_analysis_spec(
        (
            f"Run stringtie_quant on {input_bam} with annotation {annotation_gtf}. "
            "Do not realign anything."
        ),
        available_skill_names=["stringtie_quant", "salmon_quant", "kallisto_quant"],
    )
    analysis_spec["explicit_execution_intent"] = {
        "locked_tools": ["stringtie_quant"],
        "locked_argument_values": {
            "stringtie_quant": {
                "output_gtf": str(selected_dir / "stringtie" / "custom_transcripts.gtf"),
                "gene_abundance_tsv": str(selected_dir / "stringtie" / "custom_gene_table.tsv"),
            }
        },
    }
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": str(input_bam),
                    "annotation_gtf": str(annotation_gtf),
                    "output_gtf": str(selected_dir / "stringtie" / "custom_transcripts.gtf"),
                    "gene_abundance_tsv": str(selected_dir / "stringtie" / "custom_gene_table.tsv"),
                },
            }
        ]
    }

    repaired, meta = deterministic_protocol_repair(
        plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    repaired_args = repaired["plan"][0]["arguments"]
    assert repaired_args["output_gtf"] == plan["plan"][0]["arguments"]["output_gtf"]
    assert repaired_args["gene_abundance_tsv"] == plan["plan"][0]["arguments"]["gene_abundance_tsv"]
    assert all(row.get("why") != "canonical_output_filenames" for row in meta["repairs"])


def test_assess_protocol_grounding_accepts_variant_filter_for_variant_annotation():
    analysis_spec = {
        "protocol_grounding": {
            "task_name": "variant_annotation",
            "required_tools": ["snpeff_annotate"],
            "required_plan_signals": ["snpeff_annotate", "snpsift"],
            "source_files": [],
        }
    }
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/tmp/input.vcf",
                    "output_vcf": "/tmp/output/annotated.vcf",
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "bcftools filter -i 'INFO/ANN[*].IMPACT = \"HIGH\"' /tmp/output/annotated.vcf > /tmp/output/filtered_pathogenic.vcf",
                },
                "step_id": 2,
            },
        ]
    }

    validation = assess_protocol_grounding(plan, analysis_spec)

    assert validation["passed"] is True
    assert validation["missing_plan_signals"] == []


def test_assess_protocol_grounding_detects_bad_shared_variant_plan_shape():
    analysis_spec = {
        "chosen_method": "freebayes_call",
        "protocol_grounding": {
            "required_tools": ["freebayes_call", "snpeff_annotate"],
            "required_plan_signals": ["vcffilter"],
            "source_files": ["/tmp/run_script.sh"],
            "min_variant_branches": 2,
        },
    }
    plan = {
        "plan": [
            {"tool_name": "freebayes_call", "arguments": {"output_vcf": "raw.vcf"}, "step_id": 1},
            {
                "tool_name": "bcftools_call",
                "arguments": {
                    "input_bam": "sample.bam",
                    "output_vcf_gz": "filtered_variants.vcf.gz",
                },
                "step_id": 2,
            },
            {"tool_name": "snpeff_annotate", "arguments": {"input_vcf": "filtered_variants.vcf.gz"}, "step_id": 3},
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "python3 << 'EOF'\n"
                        "import csv\n"
                        "import vcf\n"
                        "with open('variants.vcf') as handle:\n"
                        "    reader = vcf.Reader(handle)\n"
                        "EOF"
                    )
                },
                "step_id": 4,
            },
        ]
    }

    validation = assess_protocol_grounding(plan, analysis_spec)

    assert validation["passed"] is False
    issue_names = {row["issue"] for row in validation["issues"]}
    assert "insufficient_comparison_branches" in issue_names
    assert "secondary_variant_caller_in_filter_stage" in issue_names
    assert "brittle_structured_variant_export" in issue_names


def test_analysis_brief_includes_protocol_grounding_lines():
    grounding = {
        "grounded": True,
        "task_name": "evolution",
        "source_files": ["/tmp/run_script.sh"],
        "required_tools": ["freebayes_call", "snpeff_annotate"],
        "required_plan_signals": ["vcffilter"],
        "binding_rules": ["Include an explicit post-caller variant filtering step before annotation/export."],
        "output_columns": ["CHROM", "POS", "REF", "ALT", "GENE", "IMPACT", "EFFECT", "STATUS"],
        "analytical_method": "freebayes_call",
    }
    patch = analysis_patch_from_protocol(grounding, available_skill_names=["freebayes_call", "snpeff_annotate"])
    spec = normalize_analysis_spec(
        {
            "analysis_type": "bacterial_evolution_variant_calling",
            "biological_objective": "test",
            **patch,
        },
        user_query="test",
        contract={"must_include_capabilities": ["variant_calling"]},
        available_skill_names=["freebayes_call", "snpeff_annotate"],
    )

    brief = build_analysis_brief(spec)

    assert "protocol_task=evolution" in brief
    assert "protocol_required_tools=freebayes_call, snpeff_annotate" in brief
    assert "protocol_required_signals=vcffilter" in brief
    assert "protocol_analytical_method=freebayes_call" in brief


def test_assess_protocol_grounding_allows_cystic_fibrosis_strict_scaffold() -> None:
    selected_dir = Path("/tmp/official_runs/cystic-fibrosis/attempt1")
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        "selected_dir": str(selected_dir),
        "biological_objective": "Identify the causal recessive CFTR variant in affected siblings.",
        "protocol_grounding": {
            "required_tools": ["snpeff_annotate"],
            "required_plan_signals": [],
            "source_files": [],
        },
    }
    filter_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "step_id": 2,
            "arguments": {"command": "python3 old_filter.py"},
        },
        workflow_step={"tool_name": "bash_run", "objective": ""},
        analysis_spec=analysis_spec,
    )

    plan = {
        "plan": [
            {"tool_name": "snpeff_annotate", "arguments": {"input_vcf": "ex1.eff.vcf"}, "step_id": 1},
            filter_step,
        ]
    }

    validation = assess_protocol_grounding(plan, analysis_spec)

    assert validation["passed"] is True
    assert validation["issues"] == []


def test_discover_protocol_files_scientific_mode_includes_external_recipe_and_results(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "evolution" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "evolution" / "data"
    recipe_root = project_root / "external" / "bioagent-bench" / "tasks" / "evolution"
    results_root = data_root.parent / "results"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    recipe_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)
    (recipe_root / "run_script.sh").write_text("freebayes -p 1\n", encoding="utf-8")
    (results_root / "variants_shared.csv").write_text("CHROM,POS\n", encoding="utf-8")

    discovered = discover_protocol_files(
        user_query="Identify shared variants.",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        benchmark_policy=SCIENTIFIC_HARNESS_POLICY,
    )

    discovered_paths = [str(path) for path in discovered]
    assert any("external/bioagent-bench/tasks/evolution/run_script.sh" in path for path in discovered_paths)
    assert any("tasks/evolution/results/variants_shared.csv" in path for path in discovered_paths)


def test_discover_protocol_files_local_scope_blocks_external_recipe_and_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "mini" / "runs" / "evolution" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "mini" / "tasks" / "evolution" / "data"
    recipe_root = project_root / "external" / "bioagent-bench" / "tasks" / "evolution"
    results_root = data_root.parent / "results"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    recipe_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)
    (data_root.parent / "run_script.sh").write_text("freebayes -p 1\n", encoding="utf-8")
    (recipe_root / "run_script.sh").write_text("forbidden recipe\n", encoding="utf-8")
    (results_root / "variants_shared.csv").write_text("CHROM,POS\n", encoding="utf-8")
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_GROUNDING_SCOPE", "local")

    discovered = discover_protocol_files(
        user_query="Identify shared variants.",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        benchmark_policy=SCIENTIFIC_HARNESS_POLICY,
    )

    discovered_paths = [str(path) for path in discovered]
    assert any("tasks/evolution/run_script.sh" in path for path in discovered_paths)
    assert not any("external/bioagent-bench" in path for path in discovered_paths)
    assert not any("tasks/evolution/results/variants_shared.csv" in path for path in discovered_paths)


def test_discover_protocol_files_official_mode_blocks_external_recipe_and_results(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "evolution" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "evolution" / "data"
    recipe_root = project_root / "external" / "bioagent-bench" / "tasks" / "evolution"
    results_root = data_root.parent / "results"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    recipe_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)
    (recipe_root / "run_script.sh").write_text("freebayes -p 1\n", encoding="utf-8")
    (results_root / "variants_shared.csv").write_text("CHROM,POS\n", encoding="utf-8")

    discovered = discover_protocol_files(
        user_query="Identify shared variants.",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )

    assert discovered == []


def test_discover_protocol_files_planning_strict_blocks_external_recipe_and_results(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "evolution" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "evolution" / "data"
    recipe_root = project_root / "external" / "bioagent-bench" / "tasks" / "evolution"
    results_root = data_root.parent / "results"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    recipe_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)
    (recipe_root / "run_script.sh").write_text("freebayes -p 1\n", encoding="utf-8")
    (results_root / "variants_shared.csv").write_text("CHROM,POS\n", encoding="utf-8")

    discovered = discover_protocol_files(
        user_query="Identify shared variants.",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    )

    assert discovered == []


def test_analysis_brief_official_mode_hides_protocol_grounding_lines():
    grounding = {
        "grounded": True,
        "task_name": "evolution",
        "source_files": ["/tmp/run_script.sh"],
        "required_tools": ["freebayes_call", "snpeff_annotate"],
        "required_plan_signals": ["vcffilter"],
        "binding_rules": ["Include an explicit post-caller variant filtering step before annotation/export."],
        "output_columns": ["CHROM", "POS", "REF", "ALT", "GENE", "IMPACT", "EFFECT", "STATUS"],
        "analytical_method": "freebayes_call",
    }
    patch = analysis_patch_from_protocol(grounding, available_skill_names=["freebayes_call", "snpeff_annotate"])
    spec = normalize_analysis_spec(
        {
            "analysis_type": "bacterial_evolution_variant_calling",
            "biological_objective": "test",
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
            **patch,
        },
        user_query="test",
        contract={"must_include_capabilities": ["variant_calling"]},
        available_skill_names=["freebayes_call", "snpeff_annotate"],
    )

    brief = build_analysis_brief(spec)

    assert "analysis_type=bacterial_evolution_variant_calling" in brief
    assert "protocol_task=" not in brief
    assert "protocol_required_tools=" not in brief
    assert "protocol_analytical_method=" not in brief
    assert "protocol_sources=" not in brief


def test_analysis_brief_planning_strict_hides_protocol_grounding_lines():
    grounding = {
        "grounded": True,
        "task_name": "evolution",
        "source_files": ["/tmp/run_script.sh"],
        "required_tools": ["freebayes_call", "snpeff_annotate"],
        "required_plan_signals": ["vcffilter"],
        "binding_rules": ["Include an explicit post-caller variant filtering step before annotation/export."],
        "output_columns": ["CHROM", "POS", "REF", "ALT", "GENE", "IMPACT", "EFFECT", "STATUS"],
        "analytical_method": "freebayes_call",
    }
    patch = analysis_patch_from_protocol(grounding, available_skill_names=["freebayes_call", "snpeff_annotate"])
    spec = normalize_analysis_spec(
        {
            "analysis_type": "bacterial_evolution_variant_calling",
            "biological_objective": "test",
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            **patch,
        },
        user_query="test",
        contract={"must_include_capabilities": ["variant_calling"]},
        available_skill_names=["freebayes_call", "snpeff_annotate"],
    )

    brief = build_analysis_brief(spec)

    assert "analysis_type=bacterial_evolution_variant_calling" in brief
    assert "protocol_task=" not in brief
    assert "protocol_required_tools=" not in brief
    assert "protocol_analytical_method=" not in brief
    assert "protocol_sources=" not in brief


def test_extract_protocol_grounding_official_giab_drops_happy_signal_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "giab" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "giab" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Call germline variants for GIAB reads and write a final VCF.",
        analysis_type="germline_variant_calling",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["bwa_mem_align", "gatk_haplotypecaller", "bash_run"],
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )

    assert grounding["grounded"] is True
    assert "bwa_mem_align" in grounding["required_tools"]
    assert "gatk_haplotypecaller" in grounding["required_tools"]
    assert "bwa_mem_align" in grounding["required_plan_signals"]
    assert "gatk_haplotypecaller" in grounding["required_plan_signals"]
    assert "hap.py" not in grounding["required_plan_signals"]


def test_extract_protocol_grounding_scientific_giab_keeps_happy_signal_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "giab" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "giab" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Call germline variants for GIAB reads and benchmark against the truth set.",
        analysis_type="germline_variant_calling",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["bwa_mem_align", "gatk_haplotypecaller", "bash_run"],
        benchmark_policy=SCIENTIFIC_HARNESS_POLICY,
    )

    assert grounding["grounded"] is True
    assert "hap.py" in grounding["required_plan_signals"]


def test_extract_protocol_grounding_official_variant_annotation_drops_snpsift_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "cystic-fibrosis" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "cystic-fibrosis" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Identify the cystic fibrosis causal variant from the annotated family VCF.",
        analysis_type="variant_annotation",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["snpeff_annotate", "bash_run"],
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )

    assert "snpeff_annotate" in grounding["required_plan_signals"]
    assert "snpsift" not in grounding["required_plan_signals"]


def test_extract_protocol_grounding_official_multi_model_dge_drops_python_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "alzheimer-mouse" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "alzheimer-mouse" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Compare shared KEGG pathways across three Alzheimer's mouse models.",
        analysis_type="multi_model_dge_pathway",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["bash_run", "deseq2_run"],
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )

    assert grounding == {} or "python" not in grounding.get("required_plan_signals", [])


def test_extract_protocol_grounding_scientific_multi_model_dge_keeps_python_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "alzheimer-mouse" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "alzheimer-mouse" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Compare shared KEGG pathways across three Alzheimer's mouse models.",
        analysis_type="multi_model_dge_pathway",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["bash_run", "deseq2_run"],
        benchmark_policy=SCIENTIFIC_HARNESS_POLICY,
    )

    assert "python3" in grounding["required_plan_signals"]


def test_extract_protocol_grounding_official_deseq_gff_drops_featurecounts_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "deseq" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (references / "candida.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references / "candida.gff").write_text(
        "##gff-version 3\n"
        "chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=CPAR2_1;Name=CPAR2_1\n",
        encoding="utf-8",
    )

    grounding = extract_protocol_grounding(
        user_query="Run differential expression on the paired-end RNA-seq samples and report up-regulated genes.",
        analysis_type="rna_seq_differential_expression",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["star_align", "subread_align", "featurecounts_run", "deseq2_run", "bash_run"],
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )

    assert grounding["grounded"] is True
    assert "subread_align" in grounding["required_plan_signals"]
    assert "deseq2_run" in grounding["required_plan_signals"]
    assert "featurecounts_run" in grounding["required_plan_signals"]
    assert "star_align" not in grounding["required_plan_signals"]


def test_compile_rna_seq_de_plan_uses_sample_metadata_for_conditions(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "deseq" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (references / "candida.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references / "candida.gtf").write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    for sample in ("SRR1278968", "SRR1278969", "SRR1278971", "SRR1278972"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")
    (data_root / "sample_metadata.tsv").write_text(
        "sample\tcondition\n"
        "SRR1278968\tPlankton\n"
        "SRR1278969\tPlankton\n"
        "SRR1278971\tBiofilm\n"
        "SRR1278972\tBiofilm\n",
        encoding="utf-8",
    )

    compiled, meta = _compile_rna_seq_de_plan(
        plan={},
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "protocol_grounding": {},
        },
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["sample_groups"]["SRR1278968"] == "Plankton"
    assert meta["sample_groups"]["SRR1278972"] == "Biofilm"
    deseq_step = next(step for step in compiled["plan"] if step["tool_name"] == "deseq2_run")
    assert deseq_step["arguments"]["contrast"] == "condition_Biofilm_vs_Plankton"
    assert deseq_step["arguments"]["metadata_table"].endswith("sample_metadata.tsv")
    assert deseq_step["arguments"]["script_path"].endswith("bio_harness/pipeline_scripts/deseq2_wrapper.R")
    assert not any(
        step["tool_name"] == "bash_run" and "sample-condition" in str(step.get("arguments", {}).get("command", ""))
        for step in compiled["plan"]
    )


def test_compile_rna_seq_de_plan_uses_gff_featurecounts_options(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "deseq" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (references / "candida.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references / "candida.gff").write_text(
        "##gff-version 3\n"
        "chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=CPAR2_1;Name=CPAR2_1\n",
        encoding="utf-8",
    )
    for sample in ("SRR1278968", "SRR1278969"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")

    compiled, _ = _compile_rna_seq_de_plan(
        plan={},
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "protocol_grounding": {},
        },
        selected_dir=selected_dir,
        data_root=data_root,
    )

    # Compiler uses subread_align when subjunc is on PATH, star_align otherwise
    align_tools = [step for step in compiled["plan"] if step["tool_name"] in ("subread_align", "star_align")]
    assert len(align_tools) >= 1, "Expected at least one alignment step"
    align_step = align_tools[0]
    if align_step["tool_name"] == "subread_align":
        assert align_step["arguments"]["reference_fasta"].endswith("candida.fa")
        assert align_step["arguments"]["index_base"].endswith("subread_index/genome")
    else:
        assert align_step["tool_name"] == "star_align"

    count_step = next(step for step in compiled["plan"] if step["tool_name"] == "featurecounts_run")
    assert count_step["arguments"]["count_read_pairs"] is True
    # GFF-specific options only set in subread path (with subjunc available)
    if align_tools[0]["tool_name"] == "subread_align":
        assert count_step["arguments"]["annotation_format"] == "GFF"
        assert count_step["arguments"]["feature_type"] == "gene"
        assert count_step["arguments"]["attribute_type"] == "ID"


def test_compile_rna_seq_de_plan_metadata_writer_uses_cli_safe_entries(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "deseq" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (references / "candida.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references / "candida.gff").write_text(
        "##gff-version 3\n"
        "chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=CPAR2_1;Name=CPAR2_1\n",
        encoding="utf-8",
    )
    for sample in ("SRR1278968", "SRR1278969", "SRR1278971", "SRR1278972"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")

    compiled, _ = _compile_rna_seq_de_plan(
        plan={},
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "protocol_grounding": {},
        },
        selected_dir=selected_dir,
        data_root=data_root,
    )

    metadata_step = next(
        step
        for step in compiled["plan"]
        if step["tool_name"] == "bash_run" and "write_sample_metadata_table.py" in step["arguments"].get("command", "")
    )
    command = metadata_step["arguments"]["command"]
    assert "sample_metadata.tsv" in command
    assert "SRR1278968=control" in command
    assert "SRR1278971=treatment" in command
    assert r"SRR1278968\tcontrol" not in command


def test_compile_rna_seq_de_plan_detects_benchmark_deseq_from_data_root(
    tmp_path: Path,
    monkeypatch,
):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "runs" / "ui_chat_run"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (references / "candida.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references / "candida.gff").write_text(
        "##gff-version 3\n"
        "chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=CPAR2_1;Name=CPAR2_1\n",
        encoding="utf-8",
    )
    for sample in ("SRR1278968", "SRR1278969", "SRR1278971", "SRR1278972"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.core.protocol_grounding._compiler_rna_seq.requirement_available",
        lambda tool: tool == "subread",
    )

    compiled, _ = _compile_rna_seq_de_plan(
        plan={},
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "protocol_grounding": {},
            "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        },
        selected_dir=selected_dir,
        data_root=data_root,
    )

    featurecounts_step = next(
        step for step in compiled["plan"] if step["tool_name"] == "featurecounts_run"
    )
    deseq_step = next(step for step in compiled["plan"] if step["tool_name"] == "deseq2_run")

    assert featurecounts_step["arguments"]["strand_specificity"] == 2
    assert deseq_step["arguments"]["engine"] == "pydeseq2"
    assert deseq_step["arguments"]["script_path"].endswith("bio_harness/pipeline_scripts/pydeseq2_wrapper.py")


def test_compile_rna_seq_de_plan_prefers_cutadapt_for_rna_trimming(
    tmp_path: Path,
    monkeypatch,
):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "deseq" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (references / "candida.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references / "candida.gtf").write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    for sample in ("SRR1278968", "SRR1278969"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.core.protocol_grounding._compiler_rna_seq.requirement_available",
        lambda tool: tool == "cutadapt",
    )

    compiled, _ = _compile_rna_seq_de_plan(
        plan={},
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "protocol_grounding": {},
        },
        selected_dir=selected_dir,
        data_root=data_root,
    )

    trimming_steps = [step for step in compiled["plan"] if step["tool_name"] == "cutadapt_run"]
    assert len(trimming_steps) == 2
    first_args = trimming_steps[0]["arguments"]
    assert first_args["adapter_3prime_r1"] == "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"
    assert first_args["adapter_3prime_r2"] == "AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT"
    assert first_args["quality_cutoff"] == "20,20"
    assert first_args["minimum_length"] == 50
    assert all(step["tool_name"] != "bash_run" or "fastp" not in step["arguments"].get("command", "") for step in compiled["plan"])


def test_compile_rna_seq_de_plan_falls_back_to_fastp_when_cutadapt_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "deseq" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (references / "candida.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references / "candida.gtf").write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    for sample in ("SRR1278968", "SRR1278969"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")

    monkeypatch.setattr(
        "bio_harness.core.protocol_grounding._compiler_rna_seq.requirement_available",
        lambda tool: tool == "fastp",
    )

    compiled, _ = _compile_rna_seq_de_plan(
        plan={},
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "protocol_grounding": {},
        },
        selected_dir=selected_dir,
        data_root=data_root,
    )

    first_bash_step = next(step for step in compiled["plan"] if step["tool_name"] == "bash_run")
    assert "fastp" in first_bash_step["arguments"]["command"]


def test_extract_protocol_grounding_scientific_variant_annotation_keeps_snpsift_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "cystic-fibrosis" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "cystic-fibrosis" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Identify the cystic fibrosis causal variant from the annotated family VCF.",
        analysis_type="variant_annotation",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["snpeff_annotate", "bash_run"],
        benchmark_policy=SCIENTIFIC_HARNESS_POLICY,
    )

    assert "snpsift" in grounding["required_plan_signals"]


def test_extract_protocol_grounding_official_viral_drops_fastp_signal_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "viral-metagenomics" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "viral-metagenomics" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Identify viruses in paired-end reads by mapping against a staged viral reference panel.",
        analysis_type="viral_metagenomics",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["minimap2_align", "fastp_run", "bash_run", "fastqc_run"],
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )

    assert grounding["grounded"] is True
    assert grounding["required_tools"] == ["fastp_run"]
    assert grounding["required_plan_signals"] == ["fastp_run"]
    assert "python3" not in grounding["required_plan_signals"]
    assert "samtools" not in grounding["required_plan_signals"]


def test_extract_protocol_grounding_scientific_viral_keeps_fastp_signal_without_protocol_files(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "viral-metagenomics" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "viral-metagenomics" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Identify viruses in paired-end reads by mapping against a staged viral reference panel.",
        analysis_type="viral_metagenomics",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["minimap2_align", "fastp_run", "bash_run", "fastqc_run"],
        benchmark_policy=SCIENTIFIC_HARNESS_POLICY,
    )

    assert grounding["grounded"] is True
    assert "fastp_run" in grounding["required_tools"]
    assert "fastp_run" in grounding["required_plan_signals"]
    assert "python3" in grounding["required_plan_signals"]
    assert "samtools" not in grounding["required_plan_signals"]


def test_assess_protocol_grounding_official_giab_accepts_minimal_calling_plan():
    analysis_spec = {
        "analysis_type": "germline_variant_calling",
        "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
        "protocol_grounding": {
            "required_tools": ["bwa_mem_align", "gatk_haplotypecaller"],
            "required_plan_signals": ["bwa_mem_align", "gatk_haplotypecaller"],
            "source_files": [],
        },
    }
    plan = {
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "arguments": {"reference_fasta": "ref.fa", "reads_1": "r1.fq.gz", "reads_2": "r2.fq.gz"},
                "step_id": 1,
            },
            {
                "tool_name": "gatk_haplotypecaller",
                "arguments": {"reference_fasta": "ref.fa", "input_bam": "sample.bam", "output_vcf": "final.vcf.gz"},
                "step_id": 2,
            },
        ]
    }

    validation = assess_protocol_grounding(plan, analysis_spec)

    assert validation["passed"] is True
    assert validation["missing_required_tools"] == []
    assert validation["missing_plan_signals"] == []


def test_extract_protocol_grounding_official_metagenomics_drops_copy_signal_and_normalizes_spades(tmp_path: Path):
    project_root = tmp_path / "repo"
    selected_dir = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "runs" / "metagenomics" / "attempt1"
    data_root = project_root / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "metagenomics" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    grounding = extract_protocol_grounding(
        user_query="Classify metagenomic reads with Kraken2 and write the final report.",
        analysis_type="metagenomics_classification",
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        available_skill_names=["fastqc_run", "bash_run", "spades_assemble"],
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    )

    assert grounding["grounded"] is True
    assert "cp" not in grounding["required_plan_signals"]
    assert "spades_assemble" in grounding["required_plan_signals"]
    assert "spades.py --meta" not in grounding["required_plan_signals"]


def test_deterministic_protocol_repair_compiles_shared_evolution_plan(tmp_path: Path):
    selected_dir = tmp_path / "runs" / "evolution" / "attempt1"
    data_root = tmp_path / "tasks" / "evolution" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    for name in (
        "ancestor_R1.fastq.gz",
        "ancestor_R2.fastq.gz",
        "evolved1_R1.fastq.gz",
        "evolved1_R2.fastq.gz",
        "evolved2_R1.fastq.gz",
        "evolved2_R2.fastq.gz",
    ):
        (data_root / name).write_text("stub\n", encoding="utf-8")

    bad_plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "ancestor_R1.fastq.gz",
                    "reads_2": "ancestor_R2.fastq.gz",
                    "output_dir": "assembly",
                    "threads": 8,
                    "memory_gb": 32,
                },
                "step_id": 1,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "assembly/scaffolds.fasta",
                    "input_bam": "sample.sorted.bam",
                    "output_vcf": "raw.vcf",
                },
                "step_id": 2,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": "bcftools query -f '%CHROM\\t%POS\\n' raw.vcf > variants_shared.csv",
                },
                "step_id": 3,
            },
        ]
    }
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "chosen_method": "freebayes_call",
        "parameter_profile": [
            {"tool_name": "spades_assemble", "settings": {"careful": True}, "rationale": "recipe"},
            {"tool_name": "freebayes_call", "settings": {"ploidy": 1}, "rationale": "recipe"},
        ],
        "protocol_grounding": {
            "grounded": True,
            "task_name": "evolution",
            "required_tools": ["spades_assemble", "freebayes_call", "snpeff_annotate"],
            "required_plan_signals": ["vcffilter", "snpeff"],
            "requires_shared_comparison": True,
            "min_variant_branches": 2,
            "benchmark_profile": {
                "profile_id": "bioagent_bench_evolution_shared_v1",
                "annotation_strategy": {"tool_name": "prokka_annotate"},
                "export_profile": {"header_case": "upper", "status": "shared", "min_impact": "MODERATE", "dedupe_by_gene": True},
                "shared_variant_policy": {"normalize_before_compare": True},
            },
            "binding_rules": [
                "Include an explicit post-caller variant filtering step before annotation/export.",
                "Final deliverable must be a shared-variant CSV matching the benchmark naming/column semantics.",
            ],
        },
    }

    repaired, meta = deterministic_protocol_repair(
        bad_plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    steps = repaired["plan"]
    assert [step["tool_name"] for step in steps].count("freebayes_call") == 2
    assert [step["tool_name"] for step in steps].count("snpeff_annotate") == 2
    # Accept either prokka_annotate or prodigal_annotate depending on tool availability
    annotation_count = (
        [step["tool_name"] for step in steps].count("prokka_annotate")
        + [step["tool_name"] for step in steps].count("prodigal_annotate")
    )
    assert annotation_count == 1
    assert "--header-case upper" in str(steps[-1]["arguments"]["command"])
    assert "--dedupe-by-gene" in str(steps[-1]["arguments"]["command"])
    assert str(preferred_helper_python_executable()) in str(steps[-1]["arguments"]["command"])
    assert any(
        step["tool_name"] == "bwa_mem_align" and step["arguments"].get("postprocess_mode") == "fixmate_markdup_q20"
        for step in steps
    )
    assert any(".normalized.vcf.gz" in str(step.get("arguments", {}).get("command", "")) for step in steps if step["tool_name"] == "bash_run")

    validation = assess_protocol_grounding(repaired, analysis_spec)
    assert validation["passed"] is True


def test_deterministic_protocol_repair_prefers_prodigal_for_container_backed_prokka(
    tmp_path: Path,
    monkeypatch,
):
    import bio_harness.core.protocol_grounding._compiler_evolution as evolution_compiler

    selected_dir = tmp_path / "runs" / "evolution" / "attempt1"
    data_root = tmp_path / "tasks" / "evolution" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    for name in (
        "ancestor_R1.fastq.gz",
        "ancestor_R2.fastq.gz",
        "evolved1_R1.fastq.gz",
        "evolved1_R2.fastq.gz",
        "evolved2_R1.fastq.gz",
        "evolved2_R2.fastq.gz",
    ):
        (data_root / name).write_text("stub\n", encoding="utf-8")

    monkeypatch.setattr(evolution_compiler, "requirement_available", lambda _tool: True)
    monkeypatch.setattr(evolution_compiler, "tool_launcher_uses_container", lambda _tool: True)

    repaired, meta = deterministic_protocol_repair(
        {"plan": []},
        analysis_spec={
            "analysis_type": "bacterial_evolution_variant_calling",
            "protocol_grounding": {
                "grounded": True,
                "required_tools": ["prokka_annotate"],
                "min_variant_branches": 2,
                "benchmark_profile": {
                    "annotation_strategy": {
                        "tool_name": "prokka_annotate",
                        "fallback_tool_name": "prodigal_annotate",
                    }
                },
            },
        },
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    assert any(step["tool_name"] == "prodigal_annotate" for step in repaired["plan"])
    assert all(step["tool_name"] != "prokka_annotate" for step in repaired["plan"])
    validation = assess_protocol_grounding(
        repaired,
        {
            "analysis_type": "bacterial_evolution_variant_calling",
            "protocol_grounding": {
                "grounded": True,
                "required_tools": ["prokka_annotate"],
                "benchmark_profile": {
                    "annotation_strategy": {
                        "tool_name": "prokka_annotate",
                        "fallback_tool_name": "prodigal_annotate",
                    }
                },
            },
        },
    )
    assert validation["passed"] is True
    assert validation["missing_required_tools"] == []


def test_apply_parameter_profile_skips_bash_run():
    """Parameter profile with script_type for bash_run must NOT inject into step args.

    LLMs sometimes put descriptive metadata like script_type/dependencies/logic
    in the parameter_profile for bash_run, which the system would blindly inject
    as invalid keyword arguments.
    """
    from bio_harness.core.protocol_grounding import _apply_parameter_profile

    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {"command": "Rscript -e 'library(DESeq2); ...'"},
            },
            {
                "step_id": 2,
                "tool_name": "star_align",
                "arguments": {"genome_dir": "/ref", "reads_1": "r1.fq"},
            },
        ]
    }
    profile = [
        {
            "tool_name": "bash_run",
            "settings": {"script_type": "R_script", "dependencies": ["DESeq2"]},
        },
        {
            "tool_name": "star_align",
            "settings": {"threads": 8},
        },
    ]
    patched, meta = _apply_parameter_profile(plan, profile)

    # bash_run should NOT have script_type injected
    bash_step = patched["plan"][0]
    assert "script_type" not in bash_step["arguments"], (
        f"script_type should not be injected into bash_run: {bash_step['arguments']}"
    )
    assert bash_step["arguments"]["command"] == "Rscript -e 'library(DESeq2); ...'"

    # star_align SHOULD have threads injected
    star_step = patched["plan"][1]
    assert star_step["arguments"]["threads"] == 8


def test_apply_parameter_profile_preserves_existing_path_arguments():
    from bio_harness.core.protocol_grounding import _apply_parameter_profile

    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "custom_ref",
                    "input_vcf": "/tmp/task/input_variants.vcf",
                    "reference_fasta": "/tmp/task/reference.fa",
                    "annotation_gff": "/tmp/task/genes.gff",
                    "output_vcf": "/tmp/run/output/annotated.vcf",
                    "config_dir": "/tmp/run/output/snpeff_custom_db",
                },
            }
        ]
    }
    profile = [
        {
            "tool_name": "snpeff_annotate",
            "settings": {
                "genome_db": "custom_ref",
                "input_vcf": "input_variants.vcf",
                "reference_fasta": "reference.fa",
                "annotation_gff": "genes.gff",
            },
        }
    ]

    patched, meta = _apply_parameter_profile(plan, profile)

    step = patched["plan"][0]
    assert step["arguments"]["input_vcf"] == "/tmp/task/input_variants.vcf"
    assert step["arguments"]["reference_fasta"] == "/tmp/task/reference.fa"
    assert step["arguments"]["annotation_gff"] == "/tmp/task/genes.gff"
    assert meta["changed"] is False
    assert meta["why"] == "profile_already_applied"


def test_apply_parameter_profile_skips_undeclared_wrapper_settings():
    """Profile hints must not inject descriptive, non-wrapper arguments."""

    from bio_harness.core.protocol_grounding import _apply_parameter_profile

    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "ancestor",
                    "input_vcf": "/tmp/evol1.ancestor_subtracted.vcf.gz",
                    "output_vcf": "/tmp/evol1.annotated.vcf",
                },
            }
        ]
    }
    profile = [
        {
            "tool_name": "snpeff_annotate",
            "settings": {
                "annotation_field": "ANN",
                "genome_db": "ancestor",
            },
        }
    ]

    patched, meta = _apply_parameter_profile(plan, profile)

    args = patched["plan"][0]["arguments"]
    assert args["genome_db"] == "ancestor"
    assert "annotation_field" not in args
    assert meta["skipped_undeclared_settings"] == {
        "snpeff_annotate": ["annotation_field"],
    }


# ---------------------------------------------------------------------------
# Tests for _compile_multi_model_dge_plan
# ---------------------------------------------------------------------------


def _make_dge_data(tmp_path: Path, *, multi_model: bool = True, include_de_table: bool = False):
    """Create minimal DGE benchmark data in tmp_path.

    When multi_model=True (default), creates filenames matching the multi-model
    classifier (5xFAD count matrix, 3xTG count matrix, and optionally a PS3O1S
    pre-computed DE table).  When False, creates only a generic counts file that
    matches the "model_unknown" fallback.
    """
    data_root = tmp_path / "data"
    data_root.mkdir()
    count_header = "gene_id,ctrl1,ctrl2,treat1,treat2\n"
    count_rows = "GENE_A,100,110,300,310\nGENE_B,200,210,50,55\nGENE_C,150,160,155,160\n"
    if multi_model:
        # 5xFAD count matrix
        (data_root / "GSE168137_5xFAD_counts.csv").write_text(
            "gene_id,BL6_1,BL6_2,5xFAD_1,5xFAD_2\n"
            "GENE_A,100,110,300,310\n"
            "GENE_B,200,210,50,55\n"
            "GENE_C,150,160,155,160\n"
        )
        # 3xTG count matrix
        (data_root / "GSE161904_3xTG_counts.csv").write_text(
            "gene_id,WT_1,WT_2,3xTgAD_1,3xTgAD_2\n"
            "GENE_A,90,95,280,290\n"
            "GENE_B,190,200,60,65\n"
            "GENE_C,140,150,145,150\n"
        )
        if include_de_table:
            (data_root / "DEA_PS3O1S_results.csv").write_text(
                "gene_name,gene_id,pval,qval,log2FC\n"
                "GENE_A,ENSMUSG001,0.001,0.01,2.5\n"
                "GENE_B,ENSMUSG002,0.8,0.95,-0.1\n"
            )
    else:
        (data_root / "counts.csv").write_text(count_header + count_rows)
    return data_root


def test_compile_multi_model_dge_plan_discovers_models(tmp_path: Path):
    """With multi-model filenames, compiler discovers 5xFAD and 3xTG_AD."""
    from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
    from bio_harness.core.protocol_grounding import _compile_multi_model_dge_plan

    data_root = _make_dge_data(tmp_path, multi_model=True)
    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    plan = {"thought_process": "test", "plan": []}
    compiled, meta = _compile_multi_model_dge_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )
    assert meta["changed"] is True
    assert "5xFAD" in meta["models"]
    assert "3xTG_AD" in meta["models"]
    assert len(compiled["plan"]) == 2
    cmd = compiled["plan"][0]["arguments"]["command"]
    assert "PYTHONPATH=" in cmd
    assert str(preferred_helper_python_executable()) in cmd
    assert "compare_pathways.py" in cmd
    assert "--count-table" in cmd
    assert "pathway_comparison.csv" in cmd
    assert compiled["plan"][1]["tool_name"] == "artifact_schema_profile"


def test_compile_multi_model_dge_plan_with_de_table(tmp_path: Path):
    """When a pre-computed DE table (PS3O1S) is present, it is classified as de_table."""
    from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
    from bio_harness.core.protocol_grounding import _compile_multi_model_dge_plan

    data_root = _make_dge_data(tmp_path, multi_model=True, include_de_table=True)
    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    plan = {"thought_process": "test", "plan": []}
    compiled, meta = _compile_multi_model_dge_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )
    assert meta["changed"] is True
    assert "PS3O1S" in meta["models"]
    assert meta["models"]["PS3O1S"] == "de_table"
    cmd = compiled["plan"][0]["arguments"]["command"]
    assert "PYTHONPATH=" in cmd
    assert str(preferred_helper_python_executable()) in cmd
    assert "--precomputed-de-table" in cmd
    assert "PS3O1S=" in cmd


def test_compile_multi_model_dge_plan_no_data(tmp_path: Path):
    """With no recognizable data files, compiler returns changed=False."""
    from bio_harness.core.protocol_grounding import _compile_multi_model_dge_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    # Write a file that doesn't match any pattern
    (data_root / "readme.txt").write_text("nothing here")
    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    plan = {"thought_process": "test", "plan": []}
    _, meta = _compile_multi_model_dge_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )
    assert meta["changed"] is False
    assert meta["why"] == "no_data_files"


# ---------------------------------------------------------------------------
# Tests for _compile_metagenomics_plan
# ---------------------------------------------------------------------------


def test_compile_metagenomics_plan_uses_fastp(tmp_path: Path):
    """Compiler uses fastp_run plus helper-backed classification."""
    from bio_harness.core.protocol_grounding import _compile_metagenomics_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sample_R1.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "sample_R2.fastq.gz").write_text("stub\n", encoding="utf-8")

    kraken_db = data_root / "kraken2_db"
    kraken_db.mkdir()
    for token in ("hash.k2d", "opts.k2d", "taxo.k2d", "ktaxonomy.tsv"):
        (kraken_db / token).write_text("stub\n", encoding="utf-8")
    refs_dir = data_root / "references"
    refs_dir.mkdir()
    (refs_dir / "bac_a.fna").write_text(">refA\nACGTACGT\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()
    (selected_dir / "references").mkdir()

    plan = {"thought_process": "test", "plan": []}
    compiled, meta = _compile_metagenomics_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )

    assert meta["changed"] is True
    assert meta["why"] == "compiled_metagenomics_protocol"
    assert meta["sample_label"] == "sample"
    assert meta["kraken_db"].endswith("kraken2_db")

    steps = compiled["plan"]
    assert len(steps) == 3

    # Step 1: fastp trimming
    step1 = steps[0]
    assert step1["tool_name"] == "fastp_run"
    assert step1["arguments"]["reads_1"].endswith("sample_R1.fastq.gz")
    assert step1["arguments"]["reads_2"].endswith("sample_R2.fastq.gz")
    assert step1["arguments"]["detect_adapter_for_pe"] is True
    assert step1["arguments"]["cut_right"] is True
    assert step1["arguments"]["length_required"] == 35

    # Step 2: metaSPAdes assembly
    step2 = steps[1]
    assert step2["tool_name"] == "spades_assemble"
    assert step2["arguments"]["meta_mode"] is True
    assert step2["arguments"]["output_dir"].endswith("assembly/metaspades")

    # Step 3: helper-backed classification
    cmd3 = steps[2]["arguments"]["command"]
    assert "PYTHONPATH=" in cmd3
    assert "classify_metagenomics_kmer.py" in cmd3
    assert str(refs_dir.resolve(strict=False)) in cmd3
    assert "--output-report" in cmd3
    assert "sample_kraken2_report.txt" in cmd3

    # Thought process mentions fastp, not trimmomatic
    assert "fastp" in compiled["thought_process"].lower()
    assert "trimmomatic" not in compiled["thought_process"].lower()


def test_compile_metagenomics_plan_prefers_validated_prebuilt_db(tmp_path: Path):
    from bio_harness.core.protocol_grounding import _compile_metagenomics_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sample_R1.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "sample_R2.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "truth.json").write_text(
        json.dumps(
            {
                "species": [
                    {"name": "Escherichia coli", "taxid": 562},
                    {"name": "Bacillus subtilis", "taxid": 1423},
                    {"name": "Staphylococcus aureus", "taxid": 1280},
                ],
                "expected_top_genus": ["Escherichia", "Bacillus", "Staphylococcus"],
            }
        ),
        encoding="utf-8",
    )

    kraken_db = data_root / "kraken2_db"
    kraken_db.mkdir()
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

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    compiled, meta = _compile_metagenomics_plan(
        plan={"thought_process": "test", "plan": []},
        analysis_spec=None,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["kraken_db"] == str(kraken_db.resolve())
    assert meta["kraken_db_meta"]["validation"]["valid"] is True
    assert meta["kraken_db_meta"]["validation"]["missing_taxids"] == []
    cmd = compiled["plan"][2]["arguments"]["command"]
    assert "PYTHONPATH=" in cmd
    assert str(kraken_db.resolve() / "ktaxonomy.tsv") in cmd


def test_compile_metagenomics_plan_no_reads(tmp_path: Path):
    """Without FASTQ files, compiler returns unchanged plan."""
    from bio_harness.core.protocol_grounding import _compile_metagenomics_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    plan = {"thought_process": "test", "plan": []}
    compiled, meta = _compile_metagenomics_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )

    assert meta["changed"] is False
    assert meta["why"] == "no_fastq_pairs"


def test_compile_metagenomics_via_deterministic_repair(tmp_path: Path):
    """Full route through deterministic_protocol_repair for metagenomics_classification."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sample_R1.fastq.gz").write_text("stub\n", encoding="utf-8")
    (data_root / "sample_R2.fastq.gz").write_text("stub\n", encoding="utf-8")

    kraken_db = data_root / "kraken2_db"
    kraken_db.mkdir()
    (kraken_db / "hash.k2d").write_text("stub\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    bad_plan = {
        "thought_process": "LLM plan",
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "echo hello"}, "step_id": 1},
        ],
    }
    analysis_spec = {"analysis_type": "metagenomics_classification"}

    repaired, meta = deterministic_protocol_repair(
        bad_plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    steps = repaired["plan"]
    assert len(steps) == 3
    assert steps[0]["tool_name"] == "fastp_run"
    assert steps[0]["arguments"]["detect_adapter_for_pe"] is True
    assert steps[0]["arguments"]["cut_right"] is True
    assert steps[1]["tool_name"] == "spades_assemble"
    assert steps[1]["arguments"]["meta_mode"] is True
    assert "PYTHONPATH=" in steps[2]["arguments"]["command"]
    assert "classify_metagenomics_kmer.py" in steps[2]["arguments"]["command"]


# ---------------------------------------------------------------------------
# Tests for _compile_comparative_genomics_plan
# ---------------------------------------------------------------------------


def test_compile_comparative_genomics_plan_uses_minimap2(tmp_path: Path):
    """Compiler uses minimap2 (not DECIPHER) and produces 2-step pipeline."""
    from bio_harness.core.protocol_grounding import _compile_comparative_genomics_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "genome_a.fna").write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    (data_root / "genome_b.fna").write_text(">chr1\nACGGACGG\n", encoding="utf-8")
    (data_root / "genome_c.fna").write_text(">chr1\nACGAACGA\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    plan = {"thought_process": "test", "plan": []}
    compiled, meta = _compile_comparative_genomics_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )

    assert meta["changed"] is True
    assert meta["why"] == "compiled_comparative_genomics_protocol"
    assert meta["genome_count"] == 3

    steps = compiled["plan"]
    assert len(steps) == 2

    # Step 1: minimap2 alignment
    cmd1 = steps[0]["arguments"]["command"]
    assert "minimap2" in cmd1, f"Step 1 should use minimap2, got: {cmd1}"
    assert "asm20" in cmd1
    assert "DECIPHER" not in cmd1
    assert "Rscript" not in cmd1
    # 3 genomes -> 3 pairwise PAF files
    assert cmd1.count("pair_") == 3

    # Step 2: ANI computation
    cmd2 = steps[1]["arguments"]["command"]
    assert "python3" in cmd2
    assert "distance_matrix" in cmd2
    assert "closest_pair" in cmd2

    # Thought process mentions minimap2
    assert "minimap2" in compiled["thought_process"].lower()
    assert "decipher" not in compiled["thought_process"].lower()


def test_compile_comparative_genomics_plan_fewer_than_2_genomes(tmp_path: Path):
    """With fewer than 2 genome files, compiler returns unchanged plan."""
    from bio_harness.core.protocol_grounding import _compile_comparative_genomics_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "genome_a.fna").write_text(">chr1\nACGT\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    plan = {"thought_process": "test", "plan": []}
    compiled, meta = _compile_comparative_genomics_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )

    assert meta["changed"] is False
    assert meta["why"] == "fewer_than_2_genomes"


def test_compile_comparative_genomics_via_deterministic_repair(tmp_path: Path):
    """Full route through deterministic_protocol_repair for comparative_genomics."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "genome_a.fna").write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    (data_root / "genome_b.fna").write_text(">chr1\nACGGACGG\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    bad_plan = {
        "thought_process": "LLM plan",
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "echo hello"}, "step_id": 1},
        ],
    }
    analysis_spec = {"analysis_type": "comparative_genomics"}

    repaired, meta = deterministic_protocol_repair(
        bad_plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    steps = repaired["plan"]
    assert len(steps) == 2
    assert "minimap2" in steps[0]["arguments"]["command"]
    assert "DECIPHER" not in steps[0]["arguments"]["command"]


# ---------------------------------------------------------------------------
# Viral metagenomics compiler tests
# ---------------------------------------------------------------------------


def test_compile_viral_metagenomics_plan_uses_helper_backed_classifier(tmp_path: Path):
    """Compiler trims reads then invokes the helper-backed viral classifier."""
    from bio_harness.core.protocol_grounding import _compile_viral_metagenomics_plan

    data_root = tmp_path / "data"
    refs_dir = data_root / "references"
    refs_dir.mkdir(parents=True)
    (refs_dir / "virus_a.fna").write_text(">NC_000001\nACGTACGTACGT\n", encoding="utf-8")
    (refs_dir / "virus_b.fna").write_text(">NC_000002\nGGCCTTAAGGCC\n", encoding="utf-8")
    # Create stub FASTQ pair
    (data_root / "sample_R1.fastq.gz").write_bytes(b"")
    (data_root / "sample_R2.fastq.gz").write_bytes(b"")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    plan = {"thought_process": "test", "plan": []}
    compiled, meta = _compile_viral_metagenomics_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )

    assert meta["changed"] is True
    assert meta["why"] == "compiled_viral_metagenomics_protocol"
    assert meta["n_references"] == 2

    steps = compiled["plan"]
    assert len(steps) == 2

    step1 = steps[0]
    assert step1["tool_name"] == "fastp_run"
    assert step1["arguments"]["reads_1"].endswith("sample_R1.fastq.gz")
    assert step1["arguments"]["reads_2"].endswith("sample_R2.fastq.gz")
    assert step1["arguments"]["detect_adapter_for_pe"] is True
    assert step1["arguments"]["length_required"] == 30

    cmd2 = steps[1]["arguments"]["command"]
    assert "PYTHONPATH=" in cmd2
    assert "classify_viral_reads_kmer.py" in cmd2
    assert "--reference-dir" in cmd2
    assert "--output-report" in cmd2
    assert "--output-detected" in cmd2
    assert "minimap2" not in cmd2

    assert "helper-backed viral reference classification" in compiled["thought_process"].lower()
    assert "megahit" not in compiled["thought_process"].lower()


def test_compile_viral_metagenomics_plan_no_reads(tmp_path: Path):
    """With no FASTQ pairs, compiler returns unchanged plan."""
    from bio_harness.core.protocol_grounding import _compile_viral_metagenomics_plan

    data_root = tmp_path / "data"
    refs_dir = data_root / "references"
    refs_dir.mkdir(parents=True)
    (refs_dir / "virus_a.fna").write_text(">NC_000001\nACGT\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    plan = {"thought_process": "test", "plan": []}
    compiled, meta = _compile_viral_metagenomics_plan(
        plan=plan, analysis_spec=None, selected_dir=selected_dir, data_root=data_root,
    )

    assert meta["changed"] is False
    assert meta["why"] == "no_fastq_pairs"


def test_compile_single_cell_plan_prefers_10x_fastq_for_sample_r_gz_names(tmp_path: Path) -> None:
    from bio_harness.core.protocol_grounding import _compile_single_cell_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    for name in (
        "sample_R1.fastq.gz",
        "sample_R2.fastq.gz",
        "barcodes_whitelist.txt",
        "reference.fa",
        "annotation.gtf",
        "adata.h5ad",
    ):
        (data_root / name).write_text("stub\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    compiled, meta = _compile_single_cell_plan(
        plan={"thought_process": "test", "plan": []},
        analysis_spec=None,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    assert meta["why"] == "compiled_single_cell_10x_fastq"
    assert compiled["plan"][0]["tool_name"] == "sc_count_and_cluster"
    assert compiled["plan"][0]["arguments"]["r1"].endswith("sample_R1.fastq.gz")
    assert compiled["plan"][0]["arguments"]["r2"].endswith("sample_R2.fastq.gz")


def test_compile_viral_metagenomics_via_deterministic_repair(tmp_path: Path):
    """Full route through deterministic_protocol_repair for viral_metagenomics."""
    data_root = tmp_path / "data"
    refs_dir = data_root / "references"
    refs_dir.mkdir(parents=True)
    (refs_dir / "virus_a.fna").write_text(">NC_000001\nACGTACGTACGT\n", encoding="utf-8")
    (refs_dir / "virus_b.fna").write_text(">NC_000002\nGGCCTTAAGGCC\n", encoding="utf-8")
    (data_root / "sample_R1.fastq.gz").write_bytes(b"")
    (data_root / "sample_R2.fastq.gz").write_bytes(b"")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    bad_plan = {
        "thought_process": "LLM plan",
        "plan": [
            {"tool_name": "bash_run", "arguments": {"command": "echo hello"}, "step_id": 1},
        ],
    }
    analysis_spec = {"analysis_type": "viral_metagenomics"}

    repaired, meta = deterministic_protocol_repair(
        bad_plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    steps = repaired["plan"]
    assert len(steps) == 2
    assert steps[0]["tool_name"] == "fastp_run"
    assert "classify_viral_reads_kmer.py" in steps[1]["arguments"]["command"]
    assert "megahit" not in str(steps)
    assert "kaiju" not in str(steps)


def test_compile_transcript_quant_plan_uses_transcriptome_fasta(tmp_path: Path) -> None:
    from bio_harness.core.protocol_grounding import _compile_transcript_quant_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sample_R1.fastq.gz").write_bytes(b"")
    (data_root / "sample_R2.fastq.gz").write_bytes(b"")
    (data_root / "genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    transcriptome = data_root / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    compiled, meta = _compile_transcript_quant_plan(
        plan={"thought_process": "test", "plan": []},
        analysis_spec=None,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    assert meta["why"] == "compiled_transcript_quant_protocol"
    assert meta["transcriptome_fasta"] == str(transcriptome)
    assert len(compiled["plan"]) == 1
    assert compiled["plan"][0]["tool_name"] == "salmon_quant"
    assert compiled["plan"][0]["arguments"]["transcriptome_fasta"] == str(transcriptome)
    assert compiled["plan"][0]["arguments"]["output_dir"] == str(selected_dir / "salmon_quant_out")


def test_compile_transcript_quant_plan_preserves_requested_output_root(tmp_path: Path) -> None:
    from bio_harness.core.protocol_grounding import _compile_transcript_quant_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sample_R1.fastq.gz").write_bytes(b"")
    (data_root / "sample_R2.fastq.gz").write_bytes(b"")
    transcriptome = data_root / "transcriptome.fa"
    transcriptome.write_text(">tx1\nACGT\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()
    requested_output_dir = selected_dir / "salmon_out"

    compiled, meta = _compile_transcript_quant_plan(
        plan={"thought_process": "test", "plan": []},
        analysis_spec={"requested_output_paths": [str(requested_output_dir)]},
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    assert compiled["plan"][0]["arguments"]["output_dir"] == str(
        requested_output_dir.resolve(strict=False)
    )


def test_compile_transcript_quant_plan_requires_transcriptome_fasta(tmp_path: Path) -> None:
    from bio_harness.core.protocol_grounding import _compile_transcript_quant_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sample_R1.fastq.gz").write_bytes(b"")
    (data_root / "sample_R2.fastq.gz").write_bytes(b"")
    (data_root / "genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    compiled, meta = _compile_transcript_quant_plan(
        plan={"thought_process": "test", "plan": []},
        analysis_spec=None,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert compiled == {"thought_process": "test", "plan": []}
    assert meta["changed"] is False
    assert meta["why"] == "no_transcriptome_fasta"


def test_compile_germline_variant_calling_plan_preserves_requested_output_vcf(
    tmp_path: Path,
) -> None:
    from bio_harness.core.protocol_grounding import _compile_germline_variant_calling_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "sample_R1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
    (data_root / "sample_R2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")
    ref_fasta = data_root / "ref_genome.fa"
    ref_fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()
    requested_vcf = selected_dir / "exome" / "variants.vcf"

    compiled, meta = _compile_germline_variant_calling_plan(
        plan={"thought_process": "test", "plan": []},
        analysis_spec={"requested_output_paths": [str(requested_vcf)]},
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    assert compiled["plan"][0]["arguments"]["reference_fasta"] == str(ref_fasta.resolve())
    assert compiled["plan"][1]["arguments"]["output_vcf"] == str(
        requested_vcf.resolve(strict=False)
    )


def test_compile_phylogenetics_plan_uses_mafft_align(tmp_path: Path) -> None:
    from bio_harness.core.protocol_grounding import _compile_phylogenetics_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    input_fasta = data_root / "sequences.fasta"
    input_fasta.write_text(">seq1\nACGT\n>seq2\nACGA\n", encoding="utf-8")

    selected_dir = tmp_path / "output"
    selected_dir.mkdir()

    compiled, meta = _compile_phylogenetics_plan(
        plan={"thought_process": "test", "plan": []},
        analysis_spec=None,
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert meta["changed"] is True
    assert meta["why"] == "compiled_phylogenetics_protocol"
    assert meta["input_fasta"] == str(input_fasta.resolve())
    assert compiled["plan"][0]["tool_name"] == "mafft_align"
    assert compiled["plan"][0]["arguments"] == {
        "input_fasta": str(input_fasta.resolve()),
        "output_fasta": str(selected_dir / "aligned_sequences.fasta"),
        "threads": 2,
        "strategy_mode": "auto",
    }
    assert compiled["plan"][1]["tool_name"] == "phylogenetics_iqtree_style"
    assert compiled["plan"][1]["arguments"]["alignment_fasta"] == str(selected_dir / "aligned_sequences.fasta")
