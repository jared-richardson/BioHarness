from __future__ import annotations

import queue

from bio_harness.ui.terminal_state import (
    drain_shell_log_queue,
    mark_shell_command_started,
    shell_output_text,
)


def test_mark_shell_command_started_enables_live_refresh_and_clears_output() -> None:
    state = {
        "shell_running": False,
        "shell_output": ["old"],
        "live_refresh_enabled": False,
        "live_refresh_last_tick": 14.2,
    }

    mark_shell_command_started(state)

    assert state["shell_running"] is True
    assert state["shell_output"] == []
    assert state["live_refresh_enabled"] is True
    assert state["live_refresh_last_tick"] == 0.0


def test_drain_shell_log_queue_collects_lines_and_detects_completion() -> None:
    log_queue: queue.Queue[object] = queue.Queue()
    log_queue.put("[status] starting\n")
    log_queue.put("[stdout] /tmp\n")
    log_queue.put(None)

    output, running = drain_shell_log_queue(log_queue, [])

    assert running is False
    assert output == ["[status] starting\n", "[stdout] /tmp\n"]


def test_shell_output_text_returns_placeholder_when_empty() -> None:
    assert shell_output_text([]) == "(no terminal output yet)"
    assert shell_output_text(["a", "b"]) == "ab"
