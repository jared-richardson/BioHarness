"""Session and context-compaction helpers for the orchestrator."""
from __future__ import annotations

from typing import Any, Callable


def new_session(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "messages": [],
        "compact_memory": "",
        "compactions": 0,
        "last_context": {},
    }


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def session_token_load(session: dict[str, Any]) -> int:
    parts = [str(session.get("compact_memory", ""))]
    for message in session.get("messages", []):
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
    return estimate_tokens("\n".join(parts))


def compact_session_if_needed(
    session: dict[str, Any],
    *,
    context_limit_tokens: int,
    compact_ratio: float,
    summarize_text: Callable[[str, str], str],
) -> None:
    token_load = session_token_load(session)
    threshold = int(context_limit_tokens * compact_ratio)
    if token_load <= threshold:
        return
    messages = session.get("messages", [])
    if len(messages) < 4:
        return
    split_idx = max(2, len(messages) // 2)
    to_compact = messages[:split_idx]
    keep = messages[split_idx:]

    compact_text = "\n".join([f"{msg.get('role', 'user')}: {msg.get('content', '')}" for msg in to_compact])
    summary_instruction = (
        "Compress this conversation memory for a bioinformatics orchestrator. "
        "Keep concrete facts: requested analyses, selected files/samples, constraints, "
        "decisions, unresolved questions, and approved actions."
    )
    try:
        summary = summarize_text(compact_text, summary_instruction)
    except Exception:
        summary = compact_text[:5000]

    previous = str(session.get("compact_memory", "") or "")
    session["compact_memory"] = (previous + "\n" + summary).strip() if previous else summary.strip()
    session["messages"] = keep
    session["compactions"] = int(session.get("compactions", 0) or 0) + 1
