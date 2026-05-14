from __future__ import annotations

from types import SimpleNamespace

from bio_harness.agents.orchestrator import Orchestrator


def _orchestrator_stub() -> Orchestrator:
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._sessions = {}
    orchestrator._context_limit_tokens = 100
    orchestrator._compact_ratio = 0.5
    orchestrator.biollm = SimpleNamespace(summarize_text=lambda text, instruction: "summary")
    return orchestrator


def test_get_or_create_session_initializes_expected_shape():
    orchestrator = _orchestrator_stub()

    session = orchestrator.get_or_create_session("abc")

    assert session["session_id"] == "abc"
    assert session["messages"] == []
    assert session["compact_memory"] == ""
    assert session["compactions"] == 0
    assert session["last_context"] == {}


def test_session_token_load_counts_compact_memory_and_messages():
    orchestrator = _orchestrator_stub()
    session = orchestrator.get_or_create_session("abc")
    session["compact_memory"] = "abcd"
    session["messages"] = [
        {"role": "user", "content": "1234"},
        {"role": "assistant", "content": "5678"},
    ]

    token_load = orchestrator._session_token_load(session)

    assert token_load == 3


def test_compact_session_if_needed_summarizes_and_keeps_recent_messages():
    orchestrator = _orchestrator_stub()
    session = orchestrator.get_or_create_session("abc")
    session["messages"] = [
        {"role": "user", "content": "a" * 80},
        {"role": "assistant", "content": "b" * 80},
        {"role": "user", "content": "c" * 80},
        {"role": "assistant", "content": "d" * 80},
    ]

    orchestrator._compact_session_if_needed(session)

    assert session["compact_memory"] == "summary"
    assert len(session["messages"]) == 2
    assert session["messages"][0]["content"] == "c" * 80
    assert session["compactions"] == 1


def test_compact_session_if_needed_falls_back_when_summary_fails():
    orchestrator = _orchestrator_stub()
    orchestrator.biollm = SimpleNamespace(
        summarize_text=lambda text, instruction: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    session = orchestrator.get_or_create_session("abc")
    session["messages"] = [
        {"role": "user", "content": "a" * 80},
        {"role": "assistant", "content": "b" * 80},
        {"role": "user", "content": "c" * 80},
        {"role": "assistant", "content": "d" * 80},
    ]

    orchestrator._compact_session_if_needed(session)

    assert session["compact_memory"].startswith("user: ")
    assert session["messages"][0]["content"] == "c" * 80
    assert session["compactions"] == 1
