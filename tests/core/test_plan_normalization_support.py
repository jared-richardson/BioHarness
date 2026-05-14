from __future__ import annotations

import copy
from pathlib import Path

from bio_harness.core.artifact_role_validator import (
    repair_artifact_role_violations,
    summarize_artifact_role_violations,
    validate_artifact_role_invariants,
)
from scripts.run_agent_e2e_plan_normalization_support import (
    PlanNormalizationContext,
    normalize_plan_for_execution,
)


def _context(*, benchmark_policy: str = "scientific_harness") -> PlanNormalizationContext:
    return PlanNormalizationContext(
        selected_dir="/tmp/workspace",
        data_root="/tmp/workspace/inputs_readonly",
        benchmark_policy=benchmark_policy,
        user_request="test request",
        analysis_spec={},
        runtime_binding_analysis_spec={},
        plan_contract={},
        preserved_tool_names=set(),
    )


def test_normalize_plan_for_execution_records_final_artifact_issues() -> None:
    normalized, meta, fc_meta = normalize_plan_for_execution(
        {"plan": []},
        context=_context(),
        stabilize_artifact_roles=lambda plan, source_plan: (plan, {"changed": False}),
        artifact_role_issue_strings=lambda plan: ["artifact-role-issue"],
    )

    assert normalized == {"plan": []}
    assert meta.get("artifact_role_issues", []) == ["artifact-role-issue"]
    assert isinstance(fc_meta, dict)


def test_normalize_plan_for_execution_records_strict_rebinding(monkeypatch) -> None:
    def _fake_rebind(plan, *, analysis_spec):
        return {**plan, "marker": "strict"}, {"changed": True, "why": "unit_test"}

    monkeypatch.setattr(
        "scripts.run_agent_e2e_plan_normalization_support.is_bioagentbench_planning_strict_policy",
        lambda policy: True,
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_plan_normalization_support.rebind_direct_plan_for_strict_mode",
        _fake_rebind,
    )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        {"plan": []},
        context=_context(benchmark_policy="official_bioagentbench"),
        stabilize_artifact_roles=lambda plan, source_plan: (plan, {"changed": False}),
        artifact_role_issue_strings=lambda plan: [],
    )

    assert normalized["marker"] == "strict"
    assert meta.get("strict_direct_plan_rebinding", {}).get("why") == "unit_test"


def test_normalize_plan_for_execution_records_strict_rebinding_for_no_template_scientific_harness(
    monkeypatch,
) -> None:
    def _fake_rebind(plan, *, analysis_spec):
        return {**plan, "marker": "strict_no_template"}, {"changed": True, "why": "unit_test_no_template"}

    monkeypatch.delenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", raising=False)
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", "0")
    monkeypatch.setattr(
        "scripts.run_agent_e2e_plan_normalization_support.rebind_direct_plan_for_strict_mode",
        _fake_rebind,
    )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        {"plan": []},
        context=_context(benchmark_policy="scientific_harness"),
        stabilize_artifact_roles=lambda plan, source_plan: (plan, {"changed": False}),
        artifact_role_issue_strings=lambda plan: [],
    )

    assert normalized["marker"] == "strict_no_template"
    assert meta.get("strict_direct_plan_rebinding", {}).get("why") == "unit_test_no_template"


def test_normalize_plan_for_execution_rebinds_deterministic_isec_consumers(tmp_path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    context = PlanNormalizationContext(
        selected_dir=selected_dir,
        data_root=data_root,
        benchmark_policy="scientific_harness",
        user_request="test request",
        analysis_spec={},
        runtime_binding_analysis_spec={},
        plan_contract={},
        preserved_tool_names=set(),
    )
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o ancestor_filtered.vcf.gz /refs/ancestor_raw.vcf.gz && "
                        "bcftools view -Oz -o evol1_filtered.vcf.gz /refs/evol1_raw.vcf.gz && "
                        "bcftools isec -n=2 -w1 evol1_filtered.vcf.gz ancestor_filtered.vcf.gz -p isec_dir1"
                    )
                },
            },
            {
                "step_id": 2,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "genome_db": "ecoli_custom",
                    "input_vcf": str(selected_dir / "isec_dir1" / "0002.vcf"),
                    "output_vcf": str(selected_dir / "evol1_annotated.vcf"),
                    "annotation_gff": str(data_root / "genes.gff"),
                    "reference_fasta": str(data_root / "scaffolds.fasta"),
                },
            },
        ]
    }

    def _stabilize(plan, source_plan):
        return repair_artifact_role_violations(
            plan,
            source_plan=source_plan,
            selected_dir=selected_dir,
            allowed_input_roots=[data_root],
        )

    def _issues(plan):
        return summarize_artifact_role_violations(
            validate_artifact_role_invariants(
                plan,
                selected_dir=selected_dir,
                allowed_input_roots=[data_root],
            )
        )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        raw_plan,
        context=context,
        stabilize_artifact_roles=_stabilize,
        artifact_role_issue_strings=_issues,
    )

    assert _issues(raw_plan) == [
        f"snpeff_annotate.input_vcf:input_in_selected_dir_without_producer:{selected_dir / 'isec_dir1' / '0002.vcf'}"
    ]
    assert _issues(normalized) == []
    assert normalized["plan"][1]["arguments"]["input_vcf"] == str(selected_dir / "isec_dir1" / "0000.vcf")


def test_normalize_plan_for_execution_rebinds_partial_evolution_raw_prep_in_no_template_mode(
    tmp_path,
    monkeypatch,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    monkeypatch.delenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", raising=False)
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", "0")
    context = PlanNormalizationContext(
        selected_dir=selected_dir,
        data_root=data_root,
        benchmark_policy="scientific_harness",
        user_request="Identify shared evolved variants relative to the ancestor.",
        analysis_spec={
            "analysis_type": "bacterial_evolution_variant_calling",
            "benchmark_policy": "scientific_harness",
            "selected_dir": str(selected_dir),
        },
        runtime_binding_analysis_spec={
            "analysis_type": "bacterial_evolution_variant_calling",
            "benchmark_policy": "scientific_harness",
            "selected_dir": str(selected_dir),
            "requested_data_root": str(data_root),
        },
        plan_contract={},
        preserved_tool_names=set(),
    )
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "output_dir": str(selected_dir / "assembly"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": str(data_root / "anc_R1.fastq.gz"),
                    "reads_2": str(data_root / "anc_R2.fastq.gz"),
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "output_bam": str(selected_dir / "alignments" / "anc_aligned.bam"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": str(data_root / "evol1_R1.fastq.gz"),
                    "reads_2": str(data_root / "evol1_R2.fastq.gz"),
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "output_bam": str(selected_dir / "alignments" / "evol1_aligned.bam"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": str(data_root / "evol2_R1.fastq.gz"),
                    "reads_2": str(data_root / "evol2_R2.fastq.gz"),
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "output_bam": str(selected_dir / "alignments" / "evol2_aligned.bam"),
                },
            },
            {
                "step_id": 5,
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments" / "anc_aligned.bam"),
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "output_vcf": str(selected_dir / "variants" / "anc_raw.vcf"),
                },
            },
            {
                "step_id": 6,
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments" / "evol1_aligned.bam"),
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "output_vcf": str(selected_dir / "variants" / "evol1_raw.vcf"),
                },
            },
            {
                "step_id": 7,
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments" / "evol2_aligned.bam"),
                    "reference_fasta": str(selected_dir / "assembly" / "scaffolds.fasta"),
                    "output_vcf": str(selected_dir / "variants" / "evol2_raw.vcf"),
                },
            },
            {
                "step_id": 8,
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
            },
            {
                "step_id": 9,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"bcftools isec -C -w1 {selected_dir / 'variants' / 'evol1.filtered.vcf.gz'} "
                        f"{selected_dir / 'variants' / 'anc.filtered.vcf.gz'} -Oz -o "
                        f"{selected_dir / 'variants' / 'evol1.ancestor_subtracted.vcf.gz'} && "
                        f"tabix -f -p vcf {selected_dir / 'variants' / 'evol1.ancestor_subtracted.vcf.gz'}"
                    )
                },
            },
        ]
    }

    def _stabilize(plan, source_plan):
        return repair_artifact_role_violations(
            plan,
            source_plan=source_plan,
            selected_dir=selected_dir,
            allowed_input_roots=[data_root],
        )

    def _issues(plan):
        return summarize_artifact_role_violations(
            validate_artifact_role_invariants(
                plan,
                selected_dir=selected_dir,
                allowed_input_roots=[data_root],
            )
        )

    raw_issues = _issues(raw_plan)
    assert (
        f"bash_run.command:input_in_selected_dir_without_producer:{selected_dir / 'variants' / 'anc.filtered.vcf.gz'}"
        in raw_issues
    )
    assert (
        f"bash_run.command:input_in_selected_dir_without_producer:{selected_dir / 'variants' / 'evol1.filtered.vcf.gz'}"
        in raw_issues
    )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        raw_plan,
        context=context,
        stabilize_artifact_roles=_stabilize,
        artifact_role_issue_strings=_issues,
    )

    assert _issues(normalized) == []
    assert meta.get("strict_direct_plan_rebinding", {}).get("changed", False) is True
    rebound_command = normalized["plan"][7]["arguments"]["command"]
    assert str(selected_dir / "variants" / "anc.filtered.vcf.gz") in rebound_command
    assert str(selected_dir / "variants" / "evol1.filtered.vcf.gz") in rebound_command
    assert str(selected_dir / "variants" / "evol2.filtered.vcf.gz") in rebound_command
    assert meta.get("changed") is True


def test_normalize_plan_for_execution_preserves_valid_ancestor_branch_when_templates_disabled(
    monkeypatch,
    tmp_path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", "0")

    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "scientific_harness",
    }
    context = PlanNormalizationContext(
        selected_dir=selected_dir,
        data_root=data_root,
        benchmark_policy="scientific_harness",
        user_request=(
            "Identify and annotate genome variants shared by two evolved lines "
            "relative to an ancestor."
        ),
        analysis_spec=analysis_spec,
        runtime_binding_analysis_spec=analysis_spec,
        plan_contract={"must_include_capabilities": ["annotation", "reference_inputs"]},
        preserved_tool_names=set(),
    )
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "output_dir": str(selected_dir / "anc_assembly"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_faa": str(selected_dir / "anc_assembly" / "genes.faa"),
                    "output_gff": str(selected_dir / "anc_assembly" / "genes.gff"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "reference_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_bam": str(selected_dir / "ancestor_align" / "anc.sorted.bam"),
                },
            },
            {
                "step_id": 4,
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(selected_dir / "ancestor_align" / "anc.sorted.bam"),
                    "reference_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_vcf": "ancestor_raw.vcf",
                },
            },
            {
                "step_id": 5,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "/inputs/evol1_R1.fastq.gz",
                    "reads_2": "/inputs/evol1_R2.fastq.gz",
                    "reference_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_bam": str(selected_dir / "alignments" / "evol1_aligned.bam"),
                },
            },
            {
                "step_id": 6,
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(selected_dir / "alignments" / "evol1_aligned.bam"),
                    "reference_fasta": str(selected_dir / "anc_assembly" / "scaffolds.fasta"),
                    "output_vcf": "evol1_raw.vcf",
                },
            },
            {
                "step_id": 7,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"cd {selected_dir} && "
                        "bcftools view -Oz -o filtered_anc.vcf.gz ancestor_raw.vcf && "
                        "bcftools view -Oz -o filtered_evol1.vcf.gz evol1_raw.vcf"
                    )
                },
            },
        ]
    }

    def _stabilize(plan, source_plan):
        return repair_artifact_role_violations(
            plan,
            source_plan=source_plan,
            selected_dir=selected_dir,
            allowed_input_roots=[data_root],
        )

    def _issues(plan):
        return summarize_artifact_role_violations(
            validate_artifact_role_invariants(
                plan,
                selected_dir=selected_dir,
                allowed_input_roots=[data_root],
            )
        )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        raw_plan,
        context=context,
        stabilize_artifact_roles=_stabilize,
        artifact_role_issue_strings=_issues,
    )

    assert _issues(raw_plan) == []
    assert _issues(normalized) == []
    assert "evolution_spades_repairs" not in meta
    assert "evolution_branch_repairs" not in meta
    tool_names = [step["tool_name"] for step in normalized["plan"]]
    assert tool_names.count("bwa_mem_align") == 2
    assert tool_names.count("freebayes_call") == 2


def test_normalize_plan_for_execution_resolves_compacted_output_dir_placeholders(
    tmp_path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "output_dir": "[PATH:output_dir]/ancestor_assembly",
                },
            },
            {
                "step_id": 2,
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": str(selected_dir / "ancestor_assembly" / "contigs.fasta"),
                    "output_gff": str(selected_dir / "annotation" / "genes.gff"),
                    "output_faa": str(selected_dir / "annotation" / "genes.faa"),
                },
            },
        ]
    }
    context = PlanNormalizationContext(
        selected_dir=selected_dir,
        data_root=data_root,
        benchmark_policy="scientific_harness",
        user_request="Assemble the ancestor and annotate its genes.",
        analysis_spec={},
        runtime_binding_analysis_spec={},
        plan_contract={},
        preserved_tool_names=set(),
    )

    def _stabilize(candidate_plan, source_plan):
        return repair_artifact_role_violations(
            candidate_plan,
            source_plan=source_plan,
            selected_dir=selected_dir,
            allowed_input_roots=[data_root],
        )

    def _issues(candidate_plan):
        return summarize_artifact_role_violations(
            validate_artifact_role_invariants(
                candidate_plan,
                selected_dir=selected_dir,
                allowed_input_roots=[data_root],
            )
        )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        plan,
        context=context,
        stabilize_artifact_roles=_stabilize,
        artifact_role_issue_strings=_issues,
    )

    assert normalized["plan"][0]["arguments"]["output_dir"] == str(selected_dir / "ancestor_assembly")
    assert _issues(normalized) == []
    assert "workspace_placeholder_repairs" in meta


def test_normalize_plan_for_execution_applies_parameter_knowledge_base_defaults() -> None:
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "output_dir": "/tmp/assembly",
                },
            },
            {
                "step_id": 2,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "/inputs/evol1_R1.fastq.gz",
                    "reads_2": "/inputs/evol1_R2.fastq.gz",
                    "reference_fasta": "/tmp/assembly/scaffolds.fasta",
                    "output_bam": "/tmp/evol1.bam",
                },
            },
        ]
    }

    context = PlanNormalizationContext(
        selected_dir=Path("/tmp/workspace"),
        data_root=Path("/tmp/workspace/inputs_readonly"),
        benchmark_policy="scientific_harness",
        user_request="test request",
        analysis_spec={},
        runtime_binding_analysis_spec={},
        plan_contract={},
        preserved_tool_names=set(),
    )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        raw_plan,
        context=context,
        stabilize_artifact_roles=lambda plan, source_plan: (plan, {"changed": False}),
        artifact_role_issue_strings=lambda plan: [],
    )

    spades_args = normalized["plan"][0]["arguments"]
    bwa_args = normalized["plan"][1]["arguments"]
    assert spades_args["careful"] is True
    assert spades_args["threads"] == 8
    assert spades_args["memory_gb"] == 32
    assert bwa_args["threads"] == 4
    assert meta.get("parameter_knowledge_base_repairs", {}).get("why") == "parameter_knowledge_base_applied"


def test_normalize_plan_for_execution_applies_safe_evolution_reference_repairs_without_templates(
    monkeypatch,
    tmp_path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", "0")

    analysis_spec = {
        "analysis_type": "bacterial_evolution_variant_calling",
        "benchmark_policy": "scientific_harness",
    }
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "spades_assemble",
                "arguments": {
                    "reads_1": "/inputs/anc_R1.fastq.gz",
                    "reads_2": "/inputs/anc_R2.fastq.gz",
                    "output_dir": str(selected_dir / "anc_assembly"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "prodigal_annotate",
                "arguments": {
                    "input_fasta": "spades_assemble.fasta",
                    "output_gff": str(selected_dir / "anc_annotation" / "prodigal.gff"),
                    "output_faa": str(selected_dir / "anc_annotation" / "prodigal.faa"),
                },
            },
            {
                "step_id": 3,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reads_1": "/inputs/evol1_R1.fastq.gz",
                    "reads_2": "/inputs/evol1_R2.fastq.gz",
                    "reference_fasta": "spades_assemble.fasta",
                    "output_bam": "evol1_aligned.bam",
                },
            },
        ]
    }
    context = PlanNormalizationContext(
        selected_dir=selected_dir,
        data_root=data_root,
        benchmark_policy="scientific_harness",
        user_request="Identify and annotate genome variants shared by two evolved lines relative to an ancestor.",
        analysis_spec=analysis_spec,
        runtime_binding_analysis_spec=analysis_spec,
        plan_contract={"must_include_capabilities": ["annotation", "reference_inputs"]},
        preserved_tool_names=set(),
    )

    def _stabilize(candidate_plan, source_plan):
        return repair_artifact_role_violations(
            candidate_plan,
            source_plan=source_plan,
            selected_dir=selected_dir,
            allowed_input_roots=[data_root],
        )

    def _issues(candidate_plan):
        return summarize_artifact_role_violations(
            validate_artifact_role_invariants(
                candidate_plan,
                selected_dir=selected_dir,
                allowed_input_roots=[data_root],
            )
        )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        raw_plan,
        context=context,
        stabilize_artifact_roles=_stabilize,
        artifact_role_issue_strings=_issues,
    )

    assert _issues(normalized) == []
    assert normalized["plan"][1]["arguments"]["input_fasta"] == str(
        (selected_dir / "anc_assembly" / "scaffolds.fasta").resolve(strict=False)
    )
    assert normalized["plan"][2]["arguments"]["reference_fasta"] == str(
        (selected_dir / "anc_assembly" / "scaffolds.fasta").resolve(strict=False)
    )
    assert "evolution_reference_path_repairs" in meta
    assert "evolution_spades_repairs" not in meta


def test_normalize_plan_for_execution_reverts_repairs_that_add_artifact_role_issues(
    monkeypatch,
    tmp_path,
) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "reads_1": "/inputs/evol1_R1.fastq.gz",
                    "reads_2": "/inputs/evol1_R2.fastq.gz",
                    "output_bam": str(selected_dir / "alignments" / "evol1_aligned.bam"),
                    # PKB default for bwa_mem_align — included so the parameter-
                    # knowledge-base repair (which legitimately fills safety-net
                    # defaults) is a no-op and the final `normalized == raw_plan`
                    # assertion stays tight.
                    "threads": 4,
                },
            },
            {
                "step_id": 2,
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": "/refs/ancestor.fa",
                    "input_bam": str(selected_dir / "alignments" / "evol1_aligned.bam"),
                    "output_vcf": str(selected_dir / "variants" / "evol1.raw.vcf"),
                    # PKB default for freebayes_call — bacterial ploidy; same
                    # rationale as threads=4 above.
                    "ploidy": 1,
                },
            },
        ]
    }

    def _inject_bad_repair(plan, *, selected_dir, data_root, request_text):
        repaired = copy.deepcopy(plan)
        repaired["plan"][1]["arguments"]["input_bam"] = repaired["plan"][1]["arguments"]["output_vcf"]
        return repaired, {"changed": True, "why": "unit_test_regression"}

    monkeypatch.setattr(
        "scripts.run_agent_e2e_plan_normalization_support._repair_requested_references_and_index_bases_in_plan",
        _inject_bad_repair,
    )

    context = PlanNormalizationContext(
        selected_dir=selected_dir,
        data_root=data_root,
        benchmark_policy="scientific_harness",
        user_request="Call variants in one evolved lineage.",
        analysis_spec={},
        runtime_binding_analysis_spec={},
        plan_contract={},
        preserved_tool_names=set(),
    )

    def _stabilize(candidate_plan, source_plan):
        return repair_artifact_role_violations(
            candidate_plan,
            source_plan=source_plan,
            selected_dir=selected_dir,
            allowed_input_roots=[data_root],
        )

    def _issues(candidate_plan):
        return summarize_artifact_role_violations(
            validate_artifact_role_invariants(
                candidate_plan,
                selected_dir=selected_dir,
                allowed_input_roots=[data_root],
            )
        )

    normalized, meta, _fc_meta = normalize_plan_for_execution(
        raw_plan,
        context=context,
        stabilize_artifact_roles=_stabilize,
        artifact_role_issue_strings=_issues,
    )

    assert normalized == raw_plan
    assert "requested_reference_or_index_repairs" not in meta
    assert _issues(normalized) == []
