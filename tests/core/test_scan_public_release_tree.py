from __future__ import annotations

import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

from scripts.scan_public_release_tree import main, scan_public_release_tree


def test_scan_public_release_tree_passes_clean_tree(tmp_path: Path) -> None:
    """Clean public trees should produce no release-scan findings."""

    _write(tmp_path / "README.md", "# Bio-Harness\n")
    _write(tmp_path / "bio_harness" / "__init__.py", "")

    assert scan_public_release_tree(tmp_path) == []


def test_scan_public_release_tree_flags_release_blockers(tmp_path: Path) -> None:
    """Private paths, key material, and large files should block release."""

    _write(
        tmp_path / "docs" / "bad.md",
        "/Users/" + 'jared/project\nOPENAI_API_KEY="sk-not-a-real-test-token-1234567890"\n',
    )
    (tmp_path / "large.bin").write_bytes(b"x" * 12)

    findings = scan_public_release_tree(tmp_path, max_bytes=10)
    kinds = {finding.kind for finding in findings}

    assert {"large_file", "private_path", "secret_pattern"} <= kinds


def test_scan_public_release_tree_scans_archives(tmp_path: Path) -> None:
    """Built artifacts should be scanned for private text payloads too."""

    archive_path = tmp_path / "dist" / "bio_harness-0.1.0-py3-none-any.whl"
    archive_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("bio_harness/leak.txt", "Desktop/" + "bio_harness\n")

    findings = scan_public_release_tree(tmp_path)

    assert any(
        finding.kind == "private_path" and finding.path.endswith("bio_harness/leak.txt")
        for finding in findings
    )


def test_scan_public_release_tree_json_payload_is_stable(tmp_path: Path) -> None:
    """Findings should be easy to serialize in CI diagnostics."""

    _write(tmp_path / "bad.txt", "private/" + "local/path\n")
    findings = scan_public_release_tree(tmp_path)
    payload = [
        {
            "kind": finding.kind,
            "path": finding.path,
            "detail": finding.detail,
        }
        for finding in findings
    ]

    assert json.loads(json.dumps(payload))[0]["kind"] == "private_path"


def test_scan_public_release_tree_skips_archives_when_requested(tmp_path: Path) -> None:
    """Archive scanning should be opt-out for fast local diagnostics."""

    archive_path = tmp_path / "dist" / "bio_harness-0.1.0-py3-none-any.whl"
    archive_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("bio_harness/leak.txt", "Desktop/" + "bio_harness\n")

    assert scan_public_release_tree(tmp_path, include_archives=False) == []


def test_scan_public_release_tree_scans_tar_archives(tmp_path: Path) -> None:
    """Tarballs should receive the same size and text-content checks."""

    archive_path = tmp_path / "dist" / "bio-harness.tar.gz"
    archive_path.parent.mkdir(parents=True)
    payload = ("private/" + "local/path\n").encode()
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("bio_harness/leak.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    findings = scan_public_release_tree(tmp_path, max_bytes=5)
    kinds = {finding.kind for finding in findings}

    assert {"large_file", "private_path"} <= kinds


def test_scan_public_release_tree_tolerates_bad_archives(tmp_path: Path) -> None:
    """Corrupt archives should not crash the release scanner."""

    (tmp_path / "bad.zip").write_text("not a zip", encoding="utf-8")
    (tmp_path / "bad.tar.gz").write_text("not a tarball", encoding="utf-8")

    assert scan_public_release_tree(tmp_path) == []


def test_scan_public_release_tree_skips_excluded_dirs_and_missing_roots(
    tmp_path: Path,
) -> None:
    """Workspace caches and missing roots should not produce false findings."""

    _write(tmp_path / "workspace" / "bad.md", "Desktop/" + "bio_harness\n")
    _write(tmp_path / "node_modules" / "bad.md", "Desktop/" + "bio_harness\n")
    _write(tmp_path / ".pixi" / "bad.md", "Desktop/" + "bio_harness\n")
    _write(tmp_path / ".tool-envs" / "bad.md", "Desktop/" + "bio_harness\n")

    assert scan_public_release_tree(tmp_path) == []
    assert scan_public_release_tree(tmp_path / "does-not-exist") == []


def test_scan_public_release_tree_cli_outputs_json(monkeypatch, capsys, tmp_path: Path) -> None:
    """The CLI should support JSON diagnostics for automation."""

    _write(tmp_path / "bad.txt", "private/" + "local/path\n")
    monkeypatch.setattr(
        sys,
        "argv",
        ["scan_public_release_tree.py", "--root", str(tmp_path), "--json"],
    )

    assert main() == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload[0]["kind"] == "private_path"


def test_scan_public_release_tree_cli_outputs_human_status(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    """The CLI should emit useful text for clean and blocked trees."""

    monkeypatch.setattr(sys, "argv", ["scan_public_release_tree.py", "--root", str(tmp_path)])
    assert main() == 0
    assert "Release scan passed" in capsys.readouterr().out

    _write(tmp_path / "bad.txt", "private/" + "local/path\n")
    monkeypatch.setattr(sys, "argv", ["scan_public_release_tree.py", "--root", str(tmp_path)])
    assert main() == 1
    assert "Release scan found" in capsys.readouterr().out


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
