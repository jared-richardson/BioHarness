"""Regression tests for semantic plan validation."""

from __future__ import annotations

from bio_harness.core.semantic_plan_validation import semantic_plan_issues


def test_semantic_plan_issues_flags_invalid_evolution_minus_ancestor_handoff() -> None:
    plan = {
        "plan": [
            {
                "step_id": 10,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools isec -w1 -n=2 evol1_filtered.vcf.gz ancestor_filtered.vcf.gz -p isec_evol1_anc && "
                        "bcftools isec -w1 -n=2 evol2_filtered.vcf.gz ancestor_filtered.vcf.gz -p isec_evol2_anc && "
                        "bcftools view -i 'TYPE=\"snp\" || TYPE=\"indel\"' evol1_call/evol1_raw.vcf "
                        "| bgzip -c > evol1_novel.vcf.gz && "
                        "bcftools view -i 'TYPE=\"snp\" || TYPE=\"indel\"' evol2_call/evol2_raw.vcf "
                        "| bgzip -c > evol2_novel.vcf.gz"
                    )
                },
            },
            {
                "step_id": 11,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/tmp/selected/evol1_subtracted_anc.vcf.gz",
                    "output_vcf": "/tmp/selected/evol1_annotated.vcf",
                },
            },
            {
                "step_id": 12,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/tmp/selected/evol2_subtracted_anc.vcf.gz",
                    "output_vcf": "/tmp/selected/evol2_annotated.vcf",
                },
            },
        ]
    }

    issues = semantic_plan_issues(
        plan,
        analysis_spec={"analysis_type": "bacterial_evolution_variant_calling"},
    )

    assert any(
        issue["issue"] == "invalid_evolution_minus_ancestor_handoff"
        and issue["reason"] == "missing_concrete_minus_ancestor_outputs"
        for issue in issues
    )
    assert any(
        issue["issue"] == "invalid_evolution_minus_ancestor_handoff"
        and issue["reason"] == "shared_with_ancestor_intersection_for_minus_ancestor_step"
        for issue in issues
    )
    assert any(
        issue["issue"] == "invalid_evolution_minus_ancestor_handoff"
        and issue["reason"] == "raw_call_filter_does_not_subtract_ancestor"
        for issue in issues
    )


def test_semantic_plan_issues_allows_valid_evolution_minus_ancestor_handoff() -> None:
    plan = {
        "plan": [
            {
                "step_id": 10,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools isec -C -w1 evol1_filtered.vcf.gz anc_filtered.vcf.gz -p .isec_export_evol1_ancestor_subtracted && "
                        "bgzip -c .isec_export_evol1_ancestor_subtracted/0000.vcf > evol1_subtracted_anc.vcf.gz && "
                        "tabix -f -p vcf evol1_subtracted_anc.vcf.gz && "
                        "bcftools isec -C -w1 evol2_filtered.vcf.gz anc_filtered.vcf.gz -p .isec_export_evol2_ancestor_subtracted && "
                        "bgzip -c .isec_export_evol2_ancestor_subtracted/0000.vcf > evol2_subtracted_anc.vcf.gz && "
                        "tabix -f -p vcf evol2_subtracted_anc.vcf.gz"
                    )
                },
            },
            {
                "step_id": 11,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/tmp/selected/evol1_subtracted_anc.vcf.gz",
                    "output_vcf": "/tmp/selected/evol1_annotated.vcf",
                },
            },
            {
                "step_id": 12,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "input_vcf": "/tmp/selected/evol2_subtracted_anc.vcf.gz",
                    "output_vcf": "/tmp/selected/evol2_annotated.vcf",
                },
            },
        ]
    }

    issues = semantic_plan_issues(
        plan,
        analysis_spec={"analysis_type": "bacterial_evolution_variant_calling"},
    )

    assert [
        issue
        for issue in issues
        if issue.get("issue") == "invalid_evolution_minus_ancestor_handoff"
    ] == []


def test_semantic_plan_issues_flags_oops_violation_for_compound_bash_run() -> None:
    plan = {
        "plan": [
            {
                "step_id": 3,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "bcftools norm -m -any -f ref.fa sample_A.annotated.vcf -o sample_A.norm.vcf\n"
                        "bcftools norm -m -any -f ref.fa sample_B.annotated.vcf -o sample_B.norm.vcf"
                    )
                },
            }
        ]
    }

    issues = semantic_plan_issues(plan, analysis_spec={"analysis_type": "bacterial_evolution_variant_calling"})

    oops_issues = [issue for issue in issues if issue.get("issue") == "oops_violation"]
    assert len(oops_issues) == 1
    assert oops_issues[0]["step_id"] == 3
    assert "missing_command_separator" in oops_issues[0]["violations"]
