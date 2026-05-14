"""Helpers for extended scientific-suite ablation studies.

This module keeps the extended-suite ablation configuration and aggregation
logic reusable so the CLI runner can stay focused on orchestration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from bio_harness.core.variant_benchmark import HarnessVariant, VariantResult

QWEN_MODEL = "qwen3-coder-next:latest"
GEMMA26_MODEL = "gemma4:26b"

SANITY_LANES = frozenset(
    {
        "scanpy_prompt_grounding",
        "deseq_prompt_grounding",
        "stringtie_prompt_grounding",
    }
)
STRESS_LANES = frozenset(
    {
        "scanpy_adversarial",
        "deseq_adversarial",
        "stringtie_adversarial",
        "cross_tool_contamination",
        "output_path_fidelity",
    }
)

EXTENDED_SUITE_ABLATION_VARIANTS: dict[str, HarnessVariant] = {
    "qwen_full": HarnessVariant(
        variant_id="qwen_full",
        description="Qwen 3 Coder with the default scientific harness.",
        env_overrides={
            "BIO_HARNESS_MODEL": QWEN_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": QWEN_MODEL,
        },
    ),
    "qwen_true_no_templates": HarnessVariant(
        variant_id="qwen_true_no_templates",
        description="Qwen with deterministic scientific template assistance disabled.",
        env_overrides={
            "BIO_HARNESS_MODEL": QWEN_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": QWEN_MODEL,
            "BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH": "0",
        },
        config_overrides={"protocol_template_assistance": False},
    ),
    "qwen_no_bootstrap": HarnessVariant(
        variant_id="qwen_no_bootstrap",
        description="Qwen without environment bootstrap context.",
        env_overrides={
            "BIO_HARNESS_MODEL": QWEN_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": QWEN_MODEL,
        },
        config_overrides={"env_bootstrap": False},
    ),
    "qwen_no_recovery": HarnessVariant(
        variant_id="qwen_no_recovery",
        description="Qwen without automatic repair loops.",
        env_overrides={
            "BIO_HARNESS_MODEL": QWEN_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": QWEN_MODEL,
        },
        config_overrides={"max_repairs": 0},
    ),
    "gemma26_full": HarnessVariant(
        variant_id="gemma26_full",
        description="Gemma 4 26B with the default scientific harness.",
        env_overrides={
            "BIO_HARNESS_MODEL": GEMMA26_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": GEMMA26_MODEL,
        },
    ),
    "gemma26_true_no_templates": HarnessVariant(
        variant_id="gemma26_true_no_templates",
        description="Gemma 4 26B with deterministic scientific template assistance disabled.",
        env_overrides={
            "BIO_HARNESS_MODEL": GEMMA26_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": GEMMA26_MODEL,
            "BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH": "0",
        },
        config_overrides={"protocol_template_assistance": False},
    ),
    "gemma26_no_bootstrap": HarnessVariant(
        variant_id="gemma26_no_bootstrap",
        description="Gemma 4 26B without environment bootstrap context.",
        env_overrides={
            "BIO_HARNESS_MODEL": GEMMA26_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": GEMMA26_MODEL,
        },
        config_overrides={"env_bootstrap": False},
    ),
    "gemma26_no_recovery": HarnessVariant(
        variant_id="gemma26_no_recovery",
        description="Gemma 4 26B without automatic repair loops.",
        env_overrides={
            "BIO_HARNESS_MODEL": GEMMA26_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": GEMMA26_MODEL,
        },
        config_overrides={"max_repairs": 0},
    ),
}


@dataclass(frozen=True)
class LaneSummary:
    """Aggregate metrics for one extended-suite lane."""

    lane: str
    count: int
    passed: int
    pass_rate: float
    mean_runtime_seconds: float
    mean_repairs: float
    forbidden_tool_cases: int
    output_path_clean_cases: int
    generic_fallback_cases: int
    protocol_fallback_cases: int
    planner_failopen_cases: int


@dataclass(frozen=True)
class ExtendedSuiteStudySummary:
    """Aggregate metrics for one extended-suite ablation sweep."""

    variant_id: str
    description: str
    count: int
    passed: int
    failures: int
    pass_rate: float
    mean_runtime_seconds: float
    mean_repairs: float
    generic_fallback_rate: float
    protocol_fallback_rate: float
    forbidden_tool_drift_rate: float
    output_path_fidelity_rate: float
    planner_failopen_rate: float
    stress_case_count: int
    stress_pass_rate: float
    sanity_case_count: int
    sanity_pass_rate: float
    lane_breakdown: list[LaneSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary payload."""

        payload = asdict(self)
        payload["lane_breakdown"] = [asdict(row) for row in self.lane_breakdown]
        return payload


def case_result_to_variant_result(
    *,
    variant_id: str,
    attempt_label: str,
    suite_out_root: str,
    item: dict[str, Any],
) -> VariantResult:
    """Convert one suite-summary case item into a stored variant result."""

    passed = bool(item.get("passed", False))
    status_token = str(item.get("status", "") or "").strip().lower()
    status = "pass" if passed else (status_token if status_token not in {"", "completed"} else "fail")
    metadata = {
        "attempt_label": str(attempt_label),
        "suite_out_root": str(suite_out_root),
        "lane": str(item.get("lane", "") or "").strip(),
        "description": str(item.get("description", "") or "").strip(),
        "benchmark_policy": str(item.get("benchmark_policy", "") or "").strip(),
        "selected_dir": str(item.get("selected_dir", "") or "").strip(),
        "result_json": str(item.get("result_json", "") or "").strip(),
        "log_file": str(item.get("log_file", "") or "").strip(),
        "run_dir": str(item.get("run_dir", "") or "").strip(),
        "exec_file": str(item.get("exec_file", "") or "").strip(),
        "elapsed_seconds": float(item.get("elapsed_seconds", 0.0) or 0.0),
        "planner_strategy_used": str(item.get("planner_strategy_used", "") or "").strip(),
        "auto_repair_history_count": int(item.get("auto_repair_history_count", 0) or 0),
        "planner_failopen_used": bool(item.get("planner_failopen_used", False)),
        "actual_tools": list(item.get("actual_tools", []) or []),
        "missing_artifacts": list(item.get("missing_artifacts", []) or []),
        "missing_required_tools": list(item.get("missing_required_tools", []) or []),
        "forbidden_tools_detected": list(item.get("forbidden_tools_detected", []) or []),
        "failed_column_checks": list(item.get("failed_column_checks", []) or []),
        "generic_template_fallback_used": bool(item.get("generic_template_fallback_used", False)),
        "protocol_template_fallback_used": bool(item.get("protocol_template_fallback_used", False)),
        "reasons": list(item.get("reasons", []) or []),
    }
    return VariantResult(
        variant_id=variant_id,
        task_name=str(item.get("case_id", "") or "").strip(),
        status=status,
        score=1.0 if passed else 0.0,
        runtime_seconds=float(item.get("elapsed_seconds", 0.0) or 0.0),
        repairs_needed=int(item.get("auto_repair_history_count", 0) or 0),
        error_message=str(item.get("error", "") or "").strip(),
        metadata=metadata,
    )


def summarize_variant_results(
    *,
    variant: HarnessVariant,
    results: list[VariantResult],
) -> ExtendedSuiteStudySummary:
    """Aggregate one sweep's stored case results into study metrics."""

    if not results:
        return ExtendedSuiteStudySummary(
            variant_id=variant.variant_id,
            description=variant.description,
            count=0,
            passed=0,
            failures=0,
            pass_rate=0.0,
            mean_runtime_seconds=0.0,
            mean_repairs=0.0,
            generic_fallback_rate=0.0,
            protocol_fallback_rate=0.0,
            forbidden_tool_drift_rate=0.0,
            output_path_fidelity_rate=0.0,
            planner_failopen_rate=0.0,
            stress_case_count=0,
            stress_pass_rate=0.0,
            sanity_case_count=0,
            sanity_pass_rate=0.0,
        )

    passed_count = sum(1 for row in results if row.status == "pass")
    mean_runtime = sum(float(row.runtime_seconds) for row in results) / len(results)
    mean_repairs = sum(int(row.repairs_needed) for row in results) / len(results)
    generic_fallback_cases = sum(1 for row in results if _metadata_bool(row, "generic_template_fallback_used"))
    protocol_fallback_cases = sum(1 for row in results if _metadata_bool(row, "protocol_template_fallback_used"))
    forbidden_tool_cases = sum(1 for row in results if _metadata_list(row, "forbidden_tools_detected"))
    output_path_clean_cases = sum(
        1
        for row in results
        if not _metadata_list(row, "missing_artifacts") and not _metadata_list(row, "failed_column_checks")
    )
    planner_failopen_cases = sum(1 for row in results if _metadata_bool(row, "planner_failopen_used"))

    stress_rows = [row for row in results if _metadata_lane(row) in STRESS_LANES]
    sanity_rows = [row for row in results if _metadata_lane(row) in SANITY_LANES]

    lane_breakdown: list[LaneSummary] = []
    for lane in sorted({_metadata_lane(row) for row in results if _metadata_lane(row)}):
        lane_rows = [row for row in results if _metadata_lane(row) == lane]
        lane_passed = sum(1 for row in lane_rows if row.status == "pass")
        lane_breakdown.append(
            LaneSummary(
                lane=lane,
                count=len(lane_rows),
                passed=lane_passed,
                pass_rate=round(lane_passed / len(lane_rows), 4),
                mean_runtime_seconds=round(
                    sum(float(row.runtime_seconds) for row in lane_rows) / len(lane_rows), 4
                ),
                mean_repairs=round(
                    sum(int(row.repairs_needed) for row in lane_rows) / len(lane_rows), 4
                ),
                forbidden_tool_cases=sum(
                    1 for row in lane_rows if _metadata_list(row, "forbidden_tools_detected")
                ),
                output_path_clean_cases=sum(
                    1
                    for row in lane_rows
                    if not _metadata_list(row, "missing_artifacts")
                    and not _metadata_list(row, "failed_column_checks")
                ),
                generic_fallback_cases=sum(
                    1 for row in lane_rows if _metadata_bool(row, "generic_template_fallback_used")
                ),
                protocol_fallback_cases=sum(
                    1 for row in lane_rows if _metadata_bool(row, "protocol_template_fallback_used")
                ),
                planner_failopen_cases=sum(
                    1 for row in lane_rows if _metadata_bool(row, "planner_failopen_used")
                ),
            )
        )

    return ExtendedSuiteStudySummary(
        variant_id=variant.variant_id,
        description=variant.description,
        count=len(results),
        passed=passed_count,
        failures=len(results) - passed_count,
        pass_rate=round(passed_count / len(results), 4),
        mean_runtime_seconds=round(mean_runtime, 4),
        mean_repairs=round(mean_repairs, 4),
        generic_fallback_rate=round(generic_fallback_cases / len(results), 4),
        protocol_fallback_rate=round(protocol_fallback_cases / len(results), 4),
        forbidden_tool_drift_rate=round(forbidden_tool_cases / len(results), 4),
        output_path_fidelity_rate=round(output_path_clean_cases / len(results), 4),
        planner_failopen_rate=round(planner_failopen_cases / len(results), 4),
        stress_case_count=len(stress_rows),
        stress_pass_rate=_pass_rate(stress_rows),
        sanity_case_count=len(sanity_rows),
        sanity_pass_rate=_pass_rate(sanity_rows),
        lane_breakdown=lane_breakdown,
    )


def render_study_markdown(summaries: list[ExtendedSuiteStudySummary]) -> str:
    """Render a compact Markdown summary for multiple ablation sweeps."""

    lines = [
        "# Extended Suite Ablation Study",
        "",
        "| Variant | Cases | Pass Rate | Stress Pass | Sanity Pass | Forbidden Drift | Output Fidelity | Mean Runtime (s) | Mean Repairs |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        lines.append(
            "| "
            f"{summary.variant_id} | {summary.count} | {summary.pass_rate:.1%} | "
            f"{summary.stress_pass_rate:.1%} | {summary.sanity_pass_rate:.1%} | "
            f"{summary.forbidden_tool_drift_rate:.1%} | {summary.output_path_fidelity_rate:.1%} | "
            f"{summary.mean_runtime_seconds:.1f} | {summary.mean_repairs:.1f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _metadata_bool(row: VariantResult, key: str) -> bool:
    return bool(row.metadata.get(key, False)) if isinstance(row.metadata, dict) else False


def _metadata_list(row: VariantResult, key: str) -> list[Any]:
    if not isinstance(row.metadata, dict):
        return []
    value = row.metadata.get(key, [])
    return list(value) if isinstance(value, list) else []


def _metadata_lane(row: VariantResult) -> str:
    if not isinstance(row.metadata, dict):
        return ""
    return str(row.metadata.get("lane", "") or "").strip()


def _pass_rate(rows: list[VariantResult]) -> float:
    if not rows:
        return 0.0
    return round(sum(1 for row in rows if row.status == "pass") / len(rows), 4)


__all__ = [
    "EXTENDED_SUITE_ABLATION_VARIANTS",
    "ExtendedSuiteStudySummary",
    "GEMMA26_MODEL",
    "LaneSummary",
    "QWEN_MODEL",
    "SANITY_LANES",
    "STRESS_LANES",
    "case_result_to_variant_result",
    "render_study_markdown",
    "summarize_variant_results",
]
