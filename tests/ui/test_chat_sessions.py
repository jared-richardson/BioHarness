from __future__ import annotations

from bio_harness.ui.chat_sessions import (
    build_chat_session_id,
    ensure_user_message_in_session,
    session_id_for_run,
)


def test_build_chat_session_id_uses_run_scope_and_timestamp() -> None:
    assert build_chat_session_id(7, epoch_ms=123456789) == "run-7-123456789"


def test_session_id_for_run_uses_stored_value_or_fallback() -> None:
    assert session_id_for_run({"chat_session_id": "run-4-999"}) == "run-4-999"
    assert session_id_for_run({}, fallback="default") == "default"


def test_ensure_user_message_in_session_appends_once() -> None:
    session = {"messages": [{"role": "assistant", "content": "ready"}]}

    assert ensure_user_message_in_session(session, "Proceed now.") is True
    assert ensure_user_message_in_session(session, "Proceed now.") is False
    assert session["messages"][-1] == {"role": "user", "content": "Proceed now."}
