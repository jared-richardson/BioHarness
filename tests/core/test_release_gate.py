"""Tests for fast-signal release-gate policy logic."""

from __future__ import annotations

from bio_harness.core.failure_classes import UNCLASSIFIED_FAILURE_CLASS_ID
from bio_harness.core.fast_signal_scorecard import (
    CANONICAL_B1_REPRODUCTION_BASELINE_KIND,
    GateEvidence,
    ReproductionBaseline,
    ScorecardRow,
    apply_scorecard_calibration,
    compute_gate_effectiveness,
    load_reproduction_baseline,
    release_gate_status,
)


def test_release_gate_go_when_hard_preconditions_are_green() -> None:
    rows = [ScorecardRow("exp44", "reproduction", "pass")]
    evidence = [
        GateEvidence("exp44_duplicate_branch", "fixture", "pass"),
        GateEvidence("control_evolution_mini", "mini_benchmark", "pass"),
    ]

    decision = release_gate_status(rows, experiment_id="exp44", evidence=evidence)

    assert decision.status == "go"
    assert decision.reasons == []
    assert decision.checked_evidence_ids == [
        "exp44_duplicate_branch",
        "control_evolution_mini",
    ]


def test_release_gate_waits_without_reproduction_baseline() -> None:
    decision = release_gate_status([], experiment_id="exp44")

    assert decision.status == "wait"
    assert "no_reproduction_baseline" in decision.reasons


def test_release_gate_blocks_on_red_relevant_fixture() -> None:
    rows = [ScorecardRow("exp44", "reproduction", "pass")]
    evidence = [GateEvidence("exp44_duplicate_branch", "fixture", "fail")]

    decision = release_gate_status(rows, experiment_id="exp44", evidence=evidence)

    assert decision.status == "blocked"
    assert "red_relevant_fixture:exp44_duplicate_branch" in decision.reasons


def test_release_gate_skips_not_relevant_evidence() -> None:
    rows = [ScorecardRow("exp44", "reproduction", "pass")]
    evidence = [
        GateEvidence("de_mini", "mini_benchmark", "fail", relevant=False),
    ]

    decision = release_gate_status(rows, experiment_id="exp44", evidence=evidence)

    assert decision.status == "go"
    assert decision.skipped_not_relevant_evidence_ids == ["de_mini"]


def test_release_gate_waits_on_stale_corpus() -> None:
    rows = [ScorecardRow("exp44", "reproduction", "pass")]

    decision = release_gate_status(rows, experiment_id="exp44", corpus_stale=True)

    assert decision.status == "wait"
    assert "corpus_baseline_stale" in decision.reasons


def test_exploratory_rows_are_excluded_from_gate_math() -> None:
    rows = [
        ScorecardRow(
            "exp44",
            "replay",
            "fail",
            full_run_status="fail_same_class",
            failure_class="duplicate_detector_granularity",
            optimization_profile="exploratory_only",
        )
    ]

    assert compute_gate_effectiveness(rows, min_observations=1) == []


def test_override_metadata_absence_does_not_grant_go() -> None:
    rows = [
        ScorecardRow("exp44", "reproduction", "pass"),
        ScorecardRow(
            "exp44",
            "live_sentinel",
            "pass",
            override_gate_status="wait",
            override_reason="",
            measurement_purpose="",
        ),
    ]

    decision = release_gate_status(rows, experiment_id="exp44")

    assert decision.status == "wait"
    assert "override_metadata_missing" in decision.reasons


def test_unknown_failure_class_becomes_unclassified() -> None:
    row = ScorecardRow("exp44", "live_sentinel", "fail", failure_class="new spelling")

    assert row.failure_class_id == UNCLASSIFIED_FAILURE_CLASS_ID
    assert row.failure_class_unclassified is True


def test_canonical_reproduction_baseline_replaces_superseded_rows() -> None:
    raw_rows = [
        ScorecardRow("exp42_current_release", "reproduction", "fail_different_class"),
        ScorecardRow("exp42_current_release_abs_python", "reproduction", "pass"),
        ScorecardRow("other_case", "reproduction", "pass"),
    ]
    baseline = ReproductionBaseline(
        path="baseline.json",
        rows=[
            ScorecardRow(
                "exp42_current_release_abs_python",
                "reproduction",
                "pass",
            )
        ],
        canonical_experiment_ids=["exp42_current_release_abs_python"],
        superseded_experiment_ids=["exp42_current_release"],
    )

    calibration = apply_scorecard_calibration(
        raw_rows,
        reproduction_baseline=baseline,
    )

    reproduction_ids = [
        row.experiment_id for row in calibration.rows if row.gate == "reproduction"
    ]
    assert "exp42_current_release" not in reproduction_ids
    assert reproduction_ids.count("exp42_current_release_abs_python") == 1
    assert "other_case" in reproduction_ids
    assert calibration.excluded_reproduction_rows == 2
    assert release_gate_status(
        calibration.rows,
        experiment_id="exp42_current_release",
    ).status == "wait"
    assert release_gate_status(
        calibration.rows,
        experiment_id="exp42_current_release_abs_python",
    ).status == "go"


def test_load_reproduction_baseline_infers_superseded_experiment_ids(tmp_path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        """
        {
          "kind": "canonical_b1_reproduction_baseline",
          "included_sources": ["corrected.json"],
          "excluded_sources": [
            {"path": "bad.json", "reason": "bootstrap artifact"}
          ],
          "rows": [
            {
              "experiment_id": "exp42_current_release_abs_python",
              "gate": "reproduction",
              "status": "pass"
            },
            {
              "experiment_id": "exp44_after_parameter_profile_filter",
              "gate": "reproduction",
              "status": "pass"
            }
          ],
          "summary_by_experiment": {
            "exp42_current_release_abs_python": {"pass": 1}
          }
        }
        """,
        encoding="utf-8",
    )

    baseline = load_reproduction_baseline(baseline_path)

    assert baseline.baseline_kind == CANONICAL_B1_REPRODUCTION_BASELINE_KIND
    assert baseline.canonical_experiment_ids == [
        "exp42_current_release_abs_python",
        "exp44_after_parameter_profile_filter",
    ]
    assert "exp42_current_release" in baseline.superseded_experiment_ids
    assert "exp44" in baseline.superseded_experiment_ids
    assert baseline.excluded_sources == [
        {"path": "bad.json", "reason": "bootstrap artifact"}
    ]


def test_load_reproduction_baseline_rejects_unknown_kind(tmp_path) -> None:
    baseline_path = tmp_path / "bad_baseline.json"
    baseline_path.write_text(
        """
        {
          "kind": "raw_reproduction_rows",
          "rows": [
            {
              "experiment_id": "exp42_current_release_abs_python",
              "gate": "reproduction",
              "status": "pass"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    try:
        load_reproduction_baseline(baseline_path)
    except ValueError as exc:
        assert "Unsupported reproduction baseline kind" in str(exc)
    else:
        raise AssertionError("Expected unsupported baseline kind to fail")
