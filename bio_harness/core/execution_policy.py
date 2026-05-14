"""Audit-first execution-policy helpers for runtime shell commands."""

from __future__ import annotations

import os
import re
from typing import Any, Sequence
from urllib.parse import urlparse


DEFAULT_EXECUTION_POLICY_MODE = "audit"
DEFAULT_TRUSTED_DOWNLOAD_HOSTS = (
    "github.com",
    "raw.githubusercontent.com",
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "ftp.ncbi.nlm.nih.gov",
    "ebi.ac.uk",
    "ensembl.org",
    "encodeproject.org",
)

_URL_RE = re.compile(r"https?://[^\s'\"`<>]+", flags=re.IGNORECASE)
_DOWNLOAD_TOKEN_RE = re.compile(r"(?<![\w.-])(curl|wget)(?=\s|$)", flags=re.IGNORECASE)
_INSTALL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w.-])pip3?\s+install\b", flags=re.IGNORECASE), "pip_install"),
    (re.compile(r"(?<![\w.-])conda\s+install\b", flags=re.IGNORECASE), "conda_install"),
    (re.compile(r"(?<![\w.-])mamba\s+install\b", flags=re.IGNORECASE), "mamba_install"),
    (re.compile(r"(?<![\w.-])apt-get\s+install\b", flags=re.IGNORECASE), "apt_get_install"),
    (re.compile(r"(?<![\w.-])apt\s+install\b", flags=re.IGNORECASE), "apt_install"),
    (re.compile(r"(?<![\w.-])brew\s+install\b", flags=re.IGNORECASE), "brew_install"),
)


def execution_policy_mode(raw: str | None = None) -> str:
    """Normalize the configured execution-policy mode.

    Args:
        raw: Optional explicit mode.

    Returns:
        One of ``off``, ``audit``, or ``trusted_only``.
    """

    value = str(
        raw
        if raw is not None
        else os.getenv("BIO_HARNESS_EXECUTION_POLICY", DEFAULT_EXECUTION_POLICY_MODE)
    ).strip().lower()
    if value not in {"off", "audit", "trusted_only"}:
        return DEFAULT_EXECUTION_POLICY_MODE
    return value


def trusted_download_hosts(extra_hosts: Sequence[str] | None = None) -> tuple[str, ...]:
    """Return the configured trusted download host allowlist."""

    configured = str(os.getenv("BIO_HARNESS_TRUSTED_DOWNLOAD_HOSTS", "") or "").strip()
    merged = list(DEFAULT_TRUSTED_DOWNLOAD_HOSTS)
    if configured:
        merged.extend(part.strip().lower() for part in configured.split(",") if part.strip())
    if extra_hosts:
        merged.extend(str(host).strip().lower() for host in extra_hosts if str(host).strip())
    seen: list[str] = []
    for host in merged:
        if host and host not in seen:
            seen.append(host)
    return tuple(seen)


def host_allowed(host: str, allowed_hosts: Sequence[str]) -> bool:
    """Return whether a host is inside the trusted allowlist."""

    normalized = str(host or "").strip().lower()
    if not normalized:
        return False
    for candidate in allowed_hosts:
        token = str(candidate or "").strip().lower()
        if not token:
            continue
        if normalized == token or normalized.endswith(f".{token}"):
            return True
    return False


def inspect_execution_command(
    command: str,
    *,
    mode: str | None = None,
    allowed_hosts: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Inspect a shell command for network fetch or runtime install behavior.

    Args:
        command: Raw shell command string.
        mode: Optional policy mode override.
        allowed_hosts: Optional trusted host allowlist override.

    Returns:
        A dictionary containing normalized mode, detected events, audit notes,
        and blocking notes.
    """

    normalized_mode = execution_policy_mode(mode)
    trust_hosts = tuple(allowed_hosts or trusted_download_hosts())
    urls = [match.group(0).rstrip("),.;") for match in _URL_RE.finditer(str(command or ""))]
    unique_urls: list[str] = []
    for url in urls:
        if url not in unique_urls:
            unique_urls.append(url)

    events: list[dict[str, str]] = []
    for matcher, label in _INSTALL_PATTERNS:
        if matcher.search(command):
            events.append({"kind": "install", "label": label})

    if _DOWNLOAD_TOKEN_RE.search(command):
        if unique_urls:
            for url in unique_urls:
                host = (urlparse(url).hostname or "").lower()
                events.append({"kind": "download", "label": "download", "url": url, "host": host})
        else:
            events.append({"kind": "download", "label": "download", "host": "", "url": ""})

    audits: list[str] = []
    blocking: list[str] = []
    if normalized_mode == "audit":
        for event in events:
            if event["kind"] == "install":
                audits.append(f"execution_policy_audit:runtime_install:{event['label']}")
            else:
                host = event.get("host", "") or "unknown_host"
                audits.append(f"execution_policy_audit:runtime_download:{host}")
    elif normalized_mode == "trusted_only":
        for event in events:
            if event["kind"] == "install":
                blocking.append(f"execution_policy_block:runtime_install:{event['label']}")
                continue
            host = event.get("host", "") or "unknown_host"
            if not host_allowed(host, trust_hosts):
                blocking.append(f"execution_policy_block:runtime_download_untrusted_host:{host}")

    return {
        "mode": normalized_mode,
        "trusted_hosts": list(trust_hosts),
        "events": events,
        "audits": audits,
        "blocking": blocking,
    }
