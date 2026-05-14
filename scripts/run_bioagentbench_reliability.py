#!/usr/bin/env python3
# ruff: noqa: E402
"""Run BioAgentBench reliability passes with per-task gating.

This helper watches independent strict benchmark attempts and only launches the
next pass for a task after the previous pass has completed successfully. It is
intended for overnight reliability sweeps where tasks should run independently
but still advance toward a target pass count without manual relaunching.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "benchmark_data" / "bioagentbench_official_manifest.json"
DEFAULT_MONITOR_ROOT = PROJECT_ROOT / "workspace" / "runs" / "_bioagentbench_reliability"
OFFICIAL_RUNNER = PROJECT_ROOT / "scripts" / "run_bioagentbench_official.py"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.benchmark_policy import BIOAGENTBENCH_PLANNING_STRICT_POLICY
from bio_harness.core.bioagentbench_official import resolve_manifest_entries


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Watch BioAgentBench task runs and advance each task toward a target "
            "strict reliability pass count."
        )
    )
    parser.add_argument("--manifest", type=str, default=str(DEFAULT_MANIFEST))
    parser.add_argument(
        "--benchmark-policy",
        type=str,
        default=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Restrict reliability monitoring to the specified task IDs.",
    )
    parser.add_argument(
        "--target-passes",
        type=int,
        default=3,
        help="Desired number of passing reliability runs per task.",
    )
    parser.add_argument(
        "--attempt-suffix",
        type=str,
        default="a",
        help="Suffix appended to newly launched pass labels, for example pass1a.",
    )
    parser.add_argument(
        "--label-prefix",
        type=str,
        default="planning_strict_reliability",
        help="Attempt-label namespace used to count and launch fresh reliability passes.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="Polling interval for result/status checks.",
    )
    parser.add_argument(
        "--parallel-limit",
        type=int,
        default=4,
        help="Maximum number of active task attempts allowed at once.",
    )
    parser.add_argument(
        "--launch-stagger-seconds",
        type=int,
        default=20,
        help="Minimum delay between new launches to avoid planner stampedes.",
    )
    parser.add_argument(
        "--max-load-per-core",
        type=float,
        default=1.5,
        help="Do not launch new work while 1-minute load per core exceeds this value; 0 disables the check.",
    )
    parser.add_argument(
        "--min-available-mem-gb",
        type=float,
        default=4.0,
        help="Do not launch new work while estimated available memory is below this threshold; 0 disables the check.",
    )
    parser.add_argument("--planner-model-name", type=str, default="")
    parser.add_argument("--executor-model-name", type=str, default="")
    parser.add_argument("--llm-backend", type=str, default="")
    parser.add_argument("--host", type=str, default="")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--status-root", type=str, default=str(DEFAULT_MONITOR_ROOT))
    return parser.parse_args()


def _task_slug(task_id: str) -> str:
    return task_id.strip().replace("-", "_")


def _attempt_label(task_id: str, pass_index: int, suffix: str, *, label_prefix: str = "planning_strict_reliability") -> str:
    clean_suffix = str(suffix or "").strip()
    clean_prefix = str(label_prefix or "planning_strict_reliability").strip().strip("_")
    return f"{clean_prefix}_{_task_slug(task_id)}_pass{int(pass_index)}{clean_suffix}"


def _validator_indicates_pass(validator_text: str) -> bool:
    text = str(validator_text or "")
    return "BENCHMARK PASSED: True" in text or "BENCHMARK PASSED (" in text


def _selected_dir_passed(selected_dir: Path, *, benchmark_policy: str = BIOAGENTBENCH_PLANNING_STRICT_POLICY) -> bool:
    result_path = selected_dir / "result.json"
    validator_path = selected_dir / "validator.log"
    if not result_path.exists() or not validator_path.exists():
        return False
    try:
        result_obj = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if str(result_obj.get("benchmark_policy", "")).strip() != str(benchmark_policy).strip():
        return False
    if result_obj.get("status") != "completed":
        return False
    return _validator_indicates_pass(validator_path.read_text(encoding="utf-8", errors="ignore"))


def _count_successful_reliability_passes(
    runs_root: Path,
    *,
    label_prefix: str = "planning_strict_reliability",
    benchmark_policy: str = BIOAGENTBENCH_PLANNING_STRICT_POLICY,
) -> list[str]:
    labels: list[str] = []
    clean_prefix = str(label_prefix or "planning_strict_reliability").strip().strip("_")
    expected_prefix = f"{clean_prefix}_"
    if not runs_root.exists():
        return labels
    for entry in sorted(runs_root.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith(expected_prefix):
            continue
        if _selected_dir_passed(entry, benchmark_policy=benchmark_policy):
            labels.append(entry.name)
    return labels


def _attempt_state(selected_dir: Path, *, benchmark_policy: str = BIOAGENTBENCH_PLANNING_STRICT_POLICY) -> str:
    if _selected_dir_passed(selected_dir, benchmark_policy=benchmark_policy):
        return "passed"
    result_path = selected_dir / "result.json"
    if result_path.exists():
        return "failed"
    if selected_dir.exists():
        return "running"
    return "missing"


@dataclass
class TaskStatus:
    """Current queue state for a single benchmark task."""

    task_id: str
    runs_root: str
    completed_passes: int
    successful_labels: list[str]
    active_label: str
    active_state: str
    target_passes: int
    failed_label: str
    launched_by_monitor: list[str]
    selected_dir: str
    result_status: str
    validator_pass: bool | None
    auto_repair_history_count: int | None
    launch_block_reason: str


@dataclass
class SystemStatus:
    """Current compute and runner snapshot for launch gating."""

    cpu_count: int
    active_attempts: int
    load_1: float | None
    load_5: float | None
    load_per_core_1: float | None
    available_mem_gb: float | None


def _select_entries(manifest_path: Path, task_ids: list[str]) -> list[dict[str, Any]]:
    entries = resolve_manifest_entries(manifest_path)
    wanted = {task_id.strip() for task_id in task_ids if task_id.strip()}
    if not wanted:
        return entries
    return [entry for entry in entries if str(entry.get("task_id", "")).strip() in wanted]


def _build_runner_cmd(args: argparse.Namespace, task_id: str, label: str) -> list[str]:
    cmd = [
        sys.executable,
        str(OFFICIAL_RUNNER),
        "--benchmark-policy",
        str(args.benchmark_policy),
        "--task-id",
        task_id,
        "--attempt-label",
        label,
    ]
    if str(args.planner_model_name).strip():
        cmd.extend(["--planner-model-name", str(args.planner_model_name).strip()])
    if str(args.executor_model_name).strip():
        cmd.extend(["--executor-model-name", str(args.executor_model_name).strip()])
    if str(args.llm_backend).strip():
        cmd.extend(["--llm-backend", str(args.llm_backend).strip()])
    if str(args.host).strip():
        cmd.extend(["--host", str(args.host).strip()])
    if bool(args.quiet):
        cmd.append("--quiet")
    return cmd


def _launch_attempt(
    *,
    args: argparse.Namespace,
    task_id: str,
    label: str,
    batch_dir: Path,
) -> tuple[subprocess.Popen[str], TextIO]:
    launcher_log = batch_dir / f"{task_id}__{label}.log"
    log_handle = launcher_log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        _build_runner_cmd(args, task_id, label),
        cwd=str(PROJECT_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc, log_handle


def _active_slot_count(launched: dict[str, subprocess.Popen[str]]) -> int:
    return sum(1 for proc in launched.values() if proc.poll() is None)


def _read_result_meta(selected_dir: Path) -> tuple[str, bool | None, int | None]:
    result_path = selected_dir / "result.json"
    validator_path = selected_dir / "validator.log"
    if not result_path.exists():
        return "", None, None
    try:
        result_obj = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return "unreadable", None, None
    validator_pass = None
    if validator_path.exists():
        validator_pass = _validator_indicates_pass(validator_path.read_text(encoding="utf-8", errors="ignore"))
    auto_repair_history_count = result_obj.get("auto_repair_history_count")
    if not isinstance(auto_repair_history_count, int):
        auto_repair_history_count = None
    return str(result_obj.get("status", "")), validator_pass, auto_repair_history_count


def _read_available_memory_gb() -> float | None:
    proc = subprocess.run(["vm_stat"], capture_output=True, text=True, check=False)
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        return None
    match = re.search(r"page size of (\d+) bytes", text)
    page_size = int(match.group(1)) if match else 4096
    page_counts: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        digits = re.sub(r"[^0-9]", "", value)
        if digits:
            page_counts[key.strip()] = int(digits)
    available_pages = (
        page_counts.get("Pages free", 0)
        + page_counts.get("Pages speculative", 0)
        + page_counts.get("Pages inactive", 0)
    )
    if available_pages <= 0:
        return None
    return round((available_pages * page_size) / float(1024**3), 3)


def _collect_system_status(active_attempts: int) -> SystemStatus:
    cpu_count = os.cpu_count() or 1
    try:
        load_1, load_5, _load_15 = os.getloadavg()
    except OSError:
        load_1, load_5 = None, None
    load_per_core_1 = None
    if load_1 is not None and cpu_count > 0:
        load_per_core_1 = round(float(load_1) / float(cpu_count), 3)
    return SystemStatus(
        cpu_count=cpu_count,
        active_attempts=int(active_attempts),
        load_1=None if load_1 is None else round(float(load_1), 3),
        load_5=None if load_5 is None else round(float(load_5), 3),
        load_per_core_1=load_per_core_1,
        available_mem_gb=_read_available_memory_gb(),
    )


def _count_active_attempts(task_rows: list[TaskStatus]) -> int:
    return sum(1 for row in task_rows if row.active_state in {"running", "launching"})


def _launch_block_reason(
    *,
    system_status: SystemStatus,
    parallel_limit: int,
    max_load_per_core: float,
    min_available_mem_gb: float,
    launch_stagger_seconds: int,
    seconds_since_last_launch: float,
) -> str:
    if parallel_limit > 0 and system_status.active_attempts >= parallel_limit:
        return f"parallel_limit:{system_status.active_attempts}/{parallel_limit}"
    if launch_stagger_seconds > 0 and seconds_since_last_launch < float(launch_stagger_seconds):
        return f"launch_stagger:{int(seconds_since_last_launch)}/{int(launch_stagger_seconds)}s"
    if (
        max_load_per_core > 0.0
        and system_status.load_per_core_1 is not None
        and system_status.load_per_core_1 > float(max_load_per_core)
    ):
        return f"load_per_core:{system_status.load_per_core_1}>{float(max_load_per_core):.3f}"
    if (
        min_available_mem_gb > 0.0
        and system_status.available_mem_gb is not None
        and system_status.available_mem_gb < float(min_available_mem_gb)
    ):
        return f"available_mem_gb:{system_status.available_mem_gb}<{float(min_available_mem_gb):.3f}"
    return ""


def _build_task_status(
    *,
    task_id: str,
    runs_root: Path,
    target_passes: int,
    attempt_suffix: str,
    label_prefix: str,
    benchmark_policy: str,
    launched_by_monitor: list[str],
) -> TaskStatus:
    successful_labels = _count_successful_reliability_passes(
        runs_root,
        label_prefix=label_prefix,
        benchmark_policy=benchmark_policy,
    )
    completed_passes = len(successful_labels)
    active_label = ""
    active_state = ""
    failed_label = ""
    selected_dir = ""
    result_status = ""
    validator_pass = None
    auto_repair_history_count = None
    if completed_passes < int(target_passes):
        pass_index = completed_passes + 1
        active_label = _attempt_label(task_id, pass_index, attempt_suffix, label_prefix=label_prefix)
        selected_path = runs_root / active_label
        selected_dir = str(selected_path)
        active_state = _attempt_state(selected_path, benchmark_policy=benchmark_policy)
        result_status, validator_pass, auto_repair_history_count = _read_result_meta(selected_path)
        if active_state == "failed":
            failed_label = active_label
    return TaskStatus(
        task_id=task_id,
        runs_root=str(runs_root),
        completed_passes=completed_passes,
        successful_labels=successful_labels,
        active_label=active_label,
        active_state=active_state,
        target_passes=int(target_passes),
        failed_label=failed_label,
        launched_by_monitor=launched_by_monitor,
        selected_dir=selected_dir,
        result_status=result_status,
        validator_pass=validator_pass,
        auto_repair_history_count=auto_repair_history_count,
        launch_block_reason="",
    )


def _write_status(
    status_path: Path,
    *,
    batch_dir: Path,
    task_rows: list[TaskStatus],
    system_status: SystemStatus,
) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(),
        "batch_dir": str(batch_dir),
        "system_status": asdict(system_status),
        "tasks": [asdict(row) for row in task_rows],
    }
    status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    batch_dir = Path(args.status_root).expanduser().resolve() / datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir.mkdir(parents=True, exist_ok=True)
    status_path = batch_dir / "status.json"

    entries = _select_entries(manifest_path, list(args.task_id or []))
    tasks: dict[str, Path] = {}
    for entry in entries:
        task_id = str(entry.get("task_id", "")).strip()
        runs_root = Path(str(entry.get("runs_root", "") or "")).expanduser().resolve()
        if task_id and runs_root:
            tasks[task_id] = runs_root

    launched: dict[str, subprocess.Popen[str]] = {}
    log_handles: dict[str, TextIO] = {}
    launched_labels: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    last_launch_at = 0.0

    while True:
        for label, proc in list(launched.items()):
            if proc.poll() is not None:
                handle = log_handles.pop(label, None)
                if handle is not None:
                    handle.close()
                launched.pop(label, None)

        task_rows = [
            _build_task_status(
                task_id=task_id,
                runs_root=runs_root,
                target_passes=int(args.target_passes),
                attempt_suffix=str(args.attempt_suffix),
                label_prefix=str(args.label_prefix),
                benchmark_policy=str(args.benchmark_policy),
                launched_by_monitor=launched_labels.get(task_id, []),
            )
            for task_id, runs_root in tasks.items()
        ]
        remaining = sum(1 for row in task_rows if row.completed_passes < int(args.target_passes) and not row.failed_label)
        active_attempts = _count_active_attempts(task_rows)
        system_status = _collect_system_status(active_attempts)

        for row in task_rows:
            if row.completed_passes >= int(args.target_passes) or row.failed_label or row.active_state != "missing":
                continue
            seconds_since_last_launch = time.monotonic() - last_launch_at if last_launch_at > 0 else float("inf")
            block_reason = _launch_block_reason(
                system_status=system_status,
                parallel_limit=int(args.parallel_limit or 0),
                max_load_per_core=float(args.max_load_per_core or 0.0),
                min_available_mem_gb=float(args.min_available_mem_gb or 0.0),
                launch_stagger_seconds=int(args.launch_stagger_seconds or 0),
                seconds_since_last_launch=seconds_since_last_launch,
            )
            row.launch_block_reason = block_reason
            if block_reason:
                continue
            proc, handle = _launch_attempt(
                args=args,
                task_id=row.task_id,
                label=row.active_label,
                batch_dir=batch_dir,
            )
            launched[row.active_label] = proc
            log_handles[row.active_label] = handle
            launched_labels.setdefault(row.task_id, []).append(row.active_label)
            row.active_state = "launching"
            row.launch_block_reason = ""
            last_launch_at = time.monotonic()
            system_status.active_attempts += 1

        _write_status(
            status_path,
            batch_dir=batch_dir,
            task_rows=task_rows,
            system_status=system_status,
        )

        if remaining == 0:
            break

        time.sleep(max(5, int(args.poll_seconds)))

    for handle in log_handles.values():
        handle.close()

    failed_rows = [row for row in task_rows if row.failed_label]
    if failed_rows:
        for row in failed_rows:
            print(f"[reliability] failed task={row.task_id} label={row.failed_label}")
        print(f"[reliability] status={status_path}")
        return 2

    print(f"[reliability] status={status_path}")
    print("[reliability] all requested tasks reached target pass count")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
