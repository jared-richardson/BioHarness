from __future__ import annotations

from bio_harness.ui.direct_skill_requests import (
    build_direct_single_skill_plan,
    decorate_direct_single_skill_request,
    looks_like_direct_single_skill_request,
    select_execution_contract,
)


def test_direct_single_skill_request_detects_explicit_flagstat() -> None:
    contract = {
        "explicit_tool_hints": ["samtools_flagstat"],
        "required_tool_hints": ["samtools_flagstat"],
    }

    assert looks_like_direct_single_skill_request(
        "Run samtools flagstat on workspace/control_rep1Aligned.out.bam and save the report for this run.",
        contract,
    ) is True


def test_direct_single_skill_request_does_not_capture_workflow_prompt() -> None:
    contract = {
        "explicit_tool_hints": ["samtools_flagstat"],
        "required_tool_hints": ["samtools_flagstat"],
    }

    assert looks_like_direct_single_skill_request(
        "Run samtools flagstat, then compare conditions with variant calling and summarize the workflow.",
        contract,
    ) is False


def test_decorate_direct_single_skill_request_adds_direct_smoke_marker_once() -> None:
    contract = {
        "explicit_tool_hints": ["samtools_flagstat"],
        "required_tool_hints": ["samtools_flagstat"],
    }

    decorated = decorate_direct_single_skill_request(
        "Run samtools flagstat on workspace/control_rep1Aligned.out.bam and save the report for this run.",
        contract,
    )

    assert "direct one-step skill smoke test" in decorated
    assert decorated.count("direct one-step skill smoke test") == 1
    assert "`samtools_flagstat`" in decorated


def test_build_direct_single_skill_plan_for_samtools_flagstat() -> None:
    contract = {
        "explicit_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
        "required_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
    }

    plan = build_direct_single_skill_plan(
        "Run samtools flagstat on workspace/control_rep1Aligned.out.bam and save the report for this run.",
        contract,
    )

    assert plan is not None
    assert plan["plan"][0]["tool_name"] == "samtools_flagstat"
    assert plan["plan"][0]["arguments"]["input_bam"].endswith("control_rep1Aligned.out.bam")
    assert plan["plan"][0]["arguments"]["output_txt"].endswith("control_rep1Aligned.out.flagstat.txt")


def test_build_direct_single_skill_plan_for_flagstat_without_contract_hints() -> None:
    plan = build_direct_single_skill_plan(
        "Run samtools flagstat on workspace/control_rep1Aligned.out.bam and save the report for this run.",
        {},
    )

    assert plan is not None
    assert plan["plan"][0]["tool_name"] == "samtools_flagstat"


def test_select_execution_contract_prefers_direct_message_contract() -> None:
    direct_contract = {
        "explicit_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
        "must_include_capabilities": ["alignment_qc", "variant_calling", "alignment"],
    }
    scoped_contract = {
        "must_include_capabilities": ["alignment_qc", "variant_calling", "alignment"],
        "explicit_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
        "required_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
    }

    selected = select_execution_contract(
        "Run samtools flagstat on workspace/control_rep1Aligned.out.bam and save the report for this run.",
        direct_contract,
        scoped_contract,
    )

    assert selected["must_include_capabilities"] == []
    assert selected["required_tool_hints"] == ["samtools_flagstat"]
    assert selected["explicit_tool_hints"] == ["samtools_flagstat"]


def test_select_execution_contract_keeps_scoped_contract_for_workflow_request() -> None:
    direct_contract = {
        "explicit_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
        "required_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
    }
    scoped_contract = {
        "must_include_capabilities": ["alignment_qc", "variant_calling", "alignment"],
        "explicit_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
        "required_tool_hints": ["samtools flagstat", "samtools", "flagstat"],
    }

    selected = select_execution_contract(
        "Run samtools flagstat, then compare conditions with variant calling and summarize the workflow.",
        direct_contract,
        scoped_contract,
    )

    assert selected == scoped_contract


def test_direct_single_skill_request_detects_completed_multiqc_report_bundle() -> None:
    contract = {
        "explicit_tool_hints": ["multiqc_report"],
        "required_tool_hints": ["multiqc_report"],
    }

    assert looks_like_direct_single_skill_request(
        (
            "Proceed with execution now. Build a MultiQC report bundle from the completed "
            "FastQC outputs in /tmp/run_001 and keep all generated files in the current run directory."
        ),
        contract,
    ) is True


def test_build_direct_single_skill_plan_for_multiqc_report_bundle() -> None:
    contract = {
        "explicit_tool_hints": ["multiqc_report"],
        "required_tool_hints": ["multiqc_report"],
    }

    plan = build_direct_single_skill_plan(
        (
            "Proceed with execution now. Build a MultiQC report bundle from the completed "
            "FastQC outputs in /tmp/run_001 and keep all generated files in the current run directory."
        ),
        contract,
    )

    assert plan is not None
    assert plan["plan"][0]["tool_name"] == "multiqc_report"
    assert plan["plan"][0]["arguments"]["run_input"] == "/tmp/run_001"
    assert plan["plan"][0]["arguments"]["output_dir"] == "/tmp/run_001"


def test_select_execution_contract_prefers_direct_report_bundle_contract() -> None:
    direct_contract = {
        "must_include_capabilities": ["run_reporting"],
        "explicit_tool_hints": ["multiqc_report"],
        "required_tool_hints": ["multiqc_report"],
    }
    scoped_contract = {
        "must_include_capabilities": ["fastqc", "run_reporting"],
        "explicit_tool_hints": ["multiqc_report"],
        "required_tool_hints": ["multiqc_report"],
    }

    selected = select_execution_contract(
        (
            "Proceed with execution now. Build a MultiQC report bundle from the completed "
            "FastQC outputs in /tmp/run_001 and keep all generated files in the current run directory."
        ),
        direct_contract,
        scoped_contract,
    )

    assert selected["must_include_capabilities"] == []
    assert selected["required_tool_hints"] == ["multiqc_report"]
