"""Tests for step failure diagnosis helpers."""

from __future__ import annotations

from bio_harness.core.error_diagnosis import diagnose_step_failure


def test_missing_file() -> None:
    result = diagnose_step_failure(
        tool_name="samtools_view",
        failure_class="runtime_step_failure",
        exit_code=1,
        stderr="No such file or directory: /path/to/file.bam",
    )

    assert "/path/to/file.bam" in result.root_cause
    assert result.confidence == "high"


def test_permission_denied() -> None:
    result = diagnose_step_failure(
        tool_name="bcftools_stats",
        failure_class="runtime_step_failure",
        exit_code=1,
        stderr="Permission denied: /data/ref.fa",
    )

    assert "Permission denied" in result.root_cause
    assert result.failure_class == "permission_filesystem"


def test_out_of_memory() -> None:
    result = diagnose_step_failure(
        tool_name="star_align",
        failure_class="resource_exhaustion",
        exit_code=137,
        stderr="Cannot allocate memory",
    )

    assert "memory" in result.root_cause.lower()
    assert result.failure_class == "out_of_memory"


def test_not_sorted() -> None:
    result = diagnose_step_failure(
        tool_name="featurecounts_run",
        failure_class="runtime_step_failure",
        exit_code=1,
        stderr="input file is not sorted",
    )

    assert "samtools sort" in result.suggested_fix.lower()


def test_not_indexed() -> None:
    result = diagnose_step_failure(
        tool_name="samtools_idxstats",
        failure_class="runtime_step_failure",
        exit_code=1,
        stderr="BAM file not indexed",
    )

    assert "index" in result.suggested_fix.lower()
    assert result.failure_class == "missing_index"


def test_invalid_option() -> None:
    result = diagnose_step_failure(
        tool_name="bcftools_view",
        failure_class="runtime_step_failure",
        exit_code=1,
        stderr="unrecognized option '--foo'",
    )

    assert "Invalid command-line option" in result.root_cause
    assert result.failure_class == "incompatible_parameters"


def test_corrupt_bam() -> None:
    result = diagnose_step_failure(
        tool_name="samtools_view",
        failure_class="runtime_step_failure",
        exit_code=1,
        stderr="fail to read the header",
    )

    assert "wrong format" in result.root_cause.lower() or "corrupt" in result.root_cause.lower()


def test_design_matrix() -> None:
    result = diagnose_step_failure(
        tool_name="deseq2_run",
        failure_class="validation_block",
        exit_code=1,
        stderr="design matrix is not full rank",
    )

    assert "Design formula" in result.root_cause


def test_missing_python_module() -> None:
    result = diagnose_step_failure(
        tool_name="python_script",
        failure_class="runtime_step_failure",
        exit_code=1,
        stderr="ModuleNotFoundError: No module named 'scipy'",
    )

    assert "scipy" in result.root_cause
    assert result.failure_class == "missing_dependency"


def test_missing_index_signature_precedes_missing_file() -> None:
    result = diagnose_step_failure(
        tool_name="samtools_view",
        failure_class="tool_error",
        exit_code=1,
        stderr="[E::hts_idx_load3] Could not load local index file",
    )

    assert result.failure_class == "missing_index"


def test_generic_tool_error_falls_back_to_novel_unknown() -> None:
    result = diagnose_step_failure(
        tool_name="mystery_tool",
        failure_class="tool_error",
        exit_code=1,
        stderr="opaque internal failure",
        llm=None,
    )

    assert result.failure_class == "novel_unknown"


def test_segfault() -> None:
    result = diagnose_step_failure(
        tool_name="samtools_sort",
        failure_class="runtime_step_failure",
        exit_code=139,
        stderr="Segmentation fault",
    )

    assert "segmentation fault" in result.root_cause.lower()


def test_java_error() -> None:
    result = diagnose_step_failure(
        tool_name="gatk_mutect2_call",
        failure_class="resource_exhaustion",
        exit_code=1,
        stderr="java.lang.OutOfMemoryError: Java heap space",
    )

    assert "memory" in result.root_cause.lower()


def test_empty_stderr_generic_fallback() -> None:
    result = diagnose_step_failure(
        tool_name="unknown_tool",
        failure_class="unknown_failure",
        exit_code=1,
        stderr="",
    )

    assert result.confidence == "low"
    assert result.diagnosed_by == "heuristic"


def test_generic_fallback_without_llm() -> None:
    result = diagnose_step_failure(
        tool_name="unknown_tool",
        failure_class="unknown_failure",
        exit_code=2,
        stderr="unexpected crash in worker thread",
        llm=None,
    )

    assert result.confidence == "low"
    assert result.diagnosed_by == "heuristic"


class _MockLLM:
    def __init__(self) -> None:
        self.called = False

    def summarize_text(self, text: str, instruction: str) -> str:
        self.called = True
        return "Root cause: The input format is inconsistent.\nSuggested fix: Validate the input schema before rerunning."


def test_llm_diagnosis_called_on_unknown() -> None:
    llm = _MockLLM()
    result = diagnose_step_failure(
        tool_name="mystery_tool",
        failure_class="unknown_failure",
        exit_code=1,
        stderr="opaque internal failure code 12345",
        llm=llm,
    )

    assert llm.called is True
    assert result.diagnosed_by == "llm"


def test_llm_not_called_on_heuristic_match() -> None:
    llm = _MockLLM()
    result = diagnose_step_failure(
        tool_name="samtools_view",
        failure_class="runtime_step_failure",
        exit_code=1,
        stderr="No such file or directory: /tmp/missing.bam",
        llm=llm,
    )

    assert llm.called is False
    assert result.diagnosed_by == "heuristic"


def test_llm_diagnosis_structure() -> None:
    llm = _MockLLM()
    result = diagnose_step_failure(
        tool_name="mystery_tool",
        failure_class="unknown_failure",
        exit_code=1,
        stderr="opaque internal failure code 12345",
        llm=llm,
    )

    assert result.root_cause == "The input format is inconsistent."
    assert result.suggested_fix == "Validate the input schema before rerunning."


def test_no_llm_available() -> None:
    result = diagnose_step_failure(
        tool_name="mystery_tool",
        failure_class="unknown_failure",
        exit_code=1,
        stderr="opaque internal failure code 12345",
        llm=None,
    )

    assert result.diagnosed_by == "heuristic"
    assert result.confidence == "low"
    assert result.failure_class == "unknown_failure"
