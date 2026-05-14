"""Helpers for progress-aware subprocess watchdogs.

This module centralizes the runner-side watchdog logic used by benchmark
orchestration scripts. The watchdog remains strict about idle subprocesses, but
it can grant bounded extensions when the child is still writing fresh progress
signals. Those signals can come from the combined stdout/stderr log or from
runtime artifact paths such as selected directories and run-state files.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Sequence
from pathlib import Path
import signal
import subprocess
import time


@dataclass(frozen=True)
class WatchdogProgressPolicy:
    """Bounded progress-extension settings for one subprocess watchdog.

    Attributes:
        poll_seconds: Poll interval for checking subprocess exit and log mtime.
        progress_grace_seconds: Additional time to grant after fresh progress
            is observed.
        max_extension_seconds: Maximum additional wall-clock time beyond the
            initial timeout that the watchdog may grant.
        log_only_idle_tolerance_seconds: Maximum time that log-file activity
            alone (without any artifact-path progress) is allowed to keep
            extending the deadline. Once artifacts have been idle for longer
            than this window, log chatter (e.g., a planner livelock emitting
            repeated error messages) stops counting as progress. When
            artifact progress paths are not configured, this cap is ignored
            because the log is the only progress signal available.
    """

    poll_seconds: float = 1.0
    progress_grace_seconds: float = 300.0
    max_extension_seconds: float = 1800.0
    log_only_idle_tolerance_seconds: float = 300.0


def _watchdog_path_mtime(path: Path) -> float:
    """Return the freshest observable modification time for one path.

    Args:
        path: File or directory to observe for progress.

    Returns:
        The freshest floating-point modification time reachable from ``path``,
        or ``0.0`` when the path is absent or temporarily unreadable.
    """

    try:
        root_stat = path.stat()
    except OSError:
        return 0.0
    latest_mtime = float(root_stat.st_mtime)
    if not path.is_dir():
        return latest_mtime
    try:
        for child in path.rglob("*"):
            try:
                latest_mtime = max(latest_mtime, float(child.stat().st_mtime))
            except OSError:
                continue
    except OSError:
        return latest_mtime
    return latest_mtime


def _watchdog_progress_mtime(progress_paths: Sequence[Path]) -> float:
    """Return the freshest modification time across watched progress paths.

    Args:
        progress_paths: Paths that should count as forward progress.

    Returns:
        The newest modification time across ``progress_paths``.
    """

    latest_mtime = 0.0
    for path in progress_paths:
        latest_mtime = max(latest_mtime, _watchdog_path_mtime(path))
    return latest_mtime


def _current_watched_paths(
    *,
    log_path: Path,
    static_progress_paths: Sequence[Path],
    progress_path_resolver: Callable[[], Sequence[Path]] | None,
) -> tuple[Path, ...]:
    """Return the current merged set of paths that count as progress.

    Args:
        log_path: Combined stdout/stderr log path.
        static_progress_paths: Statically configured progress paths.
        progress_path_resolver: Optional resolver for dynamic progress paths.

    Returns:
        A tuple of unique watched paths.
    """

    ordered: list[Path] = [log_path]
    seen = {log_path}
    for path in static_progress_paths:
        if path in seen:
            continue
        ordered.append(path)
        seen.add(path)
    if progress_path_resolver is not None:
        try:
            dynamic_paths = tuple(progress_path_resolver())
        except Exception:
            dynamic_paths = ()
        for path in dynamic_paths:
            if path in seen:
                continue
            ordered.append(path)
            seen.add(path)
    return tuple(ordered)


def _current_artifact_paths(
    *,
    log_path: Path,
    static_progress_paths: Sequence[Path],
    progress_path_resolver: Callable[[], Sequence[Path]] | None,
) -> tuple[Path, ...]:
    """Return the current set of artifact paths (watched paths minus the log).

    Artifact paths represent real pipeline state (output directories, run-state
    files) and are treated as stronger progress signals than log-file activity,
    which a livelocked child may continue producing indefinitely. An empty
    tuple means only the log path is available as a progress signal.
    """

    ordered: list[Path] = []
    seen: set[Path] = {log_path}
    for path in static_progress_paths:
        if path in seen:
            continue
        ordered.append(path)
        seen.add(path)
    if progress_path_resolver is not None:
        try:
            dynamic_paths = tuple(progress_path_resolver())
        except Exception:
            dynamic_paths = ()
        for path in dynamic_paths:
            if path in seen:
                continue
            ordered.append(path)
            seen.add(path)
    return tuple(ordered)


def run_subprocess_with_watchdog(
    *,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    timeout_seconds: int,
    termination_grace_seconds: float,
    timeout_message: str,
    kill_message: str,
    progress_policy: WatchdogProgressPolicy | None = None,
    progress_paths: Sequence[Path] | None = None,
    progress_path_resolver: Callable[[], Sequence[Path]] | None = None,
) -> tuple[int, bool]:
    """Run a subprocess with a bounded progress-aware watchdog.

    Args:
        cmd: Command vector for the child process.
        cwd: Working directory for the child process.
        env: Environment variables for the child process.
        log_path: Combined stdout/stderr log destination.
        timeout_seconds: Initial watchdog timeout. Use ``0`` to disable the
            watchdog.
        termination_grace_seconds: Grace period to wait after SIGTERM before
            sending SIGKILL.
        timeout_message: Log line written when the watchdog sends SIGTERM.
        kill_message: Log line written when graceful shutdown times out.
        progress_policy: Optional bounded extension policy.
        progress_paths: Optional additional files or directories whose
            modification times count as progress.
        progress_path_resolver: Optional callable that resolves additional
            progress paths during execution. This supports late-created run
            directories and other dynamic progress locations.

    Returns:
        A tuple ``(returncode, timed_out)``.
    """

    policy = progress_policy or WatchdogProgressPolicy(
        poll_seconds=1.0,
        progress_grace_seconds=max(60.0, min(300.0, float(timeout_seconds or 0))),
        max_extension_seconds=max(0.0, float(timeout_seconds or 0)),
        log_only_idle_tolerance_seconds=max(60.0, min(300.0, float(timeout_seconds or 0))),
    )
    static_progress_paths = tuple(progress_paths or ())
    with log_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        start_monotonic = time.monotonic()
        deadline = (start_monotonic + float(timeout_seconds)) if timeout_seconds > 0 else None
        max_deadline = (
            (float(deadline) + float(max(0.0, policy.max_extension_seconds)))
            if deadline is not None
            else None
        )
        log_mtime = _watchdog_progress_mtime((log_path,))
        artifact_paths_initial = _current_artifact_paths(
            log_path=log_path,
            static_progress_paths=static_progress_paths,
            progress_path_resolver=progress_path_resolver,
        )
        last_log_mtime = log_mtime
        last_artifact_mtime = _watchdog_progress_mtime(artifact_paths_initial)
        # Wall-clock marker for when artifact progress was last observed.
        # Used to decide whether log-only activity still counts as meaningful
        # progress. Starts at process start to grant initial grace.
        last_artifact_progress_at = start_monotonic
        log_only_tolerance = float(
            max(0.0, getattr(policy, "log_only_idle_tolerance_seconds", policy.progress_grace_seconds))
        )
        while True:
            returncode = proc.poll()
            if returncode is not None:
                return int(returncode), False
            now = time.monotonic()
            if deadline is not None:
                artifact_paths = _current_artifact_paths(
                    log_path=log_path,
                    static_progress_paths=static_progress_paths,
                    progress_path_resolver=progress_path_resolver,
                )
                latest_log_mtime = _watchdog_progress_mtime((log_path,))
                latest_artifact_mtime = _watchdog_progress_mtime(artifact_paths)
                artifact_advanced = latest_artifact_mtime > (last_artifact_mtime + 1e-9)
                log_advanced = latest_log_mtime > (last_log_mtime + 1e-9)
                if artifact_advanced:
                    last_artifact_mtime = latest_artifact_mtime
                    last_artifact_progress_at = now
                if log_advanced:
                    last_log_mtime = latest_log_mtime
                # Decide whether observed activity should extend the deadline.
                # Rule:
                #  * Artifact progress always counts.
                #  * Log-only progress counts only when no artifact paths are
                #    configured, OR when artifacts have been idle for less
                #    than log_only_idle_tolerance_seconds. This caps the
                #    damage from livelocked children that spam their log.
                no_artifact_paths_configured = len(artifact_paths) == 0
                artifact_idle_seconds = now - last_artifact_progress_at
                log_still_trusted = (
                    no_artifact_paths_configured
                    or artifact_idle_seconds <= log_only_tolerance
                )
                counts_as_progress = artifact_advanced or (log_advanced and log_still_trusted)
                if counts_as_progress:
                    if max_deadline is not None and now < max_deadline:
                        deadline = min(
                            max_deadline,
                            max(float(deadline), now + float(max(0.0, policy.progress_grace_seconds))),
                        )
                if now >= deadline:
                    handle.write(f"\n{timeout_message}\n")
                    handle.flush()
                    proc.terminate()
                    try:
                        proc.wait(timeout=float(termination_grace_seconds))
                    except subprocess.TimeoutExpired:
                        handle.write(f"{kill_message}\n")
                        handle.flush()
                        proc.kill()
                        proc.wait(timeout=5.0)
                    return int(proc.returncode if proc.returncode is not None else -int(signal.SIGTERM)), True
            time.sleep(max(0.05, float(policy.poll_seconds)))
