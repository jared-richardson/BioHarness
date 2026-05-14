"""Helpers for chat-session identity in the Streamlit UI."""

from __future__ import annotations

import time
from typing import Any, Mapping, MutableMapping


def build_chat_session_id(run_id: int, *, epoch_ms: int | None = None) -> str:
    """Build a stable chat session id for one run.

    Args:
        run_id: Integer run identifier.
        epoch_ms: Optional epoch-millisecond override for tests.

    Returns:
        A run-scoped chat session identifier.
    """
    if epoch_ms is None:
        epoch_ms = int(time.time() * 1000)
    return f"run-{int(run_id)}-{int(epoch_ms)}"


def session_id_for_run(run: Mapping[str, Any], *, fallback: str = "default") -> str:
    """Return the chat session id associated with a run mapping.

    Args:
        run: Run metadata mapping.
        fallback: Fallback session id when the run has no explicit binding.

    Returns:
        The stored run session id, or the fallback value.
    """
    session_id = str(run.get("chat_session_id", "")).strip()
    return session_id or fallback


def ensure_user_message_in_session(
    session: MutableMapping[str, Any],
    user_text: str,
) -> bool:
    """Append a user turn to one session only when it is not already present.

    Args:
        session: Mutable orchestrator session mapping.
        user_text: Raw user message content.

    Returns:
        ``True`` when the message was appended, otherwise ``False``.
    """
    normalized = str(user_text).strip()
    if not normalized:
        return False
    messages = session.setdefault("messages", [])
    if messages:
        last = messages[-1]
        if (
            str(last.get("role", "")).strip().lower() == "user"
            and str(last.get("content", "")).strip() == normalized
        ):
            return False
    messages.append({"role": "user", "content": normalized})
    return True
