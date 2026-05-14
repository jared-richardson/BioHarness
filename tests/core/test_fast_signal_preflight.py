"""Tests for fast-model preflight planning and recording."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from bio_harness.core.fast_signal_minibench import (
    DEFAULT_MINI_BENCHMARK_CONTRACTS,
)
from bio_harness.core.fast_signal_preflight import (
    DEFAULT_MINI_PREFLIGHT_BENCHMARK_POLICY,
    DEFAULT_MINI_PREFLIGHT_PROTOCOL_GROUNDING_SCOPE,
    DEFAULT_PREFLIGHT_GATE,
    PreflightCase,
    PreflightCaseResult,
    PreflightPlan,
    PreflightRunResult,
    append_preflight_scorecard_rows,
    build_domain_preflight_plan,
    build_mini_preflight_plan,
    resolve_mini_case_ids,
    run_preflight_plan,
)
from bio_harness.core.fast_signal_scorecard import ScorecardStore


def test_mini_preflight_plan_uses_strict_binder_selected_dirs(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    plan = build_mini_preflight_plan(
        project_root=project_root,
        mini_root=tmp_path / "mini",
        case_ids=("control_evolution",),
        model="fast-model",
        python_executable="/python",
        prepare_suite=True,
        heartbeat_seconds=11,
    )
    case = plan.cases[0]
    command = list(case.command)

    assert case.case_id == "control_evolution_mini"
    assert Path(case.selected_dir) == (
        project_root
        / "workspace"
        / "fast_signal_preflight"
        / "mini_runs"
        / "evolution"
        / "attempt1"
    ).resolve(strict=False)
    assert command[0] == "/python"
    assert command[command.index("--prompt-file") + 1].endswith(
        "/tasks/evolution/prompt.txt"
    )
    assert command[command.index("--data-root") + 1].endswith(
        "/tasks/evolution/data"
    )
    assert command[command.index("--selected-dir") + 1] == case.selected_dir
    assert command[command.index("--analysis-type") + 1] == (
        "bacterial_evolution_variant_calling"
    )
    assert command[command.index("--benchmark-policy") + 1] == (
        DEFAULT_MINI_PREFLIGHT_BENCHMARK_POLICY
    )
    assert plan.env_overrides["BIO_HARNESS_PROTOCOL_GROUNDING_SCOPE"] == (
        DEFAULT_MINI_PREFLIGHT_PROTOCOL_GROUNDING_SCOPE
    )
    assert plan.metadata["protocol_grounding_scope"] == (
        DEFAULT_MINI_PREFLIGHT_PROTOCOL_GROUNDING_SCOPE
    )
    assert command[command.index("--result-json") + 1].endswith(
        "/workspace/fast_signal_preflight/mini_runs/evolution/attempt1/fast_model_preflight_result.json"
    )
    assert case.analysis_type == "bacterial_evolution_variant_calling"
    assert command[command.index("--heartbeat-seconds") + 1] == "11"
    assert plan.env_overrides["BIO_HARNESS_MODEL"] == "fast-model"
    assert plan.env_overrides["BIO_HARNESS_MODEL_HEAVY"] == "fast-model"
    assert (tmp_path / "mini" / "tasks" / "evolution" / "prompt.txt").is_file()


def test_mini_preflight_keeps_workspace_suite_selected_dirs(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    mini_root = project_root / "workspace" / "benchmark_data" / "fast_signal_mini"

    plan = build_mini_preflight_plan(
        project_root=project_root,
        mini_root=mini_root,
        case_ids=("control_evolution",),
        model="fast-model",
        python_executable="/python",
        prepare_suite=True,
    )

    assert Path(plan.cases[0].selected_dir) == (
        mini_root / "official_runs" / "evolution" / "attempt1"
    ).resolve(strict=False)
    assert plan.metadata["selected_root"] == str(
        (mini_root / "official_runs").resolve(strict=False)
    )


def test_mini_preflight_plan_rejects_unknown_case() -> None:
    with pytest.raises(ValueError, match="Unknown mini preflight case_id"):
        resolve_mini_case_ids(("not_a_case",))


def test_domain_preflight_plan_preserves_legacy_runner_shape(tmp_path: Path) -> None:
    plan = build_domain_preflight_plan(
        project_root=tmp_path / "repo",
        manifest_file=tmp_path / "manifest.json",
        case_ids=("control_evolution",),
        model="fast-model",
        attempt_label="preflight",
        python_executable="/python",
    )
    command = list(plan.cases[0].command)

    assert plan.suite == "domain"
    assert command[0] == "/python"
    assert command[1].endswith("/scripts/run_domain_expansion_ablation.py")
    assert command[command.index("--variant") + 1] == "qwen_true_no_templates"
    assert command[command.index("--planner-model-name") + 1] == "fast-model"
    assert command[command.index("--executor-model-name") + 1] == "fast-model"
    assert command[command.index("--case-id") + 1] == "control_evolution"


def test_mini_preflight_cleans_selected_dir_before_contract_validation(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "mini" / "official_runs" / "evolution" / "attempt1"
    stale_final = selected_dir / "final"
    stale_final.mkdir(parents=True)
    (stale_final / "variants_shared.csv").write_text(
        "CHROM,POS\nchrMini,10\n",
        encoding="utf-8",
    )
    case = PreflightCase(
        case_id="control_evolution_mini",
        analysis_family="evolution",
        command=(sys.executable, "-c", "pass"),
        selected_dir=str(selected_dir),
        contract=DEFAULT_MINI_BENCHMARK_CONTRACTS[
            "control_evolution_mini"
        ].to_mapping(),
    )
    plan = PreflightPlan(
        suite="mini",
        model="fast-model",
        cases=(case,),
        env_overrides={},
        metadata={"mini_root": str(tmp_path / "mini")},
    )

    result = run_preflight_plan(
        plan,
        project_root=tmp_path,
        clean_selected_dirs=True,
    )

    assert result.status == "fail"
    assert result.case_results[0].returncode == 0
    assert result.case_results[0].contract_validation["passed"] is False
    assert not (stale_final / "variants_shared.csv").exists()


def test_mini_preflight_fails_when_forbidden_sources_are_visible(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "mini" / "official_runs" / "evolution" / "attempt1"
    final_dir = selected_dir / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "variants_shared.csv").write_text(
        "CHROM,POS\nchrMini,10\n",
        encoding="utf-8",
    )
    result_json = selected_dir / "fast_model_preflight_result.json"
    forbidden_source = (
        "/repo/external/bioagent-bench/tasks/evolution/run_script.sh"
    )
    result_json.write_text(
        json.dumps(
            {
                "assistance_manifest": {
                    "forbidden_benchmark_sources_visible": True,
                    "forbidden_benchmark_sources": [forbidden_source],
                },
            }
        ),
        encoding="utf-8",
    )
    case = PreflightCase(
        case_id="control_evolution_mini",
        analysis_family="evolution",
        command=(sys.executable, "-c", "pass"),
        selected_dir=str(selected_dir),
        result_json=str(result_json),
        contract=DEFAULT_MINI_BENCHMARK_CONTRACTS[
            "control_evolution_mini"
        ].to_mapping(),
    )
    plan = PreflightPlan(
        suite="mini",
        model="fast-model",
        cases=(case,),
        env_overrides={},
        metadata={"mini_root": str(tmp_path / "mini")},
    )

    result = run_preflight_plan(
        plan,
        project_root=tmp_path,
        clean_selected_dirs=False,
    )

    validation = result.case_results[0].contract_validation
    assert result.status == "fail"
    assert result.case_results[0].status == "fail"
    assert validation["passed"] is False
    assert validation["forbidden_benchmark_sources_visible"] is True
    assert validation["forbidden_benchmark_sources"] == [forbidden_source]
    assert any("forbidden benchmark sources visible" in issue for issue in validation["issues"])


def test_preflight_scorecard_rows_record_advisory_metadata(tmp_path: Path) -> None:
    plan = build_mini_preflight_plan(
        project_root=tmp_path / "repo",
        mini_root=tmp_path / "mini",
        case_ids=("de_mini",),
        model="fast-model",
        python_executable="/python",
    )
    run_result = PreflightRunResult(
        status="pass",
        suite=plan.suite,
        model=plan.model,
        case_results=(
            PreflightCaseResult(
                case_id="de_mini",
                status="pass",
                returncode=0,
                elapsed_seconds=1.25,
                contract_validation={"passed": True},
            ),
        ),
        plan=plan.to_mapping(),
    )
    scorecard = tmp_path / "scorecard.jsonl"

    append_preflight_scorecard_rows(
        plan=plan,
        run_result=run_result,
        scorecard_path=scorecard,
        model_digest="digest",
        backend_version="backend",
        optimization_profile="safe_local",
        measurement_purpose="preflight_smoke",
    )

    rows = ScorecardStore(scorecard).load()
    assert len(rows) == 1
    assert rows[0].experiment_id == "de_mini"
    assert rows[0].gate == DEFAULT_PREFLIGHT_GATE
    assert rows[0].status == "pass"
    assert rows[0].model == "fast-model"
    assert rows[0].model_digest == "digest"
    assert rows[0].measurement_purpose == "preflight_smoke"
    assert rows[0].metadata["analysis_family"] == "de"
    assert rows[0].metadata["analysis_type"] == "rna_seq_differential_expression"
    assert rows[0].metadata["contract_validation"] == {"passed": True}
