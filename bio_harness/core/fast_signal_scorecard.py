"""Scorecard helpers for fast-signal gate effectiveness.

The scorecard stores one append-only JSONL row per gate or benchmark outcome
and summarizes whether fast gates predict full-run outcomes after accounting
for reproduction-rate baselines.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bio_harness.core.failure_classes import (
    UNCLASSIFIED_FAILURE_CLASS_ID,
    resolve_failure_class,
)

SCORECARD_SCHEMA_VERSION = 2
EXPLORATORY_OPTIMIZATION_PROFILE = "exploratory_only"
CANONICAL_B1_REPRODUCTION_BASELINE_KIND = "canonical_b1_reproduction_baseline"


@dataclass(frozen=True)
class ScorecardRow:
    """One fast-signal scorecard observation.

    Attributes:
        experiment_id: Experiment or fixture identifier.
        gate: Gate name, such as ``replay`` or ``mini_benchmark``.
        status: Gate status, such as ``pass``, ``fail``, or ``advisory``.
        full_run_status: Full benchmark status when known.
        failure_class: Failure class observed by the gate or full run.
        reproduction_rate: Same-class reproduction rate for the experiment.
        elapsed_seconds: Runtime for this observation.
        metadata: Additional JSON-compatible diagnostic payload.
        failure_class_id: Registry-backed failure-class ID.
        failure_class_unclassified: Whether the class was unknown.
        model: Model tag or identifier.
        model_digest: Backend-resolved model digest.
        backend_version: LLM backend version.
        optimization_profile: Speed/measurement profile for this row.
        override_gate_status: Gate status explicitly overridden by operator.
        override_reason: Reason for the override.
        measurement_purpose: Measurement purpose when this is not validation.
        scorecard_schema_version: Row schema version.
    """

    experiment_id: str
    gate: str
    status: str
    full_run_status: str = ""
    failure_class: str = ""
    reproduction_rate: float | None = None
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    failure_class_id: str = ""
    failure_class_unclassified: bool = False
    model: str = ""
    model_digest: str = ""
    backend_version: str = ""
    optimization_profile: str = ""
    override_gate_status: str = ""
    override_reason: str = ""
    measurement_purpose: str = ""
    scorecard_schema_version: int = SCORECARD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Populate registry-backed failure-class fields."""
        if self.failure_class_id:
            resolution = resolve_failure_class(self.failure_class_id)
        else:
            resolution = resolve_failure_class(self.failure_class)
        object.__setattr__(self, "failure_class_id", resolution.failure_class_id)
        object.__setattr__(
            self,
            "failure_class_unclassified",
            bool(self.failure_class_unclassified or resolution.unclassified),
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> ScorecardRow:
        """Build a scorecard row from a decoded JSON mapping.

        Args:
            payload: JSON-compatible row payload.

        Returns:
            Parsed scorecard row.
        """
        return cls(
            experiment_id=str(payload.get("experiment_id", "") or ""),
            gate=str(payload.get("gate", "") or ""),
            status=str(payload.get("status", "") or ""),
            full_run_status=str(payload.get("full_run_status", "") or ""),
            failure_class=str(payload.get("failure_class", "") or ""),
            reproduction_rate=_optional_float(payload.get("reproduction_rate")),
            elapsed_seconds=float(payload.get("elapsed_seconds", 0.0) or 0.0),
            metadata=dict(payload.get("metadata", {}) or {}),
            failure_class_id=str(payload.get("failure_class_id", "") or ""),
            failure_class_unclassified=bool(payload.get("failure_class_unclassified", False)),
            model=str(payload.get("model", "") or ""),
            model_digest=str(payload.get("model_digest", "") or ""),
            backend_version=str(payload.get("backend_version", "") or ""),
            optimization_profile=str(payload.get("optimization_profile", "") or ""),
            override_gate_status=str(payload.get("override_gate_status", "") or ""),
            override_reason=str(payload.get("override_reason", "") or ""),
            measurement_purpose=str(payload.get("measurement_purpose", "") or ""),
            scorecard_schema_version=int(
                payload.get("scorecard_schema_version", SCORECARD_SCHEMA_VERSION)
                or SCORECARD_SCHEMA_VERSION
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible scorecard row."""
        return asdict(self)


@dataclass(frozen=True)
class GateEffectiveness:
    """Aggregate effectiveness metrics for one gate.

    Attributes:
        gate: Gate name.
        observations: Number of comparable observations.
        precision: Fraction of gate failures that corresponded to full-run
            failures.
        recall: Fraction of full-run failures caught by the gate.
        false_positive_rate: Fraction of gate failures whose full run passed.
        false_negative_rate: Fraction of gate passes whose full run failed.
        distinct_failure_class_count: Number of registry-backed failure classes.
        blocking_ready: Whether the gate meets default promotion thresholds.
    """

    gate: str
    observations: int
    precision: float
    recall: float
    false_positive_rate: float
    false_negative_rate: float
    distinct_failure_class_count: int
    blocking_ready: bool


@dataclass(frozen=True)
class GateEvidence:
    """One release-gate evidence item.

    Attributes:
        evidence_id: Stable fixture, dry-run, mini-benchmark, or corpus ID.
        kind: Evidence kind.
        status: Evidence status.
        relevant: Whether this evidence applies to the requested launch.
        required: Whether this evidence is a hard precondition.
    """

    evidence_id: str
    kind: str
    status: str
    relevant: bool = True
    required: bool = True


@dataclass(frozen=True)
class ReleaseGateDecision:
    """Decision returned by ``release_gate_status``.

    Attributes:
        status: ``go``, ``wait``, or ``blocked``.
        reasons: Machine-readable decision reasons.
        checked_evidence_ids: Relevant evidence included in the decision.
        skipped_not_relevant_evidence_ids: Evidence skipped by relevance tags.
    """

    status: str
    reasons: list[str]
    checked_evidence_ids: list[str] = field(default_factory=list)
    skipped_not_relevant_evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReproductionBaseline:
    """Canonical reproduction-baseline rows plus cutover metadata.

    Attributes:
        path: Source artifact path.
        rows: Canonical reproduction rows.
        baseline_kind: Artifact kind label.
        included_sources: Source files included in the canonical baseline.
        excluded_sources: Source files explicitly excluded from calibration.
        canonical_experiment_ids: Experiment IDs present in ``rows``.
        superseded_experiment_ids: Earlier experiment IDs replaced by the
            canonical rows.
        summary_by_experiment: Stored reproduction summary from the artifact.
    """

    path: str
    rows: list[ScorecardRow]
    baseline_kind: str = ""
    included_sources: list[str] = field(default_factory=list)
    excluded_sources: list[dict[str, str]] = field(default_factory=list)
    canonical_experiment_ids: list[str] = field(default_factory=list)
    superseded_experiment_ids: list[str] = field(default_factory=list)
    summary_by_experiment: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScorecardCalibration:
    """Rows after applying canonical calibration artifacts.

    Attributes:
        rows: Scorecard rows used by summaries and gate status.
        reproduction_baseline: Optional canonical reproduction baseline.
        excluded_reproduction_rows: Number of superseded reproduction rows
            removed before appending canonical rows.
    """

    rows: list[ScorecardRow]
    reproduction_baseline: ReproductionBaseline | None = None
    excluded_reproduction_rows: int = 0


class ScorecardStore:
    """Append-only scorecard JSONL store."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, row: ScorecardRow) -> None:
        """Append one scorecard row.

        Args:
            row: Row to append.
        """
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row.to_mapping(), sort_keys=True) + "\n")

    def load(self) -> list[ScorecardRow]:
        """Load all scorecard rows."""
        if not self.path.is_file():
            return []
        rows: list[ScorecardRow] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(ScorecardRow.from_mapping(payload))
        return rows


def load_reproduction_baseline(path: Path | str) -> ReproductionBaseline:
    """Load a canonical reproduction-baseline artifact.

    Args:
        path: JSON artifact containing ``rows`` emitted by the reproduction
            driver or a canonical aggregate.

    Returns:
        Parsed reproduction baseline with inferred superseded experiment IDs.

    Raises:
        ValueError: If the artifact does not decode to the expected object
            shape.
    """
    baseline_path = Path(path).expanduser().resolve(strict=False)
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Reproduction baseline must be a JSON object: {baseline_path}")
    raw_rows = payload.get("rows", [])
    if not isinstance(raw_rows, list):
        raise ValueError("Reproduction baseline must contain a 'rows' list.")
    baseline_kind = str(payload.get("kind", "") or "").strip()
    if baseline_kind and baseline_kind != CANONICAL_B1_REPRODUCTION_BASELINE_KIND:
        raise ValueError(
            "Unsupported reproduction baseline kind "
            f"{baseline_kind!r}; expected "
            f"{CANONICAL_B1_REPRODUCTION_BASELINE_KIND!r}."
        )
    rows = [ScorecardRow.from_mapping(row) for row in raw_rows if isinstance(row, dict)]
    canonical_ids = sorted({row.experiment_id for row in rows if row.experiment_id})
    explicit_superseded = [
        str(item).strip()
        for item in payload.get("superseded_experiment_ids", []) or []
        if str(item).strip()
    ]
    superseded_ids = sorted(
        {
            *explicit_superseded,
            *[
                item
                for experiment_id in canonical_ids
                for item in _inferred_superseded_experiment_ids(experiment_id)
            ],
        }
    )
    return ReproductionBaseline(
        path=str(baseline_path),
        rows=rows,
        baseline_kind=baseline_kind,
        included_sources=[
            str(item) for item in payload.get("included_sources", []) or [] if str(item).strip()
        ],
        excluded_sources=[
            {
                "path": str(item.get("path", "") or ""),
                "reason": str(item.get("reason", "") or ""),
            }
            for item in payload.get("excluded_sources", []) or []
            if isinstance(item, dict)
        ],
        canonical_experiment_ids=canonical_ids,
        superseded_experiment_ids=superseded_ids,
        summary_by_experiment=dict(payload.get("summary_by_experiment", {}) or {}),
    )


def apply_scorecard_calibration(
    rows: list[ScorecardRow],
    *,
    reproduction_baseline: ReproductionBaseline | None = None,
) -> ScorecardCalibration:
    """Apply canonical calibration artifacts to scorecard rows.

    Args:
        rows: Raw append-only scorecard rows.
        reproduction_baseline: Optional canonical reproduction baseline.

    Returns:
        Calibrated row set and metadata about excluded rows.
    """
    if reproduction_baseline is None:
        return ScorecardCalibration(rows=list(rows))
    replaced_ids = set(reproduction_baseline.canonical_experiment_ids) | set(
        reproduction_baseline.superseded_experiment_ids
    )
    calibrated: list[ScorecardRow] = []
    excluded = 0
    for row in rows:
        if row.gate == "reproduction" and row.experiment_id in replaced_ids:
            excluded += 1
            continue
        calibrated.append(row)
    calibrated.extend(reproduction_baseline.rows)
    return ScorecardCalibration(
        rows=calibrated,
        reproduction_baseline=reproduction_baseline,
        excluded_reproduction_rows=excluded,
    )


def compute_gate_effectiveness(
    rows: list[ScorecardRow],
    *,
    min_observations: int = 10,
    min_precision: float = 0.80,
    max_false_negative_rate: float = 0.10,
    min_distinct_failure_classes: int = 3,
) -> list[GateEffectiveness]:
    """Compute per-gate predictive metrics.

    Args:
        rows: Scorecard rows with both gate and full-run outcomes.
        min_observations: Minimum observations before a gate can be blocking.
        min_precision: Minimum precision for blocking promotion.
        max_false_negative_rate: Maximum false-negative rate for promotion.
        min_distinct_failure_classes: Minimum registry classes required before
            blocking promotion.

    Returns:
        Gate effectiveness metrics sorted by gate name.
    """
    by_gate: dict[str, list[ScorecardRow]] = {}
    for row in rows:
        if _is_exploratory(row):
            continue
        if not row.gate or not row.status or not row.full_run_status:
            continue
        by_gate.setdefault(row.gate, []).append(row)

    summaries: list[GateEffectiveness] = []
    for gate, gate_rows in sorted(by_gate.items()):
        true_positive = false_positive = true_negative = false_negative = 0.0
        failure_class_ids: set[str] = set()
        for row in gate_rows:
            weight = _row_weight(row)
            gate_failed = _is_fail_status(row.status)
            full_failed = _is_fail_status(row.full_run_status)
            if gate_failed or full_failed:
                class_id = _calibrated_failure_class_id(row)
                if class_id:
                    failure_class_ids.add(class_id)
            if gate_failed and full_failed:
                true_positive += weight
            elif gate_failed and not full_failed:
                false_positive += weight
            elif not gate_failed and full_failed:
                false_negative += weight
            else:
                true_negative += weight
        precision = _safe_div(true_positive, true_positive + false_positive)
        recall = _safe_div(true_positive, true_positive + false_negative)
        false_positive_rate = _safe_div(false_positive, false_positive + true_negative)
        false_negative_rate = _safe_div(false_negative, false_negative + true_positive)
        observations = len(gate_rows)
        distinct_failure_class_count = len(failure_class_ids)
        summaries.append(
            GateEffectiveness(
                gate=gate,
                observations=observations,
                precision=precision,
                recall=recall,
                false_positive_rate=false_positive_rate,
                false_negative_rate=false_negative_rate,
                distinct_failure_class_count=distinct_failure_class_count,
                blocking_ready=(
                    observations >= min_observations
                    and precision >= min_precision
                    and false_negative_rate <= max_false_negative_rate
                    and distinct_failure_class_count >= min_distinct_failure_classes
                ),
            )
        )
    return summaries


def release_gate_status(
    rows: list[ScorecardRow],
    *,
    experiment_id: str,
    evidence: list[GateEvidence] | None = None,
    corpus_stale: bool = False,
) -> ReleaseGateDecision:
    """Return the release-gate decision for a launch.

    Args:
        rows: Scorecard rows available to the gate.
        experiment_id: Experiment or case being launched.
        evidence: Relevant or skipped evidence items supplied by the caller.
        corpus_stale: Whether the corpus baseline is stale for the launch.

    Returns:
        Gate decision with explicit reasons.
    """
    reasons: list[str] = []
    checked: list[str] = []
    skipped: list[str] = []
    if not _has_reproduction_baseline(rows, experiment_id):
        reasons.append("no_reproduction_baseline")
    if corpus_stale:
        reasons.append("corpus_baseline_stale")

    for item in evidence or []:
        if not item.relevant:
            skipped.append(item.evidence_id)
            continue
        checked.append(item.evidence_id)
        if item.required and _is_fail_status(item.status):
            reasons.append(f"red_relevant_{item.kind}:{item.evidence_id}")

    for row in rows:
        if row.override_gate_status and not (row.override_reason and row.measurement_purpose):
            reasons.append("override_metadata_missing")
            break

    blocked_reasons = [reason for reason in reasons if reason.startswith("red_")]
    if blocked_reasons:
        status = "blocked"
    elif reasons:
        status = "wait"
    else:
        status = "go"
    return ReleaseGateDecision(
        status=status,
        reasons=reasons,
        checked_evidence_ids=checked,
        skipped_not_relevant_evidence_ids=skipped,
    )


def summarize_reproduction_rates(rows: list[ScorecardRow]) -> dict[str, Any]:
    """Summarize reproduction baseline rows by experiment.

    Args:
        rows: Scorecard rows from reproduction baseline runs.

    Returns:
        Mapping from experiment ID to pass/failure distribution.
    """
    grouped: dict[str, list[ScorecardRow]] = {}
    for row in rows:
        if row.gate != "reproduction":
            continue
        grouped.setdefault(row.experiment_id, []).append(row)

    summary: dict[str, Any] = {}
    for experiment_id, experiment_rows in sorted(grouped.items()):
        total = len(experiment_rows)
        pass_count = sum(1 for row in experiment_rows if _is_pass_status(row.status))
        same_class = sum(1 for row in experiment_rows if row.status == "fail_same_class")
        different_class = sum(1 for row in experiment_rows if row.status == "fail_different_class")
        infra_error = sum(1 for row in experiment_rows if row.status == "infra_error")
        summary[experiment_id] = {
            "total": total,
            "pass": pass_count,
            "fail_same_class": same_class,
            "fail_different_class": different_class,
            "infra_error": infra_error,
            "same_class_reproduction_rate": _safe_div(same_class, total),
        }
    return summary


def _row_weight(row: ScorecardRow) -> float:
    if row.reproduction_rate is None:
        return 1.0
    return max(0.0, min(1.0, row.reproduction_rate))


def _is_pass_status(status: str) -> bool:
    return status.strip().lower() in {"pass", "passed", "success", "completed"}


def _is_fail_status(status: str) -> bool:
    lowered = status.strip().lower()
    return lowered.startswith("fail") or lowered in {"error", "timeout", "blocked"}


def _is_exploratory(row: ScorecardRow) -> bool:
    return row.optimization_profile.strip().lower() == EXPLORATORY_OPTIMIZATION_PROFILE


def _calibrated_failure_class_id(row: ScorecardRow) -> str:
    class_id = row.failure_class_id.strip()
    if class_id in {"", "none", "no_failure", "pass", UNCLASSIFIED_FAILURE_CLASS_ID}:
        return ""
    return class_id


def _has_reproduction_baseline(rows: list[ScorecardRow], experiment_id: str) -> bool:
    for row in rows:
        if row.gate != "reproduction":
            continue
        if row.experiment_id != experiment_id:
            continue
        if row.status == "dry_run":
            continue
        if _is_exploratory(row):
            continue
        return True
    return False


def _inferred_superseded_experiment_ids(experiment_id: str) -> list[str]:
    labels: list[str] = []
    if experiment_id.endswith("_abs_python"):
        labels.append(experiment_id.removesuffix("_abs_python"))
    after_marker = "_after_"
    if after_marker in experiment_id:
        labels.append(experiment_id.split(after_marker, 1)[0])
    return [item for item in labels if item and item != experiment_id]


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
