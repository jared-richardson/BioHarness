from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bio_harness.core.domain_expansion_ablation import (  # noqa: E402
    DOMAIN_EXPANSION_ABLATION_VARIANTS,
    DomainExpansionCaseResult,
    expected_outcome_for_case,
    load_domain_expansion_manifest,
    render_template_lift_by_band,
    summarize_variant_results,
)
from run_domain_expansion_ablation import (  # noqa: E402
    _build_variant_env,
    _build_harness_command,
    _evaluate_case,
    _find_run_dir_for_selected_dir,
    _run_case_subprocess,
    _select_cases_for_variant,
    _watchdog_progress_paths,
)
import run_domain_expansion_ablation as runner_mod  # noqa: E402


class _Args:
    def __init__(self) -> None:
        self.model_name = ""
        self.planner_model_name = ""
        self.executor_model_name = ""
        self.llm_backend = ""
        self.host = ""
        self.execution_mode = ""
        self.heartbeat_seconds = 15
        self.stall_timeout_seconds = 45
        self.live_process_grace_seconds = 900
        self.case_timeout_seconds = 1800
        self.planner_attempt_timeout_seconds = 0
        self.llm_timeout_seconds = 0
        self.case_id = []
        self.band = []


def test_expected_outcome_marks_only_malformed_case_as_blocked_bad_input() -> None:
    assert expected_outcome_for_case("stress_assembly_malformed") == "blocked_bad_input"
    assert expected_outcome_for_case("domain_metabolomics") == "completed"


def test_load_domain_expansion_manifest_resolves_paths_and_expected_outcomes(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    (project_root / "workspace" / "benchmark_data" / "long_read" / "assembly_malformed" / "data").mkdir(parents=True)
    prompt_file = project_root / "workspace" / "benchmark_data" / "long_read" / "assembly_malformed" / "prompt.txt"
    prompt_file.write_text("prompt\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "stress_assembly_malformed",
                        "band": 3,
                        "data_root": "workspace/benchmark_data/long_read/assembly_malformed/data",
                        "prompt_file": "workspace/benchmark_data/long_read/assembly_malformed/prompt.txt",
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )

    cases = load_domain_expansion_manifest(manifest_path=manifest_path, project_root=project_root)

    assert len(cases) == 1
    assert cases[0].expected_outcome == "blocked_bad_input"
    assert cases[0].data_root == str(
        (project_root / "workspace" / "benchmark_data" / "long_read" / "assembly_malformed" / "data").resolve()
    )


def test_select_cases_for_no_recovery_variants_restricts_to_stress_bands() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    args = _Args()

    selected = _select_cases_for_variant(all_cases=cases, variant_id="qwen_no_recovery", args=args)

    assert len(selected) == 18
    assert all(case.band in {2, 3} for case in selected)


def test_evaluate_case_fails_evolution_without_final_shared_csv_fix_18(tmp_path: Path) -> None:
    """Fix #18: the bacterial-evolution benchmark must not be marked passed
    when selected/final/variants_shared.csv is missing. Prior to the fix,
    status=="completed" was enough for any generic case, letting runs that
    stopped after annotating evol1 (no evol2, no isec, no final CSV) pass
    silently."""

    from bio_harness.core.domain_expansion_ablation import DomainExpansionCase

    case = DomainExpansionCase(
        case_id="control_evolution",
        band=1,
        data_root="/abs/workspace/benchmarks/bioagent-bench/tasks/evolution/data",
        prompt_file="/abs/prompt.txt",
        expected_outcome="completed",
    )

    # No artifact on disk → passed must be False with the new gate.
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    artifact_paths = runner_mod._find_primary_artifacts(
        case=case, selected_dir=selected_dir, run_dir=None
    )
    present = runner_mod._primary_artifact_present(case=case, artifact_paths=artifact_paths)
    passed, reasons = _evaluate_case(
        case=case,
        status="completed",
        primary_artifact_present=present,
        error_text="",
        failure_root_cause="",
    )
    assert artifact_paths == []
    assert present is False
    assert passed is False
    assert reasons == ["missing_primary_artifact"]


def test_evaluate_case_passes_evolution_with_non_empty_final_shared_csv_fix_18(tmp_path: Path) -> None:
    """Fix #18: a valid final/variants_shared.csv with a header row flips
    the evolution case to passed via ["completed_with_primary_artifact"]."""

    from bio_harness.core.domain_expansion_ablation import DomainExpansionCase

    case = DomainExpansionCase(
        case_id="control_evolution",
        band=1,
        data_root="/abs/workspace/benchmarks/bioagent-bench/tasks/evolution/data",
        prompt_file="/abs/prompt.txt",
        expected_outcome="completed",
    )

    selected_dir = tmp_path / "selected"
    final_dir = selected_dir / "final"
    final_dir.mkdir(parents=True)
    csv_path = final_dir / "variants_shared.csv"
    # Representative header (upper-case per --header-case upper) + one row.
    csv_path.write_text(
        "GENE,CHROM,POS,REF,ALT,IMPACT\nrpoB,NODE_1,12345,C,T,MODERATE\n",
        encoding="utf-8",
    )

    artifact_paths = runner_mod._find_primary_artifacts(
        case=case, selected_dir=selected_dir, run_dir=None
    )
    present = runner_mod._primary_artifact_present(case=case, artifact_paths=artifact_paths)
    passed, reasons = _evaluate_case(
        case=case,
        status="completed",
        primary_artifact_present=present,
        error_text="One or more steps failed.",  # harness may repair a step
        failure_root_cause="",
    )
    assert any("final/variants_shared.csv" in p for p in artifact_paths)
    assert present is True
    assert passed is True
    assert reasons == ["completed_with_primary_artifact"]


def test_evaluate_case_fails_evolution_with_empty_final_shared_csv_fix_18(tmp_path: Path) -> None:
    """Fix #18: a zero-byte (or sub-header) CSV does not count as present.
    Catches planners that created the file but never wrote any content."""

    from bio_harness.core.domain_expansion_ablation import DomainExpansionCase

    case = DomainExpansionCase(
        case_id="control_evolution",
        band=1,
        data_root="/abs/workspace/benchmarks/bioagent-bench/tasks/evolution/data",
        prompt_file="/abs/prompt.txt",
        expected_outcome="completed",
    )

    selected_dir = tmp_path / "selected"
    final_dir = selected_dir / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "variants_shared.csv").write_text("", encoding="utf-8")

    artifact_paths = runner_mod._find_primary_artifacts(
        case=case, selected_dir=selected_dir, run_dir=None
    )
    present = runner_mod._primary_artifact_present(case=case, artifact_paths=artifact_paths)
    passed, reasons = _evaluate_case(
        case=case,
        status="completed",
        primary_artifact_present=present,
        error_text="",
        failure_root_cause="",
    )
    assert present is False
    assert passed is False
    assert reasons == ["missing_primary_artifact"]


def test_case_prefix_classifies_evolution_by_data_root_and_case_id_fix_18() -> None:
    """Fix #18: _case_prefix recognizes evolution via either the data_root
    path substring or the case_id (so the gate works even if the manifest
    moves the data root in the future)."""

    from bio_harness.core.domain_expansion_ablation import DomainExpansionCase

    by_data_root = DomainExpansionCase(
        case_id="control_evolution",
        band=1,
        data_root="/abs/workspace/benchmarks/bioagent-bench/tasks/evolution/data",
        prompt_file="/abs/prompt.txt",
        expected_outcome="completed",
    )
    by_case_id = DomainExpansionCase(
        case_id="control_evolution",
        band=1,
        data_root="/some/other/path/without/the/evolution/token-in-data-root",
        prompt_file="/abs/prompt.txt",
        expected_outcome="completed",
    )
    unrelated = DomainExpansionCase(
        case_id="control_rnaseq",
        band=1,
        data_root="/abs/workspace/benchmark_data/rnaseq/control/data",
        prompt_file="/abs/prompt.txt",
        expected_outcome="completed",
    )

    assert runner_mod._case_prefix(by_data_root) == "evolution"
    assert runner_mod._case_prefix(by_case_id) == "evolution"
    assert runner_mod._case_prefix(unrelated) == "generic"


def test_evaluate_case_requires_primary_artifact_for_new_family_cases() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "domain_metabolomics")

    passed, reasons = _evaluate_case(
        case=case,
        status="completed",
        primary_artifact_present=False,
        error_text="",
        failure_root_cause="",
    )

    assert passed is False
    assert reasons == ["missing_primary_artifact"]


def test_evaluate_case_accepts_expected_bad_input_block() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "stress_assembly_malformed")

    passed, reasons = _evaluate_case(
        case=case,
        status="failed",
        primary_artifact_present=False,
        error_text="__FORMAT_INPUT_ERROR__:Malformed FASTQ",
        failure_root_cause="",
    )

    assert passed is True
    assert reasons == ["expected_bad_input_block"]


def test_evaluate_case_accepts_preflight_truncated_file_block() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "stress_assembly_malformed")

    passed, reasons = _evaluate_case(
        case=case,
        status="failed",
        primary_artifact_present=False,
        error_text=(
            "Preflight blocked execution due to blocking input-quality issues "
            "(truncated_file). Detected 1 input issue(s); blocking=true."
        ),
        failure_root_cause="",
    )

    assert passed is True
    assert reasons == ["expected_bad_input_block"]


def test_evaluate_case_accepts_preflight_format_mismatch_block() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "stress_assembly_malformed")

    passed, reasons = _evaluate_case(
        case=case,
        status="failed",
        primary_artifact_present=False,
        error_text=(
            "Preflight blocked execution due to blocking input-quality issues "
            "(format_mismatch). Detected 1 input issue(s); blocking=true."
        ),
        failure_root_cause="",
    )

    assert passed is True
    assert reasons == ["expected_bad_input_block"]


def test_evaluate_case_accepts_malformed_fastq_pipeline_abort() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "stress_assembly_malformed")

    passed, reasons = _evaluate_case(
        case=case,
        status="failed",
        primary_artifact_present=False,
        error_text="",
        failure_root_cause=(
            "Step failed with exit code 1. /data/assembly_malformed/reads.fastq "
            "at line 1980[2026-04-27 10:15:04] ERROR: Pipeline aborted"
        ),
    )

    assert passed is True
    assert reasons == ["expected_bad_input_block"]


def test_evaluate_case_does_not_accept_unrelated_preflight_block_as_bad_input() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "stress_assembly_malformed")

    passed, reasons = _evaluate_case(
        case=case,
        status="failed",
        primary_artifact_present=False,
        error_text=(
            "Preflight blocked execution due to blocking input-quality issues "
            "(missing_required_column). Detected 1 input issue(s); blocking=true."
        ),
        failure_root_cause="",
    )

    assert passed is False
    assert reasons == ["expected_bad_input_block_missing"]


def test_build_harness_command_applies_variant_overrides() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "domain_spatial")
    args = _Args()

    command = _build_harness_command(
        args=args,
        case=case,
        selected_dir=Path("/tmp/selected"),
        result_json=Path("/tmp/selected/result.json"),
        variant=DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_no_recovery"],
    )

    assert "--prompt-file" in command
    assert command[command.index("--max-repairs") + 1] == "0"
    assert command[command.index("--benchmark-policy") + 1] == "scientific_harness"


def test_build_harness_command_passes_explicit_executor_model_override() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "control_evolution")
    args = _Args()
    args.executor_model_name = "qwen3.6:35b-a3b"

    command = _build_harness_command(
        args=args,
        case=case,
        selected_dir=Path("/tmp/selected"),
        result_json=Path("/tmp/selected/result.json"),
        variant=DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_true_no_templates"],
    )

    assert command[command.index("--model-name") + 1] == "qwen3.6:35b-a3b"


def test_build_harness_command_passes_execution_mode_override() -> None:
    manifest_path = Path("<BIO_HARNESS_ROOT>/workspace/benchmark_data/ablation_manifest_24.json")
    cases = load_domain_expansion_manifest(
        manifest_path=manifest_path,
        project_root=Path("<BIO_HARNESS_ROOT>"),
    )
    case = next(item for item in cases if item.case_id == "control_evolution")
    args = _Args()
    args.execution_mode = "stepwise"

    command = _build_harness_command(
        args=args,
        case=case,
        selected_dir=Path("/tmp/selected"),
        result_json=Path("/tmp/selected/result.json"),
        variant=DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_true_no_templates"],
    )

    assert command[command.index("--execution-mode") + 1] == "stepwise"


def test_build_variant_env_includes_explicit_llm_timeouts() -> None:
    args = _Args()
    args.planner_attempt_timeout_seconds = 180
    args.llm_timeout_seconds = 120

    env = _build_variant_env(DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_full"], args=args)

    assert env["BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS"] == "180"
    assert env["BIO_HARNESS_LLM_TIMEOUT_SECONDS"] == "120"


def test_run_case_subprocess_forwards_selected_dir_as_progress_path(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_watchdog(**kwargs):
        captured.update(kwargs)
        return 0, False

    monkeypatch.setattr(runner_mod, "run_subprocess_with_watchdog", _fake_watchdog)
    selected_dir = tmp_path / "selected"
    log_path = tmp_path / "harness.log"
    progress_path_resolver = lambda: (selected_dir / "events.jsonl",)

    returncode, timed_out = _run_case_subprocess(
        cmd=["python3", "scripts/run_agent_e2e.py"],
        env={"A": "1"},
        log_path=log_path,
        timeout_seconds=1800,
        progress_paths=(selected_dir,),
        progress_path_resolver=progress_path_resolver,
    )

    assert returncode == 0
    assert timed_out is False
    assert captured["progress_paths"] == (selected_dir,)
    assert captured["progress_path_resolver"] is progress_path_resolver


def test_watchdog_progress_paths_discovers_run_artifacts(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    runs_root = project_root / "workspace" / "runs" / "run_001"
    runs_root.mkdir(parents=True)
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    (runs_root / "manifest.json").write_text(
        json.dumps({"selected_dir": str(selected_dir.resolve())}),
        encoding="utf-8",
    )
    for name in ("events.jsonl", "state.json", "exit.json", "completed_run_context.json"):
        (runs_root / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner_mod, "PROJECT_ROOT", project_root)

    run_dir = _find_run_dir_for_selected_dir(selected_dir=selected_dir)
    paths = _watchdog_progress_paths(selected_dir=selected_dir)

    assert run_dir == runs_root
    assert selected_dir in paths
    assert runs_root / "events.jsonl" in paths
    assert runs_root / "state.json" in paths


def test_build_variant_env_enables_fastpath_for_full_scientific_harness_variants() -> None:
    args = _Args()

    full_env = _build_variant_env(DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_full"], args=args)
    no_recovery_env = _build_variant_env(DOMAIN_EXPANSION_ABLATION_VARIANTS["gemma26_no_recovery"], args=args)
    no_templates_env = _build_variant_env(DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_true_no_templates"], args=args)

    assert full_env["BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH"] == "1"
    assert no_recovery_env["BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH"] == "1"
    assert no_templates_env["BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH"] == "0"
    assert no_templates_env["BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE"] == "False"


def test_build_variant_env_explicit_model_name_overrides_variant_defaults() -> None:
    args = _Args()
    args.model_name = "qwen3.6:35b-a3b"

    env = _build_variant_env(DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_true_no_templates"], args=args)

    assert env["BIO_HARNESS_MODEL"] == "qwen3.6:35b-a3b"
    assert env["BIO_HARNESS_MODEL_HEAVY"] == "qwen3.6:35b-a3b"
    assert env["BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH"] == "0"


def test_build_variant_env_planner_and_executor_overrides_split_models() -> None:
    args = _Args()
    args.executor_model_name = "gemma4:31b"
    args.planner_model_name = "qwen3.6:35b-a3b"

    env = _build_variant_env(DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_true_no_templates"], args=args)

    assert env["BIO_HARNESS_MODEL"] == "gemma4:31b"
    assert env["BIO_HARNESS_MODEL_HEAVY"] == "qwen3.6:35b-a3b"
    assert env["BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH"] == "0"


def test_render_template_lift_by_band_pairs_full_and_no_template_variants() -> None:
    summaries = [
        summarize_variant_results(
            variant=DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_full"],
            results=[
                DomainExpansionCaseResult(
                    case_id="a",
                    band=1,
                    variant_id="qwen_full",
                    benchmark_policy="scientific_harness",
                    expected_outcome="completed",
                    selected_dir="",
                    result_json="",
                    log_file="",
                    run_dir="",
                    status="completed",
                    passed=True,
                    timed_out=False,
                    harness_exit_code=0,
                    elapsed_seconds=10.0,
                    primary_artifact_present=True,
                    primary_artifact_paths=[],
                )
            ],
        ),
        summarize_variant_results(
            variant=DOMAIN_EXPANSION_ABLATION_VARIANTS["qwen_true_no_templates"],
            results=[
                DomainExpansionCaseResult(
                    case_id="a",
                    band=1,
                    variant_id="qwen_true_no_templates",
                    benchmark_policy="scientific_harness",
                    expected_outcome="completed",
                    selected_dir="",
                    result_json="",
                    log_file="",
                    run_dir="",
                    status="failed",
                    passed=False,
                    timed_out=False,
                    harness_exit_code=1,
                    elapsed_seconds=12.0,
                    primary_artifact_present=False,
                    primary_artifact_paths=[],
                )
            ],
        ),
    ]

    rows, markdown = render_template_lift_by_band(summaries)

    assert rows == [
        {
            "model": "qwen",
            "band": 1,
            "full_pass_rate": 1.0,
            "true_no_templates_pass_rate": 0.0,
            "template_lift": 1.0,
        },
        {
            "model": "qwen",
            "band": 2,
            "full_pass_rate": 0.0,
            "true_no_templates_pass_rate": 0.0,
            "template_lift": 0.0,
        },
        {
            "model": "qwen",
            "band": 3,
            "full_pass_rate": 0.0,
            "true_no_templates_pass_rate": 0.0,
            "template_lift": 0.0,
        },
    ]
    assert "Template Lift By Band" in markdown
