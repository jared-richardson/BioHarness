"""Helpers for building compact execution-planning request context.

The chat UI needs enough prior user intent to execute follow-up prompts like
"Proceed with execution now" without feeding the planner a verbose transcript
that can distort contract inference. These helpers keep the context limited to
recent user instructions and deliberately exclude assistant-generated status
notes.
"""

from __future__ import annotations

from typing import Any, Mapping


_EXECUTION_PREAMBLE_PREFIXES: tuple[str, ...] = (
    "proceed with execution now.",
    "proceed with execution now",
    "proceed with execution.",
    "proceed with execution",
)


def strip_execution_preamble(text: str) -> str:
    """Return *text* without a generic execution preamble when safe.

    Args:
        text: Raw user-facing execution instruction.

    Returns:
        The instruction text with a leading execution preamble removed when the
        remainder is non-empty, otherwise the stripped original text.
    """

    stripped = str(text or "").strip()
    lowered = stripped.lower()
    for prefix in _EXECUTION_PREAMBLE_PREFIXES:
        if not lowered.startswith(prefix):
            continue
        remainder = stripped[len(prefix):].strip()
        if remainder:
            return remainder
    return stripped


def build_execution_request_context(
    snapshot: Mapping[str, Any] | None,
    latest_user_text: str,
    *,
    max_prior_user_messages: int = 3,
) -> str:
    """Build a compact execution-planning request context.

    Args:
        snapshot: Session snapshot containing recent chat messages.
        latest_user_text: The newest user instruction that triggered execution.
        max_prior_user_messages: Maximum number of prior user messages to keep.

    Returns:
        A compact request string that contains only recent user instructions.
        Assistant messages and execution-status notes are intentionally
        excluded.
    """

    latest = strip_execution_preamble(latest_user_text)
    messages = snapshot.get("messages", []) if isinstance(snapshot, Mapping) else []
    prior_user_messages: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        if str(message.get("role", "")).strip().lower() != "user":
            continue
        content = strip_execution_preamble(str(message.get("content", "")).strip())
        if not content:
            continue
        prior_user_messages.append(content)

    if latest:
        prior_user_messages.append(latest)

    deduped: list[str] = []
    seen: set[str] = set()
    for content in prior_user_messages:
        key = content.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(content)

    if not deduped:
        return latest
    if len(deduped) == 1:
        return deduped[0]

    latest_instruction = deduped[-1]
    prior = deduped[:-1][-max_prior_user_messages:]
    if not prior:
        return latest_instruction
    prior_lines = "\n".join(f"- {item}" for item in prior)
    return (
        "Recent user instructions:\n"
        f"{prior_lines}\n\n"
        "Latest user instruction:\n"
        f"{latest_instruction}"
    ).strip()
