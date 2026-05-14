from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bio_harness.core.extended_suite_ablation import (  # noqa: E402
    EXTENDED_SUITE_ABLATION_VARIANTS,
    case_result_to_variant_result,
    summarize_variant_results,
)
from run_extended_suite_ablation import (  # noqa: E402
    _build_suite_command,
    _build_variant_env,
)


class _Args:
    def __init__(self) -> None:
        self.suite_file = "/tmp/extended_suite.json"
        self.case_timeout_seconds = 1800
        self.heartbeat_seconds = 15
        self.stall_timeout_seconds = 45
        self.live_process_grace_seconds = 900
        self.llm_backend = ""
        self.host = ""
        self.execution_mode = ""
        self.lane = ["cross_tool_contamination"]
        self.case_id = ["pbmc3k_scanpy_not_seurat"]
        self.stop_on_failure = False


def test_build_suite_command_uses_variant_models_and_filters(tmp_path: Path) -> None:
    command = _build_suite_command(
        args=_Args(),
        variant=EXTENDED_SUITE_ABLATION_VARIANTS["gemma26_full"],
        suite_out_root=tmp_path / "gemma26_full",
    )

    assert "--executor-model-name" in command
    assert command[command.index("--executor-model-name") + 1] == "gemma4:26b"
    assert command[command.index("--planner-model-name") + 1] == "gemma4:26b"
    assert command[command.index("--lane") + 1] == "cross_tool_contamination"
    assert command[command.index("--case-id") + 1] == "pbmc3k_scanpy_not_seurat"


def test_build_variant_env_renders_true_no_templates_flag() -> None:
    env = _build_variant_env(EXTENDED_SUITE_ABLATION_VARIANTS["qwen_true_no_templates"])

    assert env["BIO_HARNESS_MODEL"] == "qwen3-coder-next:latest"
    assert env["BIO_HARNESS_MODEL_HEAVY"] == "qwen3-coder-next:latest"
    assert env["BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH"] == "0"
    assert env["BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE"] == "False"


def test_build_suite_command_passes_execution_mode_override(tmp_path: Path) -> None:
    args = _Args()
    args.execution_mode = "stepwise"

    command = _build_suite_command(
        args=args,
        variant=EXTENDED_SUITE_ABLATION_VARIANTS["qwen_full"],
        suite_out_root=tmp_path / "qwen_full",
    )

    assert command[command.index("--execution-mode") + 1] == "stepwise"


def test_case_result_to_variant_result_lifts_extended_suite_metadata() -> None:
    result = case_result_to_variant_result(
        variant_id="qwen_full",
        attempt_label="attempt_a",
        suite_out_root="/tmp/out",
        item={
            "case_id": "airway_deseq_noisy",
            "lane": "deseq_prompt_grounding",
            "description": "Noisy DESeq2 prompt.",
            "passed": False,
            "status": "completed",
            "error": "missing artifact",
            "elapsed_seconds": 31.5,
            "planner_strategy_used": "direct_user_prompt",
            "auto_repair_history_count": 1,
            "planner_failopen_used": False,
            "forbidden_tools_detected": ["bash_run"],
            "missing_artifacts": ["final/deseq_results.csv"],
            "failed_column_checks": [],
            "generic_template_fallback_used": True,
            "protocol_template_fallback_used": False,
        },
    )

    assert result.task_name == "airway_deseq_noisy"
    assert result.status == "fail"
    assert result.runtime_seconds == 31.5
    assert result.repairs_needed == 1
    assert result.metadata["lane"] == "deseq_prompt_grounding"
    assert result.metadata["generic_template_fallback_used"] is True
    assert result.metadata["forbidden_tools_detected"] == ["bash_run"]


def test_summarize_variant_results_computes_stress_and_output_metrics() -> None:
    qwen_variant = EXTENDED_SUITE_ABLATION_VARIANTS["qwen_full"]
    results = [
        case_result_to_variant_result(
            variant_id=qwen_variant.variant_id,
            attempt_label="attempt_a",
            suite_out_root="/tmp/out",
            item={
                "case_id": "pbmc3k_scanpy_explicit",
                "lane": "scanpy_prompt_grounding",
                "description": "Sanity lane",
                "passed": True,
                "status": "completed",
                "elapsed_seconds": 28.0,
                "auto_repair_history_count": 0,
                "missing_artifacts": [],
                "failed_column_checks": [],
                "forbidden_tools_detected": [],
                "generic_template_fallback_used": False,
                "protocol_template_fallback_used": False,
                "planner_failopen_used": False,
            },
        ),
        case_result_to_variant_result(
            variant_id=qwen_variant.variant_id,
            attempt_label="attempt_a",
            suite_out_root="/tmp/out",
            item={
                "case_id": "airway_deseq_not_salmon",
                "lane": "cross_tool_contamination",
                "description": "Stress lane",
                "passed": False,
                "status": "completed",
                "elapsed_seconds": 32.0,
                "auto_repair_history_count": 2,
                "missing_artifacts": ["final/deseq_results.csv"],
                "failed_column_checks": [],
                "forbidden_tools_detected": ["salmon_quant"],
                "generic_template_fallback_used": True,
                "protocol_template_fallback_used": False,
                "planner_failopen_used": True,
            },
        ),
    ]

    summary = summarize_variant_results(variant=qwen_variant, results=results)

    assert summary.count == 2
    assert summary.pass_rate == 0.5
    assert summary.sanity_pass_rate == 1.0
    assert summary.stress_pass_rate == 0.0
    assert summary.forbidden_tool_drift_rate == 0.5
    assert summary.output_path_fidelity_rate == 0.5
    assert summary.generic_fallback_rate == 0.5
    assert summary.planner_failopen_rate == 0.5
