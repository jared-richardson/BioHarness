"""Tests for the release-quality receipt runner."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts import check_release_quality


def test_tail_keeps_short_output_and_bounds_long_output() -> None:
    assert check_release_quality._tail("short", max_chars=10) == "short"
    assert check_release_quality._tail("0123456789abcdef", max_chars=4) == "cdef"


def test_run_command_records_bounded_output(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=7,
            stdout="x" * 9000,
            stderr="y" * 9000,
        )

    monkeypatch.setattr(check_release_quality.subprocess, "run", fake_run)

    result = check_release_quality._run_command("sample", ["tool", "arg"], cwd=tmp_path)

    assert result.name == "sample"
    assert result.command == ["tool", "arg"]
    assert result.returncode == 7
    assert len(result.stdout_tail) == 8000
    assert len(result.stderr_tail) == 8000


def test_build_commands_includes_blocking_and_advisory_gates(tmp_path: Path) -> None:
    args = argparse.Namespace(
        coverage=True,
        skip_type=False,
        skip_focused_tests=False,
        skip_frontend=False,
    )

    names = [name for name, _command in check_release_quality._build_commands(tmp_path, args)]

    assert names[:4] == [
        "stage_public_tree",
        "scan_public_tree",
        "ruff_check_release_critical",
        "ruff_format_check_release_critical",
    ]
    assert "ruff_broad_hygiene_advisory" in names
    assert "mypy_scoped_release_critical" in names
    assert "coverage_focused_report" in names
    assert names[-3:] == ["frontend_lint", "frontend_build", "frontend_audit"]


def test_build_commands_respects_skip_flags(tmp_path: Path) -> None:
    args = argparse.Namespace(
        coverage=False,
        skip_type=True,
        skip_focused_tests=True,
        skip_frontend=True,
    )

    names = [name for name, _command in check_release_quality._build_commands(tmp_path, args)]

    assert "mypy_scoped_release_critical" not in names
    assert "pytest_focused" not in names
    assert "coverage_focused_run" not in names
    assert "frontend_lint" not in names


def test_write_markdown_includes_failure_details(tmp_path: Path) -> None:
    path = tmp_path / "receipt.md"
    results = [
        check_release_quality.CommandResult(
            name="ok",
            command=["true"],
            returncode=0,
            stdout_tail="",
            stderr_tail="",
        ),
        check_release_quality.CommandResult(
            name="bad",
            command=["false"],
            returncode=1,
            stdout_tail="stdout",
            stderr_tail="stderr",
        ),
    ]

    check_release_quality._write_markdown(path, results)

    text = path.read_text(encoding="utf-8")
    assert "| `ok` | pass |" in text
    assert "| `bad` | fail (1) |" in text
    assert "stdout" in text
    assert "stderr" in text


def test_main_writes_receipts_and_returns_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_build_commands(
        repo_root: Path,
        args: argparse.Namespace,
    ) -> list[tuple[str, list[str]]]:
        assert repo_root == tmp_path
        assert args.skip_frontend is True
        return [("ok", ["true"]), ("bad", ["false"])]

    results = {
        "ok": check_release_quality.CommandResult("ok", ["true"], 0, "", ""),
        "bad": check_release_quality.CommandResult("bad", ["false"], 2, "out", "err"),
    }

    def fake_run_command(
        name: str,
        command: list[str],
        *,
        cwd: Path,
    ) -> check_release_quality.CommandResult:
        assert command == results[name].command
        assert cwd == tmp_path
        return results[name]

    monkeypatch.setattr(check_release_quality, "_build_commands", fake_build_commands)
    monkeypatch.setattr(check_release_quality, "_run_command", fake_run_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_release_quality.py",
            "--skip-frontend",
            "--receipt-json",
            "receipt.json",
            "--receipt-md",
            "receipt.md",
        ],
    )

    assert check_release_quality.main() == 1
    assert (tmp_path / "receipt.json").exists()
    assert (tmp_path / "receipt.md").exists()
