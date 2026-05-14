"""Helpers for classifying filesystem parameter roles.

These helpers centralize the distinction between pre-existing inputs and
tool-built targets so plan validation and runtime preflight stay in sync.
"""

from __future__ import annotations

from typing import Any


def normalize_file_role(spec: dict[str, Any]) -> str:
    """Return the normalized file role declared for one parameter spec."""

    return str(spec.get("file_role", "") or "").strip().lower()


def is_output_like_file_role(file_role: str) -> bool:
    """Return whether *file_role* is produced by the tool itself."""

    normalized = str(file_role or "").strip().lower()
    return normalized.startswith(("output", "buildable"))


def is_input_like_file_role(file_role: str) -> bool:
    """Return whether *file_role* denotes a pre-existing input/reference path."""

    normalized = str(file_role or "").strip().lower()
    if not normalized:
        return False
    return not is_output_like_file_role(normalized)


def is_primary_output_file_role(file_role: str) -> bool:
    """Return whether *file_role* is a primary output root."""

    normalized = str(file_role or "").strip().lower()
    return normalized.startswith("output")


def is_required_existing_input(name: str, spec: dict[str, Any]) -> bool:
    """Return whether the parameter must exist before the tool runs."""

    if not bool(spec.get("required", False)):
        return False
    file_role = normalize_file_role(spec)
    if is_output_like_file_role(file_role):
        return False
    if is_input_like_file_role(file_role):
        return True
    param_type = str(spec.get("type", "") or "").strip().lower()
    param_name = str(name or "").strip().lower()
    if param_type == "path" or file_role:
        return True
    return param_name.startswith(("input_", "output_")) or param_name.endswith(
        (
            "_path",
            "_paths",
            "_dir",
            "_dirs",
            "_file",
            "_files",
            "_fasta",
            "_fa",
            "_fna",
            "_gff",
            "_gff3",
            "_gtf",
            "_vcf",
            "_vcf_gz",
            "_bam",
            "_cram",
            "_counts",
        )
    )
