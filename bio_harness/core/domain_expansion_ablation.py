"""Helpers for the domain-expansion ablation suite.

This module keeps the 24-case domain-expansion ablation manifest, harness
variant definitions, and summary logic reusable so the CLI runner can stay
focused on orchestration and persistence.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bio_harness.core.variant_benchmark import HarnessVariant, VariantResult

QWEN_MODEL = "qwen3-coder-next:latest"
GEMMA26_MODEL = "gemma4:26b"

_EXPECTED_BAD_INPUT_CASE_IDS = frozenset({"stress_assembly_malformed"})

DOMAIN_EXPANSION_ABLATION_VARIANTS: dict[str, HarnessVariant] = {
    "qwen_full": HarnessVariant(
        variant_id="qwen_full",
        description="Qwen 3 Coder with the default scientific harness.",
        env_overrides={
            "BIO_HARNESS_MODEL": QWEN_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": QWEN_MODEL,
            "BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH": "1",
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
    "gemma26_full": HarnessVariant(
        variant_id="gemma26_full",
        description="Gemma 4 26B with the default scientific harness.",
        env_overrides={
            "BIO_HARNESS_MODEL": GEMMA26_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": GEMMA26_MODEL,
            "BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH": "1",
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
    "qwen_no_recovery": HarnessVariant(
        variant_id="qwen_no_recovery",
        description="Qwen without automatic repair loops.",
        env_overrides={
            "BIO_HARNESS_MODEL": QWEN_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": QWEN_MODEL,
            "BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH": "1",
        },
        config_overrides={"max_repairs": 0},
    ),
    "gemma26_no_recovery": HarnessVariant(
        variant_id="gemma26_no_recovery",
        description="Gemma 4 26B without automatic repair loops.",
        env_overrides={
            "BIO_HARNESS_MODEL": GEMMA26_MODEL,
            "BIO_HARNESS_MODEL_HEAVY": GEMMA26_MODEL,
            "BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH": "1",
        },
        config_overrides={"max_repairs": 0},
    ),
}

PRIMARY_MATRIX_VARIANT_IDS: tuple[str, ...] = (
    "qwen_full",
    "qwen_true_no_templates",
    "gemma26_full",
    "gemma26_true_no_templates",
)
SECONDARY_STRESS_VARIANT_IDS: tuple[str, ...] = (
    "qwen_no_recovery",
    "gemma26_no_recovery",
)


@dataclass(frozen=True)
class DomainExpansionCase:
    """One manifest case from the domain-expansion ablation suite.

    Attributes:
        case_id: Stable case identifier.
        band: Stable band id from the 24-case manifest.
        data_root: Absolute data-root path for the harness run.
        prompt_file: Absolute prompt-file path for the harness run.
        expected_outcome: High-level expected result class.
    """

    case_id: str
    band: int
    data_root: str
    prompt_file: str
    expected_outcome: str


@dataclass(frozen=True)
class DomainExpansionCaseResult:
    """Observed result for one case under one ablation variant."""

    case_id: str
    band: int
    variant_id: str
    benchmark_policy: str
    expected_outcome: str
    selected_dir: str
    result_json: str
    log_file: str
    run_dir: str
    status: str
    passed: bool
    timed_out: bool
    harness_exit_code: int
    elapsed_seconds: float
    primary_artifact_present: bool
    primary_artifact_paths: list[str]
    error: str = ""
    failure_root_cause: str = ""
    failure_suggested_fix: str = ""
    auto_repair_history_count: int = 0
    planner_failopen_used: bool = False
    generic_template_fallback_used: bool = False
    protocol_template_fallback_used: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BandSummary:
    """Aggregate metrics for one band within one ablation variant."""

    band: int
    count: int
    passed: int
    pass_rate: float


@dataclass(frozen=True)
class DomainExpansionVariantSummary:
    """Aggregate metrics for one domain-expansion ablation sweep."""

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
    planner_failopen_rate: float
    band_breakdown: list[BandSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable payload."""

        payload = asdict(self)
        payload["band_breakdown"] = [asdict(item) for item in self.band_breakdown]
        return payload


def load_domain_expansion_manifest(
    *,
    manifest_path: Path,
    project_root: Path,
) -> tuple[DomainExpansionCase, ...]:
    """Load the 24-case domain-expansion manifest.

    Args:
        manifest_path: JSON manifest path.
        project_root: Repository root used to resolve relative paths.

    Returns:
        Ordered tuple of domain-expansion cases.
    """

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", [])
    if not isinstance(raw_cases, list):
        raise ValueError("Domain-expansion manifest must contain a top-level 'cases' list.")
    cases: list[DomainExpansionCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("Each domain-expansion case must be a JSON object.")
        case_id = str(raw_case.get("id", "") or "").strip()
        band = int(raw_case.get("band", 0) or 0)
        data_root = _resolve_repo_path(project_root, str(raw_case.get("data_root", "") or "").strip())
        prompt_file = _resolve_repo_path(project_root, str(raw_case.get("prompt_file", "") or "").strip())
        if not case_id or not band or not data_root or not prompt_file:
            raise ValueError(f"Domain-expansion case is missing required fields: {raw_case}")
        cases.append(
            DomainExpansionCase(
                case_id=case_id,
                band=band,
                data_root=data_root,
                prompt_file=prompt_file,
                expected_outcome=expected_outcome_for_case(case_id),
            )
        )
    return tuple(cases)


def expected_outcome_for_case(case_id: str) -> str:
    """Return the expected outcome class for one case id."""

    return "blocked_bad_input" if str(case_id).strip() in _EXPECTED_BAD_INPUT_CASE_IDS else "completed"


def summarize_variant_results(
    *,
    variant: HarnessVariant,
    results: list[DomainExpansionCaseResult],
) -> DomainExpansionVariantSummary:
    """Aggregate one sweep's stored case results into study metrics."""

    if not results:
        return DomainExpansionVariantSummary(
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
            planner_failopen_rate=0.0,
        )
    count = len(results)
    passed = sum(1 for item in results if item.passed)
    band_breakdown: list[BandSummary] = []
    for band in sorted({item.band for item in results}):
        rows = [item for item in results if item.band == band]
        band_passed = sum(1 for item in rows if item.passed)
        band_breakdown.append(
            BandSummary(
                band=band,
                count=len(rows),
                passed=band_passed,
                pass_rate=round(band_passed / len(rows), 4),
            )
        )
    return DomainExpansionVariantSummary(
        variant_id=variant.variant_id,
        description=variant.description,
        count=count,
        passed=passed,
        failures=count - passed,
        pass_rate=round(passed / count, 4),
        mean_runtime_seconds=round(sum(item.elapsed_seconds for item in results) / count, 4),
        mean_repairs=round(sum(item.auto_repair_history_count for item in results) / count, 4),
        generic_fallback_rate=round(sum(1 for item in results if item.generic_template_fallback_used) / count, 4),
        protocol_fallback_rate=round(sum(1 for item in results if item.protocol_template_fallback_used) / count, 4),
        planner_failopen_rate=round(sum(1 for item in results if item.planner_failopen_used) / count, 4),
        band_breakdown=band_breakdown,
    )


def render_variant_markdown(summaries: list[DomainExpansionVariantSummary]) -> str:
    """Render a compact Markdown summary for multiple sweeps."""

    lines = [
        "# Domain Expansion Ablation Study",
        "",
        "| Variant | Cases | Pass Rate | Band 1 | Band 2 | Band 3 | Mean Runtime (s) | Mean Repairs |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for summary in summaries:
        by_band = {item.band: item.pass_rate for item in summary.band_breakdown}
        lines.append(
            "| "
            f"{summary.variant_id} | {summary.count} | {summary.pass_rate:.1%} | "
            f"{by_band.get(1, 0.0):.1%} | {by_band.get(2, 0.0):.1%} | {by_band.get(3, 0.0):.1%} | "
            f"{summary.mean_runtime_seconds:.1f} | {summary.mean_repairs:.2f} |"
        )
    return "\n".join(lines)


def case_result_to_variant_result(result: DomainExpansionCaseResult) -> VariantResult:
    """Convert one case result into a stored variant-benchmark row."""

    return VariantResult(
        variant_id=result.variant_id,
        task_name=result.case_id,
        status="pass" if result.passed else (result.status or "fail"),
        score=1.0 if result.passed else 0.0,
        runtime_seconds=float(result.elapsed_seconds or 0.0),
        repairs_needed=int(result.auto_repair_history_count or 0),
        error_message=result.error,
        metadata={
            "band": result.band,
            "expected_outcome": result.expected_outcome,
            "selected_dir": result.selected_dir,
            "result_json": result.result_json,
            "log_file": result.log_file,
            "run_dir": result.run_dir,
            "primary_artifact_present": result.primary_artifact_present,
            "primary_artifact_paths": list(result.primary_artifact_paths),
            "failure_root_cause": result.failure_root_cause,
            "failure_suggested_fix": result.failure_suggested_fix,
            "planner_failopen_used": result.planner_failopen_used,
            "generic_template_fallback_used": result.generic_template_fallback_used,
            "protocol_template_fallback_used": result.protocol_template_fallback_used,
            "reasons": list(result.reasons),
        },
    )


def render_template_lift_by_band(
    summaries: list[DomainExpansionVariantSummary],
) -> tuple[list[dict[str, Any]], str]:
    """Render paired full-vs-template-off lift rows by model and band."""

    indexed = {item.variant_id: item for item in summaries}
    rows: list[dict[str, Any]] = []
    for model_prefix, full_id, off_id in (
        ("qwen", "qwen_full", "qwen_true_no_templates"),
        ("gemma26", "gemma26_full", "gemma26_true_no_templates"),
    ):
        full_summary = indexed.get(full_id)
        off_summary = indexed.get(off_id)
        if full_summary is None or off_summary is None:
            continue
        full_by_band = {item.band: item.pass_rate for item in full_summary.band_breakdown}
        off_by_band = {item.band: item.pass_rate for item in off_summary.band_breakdown}
        for band in (1, 2, 3):
            rows.append(
                {
                    "model": model_prefix,
                    "band": band,
                    "full_pass_rate": round(full_by_band.get(band, 0.0), 4),
                    "true_no_templates_pass_rate": round(off_by_band.get(band, 0.0), 4),
                    "template_lift": round(full_by_band.get(band, 0.0) - off_by_band.get(band, 0.0), 4),
                }
            )
    lines = [
        "# Template Lift By Band",
        "",
        "| Model | Band | Full | True No Templates | Lift |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| "
            f"{row['model']} | {row['band']} | {row['full_pass_rate']:.1%} | "
            f"{row['true_no_templates_pass_rate']:.1%} | {row['template_lift']:.1%} |"
        )
    return rows, "\n".join(lines)


def _resolve_repo_path(project_root: Path, raw_path: str) -> str:
    rendered = str(raw_path or "").strip()
    if not rendered:
        return ""
    path = Path(rendered)
    if not path.is_absolute():
        path = (project_root / path).resolve()
    return str(path)


__all__ = [
    "case_result_to_variant_result",
    "DOMAIN_EXPANSION_ABLATION_VARIANTS",
    "DomainExpansionCase",
    "DomainExpansionCaseResult",
    "DomainExpansionVariantSummary",
    "expected_outcome_for_case",
    "load_domain_expansion_manifest",
    "PRIMARY_MATRIX_VARIANT_IDS",
    "render_template_lift_by_band",
    "render_variant_markdown",
    "SECONDARY_STRESS_VARIANT_IDS",
    "summarize_variant_results",
]
