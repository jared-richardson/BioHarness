from __future__ import annotations

from pathlib import Path

from bio_harness.harness.repair_context import (
    build_repair_context,
    load_repair_advisories,
    save_repair_advisories,
    upsert_repair_advisory,
)


def test_build_repair_context_includes_tool_knowledge_and_artifact_state(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    existing_csv = selected_dir / "final" / "counts.tsv"
    existing_csv.parent.mkdir(parents=True, exist_ok=True)
    existing_csv.write_text("gene\tcount\nA\t1\n", encoding="utf-8")

    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "featurecounts_run",
                    "arguments": {
                        "input_bams": str(selected_dir / "alignments" / "sample.bam"),
                        "output_counts": str(existing_csv),
                        "count_read_pairs": True,
                    },
                    "step_id": 1,
                    "objective": "Count paired-end reads per gene.",
                },
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {
                        "input_vcf": str(selected_dir / "variants" / "input.vcf"),
                        "output_vcf": str(selected_dir / "variants" / "annotated.vcf"),
                        "database": "ancestor",
                    },
                    "step_id": 2,
                    "objective": "Annotate filtered variants before exporting impact-based results.",
                },
            ]
        },
        "analysis_spec": {
            "analysis_type": "variant_annotation",
            "chosen_method": "snpeff_annotate",
            "preferred_tools": ["snpeff_annotate"],
            "parameter_profile": [
                {
                    "tool_name": "snpeff_annotate",
                    "settings": {"java_mem_gb": 8},
                    "rationale": "Keep enough heap for benchmark-sized VCF annotation.",
                }
            ],
            "protocol_grounding": {
                "required_tools": ["snpeff_annotate"],
                "required_plan_signals": ["annotated.vcf"],
            },
        },
        "plan_contract": {
            "must_include_capabilities": ["annotation", "variant_calling"],
            "required_tool_hints": ["snpeff_annotate"],
        },
        "step_statuses": ["completed", "failed"],
        "next_step_idx": 1,
        "failure_signatures": ["empty_output_artifact"],
    }
    available_skills = [
        {
            "name": "snpeff_annotate",
            "description": "Annotate variants with predicted effects.",
            "when_to_use": "Use when the workflow needs ANN/impact fields.",
            "when_not_to_use": "Do not parse ANN fields before annotation has run.",
            "parameters": {
                "input_vcf": {"required": True},
                "output_vcf": {"required": True},
                "database": {"required": True},
                "annotation_gff": {"required": False},
            },
        }
    ]

    context = build_repair_context(
        run=run,
        selected_dir=selected_dir,
        available_skills=available_skills,
        failure_class="runtime_step_failure",
        reason="annotated VCF missing",
        validation={"passed": False, "issues": ["missing_annotated_vcf"]},
        focus_mode="step_local",
    )

    assert context["failed_step_number"] == 2
    assert context["analysis_summary"]["analysis_type"] == "variant_annotation"
    assert context["analysis_advisory"]["summary"]
    assert context["parameter_hints"][0]["tool_name"] == "snpeff_annotate"
    assert any(entry["tool_name"] == "snpeff_annotate" for entry in context["tool_knowledge"])
    assert any(
        state["path"].endswith("annotated.vcf") and state["exists"] is False
        for step in context["focus_steps"]
        for state in step["artifact_state"]
    )


def test_upsert_and_save_repair_advisories_round_trip(tmp_path: Path):
    catalog = {"version": 1, "analysis_advisories": {}, "tool_advisories": {}}
    updated = upsert_repair_advisory(
        catalog,
        scope="tool",
        name="samtools",
        summary="Keep BAM indexes aligned with repaired BAM paths.",
        repair_hints=["Reindex BAM outputs after path rebinding."],
        avoid_patterns=["Reusing stale BAM indexes from other paths."],
        source="unit-test",
    )

    target = tmp_path / "repair_advisories.json"
    save_repair_advisories(updated, target)
    reloaded = load_repair_advisories(target)

    assert reloaded["tool_advisories"]["samtools"]["source"] == "unit-test"
    assert "Reindex BAM outputs after path rebinding." in reloaded["tool_advisories"]["samtools"]["repair_hints"]


def test_build_repair_context_uses_scientific_catalog_for_unwrapped_tools(tmp_path: Path):
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "deepvariant",
                    "arguments": {
                        "reads": str(selected_dir / "aligned.bam"),
                        "reference": str(selected_dir / "reference.fa"),
                        "output_vcf": str(selected_dir / "final" / "variants.vcf"),
                    },
                    "step_id": 1,
                    "objective": "Call germline variants from aligned reads.",
                }
            ]
        },
        "analysis_spec": {
            "analysis_type": "germline_variant_calling",
            "chosen_method": "deepvariant",
            "preferred_tools": ["deepvariant"],
        },
        "plan_contract": {
            "must_include_capabilities": ["variant_calling"],
        },
        "step_statuses": ["failed"],
        "next_step_idx": 0,
    }

    context = build_repair_context(
        run=run,
        selected_dir=selected_dir,
        available_skills=[],
        failure_class="runtime_step_failure",
        reason="deepvariant command unavailable",
        validation={"passed": False, "issues": ["missing_tool"]},
        focus_mode="step_local",
    )

    deepvariant = next(entry for entry in context["tool_knowledge"] if entry["tool_name"] == "deepvariant")
    assert deepvariant["support_tier"] == "catalog_only"
    assert "reads" in deepvariant["required_args"]
    assert "gatk_haplotypecaller" in deepvariant["repo_alternatives"]


def test_build_repair_context_respects_trace_advisory_toggle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "workspace"
    selected_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "plan": {"plan": [{"tool_name": "samtools", "arguments": {}}]},
        "analysis_spec": {"analysis_type": "variant_annotation"},
        "step_statuses": ["failed"],
        "next_step_idx": 0,
    }

    monkeypatch.setenv("BIO_HARNESS_TRACE_ADVISORIES", "0")
    context = build_repair_context(
        run=run,
        selected_dir=selected_dir,
        available_skills=[],
        failure_class="runtime_step_failure",
        reason="tool failed",
        validation={"passed": False},
        focus_mode="step_local",
    )

    assert context["analysis_advisory"] == {}
    samtools = next(entry for entry in context["tool_knowledge"] if entry["tool_name"] == "samtools")
    assert samtools["repair_advisory"] == {}


def test_build_repair_context_adds_selected_dir_producer_hints(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "freebayes_call",
                    "arguments": {
                        "input_bam": str(selected_dir / "alignments" / "evol1.bam"),
                        "reference_fasta": str(selected_dir / "ancestor_assembly" / "scaffolds.fasta"),
                        "output_vcf": str(selected_dir / "evol1_call" / "evol1_raw.vcf"),
                    },
                    "step_id": 1,
                },
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": (
                            "bcftools view -Oz "
                            f"-o {selected_dir / 'evol1' / 'freebayes_evol1_filtered.vcf.gz'} "
                            f"{selected_dir / 'evol1_call' / 'evol1_raw.vcf'}"
                        )
                    },
                    "step_id": 2,
                },
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {
                        "input_vcf": str(selected_dir / "evol1" / "freebayes_evol1_filtered_annotated.vcf.gz"),
                        "output_vcf": str(selected_dir / "evol1" / "freebayes_evol1_filtered_annotated_effects.vcf"),
                    },
                    "step_id": 3,
                },
            ]
        },
        "analysis_spec": {
            "analysis_type": "bacterial_evolution_variant_calling",
        },
        "step_statuses": ["completed", "completed", "failed"],
        "next_step_idx": 2,
    }

    context = build_repair_context(
        run=run,
        selected_dir=selected_dir,
        available_skills=[],
        failure_class="contract_mismatch",
        reason="Planner output failed contract validation",
        validation={
            "artifact_role_issues": [
                (
                    "snpeff_annotate.input_vcf:input_in_selected_dir_without_producer:"
                    f"{selected_dir / 'evol1' / 'freebayes_evol1_filtered_annotated.vcf.gz'}"
                )
            ]
        },
        focus_mode="full_plan",
    )

    hints = context["contract_summary"]["selected_dir_producer_hints"]
    assert len(hints) == 1
    assert hints[0]["consumer"] == "snpeff_annotate.input_vcf"
    assert hints[0]["missing_input"] == "evol1/freebayes_evol1_filtered_annotated.vcf.gz"
    assert hints[0]["branch_hint"] == "evol1"
    assert hints[0]["nearest_upstream_outputs"][0]["path"] == "evol1/freebayes_evol1_filtered.vcf.gz"
    assert "add that producer step explicitly first" in hints[0]["repair_instruction"]


def test_build_repair_context_groups_multi_bam_producer_gaps(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "data"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    for sample in ("sample_A", "sample_B", "sample_C"):
        (data_root / f"{sample}_1.fastq").write_text("@r\nACGT\n+\n!!!!\n", encoding="utf-8")
        (data_root / f"{sample}_2.fastq").write_text("@r\nTGCA\n+\n!!!!\n", encoding="utf-8")

    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "star_align",
                    "arguments": {
                        "reads_1": str(data_root / "sample_A_1.fastq"),
                        "reads_2": str(data_root / "sample_A_2.fastq"),
                        "output_prefix": str(selected_dir / "sample_A_"),
                        "outSAMtype": "BAM SortedByCoordinate",
                    },
                    "step_id": 1,
                },
                {
                    "tool_name": "featurecounts_run",
                    "arguments": {
                        "input_bams": [
                            str(selected_dir / "sample_A.bam"),
                            str(selected_dir / "sample_B.bam"),
                            str(selected_dir / "sample_C.bam"),
                        ],
                        "output_counts": str(selected_dir / "counts.tsv"),
                    },
                    "step_id": 2,
                },
            ]
        },
        "analysis_spec": {
            "analysis_type": "rna_seq_differential_expression",
        },
        "step_statuses": ["completed", "failed"],
        "next_step_idx": 1,
    }

    context = build_repair_context(
        run=run,
        selected_dir=selected_dir,
        data_root=data_root,
        available_skills=[],
        failure_class="contract_mismatch",
        reason="Planner output failed contract validation",
        validation={
            "artifact_role_issues": [
                (
                    "featurecounts_run.input_bams:input_in_selected_dir_without_producer:"
                    f"{selected_dir / 'sample_A.bam'}"
                ),
                (
                    "featurecounts_run.input_bams:input_in_selected_dir_without_producer:"
                    f"{selected_dir / 'sample_B.bam'}"
                ),
                (
                    "featurecounts_run.input_bams:input_in_selected_dir_without_producer:"
                    f"{selected_dir / 'sample_C.bam'}"
                ),
            ]
        },
        focus_mode="full_plan",
    )

    hints = context["contract_summary"]["selected_dir_producer_hints"]
    assert len(hints) == 1
    assert hints[0]["consumer"] == "featurecounts_run.input_bams"
    assert hints[0]["artifact_family"] == "bam"
    assert hints[0]["requested_identity_count"] == 3
    assert hints[0]["planned_producer_identity_count"] == 1
    assert hints[0]["planned_producer_identities"] == ["sample_a"]
    assert hints[0]["missing_identities"] == ["sample_b", "sample_c"]
    assert hints[0]["readonly_fastq_identities"] == ["sample_a", "sample_b", "sample_c"]
    assert "one alignment producer per identity" in hints[0]["repair_instruction"]


def test_build_repair_context_handles_space_separated_input_bams_without_output_pollution(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "featurecounts_run",
                    "arguments": {
                        "input_bams": (
                            f"{selected_dir / 'sample_A.bam'} "
                            f"{selected_dir / 'sample_B.bam'}"
                        ),
                        "output_counts": str(selected_dir / "counts.tsv"),
                    },
                    "step_id": 1,
                }
            ]
        },
        "analysis_spec": {"analysis_type": "rna_seq_differential_expression"},
        "step_statuses": ["failed"],
        "next_step_idx": 0,
    }

    context = build_repair_context(
        run=run,
        selected_dir=selected_dir,
        available_skills=[],
        failure_class="contract_mismatch",
        reason="Planner output failed contract validation",
        validation={"artifact_role_issues": []},
        focus_mode="full_plan",
    )

    focus_step = context["focus_steps"][0]
    assert focus_step["input_paths"] == [
        str(selected_dir / "sample_A.bam"),
        str(selected_dir / "sample_B.bam"),
    ]
    assert focus_step["output_paths"] == [str(selected_dir / "counts.tsv")]
