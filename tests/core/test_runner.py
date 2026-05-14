"""Tests for bio_harness.core.runner.CommandRunner."""

from __future__ import annotations

import os
import queue
import threading
from pathlib import Path

import pytest

from bio_harness.core.runner import CommandRunner


@pytest.fixture
def runner() -> CommandRunner:
    return CommandRunner()


def _make_echo_tool(path: Path, message: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho {message}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# Blocked-pattern validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf / --no-preserve-root",
        "sudo apt-get install foo",
        "shutdown -h now",
        "reboot",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "echo hello >:",
    ],
)
def test_validate_command_blocks_dangerous_patterns(runner: CommandRunner, cmd: str):
    with pytest.raises(PermissionError, match="blocked pattern"):
        runner._validate_command(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "git status",
        "git clone https://example.com/repo.git",
        "echo hello; git diff",
        "git push origin main",
    ],
)
def test_validate_command_blocks_git(runner: CommandRunner, cmd: str):
    with pytest.raises(PermissionError, match="git"):
        runner._validate_command(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "echo hello world",
        "ls -la /tmp",
        "samtools sort input.bam",
        "python3 script.py",
        "STAR --runMode genomeGenerate",
    ],
)
def test_validate_command_allows_safe_commands(runner: CommandRunner, cmd: str):
    # Should not raise
    runner._validate_command(cmd)


def test_validate_command_blocks_inline_interpreter_escape(runner: CommandRunner):
    with pytest.raises(PermissionError, match="inline interpreter"):
        runner._validate_command('python3 -c "from pathlib import Path; Path(\'/tmp/outside.txt\').write_text(\'x\')"')


def test_validate_command_audits_runtime_download(monkeypatch, runner: CommandRunner):
    monkeypatch.setenv("BIO_HARNESS_EXECUTION_POLICY", "audit")
    audits = runner._validate_command("curl -L https://github.com/example/repo/releases/download/v1/tool.tar.gz")
    assert audits == ["execution_policy_audit:runtime_download:github.com"]


def test_validate_command_blocks_untrusted_download_in_trusted_only_mode(monkeypatch, runner: CommandRunner):
    monkeypatch.setenv("BIO_HARNESS_EXECUTION_POLICY", "trusted_only")
    with pytest.raises(PermissionError, match="runtime_download_untrusted_host:example.com"):
        runner._validate_command("wget https://example.com/tool.tar.gz")


# ---------------------------------------------------------------------------
# Write-target sandboxing
# ---------------------------------------------------------------------------


def test_write_targets_blocks_outside_root(runner: CommandRunner, tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    cwd = root

    with pytest.raises(PermissionError, match="outside allowed root"):
        runner._validate_write_targets("cp file.txt /etc/passwd", cwd, root)


def test_write_targets_blocks_writes_to_inputs_readonly(
    runner: CommandRunner, tmp_path: Path
):
    root = tmp_path / "workspace"
    readonly = root / "inputs_readonly"
    readonly.mkdir(parents=True)

    with pytest.raises(PermissionError, match="read-only root"):
        runner._validate_write_targets(
            f"cp file.txt {readonly / 'dest.txt'}", root, root
        )


def test_write_targets_allows_writes_inside_root(
    runner: CommandRunner, tmp_path: Path
):
    root = tmp_path / "workspace"
    outputs = root / "outputs"
    outputs.mkdir(parents=True)

    # Should not raise
    runner._validate_write_targets(
        f"cp file.txt {outputs / 'result.txt'}", root, root
    )


def test_write_targets_blocks_redirection_outside_root(
    runner: CommandRunner, tmp_path: Path
):
    root = tmp_path / "workspace"
    root.mkdir()

    with pytest.raises(PermissionError, match="outside allowed root"):
        runner._validate_write_targets("echo hello > /tmp/evil.txt", root, root)


def test_write_targets_allows_redirect_to_dev_null(
    runner: CommandRunner, tmp_path: Path
):
    root = tmp_path / "workspace"
    root.mkdir()
    # Should not raise
    runner._validate_write_targets("echo hello > /dev/null", root, root)


# ---------------------------------------------------------------------------
# run_command: command execution and streaming
# ---------------------------------------------------------------------------


def test_run_command_streams_output(runner: CommandRunner, tmp_path: Path):
    q: queue.Queue = queue.Queue()
    root = tmp_path / "workspace"
    root.mkdir()

    runner.run_command(
        "echo hello_from_runner",
        q,
        cwd=str(root),
        allowed_root=str(root),
    )

    lines = []
    while True:
        item = q.get(timeout=10)
        if item is None:
            break
        lines.append(item)

    combined = "".join(lines)
    assert "[status] spawned pid=" in combined
    assert "hello_from_runner" in combined
    assert "[exit_code=0]" in combined


def test_run_command_reports_exit_code_on_failure(
    runner: CommandRunner, tmp_path: Path
):
    q: queue.Queue = queue.Queue()
    root = tmp_path / "workspace"
    root.mkdir()

    runner.run_command(
        "false",
        q,
        cwd=str(root),
        allowed_root=str(root),
    )

    lines = []
    while True:
        item = q.get(timeout=10)
        if item is None:
            break
        lines.append(item)

    combined = "".join(lines)
    assert "[exit_code=" in combined
    # 'false' returns exit code 1
    assert "[exit_code=0]" not in combined


def test_run_command_normalizes_pixi_path_order(monkeypatch, runner: CommandRunner, tmp_path: Path):
    default_bin = tmp_path / ".pixi" / "envs" / "default" / "bin"
    specialty_bin = tmp_path / ".pixi" / "envs" / "specialty-annotation" / "bin"
    _make_echo_tool(default_bin / "bcftools", "DEFAULT")
    _make_echo_tool(specialty_bin / "bcftools", "SPECIALTY")

    monkeypatch.setattr(
        "bio_harness.core.tool_env.pixi_env_bin_dirs",
        lambda: [default_bin, specialty_bin],
    )
    monkeypatch.setattr("bio_harness.core.tool_env.pixi_jvm_bin_dirs", lambda: [])
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(
            [
                str(specialty_bin),
                str(default_bin),
                os.environ.get("PATH", ""),
            ]
        ),
    )

    q: queue.Queue = queue.Queue()
    root = tmp_path / "workspace"
    root.mkdir()

    runner.run_command(
        "bcftools",
        q,
        cwd=str(root),
        allowed_root=str(root),
    )

    lines = []
    while True:
        item = q.get(timeout=10)
        if item is None:
            break
        lines.append(item)

    combined = "".join(lines)
    assert "DEFAULT" in combined
    assert "SPECIALTY" not in combined
    assert "[exit_code=0]" in combined


def test_run_command_blocks_dangerous_command(
    runner: CommandRunner, tmp_path: Path
):
    q: queue.Queue = queue.Queue()
    root = tmp_path / "workspace"
    root.mkdir()

    runner.run_command(
        "sudo rm -rf /",
        q,
        cwd=str(root),
        allowed_root=str(root),
    )

    lines = []
    while True:
        item = q.get(timeout=10)
        if item is None:
            break
        lines.append(item)

    combined = "".join(lines)
    assert "__POLICY_BLOCK__" in combined
    assert "[exit_code=126]" in combined


def test_run_command_emits_policy_audit_marker(monkeypatch, runner: CommandRunner, tmp_path: Path):
    monkeypatch.setattr(
        runner,
        "_validate_command",
        lambda command: ["execution_policy_audit:runtime_download:github.com"],
    )
    q: queue.Queue = queue.Queue()
    root = tmp_path / "workspace"
    root.mkdir()

    runner.run_command(
        "echo policy-audit",
        q,
        cwd=str(root),
        allowed_root=str(root),
    )

    lines = []
    while True:
        item = q.get(timeout=10)
        if item is None:
            break
        lines.append(item)

    combined = "".join(lines)
    assert "__POLICY_AUDIT__:execution_policy_audit:runtime_download:github.com" in combined


def test_run_command_respects_cancel_event(
    runner: CommandRunner, tmp_path: Path
):
    q: queue.Queue = queue.Queue()
    root = tmp_path / "workspace"
    root.mkdir()
    cancel = threading.Event()
    cancel.set()  # Pre-set so it cancels immediately

    runner.run_command(
        "sleep 60",
        q,
        cwd=str(root),
        allowed_root=str(root),
        cancel_event=cancel,
    )

    lines = []
    while True:
        item = q.get(timeout=15)
        if item is None:
            break
        lines.append(item)

    combined = "".join(lines)
    assert "__COMMAND_CANCELLED__" in combined


def test_run_command_cwd_outside_root_blocked(
    runner: CommandRunner, tmp_path: Path
):
    q: queue.Queue = queue.Queue()
    root = tmp_path / "workspace"
    root.mkdir()

    runner.run_command(
        "echo hello",
        q,
        cwd="/tmp",
        allowed_root=str(root),
    )

    lines = []
    while True:
        item = q.get(timeout=10)
        if item is None:
            break
        lines.append(item)

    combined = "".join(lines)
    assert "__POLICY_BLOCK__" in combined


# ---------------------------------------------------------------------------
# resolve_token_path helper
# ---------------------------------------------------------------------------


def test_resolve_token_path_skips_flags(runner: CommandRunner, tmp_path: Path):
    assert runner._resolve_token_path("-v", tmp_path) is None
    assert runner._resolve_token_path("--output", tmp_path) is None


def test_resolve_token_path_skips_globs(runner: CommandRunner, tmp_path: Path):
    assert runner._resolve_token_path("*.bam", tmp_path) is None
    assert runner._resolve_token_path("data[0]", tmp_path) is None


def test_resolve_token_path_resolves_relative(
    runner: CommandRunner, tmp_path: Path
):
    result = runner._resolve_token_path("output.txt", tmp_path)
    assert result is not None
    assert result == (tmp_path / "output.txt").resolve()


def test_resolve_token_path_resolves_absolute(
    runner: CommandRunner, tmp_path: Path
):
    result = runner._resolve_token_path("/tmp/file.txt", tmp_path)
    assert result is not None
    assert str(result).startswith("/")
