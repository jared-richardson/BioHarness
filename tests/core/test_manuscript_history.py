from __future__ import annotations

from bio_harness.analysis.manuscript_history import (
    classify_failure_category,
    normalize_model_group,
)


def test_classify_failure_category_generic_fallback_block() -> None:
    error = "Generic template fallback is disabled in bioagentbench_planning_strict mode."
    assert classify_failure_category(error) == "forbidden_deterministic_fallback"


def test_classify_failure_category_semantic_validation() -> None:
    error = "Strict semantic validation blocked execution for bioagentbench_planning_strict because planner output failed semantic validation."
    assert classify_failure_category(error) == "semantic_validation_failure"


def test_classify_failure_category_missing_inputs() -> None:
    error = "Strict LLM planning is enabled and plan references missing inputs: freebayes_call.input_bam:/tmp/foo.bam"
    assert classify_failure_category(error) == "missing_input_handoff"


def test_classify_failure_category_missing_tool_signature() -> None:
    error = "Step 1 blocked by validation agent. Issues: missing_tool:Annotation"
    signatures = ["validation_block_missing_tool:annotation"]
    assert classify_failure_category(error, signatures) == "validation_block_missing_tool"


def test_normalize_model_group_collapses_qwen_variants() -> None:
    assert normalize_model_group("qwen3-coder-next:latest") == "qwen3_coder_next"
    assert normalize_model_group("qwen3.5:122b-a10b") == "qwen3.5_122b"
