from __future__ import annotations

import json
import os
import sys
from typing import Any

import ollama


def _main() -> int:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw or "{}")
    except Exception:
        req = {}
    model = str(req.get("model", "") or "").strip()
    host = str(req.get("host", "") or "").strip()
    try:
        timeout_seconds = float(req.get("timeout_seconds", 90.0))
    except Exception:
        timeout_seconds = 90.0
    timeout_seconds = max(1.0, min(1800.0, timeout_seconds))
    messages = req.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    try:
        num_ctx = int(req.get("num_ctx", 8192))
    except Exception:
        num_ctx = 8192
    try:
        num_predict = int(req.get("num_predict", 1600))
    except Exception:
        num_predict = 1600
    format_spec = req.get("format", "json")

    try:
        kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        client = ollama.Client(host=host, **kwargs) if host else ollama.Client(**kwargs)
        think_enabled = str(os.getenv("BIO_HARNESS_LLM_THINK", "false") or "false").strip().lower()
        think_flag: bool | None = None if think_enabled in {"1", "true", "yes", "on"} else False
        response = client.chat(
            model=model,
            messages=messages,
            format=format_spec,
            think=think_flag,
            options=ollama.Options(
                temperature=0.0,
                num_ctx=max(256, min(32768, num_ctx)),
                num_predict=max(16, min(4096, num_predict)),
            ),
        )
        content = str(((response or {}).get("message") or {}).get("content") or "")
        print(json.dumps({"ok": True, "content": content}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
