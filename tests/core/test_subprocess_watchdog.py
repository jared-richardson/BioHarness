from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bio_harness.core import subprocess_watchdog as watchdog_mod  # noqa: E402
from bio_harness.core.subprocess_watchdog import (  # noqa: E402
    WatchdogProgressPolicy,
    run_subprocess_with_watchdog,
)


def test_run_subprocess_with_watchdog_times_out_without_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "watchdog.log"

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = 0
            self.killed = 0

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated += 1
            self.returncode = -15

        def wait(self, timeout=None) -> int:
            return int(self.returncode or -15)

        def kill(self) -> None:
            self.killed += 1
            self.returncode = -9

    fake_proc = _FakeProc()
    clock = {"now": 0.0}

    def _fake_monotonic() -> float:
        value = clock["now"]
        clock["now"] += 0.6
        return value

    monkeypatch.setattr(watchdog_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(watchdog_mod.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(watchdog_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(watchdog_mod, "_watchdog_progress_mtime", lambda _paths: 0.0)

    returncode, timed_out = run_subprocess_with_watchdog(
        cmd=["python3", "scripts/run_agent_e2e.py"],
        cwd=tmp_path,
        env={},
        log_path=log_path,
        timeout_seconds=1,
        termination_grace_seconds=15.0,
        timeout_message="[test] watchdog exceeded 1s; sending SIGTERM.",
        kill_message="[test] sending SIGKILL.",
        progress_policy=WatchdogProgressPolicy(
            poll_seconds=0.1,
            progress_grace_seconds=0.5,
            max_extension_seconds=1.0,
        ),
    )

    assert timed_out is True
    assert returncode == -15
    assert fake_proc.terminated == 1
    assert fake_proc.killed == 0
    assert "[test] watchdog exceeded 1s; sending SIGTERM." in log_path.read_text(encoding="utf-8")


def test_run_subprocess_with_watchdog_extends_deadline_on_log_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "watchdog.log"

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.poll_calls = 0

        def poll(self) -> int | None:
            self.poll_calls += 1
            if self.poll_calls >= 3:
                self.returncode = 0
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self, timeout=None) -> int:
            return int(self.returncode or 0)

        def kill(self) -> None:
            self.returncode = -9

    fake_proc = _FakeProc()
    clock = {"now": 0.0}
    mtimes = iter([0.0, 1.0, 1.0, 1.0, 1.0])

    def _fake_monotonic() -> float:
        value = clock["now"]
        clock["now"] += 0.6
        return value

    monkeypatch.setattr(watchdog_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(watchdog_mod.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(watchdog_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(watchdog_mod, "_watchdog_progress_mtime", lambda _paths: next(mtimes, 1.0))

    returncode, timed_out = run_subprocess_with_watchdog(
        cmd=["python3", "scripts/run_agent_e2e.py"],
        cwd=tmp_path,
        env={},
        log_path=log_path,
        timeout_seconds=1,
        termination_grace_seconds=15.0,
        timeout_message="[test] watchdog exceeded 1s; sending SIGTERM.",
        kill_message="[test] sending SIGKILL.",
        progress_policy=WatchdogProgressPolicy(
            poll_seconds=0.1,
            progress_grace_seconds=1.0,
            max_extension_seconds=2.0,
        ),
    )

    assert timed_out is False
    assert returncode == 0
    assert fake_proc.poll_calls >= 3
    assert "[test] watchdog exceeded 1s; sending SIGTERM." not in log_path.read_text(encoding="utf-8")


def test_run_subprocess_with_watchdog_extends_deadline_on_directory_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "watchdog.log"
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.poll_calls = 0

        def poll(self) -> int | None:
            self.poll_calls += 1
            if self.poll_calls >= 4:
                self.returncode = 0
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self, timeout=None) -> int:
            return int(self.returncode or 0)

        def kill(self) -> None:
            self.returncode = -9

    fake_proc = _FakeProc()
    clock = {"now": 0.0}
    # Two reads per loop iteration now (log + artifacts). Supply enough
    # values that artifact progress is observed on the second poll.
    mtimes = iter([0.0, 0.0, 0.0, 0.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0])
    observed_progress_paths: list[tuple[Path, ...]] = []

    def _fake_monotonic() -> float:
        value = clock["now"]
        clock["now"] += 0.6
        return value

    def _fake_progress_mtime(paths: tuple[Path, ...]) -> float:
        observed_progress_paths.append(tuple(paths))
        return next(mtimes, 2.0)

    monkeypatch.setattr(watchdog_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(watchdog_mod.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(watchdog_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(watchdog_mod, "_watchdog_progress_mtime", _fake_progress_mtime)

    returncode, timed_out = run_subprocess_with_watchdog(
        cmd=["python3", "scripts/run_agent_e2e.py"],
        cwd=tmp_path,
        env={},
        log_path=log_path,
        timeout_seconds=1,
        termination_grace_seconds=15.0,
        timeout_message="[test] watchdog exceeded 1s; sending SIGTERM.",
        kill_message="[test] sending SIGKILL.",
        progress_policy=WatchdogProgressPolicy(
            poll_seconds=0.1,
            progress_grace_seconds=1.0,
            max_extension_seconds=2.0,
        ),
        progress_paths=(selected_dir,),
    )

    assert timed_out is False
    assert returncode == 0
    assert any(selected_dir in paths for paths in observed_progress_paths)


def test_run_subprocess_with_watchdog_uses_dynamic_progress_resolver(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "watchdog.log"
    dynamic_dir = tmp_path / "dynamic"
    dynamic_dir.mkdir()

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.poll_calls = 0

        def poll(self) -> int | None:
            self.poll_calls += 1
            if self.poll_calls >= 4:
                self.returncode = 0
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self, timeout=None) -> int:
            return int(self.returncode or 0)

        def kill(self) -> None:
            self.returncode = -9

    fake_proc = _FakeProc()
    clock = {"now": 0.0}
    # Two reads per loop iteration now (log + artifacts). Supply enough
    # values that artifact progress is observed on the second poll.
    mtimes = iter([0.0, 0.0, 0.0, 0.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0])
    resolver_calls = {"count": 0}

    def _fake_monotonic() -> float:
        value = clock["now"]
        clock["now"] += 0.6
        return value

    def _fake_progress_mtime(paths: tuple[Path, ...]) -> float:
        return next(mtimes, 3.0)

    def _resolver() -> tuple[Path, ...]:
        resolver_calls["count"] += 1
        return (dynamic_dir,)

    monkeypatch.setattr(watchdog_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(watchdog_mod.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(watchdog_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(watchdog_mod, "_watchdog_progress_mtime", _fake_progress_mtime)

    returncode, timed_out = run_subprocess_with_watchdog(
        cmd=["python3", "scripts/run_agent_e2e.py"],
        cwd=tmp_path,
        env={},
        log_path=log_path,
        timeout_seconds=1,
        termination_grace_seconds=15.0,
        timeout_message="[test] watchdog exceeded 1s; sending SIGTERM.",
        kill_message="[test] sending SIGKILL.",
        progress_policy=WatchdogProgressPolicy(
            poll_seconds=0.1,
            progress_grace_seconds=1.0,
            max_extension_seconds=2.0,
        ),
        progress_path_resolver=_resolver,
    )

    assert timed_out is False
    assert returncode == 0
    assert resolver_calls["count"] >= 2


def test_run_subprocess_with_watchdog_ignores_log_spam_without_artifact_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Livelock guard: if artifact paths are configured and idle, log-only
    updates (e.g., a planner emitting repeated error lines) must not keep
    extending the deadline indefinitely."""

    log_path = tmp_path / "watchdog.log"
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.poll_calls = 0
            self.terminated = 0

        def poll(self) -> int | None:
            self.poll_calls += 1
            return self.returncode

        def terminate(self) -> None:
            self.terminated += 1
            self.returncode = -15

        def wait(self, timeout=None) -> int:
            return int(self.returncode or -15)

        def kill(self) -> None:
            self.returncode = -9

    fake_proc = _FakeProc()
    clock = {"now": 0.0}

    def _fake_monotonic() -> float:
        value = clock["now"]
        clock["now"] += 0.6
        return value

    # Every call advances log mtime (simulating a livelocked child that keeps
    # writing error lines) while artifact mtime stays flat at 0.0.
    log_counter = {"value": 0.0}

    def _fake_progress_mtime(paths: tuple[Path, ...]) -> float:
        # Path tuple containing exactly the log file is the log query.
        if len(paths) == 1 and paths[0] == log_path:
            log_counter["value"] += 1.0
            return log_counter["value"]
        # Any artifact query returns 0.0 — no artifact progress ever.
        return 0.0

    monkeypatch.setattr(watchdog_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(watchdog_mod.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(watchdog_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(watchdog_mod, "_watchdog_progress_mtime", _fake_progress_mtime)

    returncode, timed_out = run_subprocess_with_watchdog(
        cmd=["python3", "scripts/run_agent_e2e.py"],
        cwd=tmp_path,
        env={},
        log_path=log_path,
        timeout_seconds=2,
        termination_grace_seconds=15.0,
        timeout_message="[test] watchdog exceeded 2s; sending SIGTERM.",
        kill_message="[test] sending SIGKILL.",
        progress_policy=WatchdogProgressPolicy(
            poll_seconds=0.1,
            progress_grace_seconds=5.0,
            max_extension_seconds=100.0,
            log_only_idle_tolerance_seconds=1.0,
        ),
        progress_paths=(selected_dir,),
    )

    assert timed_out is True
    assert returncode == -15
    assert fake_proc.terminated == 1
    assert "[test] watchdog exceeded 2s; sending SIGTERM." in log_path.read_text(encoding="utf-8")


def test_run_subprocess_with_watchdog_trusts_log_when_no_artifact_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Without artifact paths configured, log activity is the only progress
    signal and must still extend the deadline — backward-compatible fallback."""

    log_path = tmp_path / "watchdog.log"

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.poll_calls = 0

        def poll(self) -> int | None:
            self.poll_calls += 1
            if self.poll_calls >= 4:
                self.returncode = 0
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self, timeout=None) -> int:
            return int(self.returncode or 0)

        def kill(self) -> None:
            self.returncode = -9

    fake_proc = _FakeProc()
    clock = {"now": 0.0}
    # Two reads per poll (log + artifacts). Log advances on poll 2; artifacts
    # always 0.0. No artifact paths configured → log alone must extend.
    log_value = {"value": 0.0}

    def _fake_monotonic() -> float:
        value = clock["now"]
        clock["now"] += 0.6
        return value

    def _fake_progress_mtime(paths: tuple[Path, ...]) -> float:
        if len(paths) == 1 and paths[0] == log_path:
            log_value["value"] += 1.0
            return log_value["value"]
        return 0.0

    monkeypatch.setattr(watchdog_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(watchdog_mod.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(watchdog_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(watchdog_mod, "_watchdog_progress_mtime", _fake_progress_mtime)

    returncode, timed_out = run_subprocess_with_watchdog(
        cmd=["python3", "scripts/run_agent_e2e.py"],
        cwd=tmp_path,
        env={},
        log_path=log_path,
        timeout_seconds=1,
        termination_grace_seconds=15.0,
        timeout_message="[test] watchdog exceeded 1s; sending SIGTERM.",
        kill_message="[test] sending SIGKILL.",
        progress_policy=WatchdogProgressPolicy(
            poll_seconds=0.1,
            progress_grace_seconds=1.0,
            max_extension_seconds=2.0,
            log_only_idle_tolerance_seconds=0.5,
        ),
    )

    assert timed_out is False
    assert returncode == 0
