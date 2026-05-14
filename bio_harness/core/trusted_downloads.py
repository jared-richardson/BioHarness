"""Trusted-download helpers for user-approved reference retrieval."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from bio_harness.core.execution_policy import execution_policy_mode, host_allowed, trusted_download_hosts


def download_with_policy(
    url: str,
    *,
    destination: str | Path,
    mode: str | None = None,
    allowed_hosts: Sequence[str] | None = None,
    max_bytes: int | None = None,
    expected_sha256: str = "",
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Download a URL into the workspace under an audit-first policy.

    Args:
        url: Source URL.
        destination: Output path for the fetched file.
        mode: ``off``, ``audit``, or ``trusted_only``.
        allowed_hosts: Optional trusted host override.
        max_bytes: Optional byte cap.
        expected_sha256: Optional SHA256 checksum to enforce.
        timeout_seconds: Request timeout.

    Returns:
        A deterministic receipt dictionary.
    """

    normalized_mode = execution_policy_mode(mode)
    host = (urlparse(url).hostname or "").lower()
    allowlist = tuple(allowed_hosts or trusted_download_hosts())
    receipt: dict[str, Any] = {
        "url": str(url),
        "host": host,
        "mode": normalized_mode,
        "allowed_hosts": list(allowlist),
        "destination": str(Path(destination).expanduser().resolve()),
    }
    if normalized_mode == "trusted_only" and not host_allowed(host, allowlist):
        raise PermissionError(f"Trusted download policy blocked host '{host or 'unknown_host'}'.")

    dest = Path(destination).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    hasher = hashlib.sha256()
    written = 0

    with urllib.request.urlopen(url, timeout=timeout_seconds) as response, tmp.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 128)
            if not chunk:
                break
            written += len(chunk)
            if max_bytes is not None and written > max_bytes:
                tmp.unlink(missing_ok=True)
                raise ValueError(f"Trusted download exceeded max_bytes ({written} > {max_bytes}).")
            hasher.update(chunk)
            handle.write(chunk)

    digest = hasher.hexdigest()
    if expected_sha256 and digest.lower() != str(expected_sha256).strip().lower():
        tmp.unlink(missing_ok=True)
        raise ValueError("Trusted download checksum mismatch.")
    tmp.replace(dest)
    receipt["bytes_written"] = written
    receipt["sha256"] = digest
    receipt["status"] = "downloaded"
    return receipt


def write_download_receipt(receipt: dict[str, Any], output_path: str | Path) -> Path:
    """Persist a trusted-download receipt to JSON."""

    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return out
