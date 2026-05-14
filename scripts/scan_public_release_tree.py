#!/usr/bin/env python3
"""Scan a staged BioHarness public-release tree for release blockers."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_PRIVATE_PATTERNS = (
    "jared" + ".richardson",
    "Desktop/" + "bio_harness",
    "/Users/" + "jared",
    "private/" + "local/path",
)
EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pixi",
        ".pytest_cache",
        ".ruff_cache",
        ".tool-envs",
        ".tool-envs-docker",
        ".tool-envs-generic",
        ".venv",
        ".vite",
        "__pycache__",
        "node_modules",
        "workspace",
        "runs",
    }
)
TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".html",
        ".js",
        ".json",
        ".lock",
        ".md",
        ".py",
        ".R",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yml",
        ".yaml",
    }
)
SECRET_PATTERNS = (
    re.compile(r"BEGIN (?:RSA|OPENSSH|PRIVATE) KEY"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(
        r"\b(?:BIO_HARNESS_OPENAI_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*['\"][^'\"]+['\"]"
    ),
)


@dataclass(frozen=True)
class ReleaseScanFinding:
    """One release-scan finding.

    Attributes:
        kind: Finding category.
        path: Public-tree-relative path, optionally with archive member suffix.
        detail: Human-readable finding detail.
    """

    kind: str
    path: str
    detail: str


def scan_public_release_tree(
    root: Path | str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    private_patterns: Iterable[str] = DEFAULT_PRIVATE_PATTERNS,
    include_archives: bool = True,
) -> list[ReleaseScanFinding]:
    """Scan one public-release tree for large files, private paths, and keys.

    Args:
        root: Public release tree root.
        max_bytes: Maximum allowed size for one file or archive member.
        private_patterns: Literal strings that must not appear in staged text.
        include_archives: Whether to scan supported archives recursively.

    Returns:
        Findings sorted by path and kind.
    """
    root_path = Path(root).expanduser().resolve(strict=False)
    findings: list[ReleaseScanFinding] = []
    for path in _iter_public_files(root_path):
        relative = path.relative_to(root_path).as_posix()
        size = path.stat().st_size
        if size > max_bytes:
            findings.append(
                ReleaseScanFinding(
                    kind="large_file",
                    path=relative,
                    detail=f"{size} bytes exceeds {max_bytes} bytes",
                )
            )
        text = _read_text_if_reasonable(path)
        if text is not None:
            findings.extend(
                _scan_text(
                    path=relative,
                    text=text,
                    private_patterns=private_patterns,
                )
            )
        if include_archives:
            findings.extend(
                _scan_archive(
                    path=path,
                    relative=relative,
                    max_bytes=max_bytes,
                    private_patterns=private_patterns,
                )
            )
    return sorted(findings, key=lambda item: (item.path, item.kind, item.detail))


def main() -> int:
    """Run the public-release scan CLI."""
    args = _build_parser().parse_args()
    findings = scan_public_release_tree(
        args.root,
        max_bytes=args.max_bytes,
        include_archives=not args.no_archives,
    )
    payload = [asdict(finding) for finding in findings]
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif findings:
        print(f"Release scan found {len(findings)} finding(s):")
        for finding in findings:
            print(f"- {finding.kind}: {finding.path} ({finding.detail})")
    else:
        print("Release scan passed: no findings.")
    return 1 if findings else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-archives", action="store_true")
    return parser


def _iter_public_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts):
            continue
        yield path


def _read_text_if_reasonable(path: Path) -> str | None:
    if path.suffix not in TEXT_SUFFIXES and path.stat().st_size > DEFAULT_MAX_BYTES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _scan_text(
    *,
    path: str,
    text: str,
    private_patterns: Iterable[str],
) -> list[ReleaseScanFinding]:
    findings: list[ReleaseScanFinding] = []
    for pattern in private_patterns:
        if pattern and pattern in text:
            findings.append(
                ReleaseScanFinding(
                    kind="private_path",
                    path=path,
                    detail=f"contains {pattern!r}",
                )
            )
    for secret_pattern in SECRET_PATTERNS:
        if secret_pattern.search(text):
            findings.append(
                ReleaseScanFinding(
                    kind="secret_pattern",
                    path=path,
                    detail=f"matched {secret_pattern.pattern!r}",
                )
            )
    return findings


def _scan_archive(
    *,
    path: Path,
    relative: str,
    max_bytes: int,
    private_patterns: Iterable[str],
) -> list[ReleaseScanFinding]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        return _scan_zip_archive(
            path=path,
            relative=relative,
            max_bytes=max_bytes,
            private_patterns=private_patterns,
        )
    if path.name.endswith(".tar.gz") or path.name.endswith(".tgz"):
        return _scan_tar_archive(
            path=path,
            relative=relative,
            max_bytes=max_bytes,
            private_patterns=private_patterns,
        )
    return []


def _scan_zip_archive(
    *,
    path: Path,
    relative: str,
    max_bytes: int,
    private_patterns: Iterable[str],
) -> list[ReleaseScanFinding]:
    findings: list[ReleaseScanFinding] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                member_path = f"{relative}!{info.filename}"
                if info.file_size > max_bytes:
                    findings.append(
                        ReleaseScanFinding(
                            kind="large_file",
                            path=member_path,
                            detail=f"{info.file_size} bytes exceeds {max_bytes} bytes",
                        )
                    )
                try:
                    text = archive.read(info.filename).decode("utf-8")
                except (KeyError, UnicodeDecodeError):
                    continue
                findings.extend(
                    _scan_text(
                        path=member_path,
                        text=text,
                        private_patterns=private_patterns,
                    )
                )
    except zipfile.BadZipFile:
        return findings
    return findings


def _scan_tar_archive(
    *,
    path: Path,
    relative: str,
    max_bytes: int,
    private_patterns: Iterable[str],
) -> list[ReleaseScanFinding]:
    findings: list[ReleaseScanFinding] = []
    try:
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                member_path = f"{relative}!{member.name}"
                if member.size > max_bytes:
                    findings.append(
                        ReleaseScanFinding(
                            kind="large_file",
                            path=member_path,
                            detail=f"{member.size} bytes exceeds {max_bytes} bytes",
                        )
                    )
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                try:
                    text = handle.read().decode("utf-8")
                except UnicodeDecodeError:
                    continue
                findings.extend(
                    _scan_text(
                        path=member_path,
                        text=text,
                        private_patterns=private_patterns,
                    )
                )
    except tarfile.TarError:
        return findings
    return findings


if __name__ == "__main__":
    raise SystemExit(main())
