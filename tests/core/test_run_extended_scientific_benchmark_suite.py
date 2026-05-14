from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from run_extended_scientific_benchmark_suite import (  # noqa: E402
    CaseResult,
    ColumnCheck,
    SuiteCase,
    _build_harness_cmd,
    _evaluate_case,
)


class _Args:
    def __init__(self) -> None:
        self.executor_model_name = ""
        self.planner_model_name = ""
        self.llm_backend = ""
        self.host = ""
        self.execution_mode = ""
        self.max_repairs = 3
        self.heartbeat_seconds = 15
        self.stall_timeout_seconds = 45
        self.live_process_grace_seconds = 900
        self.quiet = False


def test_evaluate_case_captures_extended_runtime_and_planner_metrics(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    (selected_dir / "final").mkdir()
    (selected_dir / "final" / "deseq_results.csv").write_text(
        "gene_id,log2FoldChange,pvalue,padj\nA,1.0,0.01,0.02\n",
        encoding="utf-8",
    )
    result_json = selected_dir / "result.json"
    log_path = selected_dir / "harness.log"
    log_path.write_text("--- Executing Step 1: deseq2_run ---\n", encoding="utf-8")
    case = SuiteCase(
        case_id="airway_deseq_explicit",
        lane="deseq_prompt_grounding",
        description="Direct DESeq2 run.",
        prompt="stub",
        data_root=str(tmp_path / "data"),
        benchmark_policy="scientific_harness",
        expected_artifacts=("final/deseq_results.csv",),
        required_tools=("deseq2_run",),
        forbidden_tools=("bash_run",),
        column_checks=(
            ColumnCheck(
                path="final/deseq_results.csv",
                columns=("gene_id", "log2FoldChange", "pvalue", "padj"),
            ),
        ),
    )

    row = _evaluate_case(
        case=case,
        result_obj={
            "status": "completed",
            "benchmark_policy": "scientific_harness",
            "run_dir": str(tmp_path / "run"),
            "exec_file": str(tmp_path / "exec.log"),
            "planner_strategy_used": "direct_user_prompt",
            "auto_repair_history_count": 2,
            "planner_failopen_used": True,
        },
        selected_dir=selected_dir,
        result_json=result_json,
        log_path=log_path,
        harness_exit_code=0,
        timed_out=False,
        elapsed_seconds=42.75,
    )

    assert isinstance(row, CaseResult)
    assert row.passed is True
    assert row.elapsed_seconds == 42.75
    assert row.planner_strategy_used == "direct_user_prompt"
    assert row.auto_repair_history_count == 2
    assert row.planner_failopen_used is True


def test_build_harness_cmd_passes_execution_mode_override(tmp_path: Path) -> None:
    args = _Args()
    args.execution_mode = "stepwise"
    case = SuiteCase(
        case_id="synthetic_case",
        lane="synthetic_lane",
        description="Synthetic case.",
        prompt="stub",
        data_root=str(tmp_path / "data"),
        benchmark_policy="scientific_harness",
        expected_artifacts=("final/output.csv",),
        required_tools=("deseq2_run",),
        forbidden_tools=(),
    )

    command = _build_harness_cmd(
        args=args,
        case=case,
        prompt="Do the thing",
        selected_dir=tmp_path / "selected",
        result_json=tmp_path / "selected" / "result.json",
        data_root=tmp_path / "data",
    )

    assert command[command.index("--execution-mode") + 1] == "stepwise"
