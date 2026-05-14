"""Tests for generic stage-DAG validation and repair."""

from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.stage_dag import (
    infer_step_stage_info,
    repair_stage_dag,
    validate_stage_dag,
)
from bio_harness.core.stage_semantics import (
    canonicalize_bash_command_for_stage_dedupe,
    classify_artifact_identity,
)
from bio_harness.core.tool_registry import ToolRegistry, default_tool_registry


def _default_registry():
    return default_tool_registry()


def _shared_export_plan() -> dict[str, object]:
    return {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {
                    "command": (
                        "mkdir -p /tmp/out && "
                        "export_shared_variants_csv.py "
                        "/tmp/sample_A.annotated.vcf /tmp/sample_B.annotated.vcf "
                        "--output /tmp/variants_shared.csv"
                    )
                },
            },
            {
                "tool_name": "snpeff_annotate",
                "step_id": 2,
                "arguments": {
                    "input_vcf": "/tmp/sample_A_raw.vcf",
                    "output_vcf": "/tmp/sample_A.annotated.vcf",
                    "reference_fasta": "/tmp/ref.fasta",
                    "annotation_gff": "/tmp/ref.gff",
                    "config_dir": "/tmp/snpeff",
                    "genome_db": "sample_A",
                },
            },
            {
                "tool_name": "bash_run",
                "step_id": 3,
                "arguments": {
                    "command": (
                        "mkdir -p /tmp/out && "
                        "export_shared_variants_csv.py "
                        "/tmp/sample_A.annotated.vcf /tmp/sample_B.annotated.vcf "
                        "--output /tmp/variants_shared.csv"
                    )
                },
            },
        ]
    }


def test_validate_and_repair_stage_dag_for_missing_annotated_branch() -> None:
    plan = _shared_export_plan()

    issues = validate_stage_dag(plan, registry=_default_registry())

    assert {issue.issue for issue in issues} == {
        "consumer_before_producer",
        "duplicate_equivalent_step",
        "missing_stage_producer",
    }

    repaired = repair_stage_dag(plan, registry=_default_registry())
    repaired_step_ids = [step["step_id"] for step in repaired.plan["plan"]]

    assert repaired.removed_step_ids == (3,)
    assert repaired.moved_step_ids == (2, 1)
    assert repaired_step_ids == [2, 1]
    assert [(issue.issue, issue.identity, issue.stage) for issue in repaired.unresolved_issues] == [
        ("missing_stage_producer", "sample_B", "annotated")
    ]


def test_validate_stage_dag_allows_well_ordered_plan() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 1,
                "arguments": {
                    "input_bam": "/tmp/sample_A.bam",
                    "reference_fasta": "/tmp/ref.fasta",
                    "output_vcf": "/tmp/sample_A_raw.vcf",
                },
            },
            {
                "tool_name": "snpeff_annotate",
                "step_id": 2,
                "arguments": {
                    "input_vcf": "/tmp/sample_A_raw.vcf",
                    "output_vcf": "/tmp/sample_A.annotated.vcf",
                    "reference_fasta": "/tmp/ref.fasta",
                    "annotation_gff": "/tmp/ref.gff",
                    "config_dir": "/tmp/snpeff",
                    "genome_db": "sample_A",
                },
            },
            {
                "tool_name": "bash_run",
                "step_id": 3,
                "arguments": {
                    "command": (
                        "export_shared_variants_csv.py /tmp/sample_A.annotated.vcf "
                        "--output /tmp/variants_shared.csv"
                    )
                },
            },
        ]
    }

    issues = validate_stage_dag(plan, registry=_default_registry())
    repaired = repair_stage_dag(plan, registry=_default_registry())

    assert issues == []
    assert repaired.plan == plan
    assert repaired.repair_applied is False
    assert repaired.unresolved_issues == ()


def test_repair_stage_dag_uses_full_topological_sort() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {
                    "command": (
                        "export_shared_variants_csv.py /tmp/sample_A.annotated.vcf "
                        "--output /tmp/variants_shared.csv"
                    )
                },
            },
            {
                "tool_name": "freebayes_call",
                "step_id": 2,
                "arguments": {
                    "input_bam": "/tmp/sample_A.bam",
                    "reference_fasta": "/tmp/ref.fasta",
                    "output_vcf": "/tmp/sample_A_raw.vcf",
                },
            },
            {
                "tool_name": "snpeff_annotate",
                "step_id": 3,
                "arguments": {
                    "input_vcf": "/tmp/sample_A_raw.vcf",
                    "output_vcf": "/tmp/sample_A.annotated.vcf",
                    "reference_fasta": "/tmp/ref.fasta",
                    "annotation_gff": "/tmp/ref.gff",
                    "config_dir": "/tmp/snpeff",
                    "genome_db": "sample_A",
                },
            },
            {
                "tool_name": "bash_run",
                "step_id": 4,
                "arguments": {
                    "command": (
                        "bcftools norm -f /tmp/ref.fasta /tmp/sample_A_raw.vcf "
                        "-Oz -o /tmp/sample_A.filtered.vcf.gz"
                    )
                },
            },
        ]
    }

    repaired = repair_stage_dag(plan, registry=_default_registry())

    assert [step["step_id"] for step in repaired.plan["plan"]] == [2, 3, 1, 4]
    assert repaired.unresolved_issues == ()


def test_validate_stage_dag_detects_cycle_and_repair_fails_closed() -> None:
    registry = ToolRegistry.from_defaults()
    producer_a = registry._ensure("annotate_from_normalized")
    producer_a.input_path_keys = ["input_vcf"]
    producer_a.output_argument_keys = ["output_vcf"]
    producer_a.consumes_stages = ["normalized"]
    producer_a.produces_stages = ["annotated"]
    producer_b = registry._ensure("normalize_from_annotated")
    producer_b.input_path_keys = ["input_vcf"]
    producer_b.output_argument_keys = ["output_vcf"]
    producer_b.consumes_stages = ["annotated"]
    producer_b.produces_stages = ["normalized"]

    plan = {
        "plan": [
            {
                "tool_name": "annotate_from_normalized",
                "step_id": 1,
                "arguments": {
                    "input_vcf": "/tmp/sample_A.normalized.vcf.gz",
                    "output_vcf": "/tmp/sample_A.annotated.vcf",
                },
            },
            {
                "tool_name": "normalize_from_annotated",
                "step_id": 2,
                "arguments": {
                    "input_vcf": "/tmp/sample_A.annotated.vcf",
                    "output_vcf": "/tmp/sample_A.normalized.vcf.gz",
                },
            },
        ]
    }

    issues = validate_stage_dag(plan, registry=registry)
    repaired = repair_stage_dag(plan, registry=registry)

    assert any(issue.issue == "cycle_detected" for issue in issues)
    assert any(issue.issue == "cycle_detected" for issue in repaired.unresolved_issues)


def test_duplicate_detection_is_stable_under_whitespace_and_leading_mkdir() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "step_id": 1,
                "arguments": {
                    "command": (
                        "mkdir -p /tmp/out && mkdir -p /tmp/out &&\n"
                        "export_shared_variants_csv.py /tmp/sample_A.annotated.vcf "
                        "--output /tmp/variants_shared.csv"
                    )
                },
            },
            {
                "tool_name": "bash_run",
                "step_id": 2,
                "arguments": {
                    "command": (
                        "mkdir -p   /tmp/out && export_shared_variants_csv.py "
                        "/tmp/sample_A.annotated.vcf --output /tmp/variants_shared.csv ;"
                    )
                },
            },
        ]
    }

    issues = validate_stage_dag(plan, registry=_default_registry())

    assert any(issue.issue == "duplicate_equivalent_step" for issue in issues)


def test_command_only_bash_run_is_never_rebound() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "step_id": 1,
                "arguments": {
                    "input_vcf": "/tmp/sample_A_raw.vcf",
                    "output_vcf": "/tmp/real/sample_A.annotated.vcf",
                    "reference_fasta": "/tmp/ref.fasta",
                    "annotation_gff": "/tmp/ref.gff",
                    "config_dir": "/tmp/snpeff",
                    "genome_db": "sample_A",
                },
            },
            {
                "tool_name": "bash_run",
                "step_id": 2,
                "arguments": {
                    "command": (
                        "export_shared_variants_csv.py /tmp/fake/sample_A.annotated.vcf "
                        "--output /tmp/variants_shared.csv"
                    )
                },
            },
        ]
    }

    repaired = repair_stage_dag(plan, registry=_default_registry())

    assert repaired.rebinds == ()
    assert repaired.plan["plan"][1]["arguments"]["command"] == plan["plan"][1]["arguments"]["command"]


def test_structured_non_bash_path_argument_can_be_rebound() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "freebayes_call",
                "step_id": 1,
                "arguments": {
                    "input_bam": "/tmp/sample_A.bam",
                    "reference_fasta": "/tmp/ref.fasta",
                    "output_vcf": "/tmp/real/sample_A_raw.vcf",
                },
            },
            {
                "tool_name": "snpeff_annotate",
                "step_id": 2,
                "arguments": {
                    "input_vcf": "/tmp/fake/sample_A_raw.vcf",
                    "output_vcf": "/tmp/sample_A.annotated.vcf",
                    "reference_fasta": "/tmp/ref.fasta",
                    "annotation_gff": "/tmp/ref.gff",
                    "config_dir": "/tmp/snpeff",
                    "genome_db": "sample_A",
                },
            },
        ]
    }

    repaired = repair_stage_dag(plan, registry=_default_registry())

    assert repaired.rebinds
    assert repaired.plan["plan"][1]["arguments"]["input_vcf"] == "/tmp/real/sample_A_raw.vcf"


def test_infer_step_stage_info_returns_stage_neutral_on_bash_parse_failure() -> None:
    info = infer_step_stage_info(
        {
            "tool_name": "bash_run",
            "step_id": 1,
            "arguments": {"command": "bcftools norm 'unterminated"},
        }
    )

    assert info.parse_failed is True
    assert info.consumes == frozenset()
    assert info.produces == frozenset()


def test_classify_artifact_identity_strips_iterative_stage_suffixes() -> None:
    assert classify_artifact_identity("sample_A.annotated.vcf") == "sample_A"
    assert classify_artifact_identity("sample_B_raw.vcf") == "sample_B"
    assert classify_artifact_identity("sample_A.annotated.normalized.vcf.gz") == "sample_A"
    assert classify_artifact_identity("sample_A.sorted.bam") == "sample_A"
    assert classify_artifact_identity("raw_data_2024_raw.vcf") == "raw_data_2024"


def test_repair_stage_dag_is_idempotent() -> None:
    first = repair_stage_dag(_shared_export_plan(), registry=_default_registry())
    second = repair_stage_dag(first.plan, registry=_default_registry())

    assert second.plan == first.plan
    assert second.unresolved_issues == first.unresolved_issues


def test_unknown_wrapper_stage_registration_is_skipped() -> None:
    registry = ToolRegistry.from_defaults(
        stage_metadata={"missing_wrapper": {"produces_stages": ["raw"]}}
    )

    assert registry.get("missing_wrapper") is None


def test_canonicalize_bash_command_for_stage_dedupe_does_not_reorder_flags() -> None:
    first = canonicalize_bash_command_for_stage_dedupe(
        "bcftools norm -f ref.fa -Oz -o out.vcf.gz in.vcf"
    )
    second = canonicalize_bash_command_for_stage_dedupe(
        "bcftools norm -Oz -f ref.fa -o out.vcf.gz in.vcf"
    )

    assert first != second


def test_invalid_stage_transition_only_uses_declared_consumes_stages() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "snpeff_annotate",
                "step_id": 1,
                "arguments": {
                    "input_vcf": "/tmp/sample_A.annotated.vcf",
                    "output_vcf": "/tmp/sample_A.twice.annotated.vcf",
                    "reference_fasta": "/tmp/ref.fasta",
                    "annotation_gff": "/tmp/ref.gff",
                    "config_dir": "/tmp/snpeff",
                    "genome_db": "sample_A",
                },
            }
        ]
    }

    issues = validate_stage_dag(plan, registry=_default_registry())

    assert [(issue.issue, issue.stage, issue.identity) for issue in issues] == [
        ("invalid_stage_transition", "annotated", "sample_A")
    ]


def test_archived_exp28_replay_detects_and_repairs_stage_order_without_inventing_steps() -> None:
    path = Path(
        "<BIO_HARNESS_ROOT>/workspace/runs/"
        "20260420_063139_identify_and_annotate_genome_8299/completed_run_context.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    plan = payload["final_plan"]

    issues = validate_stage_dag(plan, registry=_default_registry())
    repaired = repair_stage_dag(plan, registry=_default_registry())
    issue_types = {issue.issue for issue in issues}
    repaired_step_ids = [step["step_id"] for step in repaired.plan["plan"]]

    assert issue_types == {
        "consumer_before_producer",
        "duplicate_equivalent_step",
        "missing_stage_producer",
    }
    assert 11 not in repaired_step_ids
    assert repaired.removed_step_ids == (11,)
    assert 9 in repaired.moved_step_ids
    assert repaired_step_ids.index(10) < repaired_step_ids.index(9)
    assert [(issue.issue, issue.identity, issue.stage) for issue in repaired.unresolved_issues] == [
        ("missing_stage_producer", "evol2", "annotated")
    ]
