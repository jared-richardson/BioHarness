from __future__ import annotations

from pathlib import Path

from bio_harness.core.artifact_role_validator import validate_artifact_role_invariants
from bio_harness.core.strict_artifact_binding import (
    bind_step_spec_for_strict_mode,
    rebind_direct_plan_for_strict_mode,
)


def test_bind_step_spec_for_strict_mode_rewrites_evolution_paths_from_selected_dir() -> None:
    selected_dir = Path("/tmp/official_runs/evolution/attempt1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    prodigal_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "prodigal_annotate",
            "arguments": {
                "input_fasta": "attempt1/assembly/contigs.fasta",
                "output_gff": "attempt1/annotation/anc.gff",
                "output_faa": "attempt1/annotation/anc.faa",
            },
        },
        workflow_step={"tool_name": "prodigal_annotate", "objective": "Annotate assembled ancestor reference"},
        analysis_spec=analysis_spec,
    )
    assert prodigal_step["arguments"]["input_fasta"] == str(resolved_dir / "assembly/scaffolds.fasta")
    assert prodigal_step["arguments"]["output_gff"] == str(resolved_dir / "annotation/genes.gff")
    assert prodigal_step["arguments"]["output_faa"] == str(resolved_dir / "annotation/proteins.faa")
    assert prodigal_step["arguments"]["mode"] == "auto"

    stale_prodigal_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "prodigal_annotate",
            "arguments": {
                "input_fasta": "attempt1/assembly/contigs.fasta",
                "output_gff": "attempt1/annotation/anc.gff",
                "output_faa": "attempt1/annotation/anc.faa",
                "mode": "single",
            },
        },
        workflow_step={
            "tool_name": "prodigal_annotate",
            "objective": "Annotate assembled ancestor reference",
        },
        analysis_spec=analysis_spec,
    )
    assert stale_prodigal_step["arguments"]["mode"] == "auto"

    align_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bwa_mem_align",
            "arguments": {
                "reference_fasta": "attempt1/assembly/spades_out/contigs.fasta",
                "output_bam": "attempt1/alignments/evol2.bam",
            },
        },
        workflow_step={
            "tool_name": "bwa_mem_align",
            "branch_id": "evol2",
            "objective": "Align evol2 reads to the assembled reference",
        },
        analysis_spec=analysis_spec,
    )
    assert align_step["arguments"]["reference_fasta"] == str(resolved_dir / "assembly/scaffolds.fasta")
    assert align_step["arguments"]["output_bam"] == str(resolved_dir / "alignments/evol2_aligned.bam")
    assert align_step["arguments"]["threads"] == 8
    assert align_step["arguments"]["postprocess_mode"] == "fixmate_markdup_q20"

    spades_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "spades_assemble",
            "arguments": {},
        },
        workflow_step={
            "tool_name": "spades_assemble",
            "objective": "Assemble the ancestor reads into the shared bacterial reference",
        },
        analysis_spec=analysis_spec,
    )
    assert spades_step["arguments"]["output_dir"] == str(resolved_dir / "assembly")
    assert spades_step["arguments"]["careful"] is True
    assert spades_step["arguments"]["threads"] == 8
    assert spades_step["arguments"]["memory_gb"] == 32


def test_bind_evolution_reference_uses_spades_contigs_when_scaffolds_absent(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "official_runs" / "evolution" / "attempt1"
    assembly_dir = selected_dir / "assembly"
    assembly_dir.mkdir(parents=True)
    (assembly_dir / "contigs.fasta").write_text(">contig1\nACGT\n", encoding="utf-8")
    (assembly_dir / "scaffolds.fasta").write_text("", encoding="utf-8")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    prodigal_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "prodigal_annotate", "arguments": {}},
        workflow_step={
            "tool_name": "prodigal_annotate",
            "objective": "Annotate assembled ancestor reference",
        },
        analysis_spec=analysis_spec,
    )
    align_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bwa_mem_align", "arguments": {}},
        workflow_step={
            "tool_name": "bwa_mem_align",
            "branch_id": "ancestor",
            "objective": "Align ancestor reads to the assembled reference",
        },
        analysis_spec=analysis_spec,
    )

    assert prodigal_step["arguments"]["input_fasta"] == str(
        resolved_dir / "assembly/contigs.fasta"
    )
    assert align_step["arguments"]["reference_fasta"] == str(
        resolved_dir / "assembly/contigs.fasta"
    )


def test_bind_evolution_bwa_prefers_branch_id_over_objective_mentioning_ancestor_fix_16() -> None:
    """Fix #16: the evolution binder must route by branch_id FIRST. Prior code
    checked `"ancestor" in ctx.objective` before branch_id, which wrongly
    bound evol1 steps to ancestor reads/output whenever the objective
    phrased the reference as "the assembled ancestor scaffold reference".
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix16")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "requested_data_root": "/tmp/fix16_data",
    }

    # bwa_mem_align for evol1 with objective that mentions "ancestor" (the
    # reference scaffold, not the sample). Must bind evol1 reads/output.
    align_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bwa_mem_align", "arguments": {}},
        workflow_step={
            "tool_name": "bwa_mem_align",
            "branch_id": "evol1",
            "objective": "Align evolved line 1 reads to the assembled ancestor scaffold reference",
        },
        analysis_spec=analysis_spec,
    )
    assert align_step["arguments"]["output_bam"] == str(resolved_dir / "alignments/evol1_aligned.bam")
    assert "evol1_R1.fastq.gz" in align_step["arguments"]["reads_1"]
    assert "evol1_R2.fastq.gz" in align_step["arguments"]["reads_2"]
    assert "anc_R1.fastq.gz" not in align_step["arguments"]["reads_1"]

    # Same defense for evol2.
    align_step_2 = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bwa_mem_align", "arguments": {}},
        workflow_step={
            "tool_name": "bwa_mem_align",
            "branch_id": "evol2",
            "objective": "Align evol2 reads to the assembled ancestor reference scaffold",
        },
        analysis_spec=analysis_spec,
    )
    assert align_step_2["arguments"]["output_bam"] == str(resolved_dir / "alignments/evol2_aligned.bam")
    assert "evol2_R1.fastq.gz" in align_step_2["arguments"]["reads_1"]


def test_bind_evolution_freebayes_prefers_branch_id_over_objective_mentioning_ancestor_fix_16() -> None:
    """Fix #16: freebayes_call routing must also prefer branch_id."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix16_fb")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    # freebayes_call for evol1 with objective mentioning "ancestor" (the reference).
    fb_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "freebayes_call", "arguments": {}},
        workflow_step={
            "tool_name": "freebayes_call",
            "branch_id": "evol1",
            "objective": "Call variants for evol1 against the ancestor reference scaffold",
        },
        analysis_spec=analysis_spec,
    )
    assert fb_step["arguments"]["input_bam"] == str(resolved_dir / "alignments/evol1_aligned.bam")
    assert fb_step["arguments"]["output_vcf"] == str(resolved_dir / "variants/evol1_raw.vcf")


def test_bind_evolution_bwa_still_routes_ancestor_when_branch_id_empty_fix_16() -> None:
    """Fix #16: regression — when branch_id is empty/missing, fall back to
    the objective-based heuristic so legacy ancestor steps (which only
    name the sample in the objective) still bind correctly."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix16_legacy")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    # No branch_id, objective mentions "ancestor" → must still bind to anc_*.
    align_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bwa_mem_align", "arguments": {}},
        workflow_step={
            "tool_name": "bwa_mem_align",
            "objective": "Align the ancestor reads to the assembled reference",
        },
        analysis_spec=analysis_spec,
    )
    assert align_step["arguments"]["output_bam"] == str(resolved_dir / "alignments/anc_aligned.bam")

    # Explicit branch_id="ancestor" or "anc" → bind to anc_*.
    align_step_b = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bwa_mem_align", "arguments": {}},
        workflow_step={
            "tool_name": "bwa_mem_align",
            "branch_id": "ancestor",
            "objective": "Align ancestor reads to assembled reference",
        },
        analysis_spec=analysis_spec,
    )
    assert align_step_b["arguments"]["output_bam"] == str(resolved_dir / "alignments/anc_aligned.bam")


def test_bind_evolution_bcftools_filter_run_rebinds_branch_paths_fix_17() -> None:
    """Fix #17: when the LLM pivots from bash_run to the typed
    bcftools_filter_run wrapper, it often invents input_vcf paths rooted at
    the raw data dir. The binder must rebind input_vcf/output_vcf to the
    deterministic selected/variants scaffold based on branch_id.
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix17_filter")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    # evol1 with the wrong-path variant the LLM tends to emit.
    evol1_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_filter_run",
            "arguments": {
                "input_vcf": "/benchmarks/bioagent-bench/tasks/evolution/data/evol1_raw.vcf",
                "output_vcf": "/benchmarks/bioagent-bench/tasks/evolution/data/evol1_filtered.vcf.gz",
            },
        },
        workflow_step={
            "tool_name": "bcftools_filter_run",
            "branch_id": "evol1",
            "objective": "Filter the evolved line 1 raw variants for comparison-ready VCF",
        },
        analysis_spec=analysis_spec,
    )
    assert evol1_step["arguments"]["input_vcf"] == str(resolved_dir / "variants/evol1_raw.vcf")
    assert evol1_step["arguments"]["output_vcf"] == str(resolved_dir / "variants/evol1.filtered.vcf.gz")
    assert "filter_expression" in evol1_step["arguments"]

    # evol2.
    evol2_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bcftools_filter_run", "arguments": {}},
        workflow_step={
            "tool_name": "bcftools_filter_run",
            "branch_id": "evol2",
            "objective": "Filter evol2 raw variants",
        },
        analysis_spec=analysis_spec,
    )
    assert evol2_step["arguments"]["input_vcf"] == str(resolved_dir / "variants/evol2_raw.vcf")
    assert evol2_step["arguments"]["output_vcf"] == str(resolved_dir / "variants/evol2.filtered.vcf.gz")

    # ancestor via explicit branch_id.
    anc_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bcftools_filter_run", "arguments": {}},
        workflow_step={
            "tool_name": "bcftools_filter_run",
            "branch_id": "ancestor",
            "objective": "Filter ancestor raw variants",
        },
        analysis_spec=analysis_spec,
    )
    assert anc_step["arguments"]["input_vcf"] == str(resolved_dir / "variants/anc_raw.vcf")
    assert anc_step["arguments"]["output_vcf"] == str(resolved_dir / "variants/anc.filtered.vcf.gz")


def test_bind_evolution_bcftools_isec_run_rebinds_branch_paths_fix_17() -> None:
    """Fix #17: the typed bcftools_isec_run wrapper also needs path rebinding
    when the LLM pivots from bash_run. isec compares an evolved filtered VCF
    against the ancestor filtered VCF and writes the ancestor-subtracted set.
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix17_isec")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    isec_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_isec_run",
            "arguments": {"input_vcfs": ["/bogus/a.vcf", "/bogus/b.vcf"]},
        },
        workflow_step={
            "tool_name": "bcftools_isec_run",
            "branch_id": "evol1",
            "objective": "Subtract ancestor variants from evol1 filtered set",
        },
        analysis_spec=analysis_spec,
    )
    assert isec_step["arguments"]["input_vcfs"] == [
        str(resolved_dir / "variants/evol1.filtered.vcf.gz"),
        str(resolved_dir / "variants/anc.filtered.vcf.gz"),
    ]
    assert isec_step["arguments"]["output_dir"] == str(
        resolved_dir / "variants/.isec_evol1.ancestor_subtracted"
    )
    assert isec_step["arguments"]["output_vcf"] == str(
        resolved_dir / "variants/evol1.ancestor_subtracted.vcf.gz"
    )
    assert isec_step["arguments"].get("mode") == "complement"


def test_bind_step_spec_for_strict_mode_rewrites_generic_evolution_subtract_step() -> None:
    selected_dir = Path("/tmp/official_runs/evolution/attempt1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    subtract_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    f"cd {selected_dir} && mkdir -p filtered && "
                    "bcftools isec -w1 -p filtered -n=2 filtered/anc_filtered.vcf.gz filtered/evol1_filtered.vcf.gz"
                ),
            },
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Subtract ancestor variants from each evolved line before comparison",
        },
        analysis_spec=analysis_spec,
    )

    command = subtract_step["arguments"]["command"]
    assert f"{resolved_dir}/variants/evol1.ancestor_subtracted.vcf.gz" in command
    assert f"{resolved_dir}/variants/evol2.ancestor_subtracted.vcf.gz" in command
    assert f"{resolved_dir}/variants/anc.filtered.vcf.gz" in command


def test_bind_step_spec_for_strict_mode_preserves_export_role_for_annotated_shared_variants() -> None:
    selected_dir = Path("/tmp/official_runs/evolution/attempt1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    export_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {
                "command": "bcftools isec ...",
            },
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": (
                "Intersect the ancestor-subtracted annotated evolved callsets and "
                "write a comma-separated final CSV with the exact required columns"
            ),
        },
        analysis_spec=analysis_spec,
    )

    command = export_step["arguments"]["command"]
    assert "export_shared_variants_csv.py" in command
    assert f"{resolved_dir}/variants/evol1.annotated.vcf" in command
    assert f"{resolved_dir}/variants/evol2.annotated.vcf" in command
    assert f"{resolved_dir}/variants/evol1.annotated.normalized.vcf.gz" in command
    assert f"{resolved_dir}/variants/evol2.annotated.normalized.vcf.gz" in command
    assert f"{resolved_dir}/final/variants_shared.csv" in command
    assert "--header-case upper" in command
    assert "ancestor_subtracted.vcf.gz" not in command
    assert "bcftools norm" in command


def test_bind_step_spec_for_strict_mode_binds_variant_annotation_custom_reference() -> None:
    selected_dir = Path("/tmp/official_runs/variant-annotation/attempt1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    snpeff_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "snpeff_annotate", "arguments": {}},
        workflow_step={
            "tool_name": "snpeff_annotate",
            "objective": "Annotate the provided input VCF with SnpEff using the supplied local FASTA and GFF annotation",
        },
        analysis_spec=analysis_spec,
    )

    assert snpeff_step["arguments"]["genome_db"] == "custom_ref"
    assert snpeff_step["arguments"]["config_dir"] == str(resolved_dir / "output" / "snpeff_custom_db")
    assert snpeff_step["arguments"]["input_vcf"] == str(
        resolved_dir.parent.parent.parent / "tasks" / "variant-annotation" / "data" / "input_variants.vcf"
    )
    assert snpeff_step["arguments"]["reference_fasta"] == str(
        resolved_dir.parent.parent.parent / "tasks" / "variant-annotation" / "data" / "reference.fa"
    )
    assert snpeff_step["arguments"]["annotation_gff"] == str(
        resolved_dir.parent.parent.parent / "tasks" / "variant-annotation" / "data" / "genes.gff"
    )
    assert snpeff_step["arguments"]["output_vcf"] == str(resolved_dir / "output" / "annotated.vcf")


def test_bind_step_spec_for_strict_mode_binds_variant_annotation_filter_command() -> None:
    selected_dir = Path("/tmp/official_runs/variant-annotation/attempt1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    filter_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {}},
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Filter the annotated VCF to keep only HIGH and MODERATE impact variants and write the requested filtered VCF",
        },
        analysis_spec=analysis_spec,
    )

    command = filter_step["arguments"]["command"]
    assert "SnpSift filter" in command
    assert str(resolved_dir / "output" / "annotated.vcf") in command
    assert str(resolved_dir / "output" / "filtered_pathogenic.vcf") in command


def test_bind_step_spec_for_strict_mode_binds_phylogenetics_helper_command(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "phylogenetics" / "attempt1"
    resolved_dir = selected_dir.resolve(strict=False)
    data_dir = tmp_path / "tasks" / "phylogenetics" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sequences.fasta").write_text(">a\nAAAA\n>b\nAAAT\n>c\nTTTT\n", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "phylogenetics",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    tree_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {}},
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Infer a phylogenetic tree from the provided sequences and write the requested Newick output",
        },
        analysis_spec=analysis_spec,
    )

    command = tree_step["arguments"]["command"]
    assert command.startswith("python3 ")
    assert "infer_phylogeny_biopython.py" in command
    assert str(data_dir / "sequences.fasta") in command
    assert str(resolved_dir / "final" / "phylogeny.treefile") in command


def test_bind_step_spec_for_strict_mode_binds_metagenomics_helper_command(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "metagenomics" / "attempt1"
    data_dir = tmp_path / "tasks" / "metagenomics" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sample_R1.fastq.gz").write_bytes(b"")
    (data_dir / "sample_R2.fastq.gz").write_bytes(b"")
    analysis_spec = {
        "analysis_type": "metagenomics_classification",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    report_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {}},
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Classify the paired-end metagenomics reads and write the requested Kraken-style report",
        },
        analysis_spec=analysis_spec,
    )

    command = report_step["arguments"]["command"]
    assert "PYTHONPATH=" in command
    assert "classify_metagenomics_kmer.py" in command
    assert str(data_dir / "sample_R1.fastq.gz") in command
    assert str(data_dir / "sample_R2.fastq.gz") in command
    assert "benchmark_data/metagenomics/references" in command
    assert str((selected_dir / "output" / "sample_kraken2_report.txt").resolve(strict=False)) in command


def test_bind_step_spec_for_strict_mode_binds_metagenomics_spades_step(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "metagenomics" / "attempt1"
    data_dir = tmp_path / "tasks" / "metagenomics" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sample_R1.fastq.gz").write_bytes(b"")
    (data_dir / "sample_R2.fastq.gz").write_bytes(b"")
    analysis_spec = {
        "analysis_type": "metagenomics_classification",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    assembly_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "spades_assemble", "arguments": {}},
        workflow_step={
            "tool_name": "spades_assemble",
            "objective": "Assemble the paired-end metagenomics reads with metaSPAdes and write the requested contigs FASTA",
        },
        analysis_spec=analysis_spec,
    )

    arguments = assembly_step["arguments"]
    assert arguments["reads_1"] == str(data_dir / "sample_R1.fastq.gz")
    assert arguments["reads_2"] == str(data_dir / "sample_R2.fastq.gz")
    assert arguments["output_dir"] == str((selected_dir / "assembly" / "metaspades").resolve(strict=False))
    assert arguments["meta_mode"] is True
    assert arguments["threads"] == 8
    assert arguments["memory_gb"] == 32


def test_bind_step_spec_for_strict_mode_binds_germline_variant_paths(tmp_path: Path) -> None:
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

    call_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "gatk_haplotypecaller",
            "arguments": {
                "input_bam": "scratch/sample.bam",
                "output_vcf": "scratch/variants.g.vcf",
                "emit_ref_confidence": "GVCF",
                "mode": "GVCF",
            },
        },
        workflow_step={
            "tool_name": "gatk_haplotypecaller",
            "objective": "Call germline variants",
        },
        analysis_spec=analysis_spec,
    )

    arguments = call_step["arguments"]
    assert arguments["reference_fasta"] == str((data_dir / "ref_genome.fa").resolve(strict=False))
    assert arguments["input_bam"] == str((selected_dir / "intermediate" / "aligned_sorted_markdup.bam").resolve(strict=False))
    assert arguments["output_vcf"] == str((selected_dir / "final" / "variants.vcf").resolve(strict=False))
    assert "emit_ref_confidence" not in arguments
    assert "mode" not in arguments

    benchmark_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {"command": "hap.py truth.vcf final/variants.vcf -o benchmark"},
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Benchmark with hap.py",
        },
        analysis_spec=analysis_spec,
    )
    assert "Validated germline VCF" in benchmark_step["arguments"]["command"]
    assert "hap.py" not in benchmark_step["arguments"]["command"]


def test_bind_step_spec_for_strict_mode_binds_single_cell_scaffold(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "single-cell" / "attempt1"
    data_dir = tmp_path / "tasks" / "single-cell" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sample_R1.fastq").write_text("", encoding="utf-8")
    (data_dir / "sample_R2.fastq").write_text("", encoding="utf-8")
    (data_dir / "barcodes_whitelist.txt").write_text("AAAA\n", encoding="utf-8")
    (data_dir / "reference.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (data_dir / "annotation.gtf").write_text("", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "single_cell_rna_seq",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "sc_count_and_cluster", "arguments": {}},
        workflow_step={
            "tool_name": "sc_count_and_cluster",
            "objective": "Demultiplex, count UMIs, cluster cells, and find marker genes",
        },
        analysis_spec=analysis_spec,
    )

    arguments = step["arguments"]
    assert arguments["r1"] == str((data_dir / "sample_R1.fastq").resolve(strict=False))
    assert arguments["r2"] == str((data_dir / "sample_R2.fastq").resolve(strict=False))
    assert arguments["whitelist"] == str((data_dir / "barcodes_whitelist.txt").resolve(strict=False))
    assert arguments["reference"] == str((data_dir / "reference.fa").resolve(strict=False))
    assert arguments["gtf"] == str((data_dir / "annotation.gtf").resolve(strict=False))
    assert arguments["output_dir"] == str(selected_dir.resolve(strict=False))


def test_bind_step_spec_for_strict_mode_allows_single_cell_without_whitelist(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "single-cell" / "attempt1"
    data_dir = tmp_path / "tasks" / "single-cell" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sample_R1.fastq.gz").write_bytes(b"")
    (data_dir / "sample_R2.fastq.gz").write_bytes(b"")
    (data_dir / "genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (data_dir / "genes.gtf").write_text("", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "single_cell_rna_seq",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "sc_count_and_cluster", "arguments": {"whitelist": "/tmp/missing.txt"}},
        workflow_step={
            "tool_name": "sc_count_and_cluster",
            "objective": "Demultiplex, count UMIs, cluster cells, and find marker genes",
        },
        analysis_spec=analysis_spec,
    )

    arguments = step["arguments"]
    assert arguments["r1"] == str((data_dir / "sample_R1.fastq.gz").resolve(strict=False))
    assert arguments["r2"] == str((data_dir / "sample_R2.fastq.gz").resolve(strict=False))
    assert arguments["reference"] == str((data_dir / "genome.fa").resolve(strict=False))
    assert arguments["gtf"] == str((data_dir / "genes.gtf").resolve(strict=False))
    assert "whitelist" not in arguments


def test_bind_step_spec_for_strict_mode_binds_viral_helper_command(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "viral-metagenomics" / "attempt1"
    data_dir = tmp_path / "tasks" / "viral-metagenomics" / "data"
    refs_dir = tmp_path / "tasks" / "viral-metagenomics" / "references"
    data_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sample_R1.fastq.gz").write_bytes(b"")
    (data_dir / "sample_R2.fastq.gz").write_bytes(b"")
    analysis_spec = {
        "analysis_type": "viral_metagenomics",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    report_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {}},
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Classify the paired-end reads against the staged viral reference panel and write coverage and detection outputs",
        },
        analysis_spec=analysis_spec,
    )

    command = report_step["arguments"]["command"]
    assert "PYTHONPATH=" in command
    assert "classify_viral_reads_kmer.py" in command
    assert str(data_dir / "sample_R1.fastq.gz") in command
    assert str(data_dir / "sample_R2.fastq.gz") in command
    assert str(refs_dir.resolve(strict=False)) in command
    assert str((selected_dir / "output" / "classification_report.tsv").resolve(strict=False)) in command
    assert str((selected_dir / "output" / "detected_viruses.txt").resolve(strict=False)) in command


def test_bind_step_spec_for_strict_mode_preserves_cystic_fibrosis_steps() -> None:
    selected_dir = Path("/tmp/official_runs/cystic-fibrosis/attempt1")
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "biological_objective": "Identify the causal recessive CFTR variant in affected siblings.",
        "context_facts": ["recessive family-segregation filter", "clinical relevance filtering"],
    }

    snpeff_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "snpeff_annotate",
            "arguments": {
                "genome_db": "GRCh37.75",
                "input_vcf": "/tmp/input/ex1.eff.vcf",
                "output_vcf": "/tmp/out/annotated.vcf",
            },
        },
        workflow_step={
            "tool_name": "snpeff_annotate",
            "objective": "Annotate variants with SnpEff if the input VCF is not already annotated",
        },
        analysis_spec=analysis_spec,
    )
    assert snpeff_step["arguments"]["genome_db"] == "GRCh37.75"
    assert snpeff_step["arguments"]["input_vcf"] == "/tmp/input/ex1.eff.vcf"
    assert snpeff_step["arguments"]["output_vcf"] == "/tmp/out/annotated.vcf"
    assert "config_dir" not in snpeff_step["arguments"]

    bash_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {"command": "python3 filter_family.py --input /tmp/input/ex1.eff.vcf --output /tmp/out/filtered.vcf"},
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Filter for recessive segregation across affected siblings and parents",
        },
        analysis_spec=analysis_spec,
    )
    command = bash_step["arguments"]["command"]
    assert str(selected_dir / "intermediate" / "filtered_variants.csv") in command
    assert "if line.startswith('#CHROM')" in command
    assert "clinical_significance" in command


def test_bind_step_spec_for_strict_mode_normalizes_cystic_fibrosis_step_handoffs() -> None:
    selected_dir = Path("/tmp/official_runs/cystic-fibrosis/attempt1")
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "biological_objective": "Identify the causal recessive CFTR variant in affected siblings.",
        "context_facts": ["recessive family-segregation filter", "clinical relevance filtering"],
    }

    join_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    "intermediate_filtered = os.path.join(output_dir, \"filtered_recessive_variants.tsv\")\n"
                    "clinvar_output = os.path.join(output_dir, \"clinvar_joined_variants.tsv\")\n"
                    "reader = csv.DictReader(f, delimiter='\\t')\n"
                    "key = (row['chromosome'], row['position'], row['reference'], row['alternate'])\n"
                    "writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter='\\t')\n"
                ),
            },
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Join ClinVar annotations when a matching local ClinVar VCF is available",
        },
        analysis_spec=analysis_spec,
    )
    command = join_step["arguments"]["command"]
    assert 'filtered_variants.csv' in command
    assert 'clinvar_annotated_variants.csv' in command
    assert "reader = csv.DictReader(handle)" in command
    assert "clinical_significance" in command
    assert "row.get('clinical_significance'" in command


def test_bind_step_spec_for_strict_mode_builds_cystic_fibrosis_canonical_bash_roles() -> None:
    selected_dir = Path("/tmp/official_runs/cystic-fibrosis/attempt1")
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "biological_objective": "Identify the causal recessive CFTR variant in affected siblings.",
        "context_facts": ["recessive family-segregation filter", "clinical relevance filtering"],
    }

    step2 = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {"command": "python3 old_step2.py"}},
        workflow_step={"tool_name": "bash_run", "objective": "Filter for recessive segregation across affected siblings and parents"},
        analysis_spec=analysis_spec,
    )
    step2_command = step2["arguments"]["command"]
    assert str(selected_dir / "intermediate" / "filtered_variants.csv") in step2_command
    assert "if line.startswith('#CHROM')" in step2_command
    assert "clinical_significance" in step2_command

    step3 = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {"command": "python3 old_step3.py"}},
        workflow_step={"tool_name": "bash_run", "objective": "Join ClinVar annotations when a matching local ClinVar VCF is available"},
        analysis_spec=analysis_spec,
    )
    step3_command = step3["arguments"]["command"]
    assert str(selected_dir / "intermediate" / "filtered_variants.csv") in step3_command
    assert str(selected_dir / "intermediate" / "clinvar_annotated_variants.csv") in step3_command
    assert "clinical_significance" in step3_command

    step4 = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {"command": "python3 old_step4.py"}},
        workflow_step={"tool_name": "bash_run", "objective": "Export the final clinically relevant CSV"},
        analysis_spec=analysis_spec,
    )
    step4_command = step4["arguments"]["command"]
    assert str(selected_dir / "intermediate" / "clinvar_annotated_variants.csv") in step4_command
    assert str(selected_dir / "final" / "cf_variants.csv") in step4_command


def test_bind_step_spec_for_strict_mode_normalizes_paraphrased_cystic_fibrosis_repair_steps() -> None:
    selected_dir = Path("/tmp/official_runs/cystic-fibrosis/attempt1")
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "biological_objective": "Identify the causal recessive CFTR variant in affected siblings.",
        "context_facts": ["recessive family-segregation filter", "clinical relevance filtering"],
    }

    filter_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {"command": "python3 repaired_step2.py"}},
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Filter variants for recessive segregation pattern consistent with affected siblings and parents",
        },
        analysis_spec=analysis_spec,
    )
    filter_command = filter_step["arguments"]["command"]
    assert str(selected_dir / "intermediate" / "filtered_variants.csv") in filter_command
    assert "if any(_gt(sample) != '1/1' for sample in affected)" in filter_command

    clinvar_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    "python3 - <<'EOF'\n"
                    "clinvar_vcf = '/tmp/clinvar.vcf.gz'\n"
                    "clinical_significance = 'CLNSIG'\n"
                    "review_status = 'CLNREVSTAT'\n"
                    "EOF"
                )
            },
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Join local ClinVar annotations and attach clinical significance fields",
        },
        analysis_spec=analysis_spec,
    )
    clinvar_command = clinvar_step["arguments"]["command"]
    assert str(selected_dir / "intermediate" / "filtered_variants.csv") in clinvar_command
    assert str(selected_dir / "intermediate" / "clinvar_annotated_variants.csv") in clinvar_command
    assert "Joined ClinVar annotations" in clinvar_command

    export_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {"command": "python3 export_cf.py --output cf_variants.csv"},
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Export the final CSV with the required 16 columns for cf_variants.csv",
        },
        analysis_spec=analysis_spec,
    )
    export_command = export_step["arguments"]["command"]
    assert str(selected_dir / "intermediate" / "clinvar_annotated_variants.csv") in export_command
    assert str(selected_dir / "final" / "cf_variants.csv") in export_command
    assert "Exported {len(rows)} CFTR variants" in export_command


def test_bind_step_spec_for_strict_mode_uses_cystic_fibrosis_step_id_fallback_roles() -> None:
    selected_dir = Path("/tmp/official_runs/cystic-fibrosis/attempt1")
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "biological_objective": "Identify the causal recessive CFTR variant in affected siblings.",
    }

    filter_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "step_id": 2,
            "arguments": {
                "command": (
                    "python3 - <<'EOF'\n"
                    "family_description = '/tmp/family_description.txt'\n"
                    "output_csv = '/tmp/intermediate/filtered.csv'\n"
                    "print('Filtered recessive CFTR variants')\n"
                    "EOF"
                )
            },
        },
        workflow_step={"tool_name": "bash_run", "objective": ""},
        analysis_spec=analysis_spec,
    )
    assert str(selected_dir / "intermediate" / "filtered_variants.csv") in filter_step["arguments"]["command"]

    clinvar_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "step_id": 3,
            "arguments": {
                "command": (
                    "python3 - <<'EOF'\n"
                    "clinvar_lookup = {}\n"
                    "output_csv = '/tmp/intermediate/clinvar.csv'\n"
                    "print('join complete')\n"
                    "EOF"
                )
            },
        },
        workflow_step={"tool_name": "bash_run", "objective": ""},
        analysis_spec=analysis_spec,
    )
    assert str(selected_dir / "intermediate" / "clinvar_annotated_variants.csv") in clinvar_step["arguments"]["command"]

    export_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "step_id": 4,
            "arguments": {
                "command": (
                    "python3 - <<'EOF'\n"
                    "output_csv = '/tmp/intermediate/clinvar.csv'\n"
                    "print('still wrong export')\n"
                    "EOF"
                )
            },
        },
        workflow_step={"tool_name": "bash_run", "objective": ""},
        analysis_spec=analysis_spec,
    )
    assert str(selected_dir / "final" / "cf_variants.csv") in export_step["arguments"]["command"]


def test_rebind_direct_plan_for_strict_mode_rewrites_evolution_direct_export_step() -> None:
    selected_dir = Path("/tmp/official_runs/evolution/attempt1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }
    plan = {
        "plan": [
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "output_bam": str(selected_dir / "alignments" / "anc.bam"),
                    "reads_1": "/tmp/anc_R1.fastq.gz",
                    "reads_2": "/tmp/anc_R2.fastq.gz",
                    "reference_fasta": str(selected_dir / "assembly" / "contigs.fasta"),
                },
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir / 'final'} && cd {selected_dir / 'calls'} && "
                        f"bcftools norm -f {selected_dir / 'assembly' / 'contigs.fasta'} evol1_annotated.vcf -Oz -o evol1_norm.vcf.gz && "
                        f"bcftools norm -f {selected_dir / 'assembly' / 'contigs.fasta'} evol2_annotated.vcf -Oz -o evol2_norm.vcf.gz && "
                        "bcftools isec -p . -n=2 evol1_norm.vcf.gz evol2_norm.vcf.gz -Oz -o shared_norm.vcf.gz && "
                        f"bcftools query -f '%CHROM\\t%POS\\t%REF\\t%ALT\\t%GENE\\t%IMPACT\\t%EFFECT\\t%STATUS\\n' shared_norm.vcf.gz > {selected_dir / 'final' / 'variants_shared.csv'}"
                    )
                },
                "step_id": 2,
            },
        ]
    }

    rebound, meta = rebind_direct_plan_for_strict_mode(plan, analysis_spec=analysis_spec)

    assert meta["changed"] is True
    assert 1 in meta["changed_step_ids"]
    assert 2 in meta["changed_step_ids"]
    align_args = rebound["plan"][0]["arguments"]
    export_command = rebound["plan"][1]["arguments"]["command"]
    assert align_args["reference_fasta"] == str(resolved_dir / "assembly" / "scaffolds.fasta")
    assert align_args["output_bam"] == str(resolved_dir / "alignments" / "anc_aligned.bam")
    assert "export_shared_variants_csv.py" in export_command
    assert f"{resolved_dir}/variants/evol1.annotated.normalized.vcf.gz" in export_command
    assert f"{resolved_dir}/variants/evol2.annotated.normalized.vcf.gz" in export_command
    assert f"{resolved_dir}/final/variants_shared.csv" in export_command
    assert "--header-case upper" in export_command


def test_rebind_direct_plan_for_strict_mode_binds_evolution_fastqs_from_requested_data_root(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "ablation_results" / "domain_expansion_ablation" / "attempt" / "selected"
    data_root = tmp_path / "benchmarks" / "bioagent-bench" / "tasks" / "evolution" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "anc_R1.fastq.gz",
        "anc_R2.fastq.gz",
        "evol1_R1.fastq.gz",
        "evol1_R2.fastq.gz",
        "evol2_R1.fastq.gz",
        "evol2_R2.fastq.gz",
    ):
        (data_root / name).write_text("fastq", encoding="utf-8")

    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "requested_data_root": str(data_root),
    }
    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {},
                "step_id": 1,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "output_bam": "${OUTPUT_DIR}/ancestor_aligned.bam",
                },
                "step_id": 2,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "sample_name": "evol1",
                    "output_bam": "${OUTPUT_DIR}/evol1_aligned.bam",
                },
                "step_id": 3,
            },
        ]
    }

    rebound, meta = rebind_direct_plan_for_strict_mode(plan, analysis_spec=analysis_spec)

    assert meta["changed"] is True
    spades_args = rebound["plan"][0]["arguments"]
    anc_align_args = rebound["plan"][1]["arguments"]
    evol1_align_args = rebound["plan"][2]["arguments"]
    assert spades_args["reads_1"] == str((data_root / "anc_R1.fastq.gz").resolve(strict=False))
    assert spades_args["reads_2"] == str((data_root / "anc_R2.fastq.gz").resolve(strict=False))
    assert anc_align_args["reads_1"] == str((data_root / "anc_R1.fastq.gz").resolve(strict=False))
    assert anc_align_args["reads_2"] == str((data_root / "anc_R2.fastq.gz").resolve(strict=False))
    assert anc_align_args["output_bam"] == str((selected_dir / "alignments" / "anc_aligned.bam").resolve(strict=False))
    assert evol1_align_args["reads_1"] == str((data_root / "evol1_R1.fastq.gz").resolve(strict=False))
    assert evol1_align_args["reads_2"] == str((data_root / "evol1_R2.fastq.gz").resolve(strict=False))
    assert evol1_align_args["output_bam"] == str((selected_dir / "alignments" / "evol1_aligned.bam").resolve(strict=False))


def test_rebind_direct_plan_for_strict_mode_rebinds_branch_local_evolution_steps(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "ablation_results" / "domain_expansion_ablation" / "attempt" / "selected"
    data_root = tmp_path / "benchmarks" / "bioagent-bench" / "tasks" / "evolution" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "anc_R1.fastq.gz",
        "anc_R2.fastq.gz",
        "evol1_R1.fastq.gz",
        "evol1_R2.fastq.gz",
        "evol2_R1.fastq.gz",
        "evol2_R2.fastq.gz",
    ):
        (data_root / name).write_text("fastq", encoding="utf-8")

    output_dir = str(selected_dir.resolve(strict=False))
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "requested_data_root": str(data_root),
    }
    plan = {
        "plan": [
            {"tool_name": "spades_assemble", "arguments": {"careful": True}, "step_id": 1},
            {
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": "$OUTPUT_DIR/ancestor_assembled.fasta",
                    "output_gff": "$OUTPUT_DIR/ancestor_genes.gff",
                    "output_faa": "$OUTPUT_DIR/ancestor_proteins.faa",
                },
                "step_id": 2,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": "${OUTPUT_DIR}/ancestor_assembled.fasta",
                    "reads_1": "${OUTPUT_DIR}/ancestor_R1.fastq.gz",
                    "reads_2": "${OUTPUT_DIR}/ancestor_R2.fastq.gz",
                    "output_bam": "${OUTPUT_DIR}/ancestor_aligned.bam",
                },
                "step_id": 3,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "${OUTPUT_DIR}/ancestor_assembled.fasta",
                    "input_bam": "${OUTPUT_DIR}/ancestor_aligned.bam",
                    "output_vcf": "${OUTPUT_DIR}/ancestor_variants.vcf",
                    "ploidy": 1,
                },
                "step_id": 4,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": "${OUTPUT_DIR}/ancestor_assembled.fasta",
                    "reads_1": "${EVOL1_READS_R1}",
                    "reads_2": "${EVOL1_READS_R2}",
                    "output_bam": "${OUTPUT_DIR}/evol1_aligned.bam",
                    "sample_name": "evol1",
                },
                "step_id": 5,
            },
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": "${OUTPUT_DIR}/ancestor_assembled.fasta",
                    "reads_1": "${EVOL2_READS_R1}",
                    "reads_2": "${EVOL2_READS_R2}",
                    "output_bam": "${OUTPUT_DIR}/evol2_aligned.bam",
                    "sample_name": "evol2",
                },
                "step_id": 6,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "${OUTPUT_DIR}/ancestor_assembled.fasta",
                    "input_bam": "${OUTPUT_DIR}/evol1_aligned.bam",
                    "output_vcf": "${OUTPUT_DIR}/evol1_raw.vcf",
                    "ploidy": 1,
                },
                "step_id": 7,
            },
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "${OUTPUT_DIR}/ancestor_assembled.fasta",
                    "input_bam": "${OUTPUT_DIR}/evol2_aligned.bam",
                    "output_vcf": "${OUTPUT_DIR}/evol2_raw_variants.vcf",
                    "ploidy": 1,
                },
                "step_id": 8,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "set -euo pipefail\n"
                        f"OUTPUT_DIR=\"{output_dir}\"\n"
                        "ANCESTOR_VCF=\"${OUTPUT_DIR}/ancestor_variants.vcf\"\n"
                        "EVOL1_VCF=\"${OUTPUT_DIR}/evol1_variants.vcf\"\n"
                        "EVOL2_VCF=\"${OUTPUT_DIR}/evol2_variants.vcf\"\n"
                        "bcftools view -f PASS -i 'QUAL>=30 && DP>=10' \"${ANCESTOR_VCF}\" -Oz -o "
                        "\"${OUTPUT_DIR}/ancestor_variants_filtered.vcf.gz\"\n"
                        "bcftools view -f PASS -i 'QUAL>=30 && DP>=10' \"${EVOL1_VCF}\" -Oz -o "
                        "\"${OUTPUT_DIR}/evol1_variants_filtered.vcf.gz\"\n"
                        "bcftools view -f PASS -i 'QUAL>=30 && DP>=10' \"${EVOL2_VCF}\" -Oz -o "
                        "\"${OUTPUT_DIR}/evol2_variants_filtered.vcf.gz\"\n"
                    )
                },
                "step_id": 9,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "set -euo pipefail\n"
                        f"OUTPUT_DIR=\"{output_dir}\"\n"
                        "EVOL1_VCF=\"${OUTPUT_DIR}/evol1_variants_filtered.vcf.gz\"\n"
                        "ANC_VCF=\"${OUTPUT_DIR}/ancestor_variants_filtered.vcf.gz\"\n"
                        "EVOL1_SUBTRACTED_VCF=\"${OUTPUT_DIR}/evol1_subtracted_variants.vcf.gz\"\n"
                        "bcftools isec -n +1 -O v -o \"${OUTPUT_DIR}/tmp.vcf\" \"${EVOL1_VCF}\" \"${ANC_VCF}\"\n"
                    )
                },
                "step_id": 10,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "set -euo pipefail\n"
                        f"OUTPUT_DIR=\"{output_dir}\"\n"
                        "ANC_VCF=\"$OUTPUT_DIR/ancestor_variants_filtered.vcf.gz\"\n"
                        "EVOL2_VCF=\"$OUTPUT_DIR/evol2_variants_filtered.vcf.gz\"\n"
                        "EVOL2_SUBTRACTED_VCF=\"$OUTPUT_DIR/evol2_subtracted.vcf.gz\"\n"
                        "ISec_DIR=\"$OUTPUT_DIR/isec_step11\"\n"
                        "bcftools isec -n -1 -p \"$ISec_DIR\" \"$EVOL2_VCF\" \"$ANC_VCF\"\n"
                    )
                },
                "step_id": 11,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "custom_ancestor",
                    "reference_fasta": "${OUTPUT_DIR}/ancestor_assembled.fasta",
                    "annotation_gff": "${OUTPUT_DIR}/ancestor_assembled.gff",
                    "input_vcf": "${OUTPUT_DIR}/evol1_subtracted_variants.vcf",
                    "output_vcf": "${OUTPUT_DIR}/evol1_annotated.vcf",
                },
                "step_id": 12,
            },
            {
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "custom_ancestor",
                    "reference_fasta": "${OUTPUT_DIR}/ancestor_assembled.fasta",
                    "annotation_gff": "${OUTPUT_DIR}/ancestor_assembled.gff",
                    "input_vcf": "${EVOL2_SUBTRACTED_VCF}",
                    "output_vcf": "${EVOL2_ANNOTATED_VCF}",
                },
                "step_id": 13,
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"OUTPUT_DIR=\"{output_dir}\"\n"
                        "ANNOTATED_EVOL1=\"$OUTPUT_DIR/evol1_annotated.vcf\"\n"
                        "ANNOTATED_EVOL2=\"$OUTPUT_DIR/evol2_annotated.vcf\"\n"
                        "REF_FASTA=\"$OUTPUT_DIR/ancestor_assembled.fasta\"\n"
                        "bcftools norm -f \"$REF_FASTA\" -m -any \"$ANNOTATED_EVOL1\" -O v -o \"$OUTPUT_DIR/evol1_normalized.vcf\"\n"
                        "bcftools norm -f \"$REF_FASTA\" -m -any \"$ANNOTATED_EVOL2\" -O v -o \"$OUTPUT_DIR/evol2_normalized.vcf\"\n"
                        "bcftools isec -p \"$OUTPUT_DIR/isec_out\" \"$OUTPUT_DIR/evol1_filtered.vcf\" \"$OUTPUT_DIR/evol2_filtered.vcf\"\n"
                    )
                },
                "step_id": 14,
            },
        ]
    }

    rebound, meta = rebind_direct_plan_for_strict_mode(plan, analysis_spec=analysis_spec)

    assert meta["changed"] is True
    evol1_align_args = rebound["plan"][4]["arguments"]
    evol2_align_args = rebound["plan"][5]["arguments"]
    evol1_call_args = rebound["plan"][6]["arguments"]
    evol2_call_args = rebound["plan"][7]["arguments"]
    evol1_subtract_command = rebound["plan"][9]["arguments"]["command"]
    evol2_subtract_command = rebound["plan"][10]["arguments"]["command"]
    evol1_annotate_args = rebound["plan"][11]["arguments"]
    evol2_annotate_args = rebound["plan"][12]["arguments"]

    assert evol1_align_args["reads_1"] == str((data_root / "evol1_R1.fastq.gz").resolve(strict=False))
    assert evol1_align_args["reads_2"] == str((data_root / "evol1_R2.fastq.gz").resolve(strict=False))
    assert evol1_align_args["output_bam"] == str((selected_dir / "alignments" / "evol1_aligned.bam").resolve(strict=False))
    assert evol2_align_args["reads_1"] == str((data_root / "evol2_R1.fastq.gz").resolve(strict=False))
    assert evol2_align_args["reads_2"] == str((data_root / "evol2_R2.fastq.gz").resolve(strict=False))
    assert evol2_align_args["output_bam"] == str((selected_dir / "alignments" / "evol2_aligned.bam").resolve(strict=False))
    assert evol1_call_args["input_bam"] == str((selected_dir / "alignments" / "evol1_aligned.bam").resolve(strict=False))
    assert evol1_call_args["output_vcf"] == str((selected_dir / "variants" / "evol1_raw.vcf").resolve(strict=False))
    assert evol2_call_args["input_bam"] == str((selected_dir / "alignments" / "evol2_aligned.bam").resolve(strict=False))
    assert evol2_call_args["output_vcf"] == str((selected_dir / "variants" / "evol2_raw.vcf").resolve(strict=False))
    assert str((selected_dir / "variants" / "evol1.ancestor_subtracted.vcf.gz").resolve(strict=False)) in evol1_subtract_command
    assert str((selected_dir / "variants" / "evol2.ancestor_subtracted.vcf.gz").resolve(strict=False)) in evol2_subtract_command
    assert evol1_annotate_args["input_vcf"] == str(
        (selected_dir / "variants" / "evol1.ancestor_subtracted.vcf.gz").resolve(strict=False)
    )
    assert evol2_annotate_args["input_vcf"] == str(
        (selected_dir / "variants" / "evol2.ancestor_subtracted.vcf.gz").resolve(strict=False)
    )
    assert evol1_annotate_args["output_vcf"] == str((selected_dir / "variants" / "evol1.annotated.vcf").resolve(strict=False))
    assert evol2_annotate_args["output_vcf"] == str((selected_dir / "variants" / "evol2.annotated.vcf").resolve(strict=False))

    issues = validate_artifact_role_invariants(
        rebound,
        selected_dir=selected_dir,
        allowed_input_roots=[str(data_root.resolve(strict=False))],
    )
    assert issues == []


def test_rebind_direct_plan_for_strict_mode_preserves_combined_evolution_subtract_step() -> None:
    selected_dir = Path("/tmp/official_runs/evolution/attempt1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"bcftools isec -C -w1 {selected_dir / 'variants' / 'evol1.filtered.vcf.gz'} "
                        f"{selected_dir / 'variants' / 'anc.filtered.vcf.gz'} -Oz -o "
                        f"{selected_dir / 'variants' / 'evol1.ancestor_subtracted.vcf.gz'} && "
                        f"tabix -f -p vcf {selected_dir / 'variants' / 'evol1.ancestor_subtracted.vcf.gz'} && "
                        f"bcftools isec -C -w1 {selected_dir / 'variants' / 'evol2.filtered.vcf.gz'} "
                        f"{selected_dir / 'variants' / 'anc.filtered.vcf.gz'} -Oz -o "
                        f"{selected_dir / 'variants' / 'evol2.ancestor_subtracted.vcf.gz'} && "
                        f"tabix -f -p vcf {selected_dir / 'variants' / 'evol2.ancestor_subtracted.vcf.gz'}"
                    )
                },
                "step_id": 10,
            }
        ]
    }

    rebound, meta = rebind_direct_plan_for_strict_mode(plan, analysis_spec=analysis_spec)

    assert meta["changed"] is True
    command = rebound["plan"][0]["arguments"]["command"]
    assert f"{resolved_dir}/variants/evol1.ancestor_subtracted.vcf.gz" in command
    assert f"{resolved_dir}/variants/evol2.ancestor_subtracted.vcf.gz" in command
    assert command.count("bcftools isec -C -w1") == 2


def test_rebind_direct_plan_for_strict_mode_recovers_partial_evolution_raw_prep_as_filter_step(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "official_runs" / "evolution" / "attempt1"
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {selected_dir / 'variants'} && "
                        f"REF={selected_dir / 'assembly' / 'scaffolds.fasta'} && "
                        f"bgzip -c {selected_dir / 'variants' / 'evol1_raw.vcf'} > {selected_dir / 'variants' / 'evol1_raw.vcf.gz'} && "
                        f"bcftools norm -f $REF -m -any {selected_dir / 'variants' / 'evol1_raw.vcf.gz'} -Oz -o {selected_dir / 'variants' / 'evol1_raw.normalized.vcf.gz'} && "
                        f"bgzip -c {selected_dir / 'variants' / 'evol2_raw.vcf'} > {selected_dir / 'variants' / 'evol2_raw.vcf.gz'} && "
                        f"bcftools norm -f $REF -m -any {selected_dir / 'variants' / 'evol2_raw.vcf.gz'} -Oz -o {selected_dir / 'variants' / 'evol2_raw.normalized.vcf.gz'}"
                    )
                },
                "step_id": 9,
            }
        ]
    }

    rebound, meta = rebind_direct_plan_for_strict_mode(plan, analysis_spec=analysis_spec)

    assert meta["changed"] is True
    command = rebound["plan"][0]["arguments"]["command"]
    assert f"{resolved_dir}/variants/anc.filtered.vcf.gz" in command
    assert f"{resolved_dir}/variants/evol1.filtered.vcf.gz" in command
    assert f"{resolved_dir}/variants/evol2.filtered.vcf.gz" in command
    assert "raw.normalized" not in command


def test_rebind_direct_plan_for_strict_mode_recovers_metagenomics_helper_command(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "metagenomics" / "attempt1"
    data_dir = tmp_path / "tasks" / "metagenomics" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sample_R1.fastq.gz").write_bytes(b"")
    (data_dir / "sample_R2.fastq.gz").write_bytes(b"")
    analysis_spec = {
        "analysis_type": "metagenomics_classification",
        "benchmark_policy": "official_bioagentbench",
        "selected_dir": str(selected_dir),
    }
    plan = {
        "plan": [
            {
                "tool_name": "spades_assemble",
                "arguments": {},
                "step_id": 1,
            },
            {
                "tool_name": "bash_run",
                "arguments": {},
                "step_id": 2,
            },
        ]
    }

    rebound, meta = rebind_direct_plan_for_strict_mode(plan, analysis_spec=analysis_spec)

    assert meta["changed"] is True
    assert meta["changed_step_ids"] == [1, 2]
    assembly_args = rebound["plan"][0]["arguments"]
    assert assembly_args["reads_1"] == str(data_dir / "sample_R1.fastq.gz")
    assert assembly_args["reads_2"] == str(data_dir / "sample_R2.fastq.gz")
    assert assembly_args["output_dir"] == str((selected_dir / "assembly" / "metaspades").resolve(strict=False))
    command = rebound["plan"][1]["arguments"]["command"]
    assert "classify_metagenomics_kmer.py" in command
    assert str((selected_dir / "output" / "sample_kraken2_report.txt").resolve(strict=False)) in command


def test_rebind_direct_plan_for_strict_mode_recovers_phylogenetics_helper_command(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "phylogenetics" / "attempt1"
    data_dir = tmp_path / "tasks" / "phylogenetics" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sequences.fasta").write_text(">a\nAAAA\n>b\nAAAT\n", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "phylogenetics",
        "benchmark_policy": "official_bioagentbench",
        "selected_dir": str(selected_dir),
    }
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {"output_dir": "output_dir"},
                "step_id": 1,
            }
        ]
    }

    rebound, meta = rebind_direct_plan_for_strict_mode(plan, analysis_spec=analysis_spec)

    assert meta["changed"] is True
    assert meta["changed_step_ids"] == [1]
    command = rebound["plan"][0]["arguments"]["command"]
    assert "infer_phylogeny_biopython.py" in command
    assert str(data_dir / "sequences.fasta") in command
    assert str((selected_dir / "final" / "phylogeny.treefile").resolve(strict=False)) in command


def test_rebind_direct_plan_for_strict_mode_preserves_populated_metagenomics_command(tmp_path: Path) -> None:
    selected_dir = tmp_path / "official_runs" / "metagenomics" / "attempt1"
    analysis_spec = {
        "analysis_type": "metagenomics_classification",
        "benchmark_policy": "official_bioagentbench",
        "selected_dir": str(selected_dir),
    }
    command = (
        f"cd {selected_dir} && kraken2 --db /tmp/references/kraken2_db "
        "--paired /tmp/sample_R1.fastq.gz /tmp/sample_R2.fastq.gz "
        "--report output/sample_kraken2_report.txt --output /dev/null --threads 16"
    )
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {"command": command, "output_dir": "."},
                "step_id": 1,
            }
        ]
    }

    rebound, meta = rebind_direct_plan_for_strict_mode(plan, analysis_spec=analysis_spec)

    assert meta == {"changed": False, "why": "already_bound"}
    assert rebound == plan


def test_bind_step_spec_for_strict_mode_prefers_evolution_export_binding_over_filter_keyword() -> None:
    selected_dir = Path("/tmp/official_runs/evolution/attempt1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    export_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "step_id": 13, "arguments": {}},
        workflow_step={
            "tool_name": "bash_run",
            "objective": (
                "Normalize the annotated evolved callsets, intersect them in the shared scaffold "
                "coordinate system, filter for moderate+ severity, and write variants_shared.csv "
                "with columns: chrom, pos, ref, alt, gene, impact, effect, status"
            ),
            "branch_id": "",
        },
        analysis_spec=analysis_spec,
    )

    command = export_step["arguments"]["command"]
    assert "export_shared_variants_csv.py" in command
    assert f"{resolved_dir}/variants/evol1.annotated.normalized.vcf.gz" in command
    assert f"{resolved_dir}/variants/evol2.annotated.normalized.vcf.gz" in command
    assert f"{resolved_dir}/final/variants_shared.csv" in command
    assert f"{resolved_dir}/variants/anc.filtered.vcf.gz" not in command


def test_bind_step_spec_for_strict_mode_prefers_cystic_fibrosis_step_id_over_stale_command_role() -> None:
    selected_dir = Path("/tmp/official_runs/cystic-fibrosis/attempt1")
    analysis_spec = {
        "analysis_type": "variant_annotation",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "biological_objective": "Identify the causal recessive CFTR variant in affected siblings.",
    }

    export_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "step_id": 4,
            "arguments": {
                "command": (
                    "python3 - <<'EOF'\n"
                    "clinvar_vcf = '/tmp/clinvar_20250521.vcf.gz'\n"
                    "output_csv = '/tmp/intermediate/clinvar_annotated_variants.csv'\n"
                    "print('Joined ClinVar annotations for repaired step')\n"
                    "EOF"
                )
            },
        },
        workflow_step={"tool_name": "bash_run", "objective": ""},
        analysis_spec=analysis_spec,
    )

    command = export_step["arguments"]["command"]
    assert str(selected_dir / "intermediate" / "clinvar_annotated_variants.csv") in command
    assert str(selected_dir / "final" / "cf_variants.csv") in command
    assert "Exported {len(rows)} CFTR variants" in command


def test_bind_step_spec_for_strict_mode_rebinds_multi_model_compare_helper(tmp_path: Path) -> None:
    from bio_harness.core.analysis_spec_support import preferred_helper_python_executable

    selected_dir = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "official_runs" / "alzheimer-mouse" / "attempt1"
    data_root = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "alzheimer-mouse" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "DEA_PS3O1S.csv").write_text("gene_name,log2fc,pval\nAPP,1.0,0.001\n", encoding="utf-8")
    (data_root / "GSE161904_Raw_gene_counts_cortex.txt").write_text("gene\tcase1\tctrl1\nENSMUSG1\t10\t1\n", encoding="utf-8")
    (data_root / "GSE168137_countList.txt").write_text("gene\tcase1\tctrl1\nENSMUSG2\t8\t2\n", encoding="utf-8")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "multi_model_dge_pathway",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    compare_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    "python /tmp/task/scripts/compare_pathways.py "
                    "--input_csv /tmp/task/data/DEA_PS3O1S.csv "
                    "--input_txt /tmp/task/data/GSE161904_Raw_gene_counts_cortex.txt "
                    "--input_txt /tmp/task/data/GSE168137_countList.txt "
                    f"--output_dir {selected_dir / 'final'}"
                )
            },
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Compare shared KEGG pathways across the three Alzheimer mouse models",
        },
        analysis_spec=analysis_spec,
    )

    command = compare_step["arguments"]["command"]
    assert "PYTHONPATH=" in command
    assert str(preferred_helper_python_executable()) in command
    assert "bio_harness/pipeline_scripts/compare_pathways.py" in command
    assert "PS3O1S=" in command
    assert "3xTG_AD=" in command
    assert "5xFAD=" in command
    assert f"{resolved_dir}/outputs/alzheimer_mouse" in command
    assert f"{resolved_dir}/final/pathway_comparison.csv" in command


def test_bind_step_spec_for_strict_mode_preserves_multi_model_compare_role_on_second_pass(tmp_path: Path) -> None:
    from bio_harness.core.analysis_spec_support import preferred_helper_python_executable

    selected_dir = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "official_runs" / "alzheimer-mouse" / "attempt1"
    data_root = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "alzheimer-mouse" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "DEA_PS3O1S.csv").write_text("gene_name,log2fc,pval\nAPP,1.0,0.001\n", encoding="utf-8")
    (data_root / "GSE161904_Raw_gene_counts_cortex.txt").write_text("gene\tcase1\tctrl1\nENSMUSG1\t10\t1\n", encoding="utf-8")
    (data_root / "GSE168137_countList.txt").write_text("gene\tcase1\tctrl1\nENSMUSG2\t8\t2\n", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "multi_model_dge_pathway",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }
    workflow_step = {
        "tool_name": "bash_run",
        "objective": (
            "Load count matrices and DE table, perform CPM filtering, normalization, "
            "and differential expression analysis for 5xFAD, 3xTG-AD, and PS3O1S models."
        ),
    }

    first_pass = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bash_run", "arguments": {"command": "python3 old_step.py"}},
        workflow_step=workflow_step,
        analysis_spec=analysis_spec,
    )
    second_pass = bind_step_spec_for_strict_mode(
        step_spec=first_pass,
        workflow_step=workflow_step,
        analysis_spec=analysis_spec,
    )

    command = second_pass["arguments"]["command"]
    assert "PYTHONPATH=" in command
    assert str(preferred_helper_python_executable()) in command
    assert "bio_harness/pipeline_scripts/compare_pathways.py" in command
    assert "Validated pathway comparison CSV" not in command


def test_bind_step_spec_for_strict_mode_rebinds_multi_model_pathway_tail_to_verification(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "official_runs" / "alzheimer-mouse" / "attempt1"
    data_root = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "alzheimer-mouse" / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    analysis_spec = {
        "analysis_type": "multi_model_dge_pathway",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    tail_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bash_run",
            "arguments": {"command": "python3 - <<'EOF'\nprint('old enrichment')\nEOF"},
        },
        workflow_step={
            "tool_name": "bash_run",
            "objective": "Perform KEGG pathway enrichment analysis and aggregate results into pathway_comparison.csv",
        },
        analysis_spec=analysis_spec,
    )

    command = tail_step["arguments"]["command"]
    assert "Validated pathway comparison CSV" in command
    assert str(selected_dir / "final" / "pathway_comparison.csv") in command
    assert "expected_columns" in command


def test_bind_step_spec_for_strict_mode_binds_rna_seq_de_scaffold(tmp_path: Path) -> None:
    selected_dir = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "official_runs" / "deseq" / "attempt1"
    data_root = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references_dir = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references_dir.mkdir(parents=True, exist_ok=True)

    (references_dir / "C_parapsilosis_CDC317_current_chromosomes.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references_dir / "C_parapsilosis_CDC317_current_features.gff").write_text("chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=gene1\n", encoding="utf-8")
    (data_root / "sample_metadata.tsv").write_text(
        "sample\tcondition\n"
        "SRR1278968\tPlankton\n"
        "SRR1278969\tPlankton\n"
        "SRR1278971\tBiofilm\n",
        encoding="utf-8",
    )
    for sample in ("SRR1278968", "SRR1278969", "SRR1278971"):
        (data_root / f"{sample}_1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    analysis_spec = {
        "analysis_type": "rna_seq_differential_expression",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    align_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "subread_align", "arguments": {}},
        workflow_step={"tool_name": "subread_align", "objective": "Align each paired-end RNA-seq sample against the reference genome"},
        analysis_spec=analysis_spec,
    )
    align_args = align_step["arguments"]
    assert "command" not in align_args
    assert align_args["reference_fasta"] == str(references_dir / "C_parapsilosis_CDC317_current_chromosomes.fasta")
    assert align_args["index_base"] == str(selected_dir / "subread_index" / "genome")
    assert align_args["reads_1"] == str(data_root / "SRR1278968_1.fastq")
    assert align_args["reads_2"] == str(data_root / "SRR1278968_2.fastq")
    assert align_args["output_bam"] == str(selected_dir / "alignments" / "SRR1278968.bam")
    assert align_args["threads"] == 8

    (selected_dir / "alignments").mkdir(parents=True, exist_ok=True)
    (selected_dir / "alignments" / "SRR1278968.bam").write_bytes(b"BAM")
    next_align_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "subread_align", "arguments": {}},
        workflow_step={"tool_name": "subread_align", "objective": "Align each paired-end RNA-seq sample against the reference genome"},
        analysis_spec=analysis_spec,
    )
    assert next_align_step["arguments"]["reads_1"] == str(data_root / "SRR1278969_1.fastq")
    assert next_align_step["arguments"]["output_bam"] == str(selected_dir / "alignments" / "SRR1278969.bam")

    hinted_align_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "subread_align", "parameter_hints": {"sample_name": "SRR1278971"}, "arguments": {}},
        workflow_step={"tool_name": "subread_align", "objective": "Align sample SRR1278971"},
        analysis_spec=analysis_spec,
    )
    assert hinted_align_step["arguments"]["reads_1"] == str(data_root / "SRR1278971_1.fastq")
    assert hinted_align_step["arguments"]["output_bam"] == str(selected_dir / "alignments" / "SRR1278971.bam")

    counts_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "featurecounts_run", "arguments": {}},
        workflow_step={"tool_name": "featurecounts_run", "objective": "Count reads per gene from the aligned BAM files"},
        analysis_spec=analysis_spec,
    )
    assert counts_step["arguments"]["annotation_gtf"] == str(references_dir / "C_parapsilosis_CDC317_current_features.gff")
    assert counts_step["arguments"]["annotation_format"] == "GFF"
    assert counts_step["arguments"]["output_counts"] == str(selected_dir / "counts" / "gene_counts.txt")
    assert counts_step["arguments"]["strand_specificity"] == 0
    assert counts_step["arguments"]["input_bams"] == [
        str(selected_dir / "alignments" / "SRR1278968.bam"),
        str(selected_dir / "alignments" / "SRR1278969.bam"),
        str(selected_dir / "alignments" / "SRR1278971.bam"),
    ]

    reverse_stranded_counts_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "featurecounts_run", "arguments": {"strand_specificity": 2}},
        workflow_step={"tool_name": "featurecounts_run", "objective": "Count reads per gene from reverse-stranded libraries"},
        analysis_spec=analysis_spec,
    )
    assert reverse_stranded_counts_step["arguments"]["strand_specificity"] == 2

    deseq_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "deseq2_run", "arguments": {}},
        workflow_step={"tool_name": "deseq2_run", "objective": "Run differential expression analysis for the planktonic versus biofilm contrast"},
        analysis_spec=analysis_spec,
    )
    assert "command" not in deseq_step["arguments"]
    assert deseq_step["arguments"]["counts_matrix"] == str(selected_dir / "counts" / "gene_counts.txt")
    assert deseq_step["arguments"]["metadata_table"] == str(data_root / "sample_metadata.tsv")
    assert deseq_step["arguments"]["design_formula"] == "~ condition"
    assert deseq_step["arguments"]["contrast"] == "condition_Biofilm_vs_Plankton"
    assert deseq_step["arguments"]["output_dir"] == str(selected_dir / "deseq2_results")
    assert deseq_step["arguments"]["engine"] == "pydeseq2"


def test_bind_rna_seq_de_scaffold_normalizes_path_like_metadata_samples(
    tmp_path: Path,
) -> None:
    """Stale path-valued metadata samples still bind to canonical FASTQ sample ids."""

    selected_dir = tmp_path / "workspace" / "runs" / "selected"
    data_root = tmp_path / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq" / "data"
    references_dir = data_root.parent / "references"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    references_dir.mkdir(parents=True, exist_ok=True)

    (references_dir / "C_parapsilosis_CDC317_current_chromosomes.fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (references_dir / "C_parapsilosis_CDC317_current_features.gff").write_text("chr1\tsrc\tgene\t1\t4\t.\t+\t.\tID=gene1\n", encoding="utf-8")
    stale_selected = tmp_path / "old" / "selected"
    (data_root / "sample_metadata.tsv").write_text(
        "sample\tcondition\n"
        f"{stale_selected}/alignments/SRR1278968.bam\tPlankton\n"
        f"{stale_selected}/alignments/SRR1278971.bam\tBiofilm\n",
        encoding="utf-8",
    )
    for sample in ("SRR1278968", "SRR1278971"):
        (data_root / f"{sample}_1.fastq").write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r2\nTGCA\n+\n!!!!\n", encoding="utf-8")

    analysis_spec = {
        "analysis_type": "rna_seq_differential_expression",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "requested_data_root": str(data_root),
    }
    align_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "subread_align",
            "arguments": {
                "reads_1": str(data_root / "SRR1278968.bam_1.fastq"),
                "reads_2": str(data_root / "SRR1278968.bam_2.fastq"),
                "output_bam": str(selected_dir / "alignments" / "SRR1278968.bam.bam"),
            },
        },
        workflow_step={
            "tool_name": "subread_align",
            "objective": "Align sample SRR1278968",
        },
        analysis_spec=analysis_spec,
    )

    assert align_step["arguments"]["reads_1"] == str(data_root / "SRR1278968_1.fastq")
    assert align_step["arguments"]["reads_2"] == str(data_root / "SRR1278968_2.fastq")
    assert align_step["arguments"]["output_bam"] == str(selected_dir / "alignments" / "SRR1278968.bam")


def test_bind_evolution_bcftools_isec_run_empty_args_empty_branch_id_fix_21() -> None:
    """Fix #21: the stepwise planner frequently emits bcftools_isec_run with
    ``arguments: {}`` (because its hierarchical ``parameter_hints`` don't map
    to the wrapper's typed keys) and with no ``branch_id``. Before Fix #21 the
    binder only rebinds when ``branch_id`` is ``evol1``/``evol2``, so the step
    runs with empty args and fails on "Missing required parameter(s) for
    template: input_vcfs, output_dir", then gets frozen into the executed
    prefix and the repair attempt is rejected as a prefix mutation
    (livelock). After Fix #21, an empty-branch_id step with no objective
    keyword defaults to the evol1 ancestor-subtraction shape so the step
    always has concrete typed params.
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix21_empty_branch")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    isec_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_isec_run",
            "arguments": {},
        },
        workflow_step={
            "tool_name": "bcftools_isec_run",
            # branch_id intentionally omitted — matches the failing planner
            # emission from exp36 turn 8.
            "objective": "Subtract ancestor-supported sites from the evolved callset",
        },
        analysis_spec=analysis_spec,
    )

    assert isec_step["arguments"]["input_vcfs"] == [
        str(resolved_dir / "variants/evol1.filtered.vcf.gz"),
        str(resolved_dir / "variants/anc.filtered.vcf.gz"),
    ]
    assert isec_step["arguments"]["output_dir"] == str(
        resolved_dir / "variants/.isec_evol1.ancestor_subtracted"
    )
    assert isec_step["arguments"]["output_vcf"] == str(
        resolved_dir / "variants/evol1.ancestor_subtracted.vcf.gz"
    )
    assert isec_step["arguments"].get("mode") == "complement"


def test_bind_evolution_bcftools_isec_run_objective_evol2_fallback_fix_21() -> None:
    """Fix #21: when branch_id is empty but the objective mentions ``evol2``,
    the fallback should prefer the evol2 ancestor-subtraction shape instead
    of the default evol1 path.
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix21_obj_evol2")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    isec_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_isec_run",
            "arguments": {},
        },
        workflow_step={
            "tool_name": "bcftools_isec_run",
            "objective": "Subtract ancestor variants from the evol2 filtered callset",
        },
        analysis_spec=analysis_spec,
    )

    assert isec_step["arguments"]["input_vcfs"] == [
        str(resolved_dir / "variants/evol2.filtered.vcf.gz"),
        str(resolved_dir / "variants/anc.filtered.vcf.gz"),
    ]
    assert isec_step["arguments"]["output_dir"] == str(
        resolved_dir / "variants/.isec_evol2.ancestor_subtracted"
    )
    assert isec_step["arguments"]["output_vcf"] == str(
        resolved_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
    )
    assert isec_step["arguments"].get("mode") == "complement"


def test_bind_evolution_bcftools_isec_run_preserves_evol2_paths_without_metadata() -> None:
    """Infer an isec branch from candidate paths when metadata was dropped."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_isec_path_branch")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    isec_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_isec_run",
            "arguments": {
                "input_vcfs": [
                    str(selected_dir / "variants/evol2.filtered.vcf.gz"),
                    str(selected_dir / "variants/anc.filtered.vcf.gz"),
                ],
                "output_dir": str(
                    selected_dir / "variants/.isec_evol2.ancestor_subtracted"
                ),
                "output_vcf": str(
                    selected_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
                ),
                "mode": "complement",
            },
        },
        workflow_step={
            "tool_name": "bcftools_isec_run",
        },
        analysis_spec=analysis_spec,
    )

    assert isec_step["arguments"]["input_vcfs"] == [
        str(resolved_dir / "variants/evol2.filtered.vcf.gz"),
        str(resolved_dir / "variants/anc.filtered.vcf.gz"),
    ]
    assert isec_step["arguments"]["output_dir"] == str(
        resolved_dir / "variants/.isec_evol2.ancestor_subtracted"
    )
    assert isec_step["arguments"]["output_vcf"] == str(
        resolved_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
    )
    assert isec_step["arguments"].get("mode") == "complement"


def test_bind_evolution_bcftools_isec_run_overrides_bogus_planner_inputs_fix_21() -> None:
    """Fix #21: when branch_id is empty the fallback must overwrite whatever
    the planner hallucinated (e.g. intersecting evol1_raw with evol1.filtered,
    as exp36 emitted), not preserve those paths. The correct semantic is
    evol1.filtered ∖ anc.filtered; the binder is the authoritative source for
    that even when the planner proposes something else.
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix21_bogus")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    isec_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_isec_run",
            "arguments": {
                # exp36 turn-8 repair tried these nonsense inputs (evol1_raw
                # compared against its own filtered file). The fallback must
                # discard them.
                "input_vcfs": [
                    "/tmp/bogus/selected/variants/evol1.filtered.vcf.gz",
                    "/tmp/bogus/selected/variants/evol1_raw.vcf",
                ],
                "output_dir": "/tmp/bogus/selected/bcftools_isec_run_out",
                "mode": "complement",
            },
        },
        workflow_step={
            "tool_name": "bcftools_isec_run",
            "objective": "Isolate evolved-specific variants",
        },
        analysis_spec=analysis_spec,
    )

    # Must rebind to the canonical scaffold regardless of what the planner
    # proposed.
    assert isec_step["arguments"]["input_vcfs"] == [
        str(resolved_dir / "variants/evol1.filtered.vcf.gz"),
        str(resolved_dir / "variants/anc.filtered.vcf.gz"),
    ]
    assert isec_step["arguments"]["output_dir"] == str(
        resolved_dir / "variants/.isec_evol1.ancestor_subtracted"
    )
    assert isec_step["arguments"]["output_vcf"] == str(
        resolved_dir / "variants/evol1.ancestor_subtracted.vcf.gz"
    )


def test_bind_evolution_bcftools_isec_run_branch_id_still_wins_fix_21() -> None:
    """Fix #21 guard: the existing Fix #17 branch_id-driven behavior must be
    preserved. When branch_id=evol2 is provided, use the evol2 shape even if
    the objective mentions evol1 (branch_id is the authoritative signal).
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix21_branch_guard")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    isec_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_isec_run",
            "arguments": {},
        },
        workflow_step={
            "tool_name": "bcftools_isec_run",
            "branch_id": "evol2",
            # Deliberately misleading objective — branch_id must still win.
            "objective": "Subtract ancestor from evol1",
        },
        analysis_spec=analysis_spec,
    )

    assert isec_step["arguments"]["input_vcfs"] == [
        str(resolved_dir / "variants/evol2.filtered.vcf.gz"),
        str(resolved_dir / "variants/anc.filtered.vcf.gz"),
    ]
    assert isec_step["arguments"]["output_dir"] == str(
        resolved_dir / "variants/.isec_evol2.ancestor_subtracted"
    )
    assert isec_step["arguments"]["output_vcf"] == str(
        resolved_dir / "variants/evol2.ancestor_subtracted.vcf.gz"
    )


def test_bind_evolution_bcftools_norm_run_uses_branch_scaffold() -> None:
    """Typed normalization should bind to branch-local annotated VCFs."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix_norm")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    norm_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_norm_run",
            "arguments": {
                "reads_1": "/tmp/stale/anc_R1.fastq.gz",
                "reads_2": "/tmp/stale/anc_R2.fastq.gz",
                "output_dir": "/tmp/stale/assembly",
            },
        },
        workflow_step={
            "tool_name": "bcftools_norm_run",
            "branch_id": "evol2",
            "objective": "Normalize the evol2 annotated VCF.",
        },
        analysis_spec=analysis_spec,
    )

    args = norm_step["arguments"]
    assert args["input_vcf"] == str(resolved_dir / "variants/evol2.annotated.vcf")
    assert args["output_vcf"] == str(
        resolved_dir / "variants/evol2.annotated.normalized.vcf.gz"
    )
    assert args["reference_fasta"] == str(resolved_dir / "assembly/scaffolds.fasta")
    assert args["multiallelic_mode"] == "-any"


def test_bind_evolution_bcftools_norm_run_uses_existing_root_annotated_vcf(
    tmp_path: Path,
) -> None:
    """Normalize the branch artifact already produced by SnpEff."""

    selected_dir = tmp_path / "selected"
    (selected_dir / "assembly").mkdir(parents=True)
    (selected_dir / "assembly/scaffolds.fasta").write_text(
        ">ref\nACGT\n",
        encoding="utf-8",
    )
    (selected_dir / "variants").mkdir()
    root_annotated = selected_dir / "evol1.annotated.vcf.gz"
    root_annotated.write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    norm_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_norm_run",
            "arguments": {
                "input_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
                "output_vcf": str(
                    selected_dir / "variants/evol1.annotated.normalized.vcf.gz"
                ),
            },
        },
        workflow_step={
            "tool_name": "bcftools_norm_run",
            "branch_id": "evol1",
            "objective": "Normalize the evol1 annotated VCF.",
        },
        analysis_spec=analysis_spec,
    )

    args = norm_step["arguments"]
    assert args["input_vcf"] == str(root_annotated.resolve(strict=False))
    assert args["output_vcf"] == str(
        (selected_dir / "variants/evol1.annotated.normalized.vcf.gz").resolve(
            strict=False
        )
    )


def test_bind_evolution_shared_variants_export_run_binds_canonical_paths_fix_22a() -> None:
    """Fix #22a: the typed shared_variants_export_run wrapper must be bound
    to the canonical evolution scaffold (evol1/evol2 annotated+normalized
    VCFs + final/variants_shared.csv) regardless of what the planner
    proposed. The stepwise planner has been observed emitting invented
    non-canonical paths (e.g. ``evol1.normalized.vcf`` alongside
    ``evol2.normalized.vcf`` in the top-level variants dir) that do not
    exist on disk, then repeatedly failing with either "Missing required
    parameter(s)" or "prefix mutation" after the normalizer rewrites the
    prefix. The binder is the authoritative source for these paths.
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix22a_export")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    export_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "shared_variants_export_run",
            "arguments": {
                # Planner-hallucinated paths — must be overwritten.
                "input_vcf_a": "/bogus/evol1.normalized.vcf",
                "input_vcf_b": "/bogus/evol2.normalized.vcf",
                "output_csv": "/bogus/shared.csv",
                "dedupe_by_gene": False,
            },
        },
        workflow_step={
            "tool_name": "shared_variants_export_run",
            "objective": "Export shared variants to CSV",
        },
        analysis_spec=analysis_spec,
    )

    args = export_step["arguments"]
    assert args["input_vcf_a"] == str(
        resolved_dir / "variants/evol1.annotated.normalized.vcf.gz"
    )
    assert args["input_vcf_b"] == str(
        resolved_dir / "variants/evol2.annotated.normalized.vcf.gz"
    )
    assert args["output_csv"] == str(resolved_dir / "final/variants_shared.csv")
    # Defaults for optional params — setdefault'd, so planner wins if
    # explicit. Here the planner set dedupe_by_gene=False; Fix #22a uses
    # setdefault so that explicit choice is respected.
    assert args["dedupe_by_gene"] is False
    assert args["min_impact"] == "MODERATE"
    assert args["status"] == "shared"
    assert args["header_case"] == "upper"


def test_bind_evolution_shared_variants_export_run_fills_empty_args_fix_22a() -> None:
    """Fix #22a: when the planner emits the export wrapper with empty args
    (a frequent stepwise-planner failure mode — see exp36 where the same
    happened with ``bcftools_isec_run``), the binder must still produce a
    complete, runnable arguments dict. Default optional params to the
    benchmark-recipe canonical values (MODERATE / shared / upper / dedupe).
    """

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix22a_empty")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    export_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "shared_variants_export_run",
            "arguments": {},
        },
        workflow_step={
            "tool_name": "shared_variants_export_run",
            "objective": "Export shared variants to CSV",
        },
        analysis_spec=analysis_spec,
    )

    args = export_step["arguments"]
    assert args["input_vcf_a"] == str(
        resolved_dir / "variants/evol1.annotated.normalized.vcf.gz"
    )
    assert args["input_vcf_b"] == str(
        resolved_dir / "variants/evol2.annotated.normalized.vcf.gz"
    )
    assert args["output_csv"] == str(resolved_dir / "final/variants_shared.csv")
    assert args["min_impact"] == "MODERATE"
    assert args["status"] == "shared"
    assert args["header_case"] == "upper"
    assert args["dedupe_by_gene"] is True


def test_bind_evolution_empty_requested_data_root_does_not_corrupt_paths_fix_23(
    tmp_path: Path,
) -> None:
    """Fix #23: an empty / missing ``requested_data_root`` must not cause
    the binder to overwrite correct planner paths with cwd-rooted fakes.

    exp38/exp39 regression: when the analysis_spec passed to the strict
    binder lacked ``requested_data_root``, ``Path("")`` degenerated to
    ``Path(".")`` (which stringifies as truthy ``"."``), the old logic
    accepted it as a real data_root, and subsequent joins like
    ``data_root / "anc_R1.fastq.gz"`` resolved against cwd. The result:
    correct planner paths (e.g. ``/abs/path/to/evolution/data/anc_R1.fastq.gz``)
    were silently rewritten to repo-root fakes like
    ``/Users/.../bio_harness/anc_R1.fastq.gz``. Because the harness cwd
    is almost never the data root in production, this corrupted the
    very first evolution step every run.

    With Fix #23 an empty / ``"."`` ``requested_data_root`` is treated
    as "not set" and the binder falls back to
    ``_benchmark_task_data_dir(selected_dir)``. For non-benchmark
    selected_dirs that helper returns ``None``, so the binder leaves
    the planner's path intact instead of overwriting it.
    """

    # Use a selected_dir that the benchmark-task fallback CANNOT
    # recognise (no ``official_runs`` segment), so data_root is None.
    selected_dir = tmp_path / "ablation_output" / "control_evolution" / "selected"
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        # Explicitly empty — simulate the stepwise loop path that did
        # not inject ``requested_data_root`` before this fix.
        "requested_data_root": "",
    }

    planner_reads_1 = "/some/real/data/root/anc_R1.fastq.gz"
    planner_reads_2 = "/some/real/data/root/anc_R2.fastq.gz"

    spades_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "spades_assemble",
            "arguments": {
                "reads_1": planner_reads_1,
                "reads_2": planner_reads_2,
            },
        },
        workflow_step={
            "tool_name": "spades_assemble",
            "branch_id": "ancestor",
        },
        analysis_spec=analysis_spec,
    )

    args = spades_step["arguments"]
    # The planner's absolute paths must survive — Path("") fallthrough
    # must NOT overwrite them with cwd-rooted names.
    assert args["reads_1"] == planner_reads_1, (
        "Fix #23: empty requested_data_root must not cause reads_1 "
        f"rewrite. Got: {args['reads_1']}"
    )
    assert args["reads_2"] == planner_reads_2
    # Absolutely must not contain the test runner's cwd repo fragment.
    assert "bio_harness/anc_R1.fastq.gz" not in args["reads_1"]


def test_bind_evolution_bwa_evol1_objective_fallback_when_branch_id_empty_fix_24() -> None:
    """Fix #24: bwa_mem_align must rebind onto evol1 canonical paths when the
    planner emits the step with empty branch_id but an objective that
    mentions evol1. Prior code (Fix #16) added this fallback ONLY for the
    ancestor branch, so evolved steps with empty branch_id silently passed
    through the planner's non-canonical output_bam (e.g. ``evol1.bam``
    instead of ``evol1_aligned.bam``), which broke downstream references
    (exp40 stalled at step 7 for exactly this reason)."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix24_bwa_evol1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "requested_data_root": "/tmp/fix24_data",
    }

    align_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bwa_mem_align",
            "arguments": {
                # Planner's non-canonical BAM name — the binder must overwrite.
                "output_bam": str(resolved_dir / "alignments/evol1.bam"),
            },
        },
        workflow_step={
            "tool_name": "bwa_mem_align",
            # Empty branch_id — the failure mode this fix targets.
            "branch_id": "",
            "objective": "Align evol1 reads to the assembled reference",
        },
        analysis_spec=analysis_spec,
    )
    args = align_step["arguments"]
    assert args["output_bam"] == str(resolved_dir / "alignments/evol1_aligned.bam"), (
        f"Fix #24: empty branch_id + evol1 objective must rebind output_bam to canonical path. Got: {args['output_bam']}"
    )
    assert "evol1_R1.fastq.gz" in args["reads_1"]
    assert "evol1_R2.fastq.gz" in args["reads_2"]
    assert "anc_R1.fastq.gz" not in args["reads_1"]


def test_bind_evolution_bwa_evol2_objective_fallback_when_branch_id_empty_fix_24() -> None:
    """Fix #24: same fallback for evol2."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix24_bwa_evol2")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "requested_data_root": "/tmp/fix24_data",
    }

    align_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bwa_mem_align", "arguments": {}},
        workflow_step={
            "tool_name": "bwa_mem_align",
            "branch_id": "",
            "objective": "Align evol2 reads against the assembled ancestor scaffold reference",
        },
        analysis_spec=analysis_spec,
    )
    args = align_step["arguments"]
    assert args["output_bam"] == str(resolved_dir / "alignments/evol2_aligned.bam")
    assert "evol2_R1.fastq.gz" in args["reads_1"]
    assert "evol2_R2.fastq.gz" in args["reads_2"]


def test_bind_evolution_bwa_branch_id_wins_over_objective_fix_24() -> None:
    """Fix #24: when branch_id and objective disagree, branch_id still wins
    (the Fix #16 invariant must not regress). An objective that mentions
    "ancestor" must NOT cause the evol1 binding to be overwritten."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix24_precedence")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
        "requested_data_root": "/tmp/fix24_data",
    }

    align_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "bwa_mem_align", "arguments": {}},
        workflow_step={
            "tool_name": "bwa_mem_align",
            "branch_id": "evol1",
            # Objective mentions "ancestor" (as a reference, not a sample).
            "objective": "Align evol1 reads against the ancestor scaffold reference",
        },
        analysis_spec=analysis_spec,
    )
    args = align_step["arguments"]
    assert args["output_bam"] == str(resolved_dir / "alignments/evol1_aligned.bam")
    assert "evol1_R1.fastq.gz" in args["reads_1"]
    # Must NOT regress to anc_* even with objective mentioning ancestor.
    assert "anc_R1.fastq.gz" not in args["reads_1"]


def test_bind_evolution_freebayes_evol1_objective_fallback_when_branch_id_empty_fix_24() -> None:
    """Fix #24: freebayes_call must also get the evol1/evol2 objective
    fallback so its input_bam + output_vcf stay on the canonical scaffold
    when the planner emits the step with empty branch_id."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix24_fb_evol1")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    fb_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "freebayes_call",
            "arguments": {
                # Planner's non-canonical path — the binder must overwrite.
                "input_bam": str(resolved_dir / "alignments/evol1.bam"),
                "output_vcf": str(resolved_dir / "variants/evol1.vcf"),
            },
        },
        workflow_step={
            "tool_name": "freebayes_call",
            "branch_id": "",
            "objective": "Call variants for evol1 against the assembled reference",
        },
        analysis_spec=analysis_spec,
    )
    args = fb_step["arguments"]
    assert args["input_bam"] == str(resolved_dir / "alignments/evol1_aligned.bam")
    assert args["output_vcf"] == str(resolved_dir / "variants/evol1_raw.vcf")


def test_bind_evolution_freebayes_evol2_objective_fallback_when_branch_id_empty_fix_24() -> None:
    """Fix #24: same fallback for evol2 freebayes_call."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix24_fb_evol2")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    fb_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "freebayes_call", "arguments": {}},
        workflow_step={
            "tool_name": "freebayes_call",
            "branch_id": "",
            "objective": "Call variants in evol2 against the ancestor scaffold",
        },
        analysis_spec=analysis_spec,
    )
    args = fb_step["arguments"]
    assert args["input_bam"] == str(resolved_dir / "alignments/evol2_aligned.bam")
    assert args["output_vcf"] == str(resolved_dir / "variants/evol2_raw.vcf")


def test_bind_evolution_bcftools_filter_run_objective_fallback_when_branch_id_empty_fix_24() -> None:
    """Fix #24: bcftools_filter_run must rebind via objective when
    branch_id is empty (parallel to the bwa/freebayes fallback)."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix24_filter")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    filter_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bcftools_filter_run",
            "arguments": {
                # Planner-invented non-canonical paths.
                "input_vcf": str(resolved_dir / "variants/evol1.vcf"),
                "output_vcf": str(resolved_dir / "variants/evol1.filtered.vcf"),
            },
        },
        workflow_step={
            "tool_name": "bcftools_filter_run",
            "branch_id": "",
            "objective": "Filter evol1 raw variants into a comparison-ready VCF",
        },
        analysis_spec=analysis_spec,
    )
    args = filter_step["arguments"]
    assert args["input_vcf"] == str(resolved_dir / "variants/evol1_raw.vcf")
    assert args["output_vcf"] == str(resolved_dir / "variants/evol1.filtered.vcf.gz")


def test_bind_evolution_snpeff_annotate_objective_fallback_when_branch_id_empty_fix_24() -> None:
    """Fix #24: snpeff_annotate must rebind via objective when branch_id is
    empty. Before this fix it only rebinds when branch_id is set, so
    empty-branch-id steps silently passed the planner's non-canonical
    input_vcf/output_vcf through to the wrapper."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix24_snpeff")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    snpeff_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "snpeff_annotate",
            "arguments": {
                "annotation_field": "ANN",
                "input_vcf": str(resolved_dir / "variants/evol2.vcf"),
                "output_vcf": str(resolved_dir / "variants/evol2_annotated.vcf"),
            },
        },
        workflow_step={
            "tool_name": "snpeff_annotate",
            "branch_id": "",
            "objective": "Annotate evol2 ancestor-subtracted variants with SnpEff",
        },
        analysis_spec=analysis_spec,
    )
    args = snpeff_step["arguments"]
    assert "annotation_field" not in args
    assert args["input_vcf"] == str(resolved_dir / "variants/evol2.ancestor_subtracted.vcf.gz")
    assert args["output_vcf"] == str(resolved_dir / "variants/evol2.annotated.vcf")


def test_bind_evolution_snpeff_prefers_existing_prokka_gff(tmp_path: Path) -> None:
    """SnpEff should consume the completed Prokka GFF, not a placeholder path."""

    selected_dir = tmp_path / "selected"
    annotation_dir = selected_dir / "annotation"
    annotation_dir.mkdir(parents=True)
    (annotation_dir / "ancestor.gff").write_text("##gff-version 3\n", encoding="utf-8")
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    snpeff_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "snpeff_annotate",
            "arguments": {
                "annotation_gff": str(annotation_dir / "genes.gff"),
                "input_vcf": str(selected_dir / "variants/evol1.ancestor_subtracted.vcf.gz"),
                "output_vcf": str(selected_dir / "variants/evol1.annotated.vcf"),
            },
        },
        workflow_step={
            "tool_name": "snpeff_annotate",
            "branch_id": "evol1",
            "objective": "Annotate evol1 ancestor-subtracted variants with SnpEff",
        },
        analysis_spec=analysis_spec,
    )

    assert snpeff_step["arguments"]["annotation_gff"] == str(
        (annotation_dir / "ancestor.gff").resolve(strict=False)
    )


def test_bind_evolution_prokka_uses_stable_annotation_prefix(tmp_path: Path) -> None:
    """Prokka producer steps should write the GFF SnpEff later discovers."""

    selected_dir = tmp_path / "selected"
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    prokka_step = bind_step_spec_for_strict_mode(
        step_spec={"tool_name": "prokka_annotate", "arguments": {}},
        workflow_step={
            "tool_name": "prokka_annotate",
            "objective": "Annotate assembled ancestor reference",
        },
        analysis_spec=analysis_spec,
    )

    assert prokka_step["arguments"]["input_fasta"] == str(
        (selected_dir / "assembly/scaffolds.fasta").resolve(strict=False)
    )
    assert prokka_step["arguments"]["output_dir"] == str(
        (selected_dir / "annotation").resolve(strict=False)
    )
    assert prokka_step["arguments"]["sample_prefix"] == "ancestor"


def test_bind_evolution_empty_branch_id_and_objective_does_not_overwrite_fix_24() -> None:
    """Fix #24 regression: when BOTH branch_id AND objective lack a
    branch marker (evol1/evol2/ancestor), the binder must NOT overwrite
    the planner's arguments — it simply has no authoritative basis to pick
    a branch. This protects against the fallback being over-aggressive
    and wiping out valid custom branches introduced by future benchmarks."""

    selected_dir = Path("/tmp/official_runs/evolution/attempt_fix24_noop")
    resolved_dir = selected_dir.resolve(strict=False)
    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "bioagentbench_planning_strict",
        "selected_dir": str(selected_dir),
    }

    planner_output = str(resolved_dir / "alignments/mystery_branch.bam")
    align_step = bind_step_spec_for_strict_mode(
        step_spec={
            "tool_name": "bwa_mem_align",
            "arguments": {"output_bam": planner_output},
        },
        workflow_step={
            "tool_name": "bwa_mem_align",
            "branch_id": "",
            "objective": "Align reads to reference",  # no branch mention
        },
        analysis_spec=analysis_spec,
    )
    args = align_step["arguments"]
    # output_bam is still the planner's value — no branch matched so no rebind.
    assert args["output_bam"] == planner_output
