from __future__ import annotations

import io
import json
from pathlib import Path

from bio_harness.core.trusted_downloads import download_with_policy, write_download_receipt


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._buffer = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_trusted_download_allows_github_in_trusted_only_mode(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "bio_harness.core.trusted_downloads.urllib.request.urlopen",
        lambda url, timeout=60.0: _FakeResponse(b"payload"),
    )

    receipt = download_with_policy(
        "https://github.com/example/repo/releases/download/v1/tool.tar.gz",
        destination=tmp_path / "tool.tar.gz",
        mode="trusted_only",
    )

    assert receipt["status"] == "downloaded"
    assert receipt["host"] == "github.com"
    assert (tmp_path / "tool.tar.gz").read_bytes() == b"payload"


def test_trusted_download_blocks_untrusted_host(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "bio_harness.core.trusted_downloads.urllib.request.urlopen",
        lambda url, timeout=60.0: _FakeResponse(b"payload"),
    )

    try:
        download_with_policy(
            "https://example.com/file.txt",
            destination=tmp_path / "file.txt",
            mode="trusted_only",
        )
    except PermissionError as exc:
        assert "blocked host 'example.com'" in str(exc)
    else:  # pragma: no cover - defensive failure path
        raise AssertionError("Expected trusted-only download policy to block example.com.")


def test_trusted_download_writes_receipt(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "bio_harness.core.trusted_downloads.urllib.request.urlopen",
        lambda url, timeout=60.0: _FakeResponse(b"paper"),
    )
    receipt = download_with_policy(
        "https://pubmed.ncbi.nlm.nih.gov/paper.pdf",
        destination=tmp_path / "paper.pdf",
        mode="audit",
    )
    receipt_path = write_download_receipt(receipt, tmp_path / "receipt.json")
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["host"] == "pubmed.ncbi.nlm.nih.gov"
