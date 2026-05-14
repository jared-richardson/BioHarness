"""Tests for bio_harness.core.sandbox.BioSandbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.core.sandbox import BioSandbox, BioSandboxError


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_init_creates_workspace_and_inputs(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    assert sandbox.workspace_root.is_dir()
    assert sandbox.input_dir.is_dir()
    assert sandbox.input_dir == sandbox.workspace_root / "inputs"


def test_init_existing_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = BioSandbox(workspace)
    assert sandbox.workspace_root.is_dir()


# ---------------------------------------------------------------------------
# validate_path — read access
# ---------------------------------------------------------------------------


def test_validate_path_read_anywhere(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    # Read access should be allowed anywhere
    result = sandbox.validate_path(Path("/etc/hosts"), allow_write=False)
    assert result.is_absolute()


def test_validate_path_read_inside_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    data_file = workspace / "data.txt"
    data_file.write_text("test")
    result = sandbox.validate_path(data_file, allow_write=False)
    assert result == data_file.resolve()


# ---------------------------------------------------------------------------
# validate_path — write access
# ---------------------------------------------------------------------------


def test_validate_path_write_inside_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    output_file = workspace / "outputs" / "result.txt"
    result = sandbox.validate_path(output_file, allow_write=True)
    assert result == output_file.resolve()


def test_validate_path_write_outside_workspace_blocked(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    with pytest.raises(BioSandboxError, match="outside the allowed workspace"):
        sandbox.validate_path(Path("/tmp/evil.txt"), allow_write=True)


def test_validate_path_write_to_sibling_dir_blocked(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sibling = tmp_path / "other"
    sibling.mkdir()
    sandbox = BioSandbox(workspace)

    with pytest.raises(BioSandboxError, match="outside the allowed workspace"):
        sandbox.validate_path(sibling / "file.txt", allow_write=True)


# ---------------------------------------------------------------------------
# import_file
# ---------------------------------------------------------------------------


def test_import_file_creates_symlink(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    # Create a source file
    src = tmp_path / "original.fastq"
    src.write_text("@read\nACGT\n+\n!!!!\n")

    link = sandbox.import_file(src)
    assert link.is_symlink()
    assert link.name == "original.fastq"
    assert link.parent == sandbox.input_dir
    assert link.resolve() == src.resolve()


def test_import_file_replaces_existing_symlink(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    src1 = tmp_path / "data.fq"
    src1.write_text("version1")

    src2 = tmp_path / "data_v2.fq"
    src2.write_text("version2")

    # Import first file
    link1 = sandbox.import_file(src1)
    assert link1.resolve() == src1.resolve()

    # Create a new source with same name to simulate replacement
    # Actually we need to use same filename, so let's test with same name
    # The import uses src_resolved.name, so two different source paths
    # with the same filename should replace the symlink
    src3 = tmp_path / "subdir" / "data.fq"
    src3.parent.mkdir()
    src3.write_text("version3")

    link2 = sandbox.import_file(src3)
    assert link2.resolve() == src3.resolve()


def test_import_file_nonexistent_source_raises(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    missing = tmp_path / "nonexistent.fastq"
    with pytest.raises(BioSandboxError, match="does not exist"):
        sandbox.import_file(missing)


def test_import_file_destination_is_regular_file_raises(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sandbox = BioSandbox(workspace)

    src = tmp_path / "data.fq"
    src.write_text("content")

    # Pre-create a regular file at destination
    dest = sandbox.input_dir / "data.fq"
    dest.write_text("blocker")

    with pytest.raises(BioSandboxError, match="not a symlink"):
        sandbox.import_file(src)
