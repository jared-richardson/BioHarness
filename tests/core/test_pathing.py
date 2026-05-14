"""Tests for bio_harness.core.pathing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bio_harness.core.pathing import (
    _in_any_root,
    canonical_resolve,
    discover_fastq_files_guarded,
    discover_read_roots,
    has_repeated_workspace_segments,
    resolve_with_rejections,
)


# ---------------------------------------------------------------------------
# has_repeated_workspace_segments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path_str, expected",
    [
        ("/home/workspace/inputs_readonly/data.fq", False),
        (
            "/home/workspace/inputs_readonly/sub/workspace/inputs_readonly/x",
            True,
        ),
        ("/home/other/inputs_readonly/workspace/data", False),
        ("/workspace/inputs_readonly/workspace/inputs_readonly", True),
    ],
    ids=["single_segment", "nested_duplicate", "non_adjacent", "exact_duplicate"],
)
def test_has_repeated_workspace_segments(path_str: str, expected: bool):
    assert has_repeated_workspace_segments(Path(path_str)) is expected


# ---------------------------------------------------------------------------
# discover_read_roots
# ---------------------------------------------------------------------------


def test_discover_read_roots_basic(tmp_path: Path):
    workspace = tmp_path / "workspace"
    readonly = tmp_path / "readonly"
    workspace.mkdir()
    readonly.mkdir()

    roots = discover_read_roots(workspace, readonly)
    assert workspace.resolve() in roots
    assert readonly.resolve() in roots


def test_discover_read_roots_follows_symlinks(tmp_path: Path):
    workspace = tmp_path / "workspace"
    readonly = tmp_path / "readonly"
    target = tmp_path / "real_data"
    workspace.mkdir()
    readonly.mkdir()
    target.mkdir()

    # Create a symlink under readonly pointing to target
    link = readonly / "data_link"
    os.symlink(target, link)

    roots = discover_read_roots(workspace, readonly)
    assert target.resolve() in roots


def test_discover_read_roots_deduplicates(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Same directory for both
    roots = discover_read_roots(workspace, workspace)
    resolved = [r for r in roots if r == workspace.resolve()]
    assert len(resolved) == 1


# ---------------------------------------------------------------------------
# _in_any_root
# ---------------------------------------------------------------------------


def test_in_any_root_positive(tmp_path: Path):
    root = tmp_path / "workspace"
    child = root / "outputs" / "result.txt"
    assert _in_any_root(child.resolve(), [root.resolve()]) is True


def test_in_any_root_negative(tmp_path: Path):
    root = tmp_path / "workspace"
    outside = tmp_path / "other" / "file.txt"
    assert _in_any_root(outside.resolve(), [root.resolve()]) is False


# ---------------------------------------------------------------------------
# canonical_resolve
# ---------------------------------------------------------------------------


def test_canonical_resolve_read_inside_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    candidate = workspace / "data.txt"
    resolved, reason = canonical_resolve(
        candidate,
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="read",
        readonly_root=readonly,
    )
    assert resolved is not None
    assert reason is None


def test_canonical_resolve_read_outside_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    resolved, reason = canonical_resolve(
        "/etc/passwd",
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="read",
        readonly_root=readonly,
    )
    assert resolved is None
    assert reason == "outside_allowed_read_roots"


def test_canonical_resolve_write_inside_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    candidate = workspace / "outputs" / "result.txt"
    resolved, reason = canonical_resolve(
        candidate,
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="write",
        readonly_root=readonly,
    )
    assert resolved is not None
    assert reason is None


def test_canonical_resolve_write_to_readonly_blocked(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    candidate = readonly / "file.txt"
    resolved, reason = canonical_resolve(
        candidate,
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="write",
        readonly_root=readonly,
    )
    assert resolved is None
    assert reason == "writes_to_inputs_readonly_forbidden"


def test_canonical_resolve_write_outside_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    resolved, reason = canonical_resolve(
        "/tmp/evil.txt",
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="write",
        readonly_root=readonly,
    )
    assert resolved is None
    assert reason == "outside_allowed_write_roots"


def test_canonical_resolve_invalid_mode(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    resolved, reason = canonical_resolve(
        workspace / "file.txt",
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="delete",
        readonly_root=readonly,
    )
    assert resolved is None
    assert "invalid_mode" in reason


def test_canonical_resolve_repeated_workspace_segment(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    # Construct a path with repeated workspace/inputs_readonly
    bad_path = (
        workspace / "inputs_readonly" / "sub" / "workspace" / "inputs_readonly" / "x"
    )
    resolved, reason = canonical_resolve(
        bad_path,
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="read",
        readonly_root=readonly,
    )
    assert resolved is None
    assert reason == "repeated_workspace_segment"


# ---------------------------------------------------------------------------
# resolve_with_rejections
# ---------------------------------------------------------------------------


def test_resolve_with_rejections_first_valid(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    candidates = ["/etc/passwd", str(workspace / "data.txt")]
    resolved, rejections = resolve_with_rejections(
        candidates,
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="read",
        readonly_root=readonly,
    )
    assert resolved is not None
    assert len(rejections) == 1  # /etc/passwd was rejected
    assert rejections[0]["reason"] == "outside_allowed_read_roots"


def test_resolve_with_rejections_all_rejected(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "inputs_readonly"
    readonly.mkdir()

    candidates = ["/etc/passwd", "/etc/hosts"]
    resolved, rejections = resolve_with_rejections(
        candidates,
        read_roots=[workspace.resolve()],
        write_roots=[workspace.resolve()],
        mode="read",
        readonly_root=readonly,
    )
    assert resolved is None
    assert len(rejections) == 2


# ---------------------------------------------------------------------------
# discover_fastq_files_guarded
# ---------------------------------------------------------------------------


def test_discover_fastq_files_basic(tmp_path: Path):
    (tmp_path / "sample_R1.fastq.gz").write_bytes(b"\x1f\x8b")
    (tmp_path / "sample_R2.fastq.gz").write_bytes(b"\x1f\x8b")
    (tmp_path / "readme.txt").write_text("not a fastq")

    found = discover_fastq_files_guarded(
        tmp_path, include_subdirs=False, name_filter="", max_files=100
    )
    assert len(found) == 2
    assert all("fastq" in f.lower() for f in found)


def test_discover_fastq_files_with_name_filter(tmp_path: Path):
    (tmp_path / "sample1_R1.fastq.gz").write_bytes(b"\x1f\x8b")
    (tmp_path / "sample2_R1.fastq.gz").write_bytes(b"\x1f\x8b")

    found = discover_fastq_files_guarded(
        tmp_path, include_subdirs=False, name_filter="sample1", max_files=100
    )
    assert len(found) == 1
    assert "sample1" in found[0]


def test_discover_fastq_files_with_subdirs(tmp_path: Path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "deep.fastq").write_text("@read\nACGT\n+\n!!!!\n")
    (tmp_path / "top.fastq").write_text("@read\nACGT\n+\n!!!!\n")

    found = discover_fastq_files_guarded(
        tmp_path, include_subdirs=True, name_filter="", max_files=100
    )
    assert len(found) == 2


def test_discover_fastq_files_without_subdirs(tmp_path: Path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "deep.fastq").write_text("@read\nACGT\n+\n!!!!\n")
    (tmp_path / "top.fastq").write_text("@read\nACGT\n+\n!!!!\n")

    found = discover_fastq_files_guarded(
        tmp_path, include_subdirs=False, name_filter="", max_files=100
    )
    assert len(found) == 1
    assert "top.fastq" in found[0]


def test_discover_fastq_files_max_files(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"sample{i}.fastq").write_text("@r\nACGT\n+\n!!!!\n")

    found = discover_fastq_files_guarded(
        tmp_path, include_subdirs=False, name_filter="", max_files=3
    )
    assert len(found) == 3


def test_discover_fastq_files_nonexistent_root(tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    found = discover_fastq_files_guarded(
        missing, include_subdirs=True, name_filter="", max_files=100
    )
    assert found == []


def test_discover_fastq_files_single_file(tmp_path: Path):
    fq = tmp_path / "single.fq"
    fq.write_text("@r\nACGT\n+\n!!!!\n")

    found = discover_fastq_files_guarded(
        fq, include_subdirs=False, name_filter="", max_files=100
    )
    assert len(found) == 1


def test_discover_fastq_files_symlink_loop_protection(tmp_path: Path):
    sub = tmp_path / "data"
    sub.mkdir()
    (sub / "reads.fastq").write_text("@r\nACGT\n+\n!!!!\n")

    # Create a symlink loop: data/loop -> data
    loop = sub / "loop"
    os.symlink(sub, loop)

    found = discover_fastq_files_guarded(
        tmp_path, include_subdirs=True, name_filter="", max_files=100
    )
    # Should find the file once without infinite recursion
    assert len(found) >= 1
    assert all("reads.fastq" in f for f in found)


def test_discover_fastq_all_extensions(tmp_path: Path):
    for ext in (".fastq", ".fq", ".fastq.gz", ".fq.gz"):
        (tmp_path / f"sample{ext}").write_bytes(b"\x1f\x8b")

    found = discover_fastq_files_guarded(
        tmp_path, include_subdirs=False, name_filter="", max_files=100
    )
    assert len(found) == 4
