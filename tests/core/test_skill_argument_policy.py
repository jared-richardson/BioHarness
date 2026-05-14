"""Tests for deterministic non-bash argument validation helpers."""

from __future__ import annotations

from bio_harness.core.skill_argument_policy import (
    harness_managed_parameter_keys,
    normalize_bash_run_arguments,
    normalize_execution_arguments,
    normalize_non_bash_run_arguments,
    resolve_execution_working_directory,
    sanitize_harness_managed_arguments,
    validate_non_bash_run_arguments,
)
from bio_harness.harness.config import SKILLS_DEFINITIONS
from bio_harness.skills.registry import SkillRegistry


def _skill_metadata(name: str) -> dict:
    registry = SkillRegistry(SKILLS_DEFINITIONS)
    skill = registry.get_skill(name)
    assert skill is not None, f"Missing skill metadata for {name}"
    return skill


def test_harness_managed_parameter_keys_returns_script_path_for_deseq2_run() -> None:
    metadata = _skill_metadata("deseq2_run")
    assert harness_managed_parameter_keys(metadata) == {"script_path"}


def test_harness_managed_parameter_keys_returns_empty_set_for_plain_wrapper() -> None:
    metadata = _skill_metadata("featurecounts_run")
    assert harness_managed_parameter_keys(metadata) == set()


def test_sanitize_harness_managed_arguments_strips_runtime_managed_values() -> None:
    metadata = _skill_metadata("deseq2_run")
    cleaned = sanitize_harness_managed_arguments(
        "deseq2_run",
        {
            "script_path": "/tmp/invented_wrapper.R",
            "counts_matrix": "/tmp/counts.tsv",
            "metadata_table": "/tmp/meta.tsv",
            "design_formula": "~ condition",
            "contrast": "condition,treat,control",
            "output_dir": "/tmp/out",
        },
        metadata,
    )

    assert "script_path" not in cleaned
    assert cleaned["counts_matrix"] == "/tmp/counts.tsv"
    assert cleaned["metadata_table"] == "/tmp/meta.tsv"
    assert cleaned["output_dir"] == "/tmp/out"


def test_validate_non_bash_run_arguments_allows_documented_harness_managed_parameter() -> None:
    metadata = _skill_metadata("deseq2_run")
    issues = validate_non_bash_run_arguments(
        "deseq2_run",
        {
            "script_path": "/tmp/wrapper.R",
            "counts_matrix": "/tmp/counts.tsv",
            "metadata_table": "/tmp/meta.tsv",
            "design_formula": "~ condition",
            "contrast": "condition,treat,control",
            "output_dir": "/tmp/out",
        },
        metadata,
    )

    assert issues == []


def test_validate_non_bash_run_arguments_flags_truly_undocumented_parameter() -> None:
    metadata = _skill_metadata("deseq2_run")
    issues = validate_non_bash_run_arguments(
        "deseq2_run",
        {
            "counts_matrix": "/tmp/counts.tsv",
            "metadata_table": "/tmp/meta.tsv",
            "design_formula": "~ condition",
            "contrast": "condition,treat,control",
            "output_dir": "/tmp/out",
            "final_csv": "/tmp/final.csv",
        },
        metadata,
    )

    assert issues == ["undocumented_argument:final_csv"]


def test_normalize_non_bash_run_arguments_splits_featurecounts_bam_string() -> None:
    normalized = normalize_non_bash_run_arguments(
        "featurecounts_run",
        {
            "input_bams": "/tmp/A.bam /tmp/B.bam",
            "annotation_gtf": "/tmp/genes.gtf",
            "output_counts": "/tmp/counts.tsv",
        },
    )

    assert normalized["input_bams"] == ["/tmp/A.bam", "/tmp/B.bam"]
    assert normalized["annotation_gtf"] == "/tmp/genes.gtf"


def test_normalize_non_bash_run_arguments_handles_atomic_isec_wrapper_inputs(tmp_path) -> None:
    selected_dir = tmp_path / "selected"
    normalized = normalize_non_bash_run_arguments(
        "bcftools_isec_run",
        {
            "input_vcfs": "./sample_A.vcf.gz ./sample_B.vcf.gz",
            "output_dir": "./isec_dir",
            "mode": "intersection",
        },
        cwd=str(selected_dir),
    )

    assert normalized["input_vcfs"] == [
        str(selected_dir / "sample_A.vcf.gz"),
        str(selected_dir / "sample_B.vcf.gz"),
    ]
    assert normalized["output_dir"] == str(selected_dir / "isec_dir")
    assert normalized["mode"] == "intersection"


def test_normalize_non_bash_run_arguments_canonicalizes_atomic_filter_paths(tmp_path) -> None:
    selected_dir = tmp_path / "selected"
    normalized = normalize_non_bash_run_arguments(
        "bcftools_filter_run",
        {
            "input_vcf": "./sample_raw.vcf.gz",
            "output_vcf": "./sample_filtered.vcf.gz",
            "filter_expression": "QUAL > 1",
        },
        cwd=str(selected_dir),
    )

    assert normalized["input_vcf"] == str(selected_dir / "sample_raw.vcf.gz")
    assert normalized["output_vcf"] == str(selected_dir / "sample_filtered.vcf.gz")
    assert normalized["filter_expression"] == "QUAL > 1"


def test_normalize_non_bash_run_arguments_canonicalizes_snpeff_relative_paths(tmp_path) -> None:
    selected_dir = tmp_path / "selected"
    normalized = normalize_non_bash_run_arguments(
        "snpeff_annotate",
        {
            "input_vcf": "./evol1_subtracted_anc.vcf.gz",
            "output_vcf": "./evol1_annotated.vcf",
            "genome_db": "ecoli_custom",
        },
        cwd=str(selected_dir),
    )

    assert normalized["input_vcf"] == str(selected_dir / "evol1_subtracted_anc.vcf.gz")
    assert normalized["output_vcf"] == str(selected_dir / "evol1_annotated.vcf")
    assert normalized["genome_db"] == "ecoli_custom"


def test_normalize_bash_run_arguments_canonicalizes_working_directory_alias(tmp_path) -> None:
    selected_dir = tmp_path / "selected"
    normalized = normalize_bash_run_arguments(
        {
            "command": "pwd",
            "working_dir": "./variants",
        },
        cwd=str(selected_dir),
    )

    assert normalized == {
        "command": "pwd",
        "working_directory": str(selected_dir / "variants"),
    }


def test_normalize_execution_arguments_routes_bash_run_to_bash_specific_normalizer(tmp_path) -> None:
    selected_dir = tmp_path / "selected"
    normalized = normalize_execution_arguments(
        "bash_run",
        {
            "command": "ls",
            "cwd": "variants",
        },
        cwd=str(selected_dir),
    )

    assert normalized["command"] == "ls"
    assert normalized["working_directory"] == str(selected_dir / "variants")
    assert resolve_execution_working_directory("bash_run", normalized, cwd=str(selected_dir)) == str(
        selected_dir / "variants"
    )
