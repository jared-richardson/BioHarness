from __future__ import annotations

from pathlib import Path

from bio_harness.core.contracts import assess_plan_contract
from bio_harness.workflows.fallback_catalog import (
    build_ranked_fallback_catalog,
    ranked_fallback_catalog_metadata,
    select_ranked_fallback_plan,
)


def _write_fastq(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")


def _write_refs(root: Path) -> tuple[str, str]:
    inputs = root / "inputs_readonly"
    inputs.mkdir(parents=True, exist_ok=True)
    fasta = inputs / "mouse_fasta"
    gtf = inputs / "mouse_gtf"
    fasta.write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    gtf.write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    return str(fasta), str(gtf)


def test_ranked_catalog_has_top20_required_fields():
    catalog = build_ranked_fallback_catalog()
    assert len(catalog) >= 26
    required = {
        "pipeline_id",
        "use_case",
        "assumptions",
        "required_inputs",
        "optional_inputs",
        "expected_outputs",
        "required_tools",
        "reference_requirements",
        "supports_short_read",
        "supports_long_read",
        "recovery_safety",
    }
    for row in catalog:
        assert required.issubset(set(row.keys()))
    summary = ranked_fallback_catalog_metadata()
    assert len(summary) == len(catalog)


def test_splicing_contract_selects_splicing_template(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")
    fasta, gtf = _write_refs(workspace)

    contract = {"must_include_capabilities": ["splicing_analysis", "alignment", "reference_inputs", "group_comparison"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run alternative splicing with rMATS comparing control vs treatment.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "sr_rna_splicing_rmats_star"
    coverage = assess_plan_contract(plan, contract)
    assert coverage["passed"] is True


def test_missing_optional_inputs_do_not_block_counts_fallback(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    counts = workspace / "outputs" / "counts.tsv"
    metadata = workspace / "outputs" / "metadata.tsv"
    counts.parent.mkdir(parents=True, exist_ok=True)
    counts.write_text("Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1\tS6\ng1\tchr1\t1\t2\t+\t2\t10\t11\n", encoding="utf-8")
    metadata.write_text("sample\tcondition\nS1\tcontrol\nS6\ttreatment\n", encoding="utf-8")
    contract = {"must_include_capabilities": ["differential_analysis", "group_comparison"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run differential expression from existing count matrix and sample metadata.",
        data_root=str(workspace / "inputs_readonly_missing"),
        selected_dir=str(workspace),
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] in {
        "differential_expression_deseq2_from_counts",
        "differential_expression_deseq2",
    }
    if details["selection"]["pipeline_id"] == "differential_expression_deseq2_from_counts":
        step = plan["plan"][0]
        assert step["arguments"]["counts_matrix"] == str(counts)
        assert step["arguments"]["metadata_table"] == str(metadata)


def test_counts_fallback_does_not_treat_metadata_tsv_as_counts_matrix(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    metadata = workspace / "sample_metadata.tsv"
    metadata.write_text("sample\tcondition\nS1\tcontrol\nS6\ttreatment\n", encoding="utf-8")
    contract = {"must_include_capabilities": ["differential_analysis", "group_comparison"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run differential expression from existing count matrix and sample metadata.",
        data_root=str(workspace / "inputs_readonly_missing"),
        selected_dir=str(workspace),
    )

    assert plan is None
    assert details["why"] in {
        "no_ranked_fallback_selected",
        "fallback_template_builder_failed",
    }


def test_transcript_quantification_does_not_cross_into_dge_fallback(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "reads_R1.fastq")
    _write_fastq(data_root / "reads_R2.fastq")
    fasta, gtf = _write_refs(workspace)
    contract = {
        "analysis_type": "transcript_quantification",
        "must_include_capabilities": ["quantification", "reference_inputs"],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Quantify transcript abundance from paired-end RNA-seq reads.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        preference_profile={"analysis_type": "transcript_quantification"},
    )

    assert plan is None
    assert details["why"] == "no_same_class_fallback"


def test_two_group_dge_fallback_requires_two_distinct_pairs(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "onlysample_R1.fastq")
    _write_fastq(data_root / "onlysample_R2.fastq")
    fasta, gtf = _write_refs(workspace)
    contract = {
        "analysis_type": "rna_seq_differential_expression",
        "must_include_capabilities": [
            "differential_analysis",
            "group_comparison",
            "reference_inputs",
        ],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run RNA-seq differential expression on control vs treatment samples.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        preference_profile={"analysis_type": "rna_seq_differential_expression"},
    )

    assert plan is None
    assert details["why"] == "fallback_template_builder_failed"
    assert details["selection"]["pipeline_id"].startswith("differential_expression_")


def test_two_group_dge_fallback_guards_featurecounts_bam_lists(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")
    fasta, gtf = _write_refs(workspace)
    contract = {
        "analysis_type": "rna_seq_differential_expression",
        "must_include_capabilities": [
            "differential_analysis",
            "group_comparison",
            "reference_inputs",
        ],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run RNA-seq differential expression on control vs treatment samples.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        preference_profile={"analysis_type": "rna_seq_differential_expression"},
    )

    assert plan is not None
    fc_step = next(
        step for step in plan["plan"] if step["tool_name"] == "bash_run" and "featureCounts -T 2 -p --countReadPairs" in step["arguments"]["command"]
    )
    command = fc_step["arguments"]["command"]
    assert "__EMPTY_INPUT_FILE__:" in command
    assert "[ ! -s " in command


def test_single_cell_request_without_single_cell_template_fails_closed(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "sample_R1.fastq")
    _write_fastq(data_root / "sample_R2.fastq")
    fasta, gtf = _write_refs(workspace)
    contract = {
        "analysis_type": "single_cell_rna_seq",
        "must_include_capabilities": [
            "single_cell_analysis",
            "alignment",
            "reference_inputs",
        ],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run single-cell RNA-seq counting and clustering on these reads.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        preference_profile={"analysis_type": "single_cell_rna_seq"},
    )

    assert plan is None
    assert details["why"] == "no_same_class_fallback"


def test_low_confidence_single_cell_misclassification_can_still_select_fusion_template(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "fusion_R1.fastq")
    _write_fastq(data_root / "fusion_R2.fastq")
    fasta, gtf = _write_refs(workspace)
    contract = {
        "analysis_type": "single_cell_rna_seq",
        "must_include_capabilities": [
            "fusion_detection",
            "alignment",
            "reference_inputs",
        ],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Detect gene fusions from paired-end RNA-seq reads with STAR-Fusion.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        preference_profile={"analysis_type": "single_cell_rna_seq"},
        tool_availability_override={
            "star-fusion": True,
            "star": False,
            "hisat2": False,
            "bwa": False,
            "bowtie2": False,
            "samtools": False,
        },
    )

    assert plan is not None
    assert details["selection"]["pipeline_id"] == "fusion_star_fusion_style"


def test_long_read_prompt_prefers_long_read_template(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "nanopore_run.fastq")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["alignment", "reference_inputs"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Do long-read DNA alignment with minimap2 for this nanopore dataset.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "lr_dna_align_minimap2"


def test_long_read_rna_request_stays_with_long_read_rna_template(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "nanopore_cdna.fastq")
    fasta, gtf = _write_refs(workspace)
    contract = {
        "analysis_type": "long_read_rna",
        "must_include_capabilities": ["alignment", "reference_inputs"],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Align Oxford Nanopore direct RNA reads with minimap2 splice mode.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        preference_profile={"analysis_type": "long_read_rna"},
    )

    assert plan is not None
    assert details["selection"]["pipeline_id"] == "lr_rna_align_minimap2_splice"


def test_long_read_assembly_request_fails_closed_without_same_class_template(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "nanopore_reads.fastq")
    fasta, _ = _write_refs(workspace)
    contract = {
        "analysis_type": "long_read_assembly",
        "must_include_capabilities": ["genome_assembly", "reference_inputs"],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Assemble these Oxford Nanopore long reads into a draft genome.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        preference_profile={"analysis_type": "long_read_assembly"},
    )

    assert plan is None
    assert details["why"] == "no_same_class_fallback"


def test_proteomics_request_fails_closed_without_same_class_template(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "abundance_matrix.csv").write_text("protein,sample_0,sample_1\nP1,1,2\n", encoding="utf-8")
    (data_root / "metadata.csv").write_text("sample,condition\nsample_0,control\nsample_1,treatment\n", encoding="utf-8")
    contract = {
        "analysis_type": "proteomics",
        "must_include_capabilities": ["proteomics", "differential_analysis", "group_comparison"],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run proteomics differential abundance on this abundance matrix.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        preference_profile={"analysis_type": "proteomics"},
    )

    assert plan is None
    assert details["why"] == "no_same_class_fallback"


def test_metabolomics_request_fails_closed_without_same_class_template(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "feature_table.csv").write_text("feature,sample_0,sample_1\nmz100_rt1,1,2\n", encoding="utf-8")
    (data_root / "metadata.csv").write_text("sample,condition\nsample_0,control\nsample_1,treatment\n", encoding="utf-8")
    contract = {
        "analysis_type": "metabolomics",
        "must_include_capabilities": ["metabolomics", "differential_analysis", "group_comparison"],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run metabolomics differential abundance on this feature table.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        preference_profile={"analysis_type": "metabolomics"},
    )

    assert plan is None
    assert details["why"] == "no_same_class_fallback"


def test_same_class_long_read_fallback_requires_capability_complete_match(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "nanopore_direct_rna.fastq")
    fasta, gtf = _write_refs(workspace)
    contract = {
        "analysis_type": "long_read_rna",
        "must_include_capabilities": ["alignment", "reference_inputs", "genome_assembly"],
    }

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Use long-read RNA alignment on this Nanopore dataset.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        preference_profile={"analysis_type": "long_read_rna"},
    )

    assert plan is None
    assert details["why"] == "no_capability_complete_fallback"
    assert details["analysis_type"] == "long_read_rna"


def test_protein_contract_compatibility(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    protein = workspace / "query.faa"
    protein.write_text(">p1\nMTEYKLVVVG\n", encoding="utf-8")
    contract = {"must_include_capabilities": ["protein_analysis", "annotation"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run a protein homology search with BLASTP.",
        data_root=str(workspace / "inputs_readonly"),
        selected_dir=str(workspace),
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "protein_blastp_homology"
    coverage = assess_plan_contract(plan, contract)
    assert coverage["passed"] is True


def test_selector_prefers_runnable_alignment_template_when_tools_differ(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    fasta, gtf = _write_refs(workspace)
    contract = {"must_include_capabilities": ["alignment", "reference_inputs"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Align short-read DNA data with bowtie2 as fallback.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        tool_availability_override={
            "star": False,
            "hisat2": False,
            "bwa": False,
            "bowtie2": True,
            "samtools": True,
        },
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "sr_dna_align_bowtie2"
    assert details["selection"]["missing_tools"] == []


def test_long_read_fallback_can_reuse_cached_bam_when_minimap2_missing(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "nanopore_reads.fastq")
    cached_bam = workspace / "outputs" / "cached_longread.bam"
    cached_bam.parent.mkdir(parents=True, exist_ok=True)
    cached_bam.write_bytes(b"BAM")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["alignment", "reference_inputs"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Perform long-read DNA alignment for nanopore data.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={"minimap2": False, "samtools": True},
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "lr_dna_align_minimap2"
    steps = plan.get("plan", [])
    assert steps
    assert steps[0]["tool_name"] == "bash_run"
    assert "cp " in str(steps[0].get("arguments", {}).get("command", ""))


def test_long_read_fallback_can_select_with_cached_bam_only(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    cached_bam = workspace / "outputs" / "cached_longread_only.bam"
    cached_bam.parent.mkdir(parents=True, exist_ok=True)
    cached_bam.write_bytes(b"BAM")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["alignment", "reference_inputs"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Perform long-read DNA alignment for nanopore data.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={"minimap2": False, "samtools": True},
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "lr_dna_align_minimap2"
    assert plan.get("plan", [])[0]["tool_name"] == "bash_run"


def test_germline_effective_required_tools_drop_bwa_with_cached_bam(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    bam = workspace / "outputs" / "cached_sample.bam"
    bam.parent.mkdir(parents=True, exist_ok=True)
    bam.write_bytes(b"BAM")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["variant_calling", "reference_inputs"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Call germline variants from cached BAM using bcftools.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={"bwa": False, "bcftools": True, "samtools": True, "gatk": False, "freebayes": False},
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "germline_variant_bcftools"


def test_preference_profile_can_bias_variant_fallback_toward_freebayes(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    bam = workspace / "outputs" / "cached_sample.bam"
    bam.parent.mkdir(parents=True, exist_ok=True)
    bam.write_bytes(b"BAM")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["variant_calling", "reference_inputs"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Call variants for evolved bacterial isolates from cached BAMs.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={"bwa": False, "bcftools": True, "samtools": True, "gatk": False, "freebayes": True},
        preference_profile={
            "analysis_type": "bacterial_evolution_variant_calling",
            "preferred_tools": ["freebayes_call"],
            "discouraged_tools": ["gatk_haplotypecaller"],
            "preferred_pipeline_ids": ["germline_variant_freebayes"],
        },
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "germline_variant_freebayes"
    assert "bwa" not in details["selection"]["required_tools_effective"]


def test_selector_can_prefer_varscan_when_requested(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    bam = workspace / "outputs" / "cached_sample.bam"
    bam.parent.mkdir(parents=True, exist_ok=True)
    bam.write_bytes(b"BAM")
    fasta, _ = _write_refs(workspace)
    contract = {
        "must_include_capabilities": ["variant_calling", "reference_inputs"],
        "required_tool_hints": ["varscan"],
        "explicit_tool_hints": ["varscan"],
    }
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Call germline variants with VarScan2.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={
            "bwa": False,
            "bcftools": False,
            "gatk": False,
            "freebayes": False,
            "varscan": True,
            "samtools": True,
            "java": True,
        },
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "germline_variant_varscan"


def test_germline_fallback_prefers_sorted_bam_candidate(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    unsorted_bam = workspace / "outputs" / "control_Aligned.out.bam"
    sorted_bam = workspace / "outputs" / "control_Aligned.sortedByCoord.out.bam"
    unsorted_bam.parent.mkdir(parents=True, exist_ok=True)
    unsorted_bam.write_bytes(b"BAM")
    sorted_bam.write_bytes(b"BAM")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["variant_calling", "reference_inputs"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Call germline variants from BAM with bcftools.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={"bwa": False, "bcftools": True, "samtools": True, "gatk": False, "freebayes": False},
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "germline_variant_bcftools"
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    bc_step = next((s for s in steps if str(s.get("tool_name", "")).strip().lower() == "bcftools_call"), {})
    bc_args = bc_step.get("arguments", {}) if isinstance(bc_step.get("arguments", {}), dict) else {}
    assert str(bc_args.get("input_bam", "")) == str(sorted_bam.resolve(strict=False))


def test_germline_fresh_alignment_mode_avoids_external_cached_bam_with_fastq(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    cached_bam = workspace / "outputs" / "clip_1_s1_vs_s6" / "S1_Aligned.sortedByCoord.out.bam"
    cached_bam.parent.mkdir(parents=True, exist_ok=True)
    cached_bam.write_bytes(b"BAM")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["variant_calling", "reference_inputs", "alignment"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run germline variant calling from FASTQ with bcftools and alignment.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        provenance_mode="fresh_alignment",
        tool_availability_override={"bwa": True, "bcftools": True, "samtools": True, "gatk": False, "freebayes": False},
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "germline_variant_bcftools"
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    assert any(str(s.get("tool_name", "")).strip().lower() == "bwa_mem_align" for s in steps)
    bc_step = next((s for s in steps if str(s.get("tool_name", "")).strip().lower() == "bcftools_call"), {})
    bc_args = bc_step.get("arguments", {}) if isinstance(bc_step.get("arguments", {}), dict) else {}
    input_bam = str(bc_args.get("input_bam", "")).strip()
    assert input_bam.endswith("outputs/fallback/germline_variant_bcftools/alignment.sorted.bam")
    assert input_bam != str(cached_bam.resolve(strict=False))


def test_selector_chooses_non_gatk_somatic_when_gatk_missing(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["variant_calling", "group_comparison", "reference_inputs", "alignment"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Call somatic variants in tumor vs normal without gatk.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={"gatk": False, "bcftools": True, "bwa": True, "samtools": True},
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "somatic_variant_bcftools_tn_degrade"


def test_selector_avoids_blacklisted_somatic_pipeline_when_viable_alternative_exists(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")
    fasta, _ = _write_refs(workspace)
    contract = {"must_include_capabilities": ["variant_calling", "group_comparison", "reference_inputs", "alignment"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Call somatic variants in tumor vs normal using Mutect2.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={"gatk": True, "bcftools": True, "bwa": True, "samtools": True},
        preference_profile={"tool_blacklist": ["gatk"]},
    )

    assert plan is not None
    assert details["selection"]["pipeline_id"] == "somatic_variant_bcftools_tn_degrade"


def test_selector_blacklist_overrides_matching_required_tool_hint(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")
    fasta, _ = _write_refs(workspace)
    contract = {
        "must_include_capabilities": ["variant_calling", "group_comparison", "reference_inputs", "alignment"],
        "required_tool_hints": ["gatk"],
        "explicit_tool_hints": ["gatk"],
    }
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Call somatic variants in tumor vs normal using Mutect2.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        tool_availability_override={"gatk": True, "bcftools": True, "bwa": True, "samtools": True},
        preference_profile={"tool_blacklist": ["gatk"]},
    )

    assert plan is not None
    assert details["selection"]["pipeline_id"] == "somatic_variant_bcftools_tn_degrade"


def test_selector_can_exclude_pipeline_ids(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    protein = workspace / "query.faa"
    protein.write_text(">p1\nMTEYKLVVVG\n", encoding="utf-8")

    contract = {"must_include_capabilities": ["protein_analysis", "annotation"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run a protein homology/domain search fallback.",
        data_root=str(workspace / "inputs_readonly"),
        selected_dir=str(workspace),
        excluded_pipeline_ids=["protein_blastp_homology"],
    )
    assert plan is not None
    assert details["selection"]["pipeline_id"] == "protein_hmmscan_domains"


def test_selector_supports_uncommon_methylation_template(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    fasta, gtf = _write_refs(workspace)

    contract = {"must_include_capabilities": ["methylation_analysis", "alignment", "reference_inputs"]}
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt="Run bisulfite methylation analysis with Bismark.",
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        tool_availability_override={"bismark": True, "samtools": True},
    )

    assert plan is not None
    assert details["selection"]["pipeline_id"] == "methylation_bismark_style"
