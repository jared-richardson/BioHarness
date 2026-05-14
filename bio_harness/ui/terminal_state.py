"""Helpers for shell-terminal state in the Streamlit UI.

These helpers keep queue-draining and session-state updates deterministic and
easy to test outside the main Streamlit layout code.
"""

from __future__ import annotations

import queue
from typing import Any, MutableMapping, Sequence


def mark_shell_command_started(state: MutableMapping[str, Any]) -> None:
    """Update session-style state when a shell command begins.

    Args:
        state: Mutable session-style state mapping.
    """
    state["shell_running"] = True
    state["shell_output"] = []
    state["live_refresh_enabled"] = True
    state["live_refresh_last_tick"] = 0.0


def drain_shell_log_queue(
    log_queue: queue.Queue[Any],
    shell_output: list[str],
) -> tuple[list[str], bool]:
    """Drain queued shell output into a list of rendered lines.

    Args:
        log_queue: Queue populated by the background command runner.
        shell_output: Existing accumulated output lines.

    Returns:
        A tuple containing the updated output lines and whether the shell is
        still running after the drain completes.
    """
    running = True
    while not log_queue.empty():
        line = log_queue.get()
        if line is None:
            running = False
            break
        shell_output.append(str(line))
    return shell_output, running


def shell_output_text(shell_output: Sequence[str]) -> str:
    """Return terminal output text or the default empty-state message.

    Args:
        shell_output: Collected shell output lines.

    Returns:
        Joined output text or the standard placeholder message.
    """
    rendered = "".join(str(line) for line in shell_output)
    return rendered or "(no terminal output yet)"
