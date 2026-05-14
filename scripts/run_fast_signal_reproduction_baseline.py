#!/usr/bin/env python3
"""Run reproduction-rate baseline commands for stochastic benchmark failures."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.fast_signal_scorecard import (  # noqa: E402
    ScorecardRow,
    ScorecardStore,
    summarize_reproduction_rates,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "workspace" / "studies" / "reproduction_rates.json"
DEFAULT_SCORECARD = PROJECT_ROOT / "workspace" / "studies" / "scorecard.jsonl"


def build_parser() -> argparse.ArgumentParser:
    """Build the reproduction baseline CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        action="append",
        required=True,
        help=(
            "Experiment spec as NAME::COMMAND. COMMAND may contain "
            "{experiment} and {replicate} placeholders."
        ),
    )
    parser.add_argument("--replicates", type=int, default=10)
    parser.add_argument("--same-class-marker", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=int, default=0)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--measurement-purpose", default="")
    parser.add_argument("--override-reason", default="")
    parser.add_argument("--shard-id", default="default")
    parser.add_argument("--optimization-profile", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--model-digest", default="")
    parser.add_argument("--backend-version", default="")
    parser.add_argument("--ollama-keep-alive", default="")
    parser.add_argument("--ollama-num-parallel", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--write-dry-run-output",
        action="store_true",
        help="Write --output-json during --dry-run. Defaults to print-only dry runs.",
    )
    return parser


def main() -> int:
    """Run reproduction baseline experiments and write calibrated summaries."""

    args = build_parser().parse_args()
    specs = [_parse_experiment_spec(item) for item in args.experiment]
    store = ScorecardStore(args.scorecard)
    rows = _load_output_rows(args.output_json) if args.resume else []
    completed_replicates = _replicate_ids_from_rows(rows) if args.resume else set()
    skipped: list[str] = []
    for name, command_template in specs:
        for replicate in range(1, args.replicates + 1):
            replicate_id = f"{name}.shard-{args.shard_id}.replicate-{replicate}"
            if replicate_id in completed_replicates:
                skipped.append(replicate_id)
                continue
            command = command_template.format(
                experiment=name,
                replicate=replicate,
                replicate_id=replicate_id,
                shard_id=args.shard_id,
            )
            env_overrides = _ollama_env_overrides(
                keep_alive=args.ollama_keep_alive,
                num_parallel=args.ollama_num_parallel,
            )
            row = _run_replicate(
                experiment_id=name,
                command=command,
                replicate=replicate,
                replicate_id=replicate_id,
                shard_id=args.shard_id,
                same_class_markers=args.same_class_marker,
                timeout_seconds=args.timeout_seconds,
                measurement_purpose=args.measurement_purpose,
                override_reason=args.override_reason,
                optimization_profile=args.optimization_profile,
                model=args.model,
                model_digest=args.model_digest,
                backend_version=args.backend_version,
                env_overrides=env_overrides,
                dry_run=args.dry_run,
            )
            rows.append(row)
            if not args.dry_run:
                store.append(row)
                _write_output_summary(args.output_json, rows, skipped=skipped)
    summary = _output_summary(rows, skipped=skipped)
    if not args.dry_run or args.write_dry_run_output:
        _write_output_summary(args.output_json, rows, skipped=skipped)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _run_replicate(
    *,
    experiment_id: str,
    command: str,
    replicate: int,
    replicate_id: str,
    shard_id: str,
    same_class_markers: list[str],
    timeout_seconds: int,
    measurement_purpose: str,
    override_reason: str,
    optimization_profile: str,
    model: str,
    model_digest: str,
    backend_version: str,
    env_overrides: dict[str, str],
    dry_run: bool,
) -> ScorecardRow:
    started = time.monotonic()
    base_metadata: dict[str, Any] = {
        "replicate": replicate,
        "replicate_id": replicate_id,
        "shard_id": shard_id,
        "command": command,
        "measurement_purpose": measurement_purpose,
        "override_reason": override_reason,
        "optimization_profile": optimization_profile,
        "env_overrides": env_overrides,
    }
    if dry_run:
        return ScorecardRow(
            experiment_id=experiment_id,
            gate="reproduction",
            status="dry_run",
            metadata=base_metadata,
            model=model,
            model_digest=model_digest,
            backend_version=backend_version,
            optimization_profile=optimization_profile,
            override_gate_status="wait" if override_reason else "",
            override_reason=override_reason,
            measurement_purpose=measurement_purpose,
        )
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=PROJECT_ROOT,
            env=_subprocess_env(env_overrides),
            text=True,
            capture_output=True,
            timeout=timeout_seconds or None,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        status = _classify_status(completed.returncode, output, same_class_markers)
        metadata: dict[str, Any] = {
            **base_metadata,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        status = "infra_error"
        metadata = {
            **base_metadata,
            "timeout_seconds": timeout_seconds,
            "stdout_tail": str(exc.stdout or "")[-4000:],
            "stderr_tail": str(exc.stderr or "")[-4000:],
        }
    elapsed = time.monotonic() - started
    return ScorecardRow(
        experiment_id=experiment_id,
        gate="reproduction",
        status=status,
        elapsed_seconds=elapsed,
        metadata=metadata,
        model=model,
        model_digest=model_digest,
        backend_version=backend_version,
        optimization_profile=optimization_profile,
        override_gate_status="wait" if override_reason else "",
        override_reason=override_reason,
        measurement_purpose=measurement_purpose,
    )


def _classify_status(
    returncode: int,
    output: str,
    same_class_markers: list[str],
) -> str:
    if returncode == 0:
        return "pass"
    lowered = output.lower()
    if _looks_like_infra_error(lowered):
        return "infra_error"
    if same_class_markers and any(marker.lower() in lowered for marker in same_class_markers):
        return "fail_same_class"
    return "fail_different_class"


def _looks_like_infra_error(lowered_output: str) -> bool:
    infra_markers = (
        "connection refused",
        "failed to connect",
        "model not found",
        "ollama",
        "out of memory",
        "oom",
        "resource temporarily unavailable",
        "stall timeout",
        "timed out",
        "timeout",
    )
    return any(marker in lowered_output for marker in infra_markers)


def _parse_experiment_spec(value: str) -> tuple[str, str]:
    if "::" not in value:
        raise SystemExit("--experiment must be formatted as NAME::COMMAND")
    name, command = value.split("::", 1)
    name = name.strip()
    command = command.strip()
    if not name or not command:
        raise SystemExit("--experiment requires non-empty NAME and COMMAND")
    return name, command


def _ollama_env_overrides(*, keep_alive: str, num_parallel: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    if keep_alive:
        overrides["OLLAMA_KEEP_ALIVE"] = keep_alive
    if num_parallel:
        overrides["OLLAMA_NUM_PARALLEL"] = num_parallel
    return overrides


def _subprocess_env(overrides: dict[str, str]) -> dict[str, str] | None:
    if not overrides:
        return None
    env = os.environ.copy()
    env.update(overrides)
    return env


def _completed_replicate_ids(output_json: Path) -> set[str]:
    return _replicate_ids_from_rows(_load_output_rows(output_json))


def _load_output_rows(output_json: Path) -> list[ScorecardRow]:
    try:
        payload = json.loads(output_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    loaded: list[ScorecardRow] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        loaded.append(ScorecardRow.from_mapping(row))
    return loaded


def _replicate_ids_from_rows(rows: list[ScorecardRow]) -> set[str]:
    completed: set[str] = set()
    for row in rows:
        metadata = row.metadata
        if isinstance(metadata, dict):
            replicate_id = str(metadata.get("replicate_id", "") or "").strip()
        else:
            replicate_id = ""
        if replicate_id:
            completed.add(replicate_id)
    return completed


def _output_summary(
    rows: list[ScorecardRow],
    *,
    skipped: list[str],
) -> dict[str, Any]:
    return {
        "rows": [row.to_mapping() for row in rows],
        "summary": summarize_reproduction_rates(rows),
        "skipped_replicate_ids": skipped,
    }


def _write_output_summary(
    output_json: Path,
    rows: list[ScorecardRow],
    *,
    skipped: list[str],
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(_output_summary(rows, skipped=skipped), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
