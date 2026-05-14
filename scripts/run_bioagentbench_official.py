#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_SCRIPT = PROJECT_ROOT / "scripts" / "run_agent_e2e.py"
DEFAULT_MANIFEST = PROJECT_ROOT / "benchmark_data" / "bioagentbench_official_manifest.json"
DEFAULT_TASK_TIMEOUT_SECONDS = 5400
TASK_TERMINATION_GRACE_SECONDS = 15
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "workspace" / "runs" / "_bioagentbench_official"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.benchmark_policy import (
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    OFFICIAL_BIOAGENTBENCH_POLICY,
    normalize_benchmark_policy,
)
from bio_harness.core.bioagentbench_official import (
    build_official_scoreboard,
    build_official_prompt,
    build_validator_argv,
    render_official_scoreboard_markdown,
    resolve_manifest_entries,
    summarize_official_run,
)
from bio_harness.core.subprocess_watchdog import (
    run_subprocess_with_watchdog,
)
from scripts.run_bioagentbench_invocation_support import (
    apply_manifest_runner_defaults,
    build_harness_env,
    build_official_harness_command,
    invocation_options_from_args,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local BioAgentBench-style suite in official_bioagentbench mode with audit reporting."
    )
    parser.add_argument("--manifest", type=str, default=str(DEFAULT_MANIFEST))
    parser.add_argument(
        "--benchmark-policy",
        type=str,
        default=OFFICIAL_BIOAGENTBENCH_POLICY,
        choices=sorted({BIOAGENTBENCH_PLANNING_STRICT_POLICY, OFFICIAL_BIOAGENTBENCH_POLICY}),
        help=(
            "Blind benchmark mode. official_bioagentbench matches the current local official path; "
            "bioagentbench_planning_strict disables compiler-driven plan rescue."
        ),
    )
    parser.add_argument("--task-id", action="append", default=[], help="Run only the specified task ID. Repeatable.")
    parser.add_argument("--out-dir", type=str, default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--attempt-label", type=str, default="")
    parser.add_argument("--model-name", type=str, default="")
    parser.add_argument(
        "--planner-model-name",
        type=str,
        default="",
        help="Override the planning model used by the harness subprocess (maps to BIO_HARNESS_MODEL_HEAVY).",
    )
    parser.add_argument(
        "--executor-model-name",
        type=str,
        default="",
        help="Override the executor model used by the harness subprocess (maps to BIO_HARNESS_MODEL).",
    )
    parser.add_argument("--llm-backend", type=str, default="")
    parser.add_argument("--host", type=str, default="")
    parser.add_argument("--max-repairs", type=int, default=3)
    parser.add_argument("--heartbeat-seconds", type=int, default=15)
    parser.add_argument("--stall-timeout-seconds", type=int, default=45)
    parser.add_argument("--live-process-grace-seconds", type=int, default=900)
    parser.add_argument(
        "--strict-llm-planning",
        action="store_true",
        help="Require the LLM planner to produce a usable plan instead of falling back to non-planner recovery.",
    )
    parser.add_argument(
        "--planner-attempt-timeout-seconds",
        type=int,
        default=0,
        help="Override BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS for the harness subprocess.",
    )
    parser.add_argument(
        "--llm-timeout-seconds",
        type=int,
        default=0,
        help="Override BIO_HARNESS_LLM_TIMEOUT_SECONDS for the harness subprocess.",
    )
    parser.add_argument("--no-replan", action="store_true")
    parser.add_argument("--no-canonicalize", action="store_true")
    parser.add_argument("--skip-validation", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument(
        "--task-timeout-seconds",
        type=int,
        default=DEFAULT_TASK_TIMEOUT_SECONDS,
        help="Per-task watchdog timeout for the harness subprocess. Use 0 to disable.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def _build_harness_env(args: argparse.Namespace) -> dict[str, str]:
    return build_harness_env(invocation_options_from_args(args), environ=os.environ)


def _apply_manifest_runner_defaults(
    env: dict[str, str],
    *,
    entry: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, str]:
    return apply_manifest_runner_defaults(
        env,
        entry=entry,
        options=invocation_options_from_args(args),
    )


def _task_timeout_seconds(entry: dict[str, Any], args: argparse.Namespace) -> int:
    """Return the watchdog timeout for one benchmark task."""

    cli_timeout = int(getattr(args, "task_timeout_seconds", 0) or 0)
    if cli_timeout > 0:
        return cli_timeout
    defaults = entry.get("runner_defaults", {}) if isinstance(entry.get("runner_defaults", {}), dict) else {}
    try:
        manifest_timeout = int(defaults.get("task_timeout_seconds", 0) or 0)
    except Exception:
        manifest_timeout = 0
    if manifest_timeout > 0:
        return manifest_timeout
    return 0


def _run_harness_subprocess(
    *,
    cmd: list[str],
    env: dict[str, str],
    log_path: Path,
    timeout_seconds: int,
    progress_paths: tuple[Path, ...] = (),
    progress_path_resolver: Callable[[], tuple[Path, ...]] | None = None,
) -> tuple[int, bool]:
    """Run one harness task with a watchdog and graceful termination."""

    return run_subprocess_with_watchdog(
        cmd=cmd,
        cwd=PROJECT_ROOT,
        env=env,
        log_path=log_path,
        timeout_seconds=timeout_seconds,
        termination_grace_seconds=float(TASK_TERMINATION_GRACE_SECONDS),
        timeout_message=(
            f"[official-runner] Task watchdog exceeded {int(timeout_seconds)}s; sending SIGTERM."
        ),
        kill_message="[official-runner] Graceful shutdown timed out; sending SIGKILL.",
        progress_paths=progress_paths,
        progress_path_resolver=progress_path_resolver,
    )


def _find_run_dir_for_selected_dir(*, selected_dir: Path) -> Path | None:
    """Return the newest run dir associated with one selected directory."""

    runs_root = PROJECT_ROOT / "workspace" / "runs"
    if not runs_root.exists():
        return None
    target_selected_dir = str(selected_dir.resolve(strict=False))
    for run_dir in sorted(runs_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not run_dir.is_dir():
            continue
        for filename in ("manifest.json", "completed_run_context.json"):
            candidate = run_dir / filename
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if str(payload.get("selected_dir", "") or "") == target_selected_dir:
                return run_dir
    return None


def _watchdog_progress_paths(*, selected_dir: Path) -> tuple[Path, ...]:
    """Return dynamic progress paths for the outer task watchdog."""

    paths: list[Path] = [selected_dir]
    run_dir = _find_run_dir_for_selected_dir(selected_dir=selected_dir)
    if run_dir is None:
        return tuple(paths)
    for name in (
        "events.jsonl",
        "state.json",
        "exit.json",
        "completed_run_context.json",
        "assistance_manifest.json",
    ):
        path = run_dir / name
        if path.exists():
            paths.append(path)
    return tuple(paths)


def _select_entries(entries: list[dict[str, Any]], task_ids: list[str]) -> list[dict[str, Any]]:
    if not task_ids:
        return entries
    wanted = {str(task_id).strip() for task_id in task_ids if str(task_id).strip()}
    return [entry for entry in entries if str(entry.get("task_id", "")).strip() in wanted]


def _unique_selected_dir(base_dir: Path, attempt_label: str) -> Path:
    candidate = base_dir / attempt_label
    if not candidate.exists():
        return candidate
    idx = 2
    while True:
        alt = base_dir / f"{attempt_label}_{idx}"
        if not alt.exists():
            return alt
        idx += 1


def _timestamp_label() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_validator(argv: list[str], *, log_path: Path) -> tuple[int | None, str]:
    if not argv:
        return None, ""
    proc = subprocess.run(
        argv,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    log_path.write_text(stdout, encoding="utf-8")
    return int(proc.returncode), stdout


def _write_summary_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# BioAgentBench Official-Mode Summary",
        "",
        "| Task | Harness | Validation | Report Bucket | Generic Fallback | Leakage |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("task_id", "")),
                    str(row.get("harness_status", "")),
                    "n/a" if row.get("validation_passed") is None else ("pass" if row.get("validation_passed") else "fail"),
                    str(row.get("official_report_bucket", "")),
                    "yes" if row.get("generic_template_fallback_used") else "no",
                    "yes" if row.get("forbidden_benchmark_sources_visible") else "no",
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = _select_entries(resolve_manifest_entries(manifest_path), args.task_id)
    if not entries:
        raise SystemExit("No benchmark entries selected.")

    snapshot_path = out_dir / "manifest_snapshot.json"
    snapshot_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    invocation_options = invocation_options_from_args(args)
    base_harness_env = build_harness_env(invocation_options, environ=os.environ)

    attempt_label = str(args.attempt_label).strip() or _timestamp_label()
    rows: list[dict[str, Any]] = []
    failures = 0

    for entry in entries:
        task_id = str(entry.get("task_id", "") or "").strip()
        base_dir = Path(str(entry.get("runs_root", "") or "")).resolve(strict=False)
        base_dir.mkdir(parents=True, exist_ok=True)
        selected_dir = _unique_selected_dir(base_dir, attempt_label)
        selected_dir.mkdir(parents=True, exist_ok=False)
        result_json = selected_dir / "result.json"
        harness_log = selected_dir / "harness.log"
        validator_log = selected_dir / "validator.log"

        prompt = build_official_prompt(entry, selected_dir=selected_dir)
        harness_env = apply_manifest_runner_defaults(
            base_harness_env,
            entry=entry,
            options=invocation_options,
        )
        cmd = build_official_harness_command(
            entry,
            selected_dir=selected_dir,
            options=invocation_options,
            result_json=result_json,
        )

        print(f"[official] task={task_id}")
        print(f"[official] selected_dir={selected_dir}")
        if args.dry_run:
            row = {
                "task_id": task_id,
                "task_name": str(entry.get("task_name", "") or task_id),
                "selected_dir": str(selected_dir),
                "dry_run": True,
                "command": cmd,
                "prompt": prompt,
            }
            rows.append(row)
            continue

        harness_returncode, timed_out = _run_harness_subprocess(
            cmd=cmd,
            env=harness_env,
            log_path=harness_log,
            timeout_seconds=_task_timeout_seconds(entry, args),
            progress_paths=(selected_dir,),
            progress_path_resolver=lambda: _watchdog_progress_paths(selected_dir=selected_dir),
        )

        if result_json.exists():
            result_obj = json.loads(result_json.read_text(encoding="utf-8"))
        else:
            result_obj = {
                "status": "failed",
                "error": (
                    f"Harness did not write result JSON before timeout/termination (exit={harness_returncode})"
                    if timed_out
                    else f"Harness did not write result JSON (exit={harness_returncode})"
                ),
                "benchmark_policy": normalize_benchmark_policy(args.benchmark_policy),
                "result_json": str(result_json),
                "run_dir": "",
                "assistance_manifest_file": "",
                "assistance_manifest": {},
                "timed_out": bool(timed_out),
            }
        result_obj["result_json"] = str(result_json)
        if timed_out and "timed_out" not in result_obj:
            result_obj["timed_out"] = True

        validator_exit_code: int | None = None
        validator_stdout = ""
        if not args.skip_validation:
            validator_argv = build_validator_argv(entry, selected_dir=selected_dir, python_executable=sys.executable)
            validator_exit_code, validator_stdout = _run_validator(validator_argv, log_path=validator_log)

        row = summarize_official_run(
            entry=entry,
            selected_dir=selected_dir,
            result_obj=result_obj,
            harness_exit_code=int(harness_returncode),
            validator_exit_code=validator_exit_code,
            validator_stdout=validator_stdout,
        )
        row["timed_out"] = bool(timed_out)
        row["harness_log"] = str(harness_log)
        row["validator_log"] = str(validator_log) if validator_exit_code is not None else ""
        rows.append(row)

        print(
            "[official] "
            f"status={row['harness_status']} "
            f"bucket={row['official_report_bucket']} "
            f"generic_fallback={row['generic_template_fallback_used']}"
        )

        if row["harness_status"] != "completed":
            failures += 1
            if args.stop_on_failure:
                break

    summary = {
        "created_at": datetime.now().isoformat(),
        "manifest": str(manifest_path),
        "attempt_label": attempt_label,
        "count": len(rows),
        "failures": failures,
        "official_blind_clean": sum(1 for row in rows if row.get("official_report_bucket") == "official_blind_clean"),
        "official_blind_with_generic_fallback": sum(
            1 for row in rows if row.get("official_report_bucket") == "official_blind_with_generic_fallback"
        ),
        "invalid_for_official_reporting": sum(
            1 for row in rows if row.get("official_report_bucket") == "invalid_for_official_reporting"
        ),
        "validation_passed": sum(1 for row in rows if row.get("validation_passed") is True),
        "validation_failed": sum(1 for row in rows if row.get("validation_passed") is False),
        "items": rows,
    }
    summary_json = out_dir / "official_summary.json"
    summary_md = out_dir / "official_summary.md"
    scoreboard_json = out_dir / "official_scoreboard.json"
    scoreboard_md = out_dir / "official_scoreboard.md"
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_summary_markdown(summary_md, rows)
    scoreboard = {
        "created_at": datetime.now().isoformat(),
        "manifest": str(manifest_path),
        "attempt_label": attempt_label,
        **build_official_scoreboard(rows),
    }
    scoreboard_json.write_text(json.dumps(scoreboard, indent=2), encoding="utf-8")
    scoreboard_md.write_text(render_official_scoreboard_markdown(scoreboard), encoding="utf-8")

    print(f"[official] summary_json={summary_json}")
    print(f"[official] summary_md={summary_md}")
    print(f"[official] scoreboard_json={scoreboard_json}")
    print(f"[official] scoreboard_md={scoreboard_md}")
    if args.dry_run:
        return 0
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
