"""Tests for safe bash placeholder extraction and resolution."""

from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.bash_placeholder_resolution import (
    extract_placeholder_tokens,
    resolve_bash_placeholders,
)
from scripts.run_agent_e2e_preexecution_repair_support import (
    assess_plan_semantic_guards_with_bash_placeholders,
)


def test_resolve_bash_placeholders_from_prior_step_arguments() -> None:
    result = resolve_bash_placeholders(
        "bcftools norm -f <reference_fasta> sample.vcf -o out.vcf",
        prior_step_arguments=[{"reference_fasta": "/tmp/ref.fa"}],
    )

    assert result.unresolved == []
    assert result.resolved_command == "bcftools norm -f /tmp/ref.fa sample.vcf -o out.vcf"
    assert result.resolutions == [
        {
            "token": "<reference_fasta>",
            "value": "/tmp/ref.fa",
            "source": "prior_step_arguments",
        }
    ]


def test_resolve_bash_placeholders_from_wrapper_defaults() -> None:
    result = resolve_bash_placeholders(
        "snpeff build -gff3 -v ${genome_db}",
        prior_step_arguments=[],
        wrapper_parameter_defaults={"genome_db": "sample_db"},
    )

    assert result.unresolved == []
    assert result.resolved_command == "snpeff build -gff3 -v sample_db"


def test_resolve_bash_placeholders_from_double_brace_syntax() -> None:
    result = resolve_bash_placeholders(
        "mkdir -p {{output_dir}}",
        prior_step_arguments=[{"output_dir": "/tmp/results"}],
    )

    assert result.unresolved == []
    assert result.resolved_command == "mkdir -p /tmp/results"


def test_resolve_bash_placeholders_does_not_touch_single_quoted_literals() -> None:
    result = resolve_bash_placeholders(
        "echo '<reference_fasta>' && echo ${output_dir}",
        prior_step_arguments=[{"reference_fasta": "/tmp/ref.fa", "output_dir": "/tmp/out"}],
    )

    assert result.resolved_command == "echo '<reference_fasta>' && echo /tmp/out"
    assert [entry["token"] for entry in result.resolutions] == ["${output_dir}"]


def test_resolve_bash_placeholders_does_not_touch_heredoc_body() -> None:
    command = (
        "cat <<EOF > script.sh\n"
        "echo <reference_fasta>\n"
        "EOF\n"
        "echo ${output_dir}\n"
    )
    result = resolve_bash_placeholders(
        command,
        prior_step_arguments=[{"reference_fasta": "/tmp/ref.fa", "output_dir": "/tmp/out"}],
    )

    assert "echo <reference_fasta>" in result.resolved_command
    assert "echo /tmp/out" in result.resolved_command
    assert [entry["token"] for entry in result.resolutions] == ["${output_dir}"]


def test_resolve_bash_placeholders_marks_ambiguous_prior_values_unresolved() -> None:
    result = resolve_bash_placeholders(
        "bcftools norm -f <reference_fasta> sample.vcf -o out.vcf",
        prior_step_arguments=[
            {"reference_fasta": "/tmp/ref_A.fa"},
            {"reference_fasta": "/tmp/ref_B.fa"},
        ],
    )

    assert result.resolved_command == "bcftools norm -f <reference_fasta> sample.vcf -o out.vcf"
    assert result.unresolved == ["reference_fasta"]


def test_resolve_bash_placeholders_leaves_plain_commands_unchanged() -> None:
    command = "bcftools view -i 'QUAL>=30' sample.vcf -o filtered.vcf"

    result = resolve_bash_placeholders(command, prior_step_arguments=[])

    assert result.resolved_command == command
    assert result.resolutions == []
    assert result.unresolved == []


def test_extract_placeholder_tokens_skips_comments() -> None:
    tokens = extract_placeholder_tokens(
        "echo ${output_dir}\n# <reference_fasta>\nmkdir -p {{results_dir}}\n"
    )

    assert [token.raw for token in tokens] == ["${output_dir}", "{{results_dir}}"]


def test_assess_plan_semantic_guards_with_bash_placeholders_merges_unresolved_issues() -> None:
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {"command": "bcftools norm -f <reference_fasta> sample.vcf -o out.vcf"},
            }
        ]
    }

    resolved_plan, validation, sidecar = assess_plan_semantic_guards_with_bash_placeholders(
        plan=plan,
        assess_semantic_guards=lambda candidate: {"passed": True, "issues": []},
        selected_dir="/tmp/selected",
    )

    assert resolved_plan["plan"][0]["arguments"]["command"] == (
        "bcftools norm -f <reference_fasta> sample.vcf -o out.vcf"
    )
    assert validation["passed"] is False
    assert [issue["issue"] for issue in validation["issues"]] == ["unresolved_placeholder"]
    assert sidecar == [
        {
            "step_id": 1,
            "resolved": [],
            "unresolved": ["reference_fasta"],
        }
    ]


def test_resolve_bash_placeholders_matches_exp36_reference_fasta_context() -> None:
    context_path = Path(
        "<BIO_HARNESS_ROOT>/workspace/runs/"
        "20260420_233133_identify_and_annotate_genome_fec1/completed_run_context.json"
    )
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    steps = payload["final_plan"]["plan"]
    step_13 = next(step for step in steps if step["step_id"] == 13)
    prior_arguments = [
        step.get("arguments", {})
        for step in steps
        if isinstance(step.get("arguments", {}), dict) and int(step.get("step_id", 0) or 0) < 13
    ]
    expected_ref = next(
        str(arguments.get("reference_fasta", "") or "").strip()
        for arguments in reversed(prior_arguments)
        if str(arguments.get("reference_fasta", "") or "").strip()
    )

    result = resolve_bash_placeholders(
        step_13["arguments"]["command"],
        prior_step_arguments=prior_arguments,
    )

    assert result.unresolved == []
    assert result.resolved_command.count(expected_ref) == 2
