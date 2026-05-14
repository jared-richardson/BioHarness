from __future__ import annotations

from typing import Any

from bio_harness.core.llm import BioLLM
from bio_harness.core.llm_backends import (
    OllamaChatBackend,
    OpenAICompatibleChatBackend,
    is_loopback_permission_error,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return dict(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeHttpxClient:
    def __init__(self, *args, **kwargs):
        self.base_url = kwargs.get("base_url", "")
        self.timeout = kwargs.get("timeout", 0)
        self.headers = dict(kwargs.get("headers", {}))
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(self, method: str, path: str, json: dict[str, Any] | None = None):
        self.requests.append((method.upper(), path, dict(json or {}) if json is not None else None))
        if path.endswith("/models"):
            return _FakeResponse({"data": [{"id": "test-model"}, {"id": "other-model"}]})
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "{\"ok\":true}",
                        }
                    }
                ]
            }
        )


def test_openai_compatible_backend_posts_chat_completions(monkeypatch):
    client_holder: dict[str, _FakeHttpxClient] = {}

    def _fake_client_factory(*args, **kwargs):
        client = _FakeHttpxClient(*args, **kwargs)
        client_holder["client"] = client
        return client

    monkeypatch.setattr("bio_harness.core.llm_backends.httpx.Client", _fake_client_factory)

    backend = OpenAICompatibleChatBackend(
        host="http://127.0.0.1:8000/v1",
        timeout_seconds=30.0,
        api_key="secret-token",
    )
    content = backend.chat(
        model_name="test-model",
        messages=[{"role": "system", "content": "json"}, {"role": "user", "content": "hello"}],
        temperature=0.0,
        num_ctx=4096,
        num_predict=256,
        format_spec={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )

    assert content == "{\"ok\":true}"
    fake_client = client_holder["client"]
    method, path, payload = fake_client.requests[0]
    assert method == "POST"
    assert path == "/chat/completions"
    assert payload is not None
    assert payload["model"] == "test-model"
    assert payload["max_tokens"] == 256
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["extra_body"]["num_ctx"] == 4096
    assert fake_client.headers["Authorization"] == "Bearer secret-token"


def test_openai_compatible_backend_lists_models(monkeypatch):
    client_holder: dict[str, _FakeHttpxClient] = {}

    def _fake_client_factory(*args, **kwargs):
        client = _FakeHttpxClient(*args, **kwargs)
        client_holder["client"] = client
        return client

    monkeypatch.setattr("bio_harness.core.llm_backends.httpx.Client", _fake_client_factory)

    backend = OpenAICompatibleChatBackend(
        host="http://127.0.0.1:8000/v1",
        timeout_seconds=30.0,
        api_key="",
    )

    fake_client = client_holder["client"]
    assert backend.list_models() == ["test-model", "other-model"]
    assert fake_client.requests == [("GET", "/models", None)]


def test_openai_compatible_backend_tries_v1_prefix_when_host_has_no_suffix(monkeypatch):
    client_holder: dict[str, _FakeHttpxClient] = {}

    def _fake_client_factory(*args, **kwargs):
        client = _FakeHttpxClient(*args, **kwargs)
        client_holder["client"] = client
        return client

    monkeypatch.setattr("bio_harness.core.llm_backends.httpx.Client", _fake_client_factory)

    backend = OpenAICompatibleChatBackend(
        host="http://127.0.0.1:8080",
        timeout_seconds=30.0,
        api_key="",
        provider_name="mlx",
    )

    assert backend.list_models() == ["test-model", "other-model"]
    fake_client = client_holder["client"]
    assert fake_client.requests == [("GET", "/v1/models", None)]
    assert backend.diagnostics()["path_prefixes"] == ["/v1", ""]


def test_ollama_backend_handles_typed_client_responses(monkeypatch):
    class _TypedMessage:
        content = "{\"ok\":true}"

    class _TypedChatResponse:
        message = _TypedMessage()

        def model_dump(self):
            return {"message": {"content": self.message.content}}

    class _TypedModel:
        def __init__(self, model: str):
            self.model = model

    class _TypedListResponse:
        def __init__(self):
            self.models = [_TypedModel("qwen3-coder-next:latest")]

        def model_dump(self):
            return {"models": [{"model": "qwen3-coder-next:latest"}]}

    class _FakeOllamaClient:
        def __init__(self, *args, **kwargs):
            pass

        def chat(self, **kwargs):
            return _TypedChatResponse()

        def list(self):
            return _TypedListResponse()

    monkeypatch.setattr("bio_harness.core.llm_backends.ollama.Client", _FakeOllamaClient)

    backend = OllamaChatBackend(host="http://127.0.0.1:11434", timeout_seconds=30.0)

    assert backend.list_models() == ["qwen3-coder-next:latest"]
    assert backend.chat(
        model_name="qwen3-coder-next",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
    ) == "{\"ok\":true}"


def test_biollm_can_use_openai_compatible_backend(monkeypatch):
    class _FakeBackend:
        backend_name = "openai_compatible"
        host = "http://127.0.0.1:8000/v1"
        timeout_seconds = 30.0

        def chat(self, **kwargs):
            return "adapter-ok"

        def list_models(self):
            return ["test-model"]

        def is_connectivity_error(self, exc: Exception) -> bool:
            return False

        def prewarm(self, *, model_name: str, mode: str, timeout_seconds: float):
            return True, "ok"

        def diagnostics(self):
            return {"direct_probe_ok": True, "backend": "openai_compatible"}

    monkeypatch.setattr("bio_harness.core.llm.build_chat_backend", lambda **_kwargs: _FakeBackend())

    llm = BioLLM(
        model_name="test-model",
        host="http://127.0.0.1:8000/v1",
        llm_backend="openai_compatible",
    )

    ok, msg = llm.is_available()
    assert ok is True
    assert "OpenAI-compatible backend" in msg
    assert llm.generate_text("sys", "user") == "adapter-ok"


def test_biollm_accepts_mlx_alias(monkeypatch):
    class _FakeBackend:
        backend_name = "mlx"
        host = "http://127.0.0.1:8080"
        timeout_seconds = 30.0
        transport_name = "openai_compatible"

        def chat(self, **kwargs):
            return "adapter-ok"

        def list_models(self):
            return ["mlx-test-model"]

        def is_connectivity_error(self, exc: Exception) -> bool:
            return False

        def prewarm(self, *, model_name: str, mode: str, timeout_seconds: float):
            return True, "ok"

        def diagnostics(self):
            return {"direct_probe_ok": True, "backend": "mlx", "transport_name": "openai_compatible"}

    monkeypatch.setattr("bio_harness.core.llm.build_chat_backend", lambda **_kwargs: _FakeBackend())

    llm = BioLLM(
        model_name="mlx-test-model",
        host="http://127.0.0.1:8080",
        llm_backend="mlx",
    )

    ok, msg = llm.is_available()
    assert ok is True
    assert "MLX OpenAI-compatible server" in msg
    assert llm.backend_name == "mlx"
    assert llm.transport_name == "openai_compatible"


def test_loopback_permission_error_helper_detects_local_permission_denial():
    exc = PermissionError(1, "Operation not permitted")
    assert is_loopback_permission_error("http://127.0.0.1:8080", exc) is True
    assert is_loopback_permission_error("http://example.com:8080", exc) is False


def test_biollm_reports_loopback_blocked_for_local_model_server(monkeypatch):
    class _BlockedBackend:
        backend_name = "mlx"
        host = "http://127.0.0.1:8080"
        timeout_seconds = 30.0
        transport_name = "openai_compatible"

        def chat(self, **kwargs):
            raise PermissionError(1, "Operation not permitted")

        def list_models(self):
            raise PermissionError(1, "Operation not permitted")

        def is_connectivity_error(self, exc: Exception) -> bool:
            return True

        def prewarm(self, *, model_name: str, mode: str, timeout_seconds: float):
            return False, "blocked"

        def diagnostics(self):
            return {"direct_probe_ok": False, "direct_probe_error": "Operation not permitted"}

    monkeypatch.setattr("bio_harness.core.llm.build_chat_backend", lambda **_kwargs: _BlockedBackend())

    llm = BioLLM(
        model_name="mlx-test-model",
        host="http://127.0.0.1:8080",
        llm_backend="mlx",
    )

    ok, msg = llm.is_available()
    assert ok is False
    assert "loopback access" in msg.lower()
