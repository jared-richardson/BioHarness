from __future__ import annotations

import queue
import threading
from pathlib import Path

from bio_harness.core.recovery_policy import can_attempt_repair, classify_failure
from bio_harness.core.runner import CommandRunner


def _run_command(command: str) -> list[str]:
    log_q: queue.Queue = queue.Queue()
    runner = CommandRunner()
    thread = threading.Thread(target=runner.run_command, args=(command, log_q))
    thread.start()
    out: list[str] = []
    while True:
        item = log_q.get(timeout=5)
        if item is None:
            break
        out.append(str(item))
    thread.join(timeout=5)
    return out


def test_blocked_command_is_hard_failure_not_success():
    lines = _run_command("rm -rf /")
    combined = "".join(lines)
    assert "__POLICY_BLOCK__" in combined
    assert "[exit_code=126]" in combined
    assert "[exit_code=0]" not in combined


def test_validation_block_classification_points_to_auto_repair():
    run = {
        "error": "Step 5 blocked by validation agent. Issues: missing_input:/tmp/x",
        "validation_block_detected": True,
    }
    failure_class = classify_failure(run)
    assert failure_class == "validation_block"
    assert can_attempt_repair({}, failure_class) is True


def test_git_command_is_policy_blocked():
    lines = _run_command("git clone https://example.com/repo.git")
    combined = "".join(lines)
    assert "__POLICY_BLOCK__" in combined
    assert "runtime git commands" in combined.lower()
    assert "[exit_code=126]" in combined


def test_wrapped_git_command_is_policy_blocked():
    lines = _run_command("bash -lc \"git pull\"")
    combined = "".join(lines)
    assert "__POLICY_BLOCK__" in combined
    assert "[exit_code=126]" in combined


def test_command_runner_honors_cancel_event():
    log_q: queue.Queue = queue.Queue()
    runner = CommandRunner()
    cancel_event = threading.Event()
    thread = threading.Thread(
        target=runner.run_command,
        args=("sleep 30", log_q),
        kwargs={"cancel_event": cancel_event},
    )
    thread.start()
    cancel_event.set()
    out: list[str] = []
    while True:
        item = log_q.get(timeout=5)
        if item is None:
            break
        out.append(str(item))
    thread.join(timeout=5)
    combined = "".join(out)
    assert "__COMMAND_CANCELLED__:external_stop" in combined


def test_command_runner_allows_reads_from_inputs_readonly_with_temp_cleanup(tmp_path: Path):
    workspace = tmp_path / "workspace"
    readonly = workspace / "inputs_readonly"
    outputs = workspace / "outputs"
    readonly.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    (readonly / "mouse_fasta").write_text(">chr1\nACGT\n", encoding="utf-8")

    log_q: queue.Queue = queue.Queue()
    runner = CommandRunner()
    command = (
        "cat inputs_readonly/mouse_fasta >/dev/null; "
        "touch outputs/tmpfile; "
        "rm -f outputs/tmpfile"
    )
    thread = threading.Thread(
        target=runner.run_command,
        args=(command, log_q),
        kwargs={"cwd": str(workspace), "allowed_root": str(workspace)},
    )
    thread.start()
    out: list[str] = []
    while True:
        item = log_q.get(timeout=5)
        if item is None:
            break
        out.append(str(item))
    thread.join(timeout=5)

    combined = "".join(out)
    assert "__POLICY_BLOCK__" not in combined
    assert "[exit_code=0]" in combined


def test_command_runner_blocks_writes_under_inputs_readonly(tmp_path: Path):
    workspace = tmp_path / "workspace"
    readonly = workspace / "inputs_readonly"
    readonly.mkdir(parents=True, exist_ok=True)

    log_q: queue.Queue = queue.Queue()
    runner = CommandRunner()
    thread = threading.Thread(
        target=runner.run_command,
        args=("touch inputs_readonly/forbidden.txt", log_q),
        kwargs={"cwd": str(workspace), "allowed_root": str(workspace)},
    )
    thread.start()
    out: list[str] = []
    while True:
        item = log_q.get(timeout=5)
        if item is None:
            break
        out.append(str(item))
    thread.join(timeout=5)

    combined = "".join(out)
    assert "__POLICY_BLOCK__" in combined
    assert "read-only root" in combined.lower()


def test_command_runner_propagates_left_side_pipeline_failure():
    lines = _run_command("false | cat")
    combined = "".join(lines)

    assert "[exit_code=0]" not in combined
    assert "[exit_code=1]" in combined


def test_command_runner_preserves_intentional_pipeline_recovery():
    lines = _run_command("false | cat || true")
    combined = "".join(lines)

    assert "[exit_code=0]" in combined
