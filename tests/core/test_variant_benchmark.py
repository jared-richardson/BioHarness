from __future__ import annotations

from pathlib import Path

from bio_harness.core.variant_benchmark import (
    ABLATION_VARIANTS,
    VariantBenchmarkStore,
    VariantResult,
    config_override_cli_args,
    config_override_env,
)


def test_variant_benchmark_store_round_trips_results(tmp_path: Path) -> None:
    store = VariantBenchmarkStore(tmp_path / "variants.jsonl")
    store.record_result(
        VariantResult(
            variant_id="baseline",
            task_name="task_a",
            status="pass",
            score=1.0,
            runtime_seconds=5.5,
            repairs_needed=1,
        )
    )

    results = store.load_results()

    assert len(results) == 1
    assert results[0].variant_id == "baseline"
    assert results[0].task_name == "task_a"


def test_variant_benchmark_store_filters_by_variant_and_task(tmp_path: Path) -> None:
    store = VariantBenchmarkStore(tmp_path / "variants.jsonl")
    store.record_result(VariantResult(variant_id="baseline", task_name="task_a", status="pass", score=1.0))
    store.record_result(VariantResult(variant_id="no_templates", task_name="task_a", status="fail", score=0.0))
    store.record_result(VariantResult(variant_id="baseline", task_name="task_b", status="pass", score=0.8))

    baseline = store.load_results(variant_id="baseline")
    task_a = store.load_results(task_name="task_a")

    assert len(baseline) == 2
    assert len(task_a) == 2
    assert all(row.task_name == "task_a" for row in task_a)


def test_compare_variants_summarizes_shared_tasks(tmp_path: Path) -> None:
    store = VariantBenchmarkStore(tmp_path / "variants.jsonl")
    store.record_result(
        VariantResult(
            variant_id="baseline",
            task_name="task_a",
            status="pass",
            score=1.0,
            runtime_seconds=10.0,
            repairs_needed=1,
        )
    )
    store.record_result(
        VariantResult(
            variant_id="baseline",
            task_name="task_b",
            status="fail",
            score=0.1,
            runtime_seconds=20.0,
            repairs_needed=2,
        )
    )
    store.record_result(
        VariantResult(
            variant_id="no_templates",
            task_name="task_a",
            status="pass",
            score=0.8,
            runtime_seconds=12.0,
            repairs_needed=0,
        )
    )
    store.record_result(
        VariantResult(
            variant_id="no_templates",
            task_name="task_b",
            status="pass",
            score=0.6,
            runtime_seconds=18.0,
            repairs_needed=1,
        )
    )

    comparison = store.compare_variants("baseline", "no_templates")

    assert comparison.tasks_compared == 2
    assert comparison.a_wins == 1
    assert comparison.b_wins == 1
    assert comparison.ties == 0
    assert comparison.a_pass_rate == 0.5
    assert comparison.b_pass_rate == 1.0
    assert len(comparison.per_task) == 2


def test_compare_variants_returns_empty_summary_without_shared_tasks(tmp_path: Path) -> None:
    store = VariantBenchmarkStore(tmp_path / "variants.jsonl")
    store.record_result(VariantResult(variant_id="baseline", task_name="task_a", status="pass", score=1.0))
    store.record_result(VariantResult(variant_id="no_templates", task_name="task_b", status="pass", score=1.0))

    comparison = store.compare_variants("baseline", "no_templates")

    assert comparison.tasks_compared == 0
    assert comparison.per_task == []


def test_ablation_variants_include_expected_baselines() -> None:
    assert "baseline" in ABLATION_VARIANTS
    assert "qwen_baseline" in ABLATION_VARIANTS
    assert "no_recovery" in ABLATION_VARIANTS


def test_config_override_env_renders_metaharness_flags() -> None:
    rendered = config_override_env(
        {
            "diagnostic_traces": False,
            "trace_advisories": True,
            "protocol_template_assistance": False,
            "max_repairs": 0,
        }
    )

    assert rendered == {
        "BIO_HARNESS_DIAGNOSTIC_TRACES": "False",
        "BIO_HARNESS_TRACE_ADVISORIES": "True",
        "BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE": "False",
    }


def test_config_override_cli_args_renders_max_repairs() -> None:
    assert config_override_cli_args({"max_repairs": 0, "execution_mode": "stepwise", "env_bootstrap": False}) == [
        "--max-repairs",
        "0",
        "--execution-mode",
        "stepwise",
    ]
