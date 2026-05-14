from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_harness.core.ollama_setup import (
    OllamaPullCancelled,
    OllamaServerStatus,
    normalize_ollama_host,
    parse_ollama_pull_event,
    pull_ollama_model,
    start_ollama_server,
    validate_ollama_model_name,
)


def test_normalize_ollama_host_defaults_and_strips_trailing_slash() -> None:
    assert normalize_ollama_host("") == "http://127.0.0.1:11434"
    assert normalize_ollama_host("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    "model_name",
    [
        "qwen3-coder-next:latest",
        "gemma4:26b",
        "qwen3.6:35b-a3b",
        "library/example-model_v2:latest",
    ],
)
def test_validate_ollama_model_name_accepts_catalog_shapes(model_name: str) -> None:
    assert validate_ollama_model_name(model_name) == model_name


@pytest.mark.parametrize(
    "model_name",
    [
        "",
        "qwen3-coder-next:latest; rm -rf ~",
        "$(touch hacked)",
        "model with spaces",
        ":bad",
    ],
)
def test_validate_ollama_model_name_rejects_shell_like_names(model_name: str) -> None:
    with pytest.raises(ValueError):
        validate_ollama_model_name(model_name)


def test_parse_ollama_pull_event_calculates_progress() -> None:
    event = parse_ollama_pull_event(
        {
            "status": "pulling manifest",
            "digest": "sha256:abc",
            "total": 100,
            "completed": 25,
        }
    )

    assert event.status == "pulling manifest"
    assert event.percent == 25.0
    assert event.done is False


def test_parse_ollama_pull_event_marks_success() -> None:
    event = parse_ollama_pull_event({"status": "success"})

    assert event.done is True
    assert event.error == ""


def test_pull_ollama_model_records_structured_progress(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.jsonl"
    callback_events: list[dict[str, object]] = []

    def fake_stream(**_kwargs: object) -> list[dict[str, object]]:
        return [
            {"status": "pulling manifest"},
            {"status": "downloading", "total": 200, "completed": 100},
            {"status": "success"},
        ]

    result = pull_ollama_model(
        model_name="qwen3-coder-next:latest",
        progress_path=progress_path,
        stream_factory=fake_stream,
        progress_callback=callback_events.append,
    )

    assert result["succeeded"] is True
    assert result["event_count"] == 3
    assert len(callback_events) == 3
    records = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["done"] is True


def test_pull_ollama_model_reports_stream_errors() -> None:
    def fake_stream(**_kwargs: object) -> list[dict[str, object]]:
        return [{"status": "error", "error": "not enough disk"}]

    result = pull_ollama_model(
        model_name="qwen3-coder-next:latest",
        stream_factory=fake_stream,
    )

    assert result["succeeded"] is False
    assert result["error"] == "not enough disk"


def test_pull_ollama_model_can_be_canceled_by_progress_callback() -> None:
    def fake_stream(**_kwargs: object) -> list[dict[str, object]]:
        return [{"status": "downloading", "total": 200, "completed": 100}]

    def cancel_callback(_event: dict[str, object]) -> None:
        raise OllamaPullCancelled("user canceled")

    result = pull_ollama_model(
        model_name="qwen3-coder-next:latest",
        stream_factory=fake_stream,
        progress_callback=cancel_callback,
    )

    assert result["succeeded"] is False
    assert result["canceled"] is True
    assert result["error"] == "user canceled"


def test_start_ollama_server_skips_when_already_running() -> None:
    result = start_ollama_server(
        status_checker=lambda **_kwargs: OllamaServerStatus(
            cli_available=True,
            host="http://127.0.0.1:11434",
            reachable=True,
        )
    )

    assert result["attempted"] is False
    assert result["succeeded"] is True
    assert result["already_running"] is True


def test_start_ollama_server_reports_missing_cli() -> None:
    result = start_ollama_server(
        status_checker=lambda **_kwargs: OllamaServerStatus(
            cli_available=False,
            host="http://127.0.0.1:11434",
            reachable=False,
            error="not running",
        )
    )

    assert result["attempted"] is False
    assert result["succeeded"] is False
    assert "CLI" in result["error"]


def test_start_ollama_server_launches_and_waits_until_reachable(tmp_path: Path) -> None:
    statuses = iter(
        [
            OllamaServerStatus(
                cli_available=True,
                host="http://127.0.0.1:11434",
                reachable=False,
                error="not running",
            ),
            OllamaServerStatus(
                cli_available=True,
                host="http://127.0.0.1:11434",
                reachable=False,
                error="warming",
            ),
            OllamaServerStatus(
                cli_available=True,
                host="http://127.0.0.1:11434",
                reachable=True,
            ),
        ]
    )
    launched_commands: list[list[str]] = []

    class FakeProcess:
        pid = 12345

    def fake_popen(command: list[str], **_kwargs: object) -> FakeProcess:
        launched_commands.append(command)
        return FakeProcess()

    result = start_ollama_server(
        log_path=tmp_path / "ollama.log",
        wait_seconds=1.0,
        poll_interval_seconds=0.01,
        popen_factory=fake_popen,
        status_checker=lambda **_kwargs: next(statuses),
        sleep_fn=lambda _seconds: None,
    )

    assert launched_commands == [["ollama", "serve"]]
    assert result["succeeded"] is True
    assert result["pid"] == 12345
