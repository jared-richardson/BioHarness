"""Shared shell helpers for idempotent wrapper-side file staging.

This module centralizes small shell fragments that wrappers use when they need
to stage inputs into a deterministic run-local location before executing a
tool. The helpers are inode-aware so relative and absolute spellings of the
same file do not trigger redundant self-copies.
"""

from __future__ import annotations

import shlex
from pathlib import Path


def idempotent_stage_copy_command(source_path: str, destination_path: str) -> str:
    """Return a shell fragment that copies one file only when needed.

    Args:
        source_path: Source file path to stage.
        destination_path: Canonical staged file location.

    Returns:
        A shell-safe command fragment that creates the destination parent
        directory and copies the source into place only when the destination is
        missing or does not already point at the same inode.
    """

    source = str(source_path or "").strip()
    destination = str(destination_path or "").strip()
    if not source or not destination:
        raise ValueError("source_path and destination_path are required")
    destination_parent = Path(destination).expanduser().parent
    quoted_source = shlex.quote(source)
    quoted_destination = shlex.quote(destination)
    quoted_parent = shlex.quote(str(destination_parent))
    return (
        f"mkdir -p {quoted_parent} && "
        f"if [ ! -e {quoted_destination} ] || ! [ {quoted_source} -ef {quoted_destination} ]; then "
        f"cp -f {quoted_source} {quoted_destination}; fi"
    )


__all__ = ["idempotent_stage_copy_command"]
