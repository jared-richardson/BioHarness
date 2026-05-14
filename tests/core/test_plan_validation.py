"""Tests for bio_harness.core.plan_validation."""

from __future__ import annotations

import pytest

from bio_harness.core.plan_validation import (
    PlanValidationResult,
    Severity,
    ValidationFinding,
    validate_plan,
)
from bio_harness.core.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(tool: str, step_id: int = 0, **kwargs: str) -> dict:
    return {"tool_name": tool, "step_id": step_id, "arguments": dict(kwargs)}


KNOWN_SKILLS = {"bash_run", "star_align", "bwa_mem_align", "deseq2_run", "salmon_quant", "featurecounts_run"}

INPUT_PATH_KEYS = {
    "bwa_mem_align": ["reads_1", "reads_2", "reference_fasta"],
    "star_align": ["reads_1", "reads_2", "reference_fasta", "annotation_gtf"],
}


# ---------------------------------------------------------------------------
# Empty plan checks
# ---------------------------------------------------------------------------

class TestEmptyPlanCheck:

    def test_empty_plan_list_is_error(self) -> None:
        result = validate_plan({"plan": []})
        assert not result.passed
        assert any(f.code == "EMPTY_PLAN" for f in result.errors)

    def test_missing_plan_key_is_error(self) -> None:
        result = validate_plan({})
        assert not result.passed
        assert any(f.code == "EMPTY_PLAN" for f in result.errors)

    def test_none_plan_is_error(self) -> None:
        result = validate_plan({"plan": None})
        assert not result.passed

    def test_non_dict_input_is_error(self) -> None:
        result = validate_plan("not a dict")
        assert not result.passed
        assert any(f.code == "INVALID_PLAN_TYPE" for f in result.errors)

    def test_valid_plan_passes(self) -> None:
        result = validate_plan({"plan": [_step("bash_run")]})
        assert result.passed


# ---------------------------------------------------------------------------
# Tool name checks
# ---------------------------------------------------------------------------

class TestToolNameCheck:

    def test_unknown_tool_is_warning(self) -> None:
        plan = {"plan": [_step("nonexistent_tool")]}
        result = validate_plan(plan, known_skill_names=KNOWN_SKILLS)
        assert result.passed  # warnings don't block
        assert any(f.code == "UNKNOWN_TOOL" for f in result.warnings)

    def test_known_tool_no_warning(self) -> None:
        plan = {"plan": [_step("bash_run")]}
        result = validate_plan(plan, known_skill_names=KNOWN_SKILLS)
        assert result.passed
        assert len(result.warnings) == 0

    def test_empty_tool_name_is_error(self) -> None:
        plan = {"plan": [{"tool_name": "", "step_id": 0, "arguments": {}}]}
        result = validate_plan(plan, known_skill_names=KNOWN_SKILLS)
        assert any(f.code == "MISSING_TOOL_NAME" for f in result.errors)

    def test_missing_tool_name_key_is_error(self) -> None:
        plan = {"plan": [{"step_id": 0, "arguments": {}}]}
        result = validate_plan(plan, known_skill_names=KNOWN_SKILLS)
        assert any(f.code == "MISSING_TOOL_NAME" for f in result.errors)

    def test_without_known_skills_skips_check(self) -> None:
        plan = {"plan": [_step("anything_goes")]}
        result = validate_plan(plan)
        assert result.passed
        assert len(result.findings) == 0


# ---------------------------------------------------------------------------
# Required arguments checks
# ---------------------------------------------------------------------------

class TestRequiredArgumentsCheck:

    def test_missing_required_arg_is_warning(self) -> None:
        plan = {"plan": [_step("bwa_mem_align", step_id=1, reads_1="/data/r1.fq")]}
        result = validate_plan(plan, input_path_keys=INPUT_PATH_KEYS)
        assert result.passed  # warnings don't block
        warnings = [f for f in result.warnings if f.code == "MISSING_INPUT_ARGS"]
        assert len(warnings) == 1
        assert "reads_2" in warnings[0].message
        assert "reference_fasta" in warnings[0].message

    def test_all_required_args_present_no_warning(self) -> None:
        plan = {"plan": [_step(
            "bwa_mem_align",
            reads_1="/data/r1.fq",
            reads_2="/data/r2.fq",
            reference_fasta="/ref/genome.fa",
        )]}
        result = validate_plan(plan, input_path_keys=INPUT_PATH_KEYS)
        missing_arg_findings = [f for f in result.findings if f.code == "MISSING_INPUT_ARGS"]
        assert len(missing_arg_findings) == 0

    def test_tool_not_in_input_path_keys_skips_check(self) -> None:
        plan = {"plan": [_step("deseq2_run", counts="/data/counts.tsv")]}
        result = validate_plan(plan, input_path_keys=INPUT_PATH_KEYS)
        assert result.passed
        assert len(result.findings) == 0

    def test_harness_managed_required_parameter_does_not_fail_strict_validation(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        plan = {
            "plan": [
                _step(
                    "deseq2_run",
                    step_id=1,
                    counts_matrix="/data/counts.tsv",
                    metadata_table="/data/meta.tsv",
                    design_formula="~ condition",
                    contrast="condition,treat,control",
                    output_dir="/tmp/out",
                )
            ]
        }
        result = validate_plan(plan, registry=registry)
        assert result.passed
        assert not any(f.code == "MISSING_REQUIRED_ARGS" for f in result.findings)

    def test_missing_user_input_required_parameter_still_fails_strict_validation(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        plan = {
            "plan": [
                _step(
                    "deseq2_run",
                    step_id=1,
                    metadata_table="/data/meta.tsv",
                    design_formula="~ condition",
                    contrast="condition,treat,control",
                    output_dir="/tmp/out",
                )
            ]
        }

        result = validate_plan(plan, registry=registry)

        assert not result.passed
        findings = [f for f in result.errors if f.code == "MISSING_REQUIRED_ARGS"]
        assert len(findings) == 1
        assert "counts_matrix" in findings[0].message

    def test_optional_harness_managed_scanpy_script_path_is_not_reported_missing(self) -> None:
        registry = ToolRegistry.from_defaults(
            signal_equivalences={},
            parameter_knowledge_base={},
            skill_index_path=None,
        )
        plan = {
            "plan": [
                _step(
                    "scanpy_workflow",
                    step_id=1,
                    input_path="/data/pbmc3k_processed.h5ad",
                    output_dir="/tmp/scanpy_output",
                )
            ]
        }

        result = validate_plan(plan, registry=registry)

        assert result.passed
        assert not any(f.code == "MISSING_REQUIRED_ARGS" for f in result.findings)


# ---------------------------------------------------------------------------
# Duplicate step ID checks
# ---------------------------------------------------------------------------

class TestDuplicateStepIdCheck:

    def test_duplicate_step_ids_warned(self) -> None:
        plan = {"plan": [_step("bash_run", step_id=1), _step("bash_run", step_id=1)]}
        result = validate_plan(plan)
        assert any(f.code == "DUPLICATE_STEP_ID" for f in result.warnings)

    def test_unique_step_ids_no_warning(self) -> None:
        plan = {"plan": [_step("bash_run", step_id=1), _step("bash_run", step_id=2)]}
        result = validate_plan(plan)
        assert not any(f.code == "DUPLICATE_STEP_ID" for f in result.findings)


# ---------------------------------------------------------------------------
# Pydantic model handling
# ---------------------------------------------------------------------------

class TestPydanticModelHandling:
    """Verify that validate_plan handles Pydantic model inputs correctly."""

    def test_pydantic_model_with_model_dump(self) -> None:
        """If plan_dict is a Pydantic model with model_dump, it should work."""

        class FakeModel:
            def model_dump(self):
                return {"plan": [{"tool_name": "bash_run", "step_id": 0, "arguments": {}}]}

        result = validate_plan(FakeModel())
        assert result.passed


# ---------------------------------------------------------------------------
# PlanValidationResult
# ---------------------------------------------------------------------------

class TestPlanValidationResult:

    def test_summary_no_findings(self) -> None:
        result = PlanValidationResult()
        assert "passed" in result.summary()

    def test_summary_with_errors(self) -> None:
        result = PlanValidationResult(findings=[
            ValidationFinding(Severity.ERROR, "X", "bad"),
            ValidationFinding(Severity.WARNING, "Y", "meh"),
        ])
        assert "1 error(s)" in result.summary()
        assert "1 warning(s)" in result.summary()

    def test_passed_false_with_errors(self) -> None:
        result = PlanValidationResult(findings=[
            ValidationFinding(Severity.ERROR, "X", "bad"),
        ])
        assert not result.passed

    def test_passed_true_with_only_warnings(self) -> None:
        result = PlanValidationResult(findings=[
            ValidationFinding(Severity.WARNING, "Y", "meh"),
        ])
        assert result.passed


# ---------------------------------------------------------------------------
# Combined checks
# ---------------------------------------------------------------------------

class TestCombinedValidation:

    def test_full_plan_with_all_checks(self) -> None:
        plan = {
            "plan": [
                _step("bwa_mem_align", step_id=1,
                      reads_1="/data/r1.fq", reads_2="/data/r2.fq",
                      reference_fasta="/ref/genome.fa", output_bam="/out/aligned.bam"),
                _step("featurecounts_run", step_id=2,
                      input_bam="/out/aligned.bam", annotation_gtf="/ref/genes.gtf"),
            ],
        }
        result = validate_plan(
            plan,
            known_skill_names=KNOWN_SKILLS,
            input_path_keys=INPUT_PATH_KEYS,
        )
        assert result.passed
