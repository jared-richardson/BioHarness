#!/usr/bin/env python3
"""Record and summarize fast-signal scorecard observations."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fast_signal_scorecard import (  # noqa: E402
    GateEvidence,
    ScorecardRow,
    ScorecardStore,
    apply_scorecard_calibration,
    compute_gate_effectiveness,
    load_reproduction_baseline,
    release_gate_status,
    summarize_reproduction_rates,
)

DEFAULT_SCORECARD = PROJECT_ROOT / "workspace" / "studies" / "scorecard.jsonl"
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "docs" / "scorecard_snapshots"
DEFAULT_REPRODUCTION_BASELINE = (
    PROJECT_ROOT
    / "workspace"
    / "studies"
    / "reproduction_rates_b1_exp42_exp43_exp44_current_20260430.json"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the scorecard CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument(
        "--reproduction-baseline",
        type=Path,
        default=DEFAULT_REPRODUCTION_BASELINE,
        help=(
            "Canonical reproduction baseline JSON. Defaults to the B1 "
            "exp42/exp43/exp44 aggregate when present."
        ),
    )
    parser.add_argument(
        "--no-reproduction-baseline",
        action="store_true",
        help="Do not merge a canonical reproduction baseline into scorecard summaries.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Append one scorecard row.")
    record.add_argument("--experiment-id", required=True)
    record.add_argument("--gate", required=True)
    record.add_argument("--status", required=True)
    record.add_argument("--full-run-status", default="")
    record.add_argument("--failure-class", default="")
    record.add_argument("--failure-class-id", default="")
    record.add_argument("--reproduction-rate", type=float, default=None)
    record.add_argument("--elapsed-seconds", type=float, default=0.0)
    record.add_argument("--model", default="")
    record.add_argument("--model-digest", default="")
    record.add_argument("--backend-version", default="")
    record.add_argument("--optimization-profile", default="")
    record.add_argument("--override-gate-status", default="")
    record.add_argument("--override-reason", default="")
    record.add_argument("--measurement-purpose", default="")
    record.add_argument("--metadata-json", default="{}")

    subparsers.add_parser("summarize", help="Summarize scorecard gates.")
    snapshot = subparsers.add_parser("snapshot", help="Write a docs snapshot.")
    snapshot.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    snapshot.add_argument("--experiment-id", default="")

    gate_status = subparsers.add_parser("gate-status", help="Evaluate a release gate.")
    gate_status.add_argument("--experiment-id", required=True)
    gate_status.add_argument(
        "--evidence-json",
        default="[]",
        help="JSON list of evidence objects with evidence_id, kind, status, and relevant.",
    )
    gate_status.add_argument("--corpus-stale", action="store_true")
    return parser


def main() -> int:
    """Record or summarize scorecard rows."""

    args = build_parser().parse_args()
    store = ScorecardStore(args.scorecard)
    if args.command == "record":
        metadata = _parse_metadata(args.metadata_json)
        store.append(
            ScorecardRow(
                experiment_id=args.experiment_id,
                gate=args.gate,
                status=args.status,
                full_run_status=args.full_run_status,
                failure_class=args.failure_class,
                failure_class_id=args.failure_class_id,
                reproduction_rate=args.reproduction_rate,
                elapsed_seconds=args.elapsed_seconds,
                model=args.model,
                model_digest=args.model_digest,
                backend_version=args.backend_version,
                optimization_profile=args.optimization_profile,
                override_gate_status=args.override_gate_status,
                override_reason=args.override_reason,
                measurement_purpose=args.measurement_purpose,
                metadata=metadata,
            )
        )
        print(json.dumps({"recorded": True, "scorecard": str(args.scorecard)}, sort_keys=True))
        return 0

    calibration = _load_calibrated_rows(args, store)
    rows = calibration.rows
    if args.command == "gate-status":
        evidence = _parse_evidence(args.evidence_json)
        decision = release_gate_status(
            rows,
            experiment_id=args.experiment_id,
            evidence=evidence,
            corpus_stale=args.corpus_stale,
        )
        print(json.dumps(asdict(decision), indent=2, sort_keys=True))
        return 0
    payload = _summary_payload(
        scorecard_path=args.scorecard,
        calibration=calibration,
        experiment_id=getattr(args, "experiment_id", ""),
    )
    if args.command == "snapshot":
        args.snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = args.snapshot_dir / "fast_signal_scorecard_latest.json"
        snapshot_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"snapshot": str(snapshot_path)}, indent=2, sort_keys=True))
        return 0
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _summary_payload(
    *,
    scorecard_path: Path,
    calibration,
    experiment_id: str = "",
) -> dict:
    rows = calibration.rows
    payload = {
        "scorecard": str(scorecard_path),
        "rows": len(rows),
        "reproduction": summarize_reproduction_rates(rows),
        "gates": [asdict(item) for item in compute_gate_effectiveness(rows)],
    }
    if calibration.reproduction_baseline is not None:
        baseline = calibration.reproduction_baseline
        payload["reproduction_baseline_cutover"] = {
            "kind": baseline.baseline_kind,
            "path": baseline.path,
            "cutover_policy": "canonical_replacement",
            "legacy_row_policy": (
                "exclude reproduction rows whose experiment_id is canonical "
                "or superseded, then append canonical baseline rows"
            ),
            "legacy_rows_backfilled": False,
            "included_sources": baseline.included_sources,
            "excluded_sources": baseline.excluded_sources,
            "canonical_experiment_ids": baseline.canonical_experiment_ids,
            "superseded_experiment_ids": baseline.superseded_experiment_ids,
            "excluded_reproduction_rows": calibration.excluded_reproduction_rows,
        }
    if experiment_id:
        payload["release_gate"] = asdict(
            release_gate_status(rows, experiment_id=experiment_id)
        )
    return payload


def _load_calibrated_rows(args: argparse.Namespace, store: ScorecardStore):
    rows = store.load()
    if args.no_reproduction_baseline:
        return apply_scorecard_calibration(rows)
    baseline_path = Path(args.reproduction_baseline).expanduser().resolve(strict=False)
    if not baseline_path.is_file():
        return apply_scorecard_calibration(rows)
    baseline = load_reproduction_baseline(baseline_path)
    return apply_scorecard_calibration(rows, reproduction_baseline=baseline)


def _parse_metadata(text: str) -> dict:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--metadata-json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--metadata-json must decode to an object")
    return payload


def _parse_evidence(text: str) -> list[GateEvidence]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--evidence-json is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise SystemExit("--evidence-json must decode to a list")
    evidence: list[GateEvidence] = []
    for item in payload:
        if not isinstance(item, dict):
            raise SystemExit("--evidence-json list items must be objects")
        evidence.append(
            GateEvidence(
                evidence_id=str(item.get("evidence_id", "") or ""),
                kind=str(item.get("kind", "") or "fixture"),
                status=str(item.get("status", "") or ""),
                relevant=bool(item.get("relevant", True)),
                required=bool(item.get("required", True)),
            )
        )
    return evidence


if __name__ == "__main__":
    raise SystemExit(main())
