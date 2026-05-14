"""Deterministic repair helpers for canonical benchmark input assets.

This module restores known benchmark-owned task files when they drift from
their repo-defined canonical content. The repairs are intentionally narrow:
they only apply to recognized benchmark task roots and never guess content for
arbitrary user datasets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


BIOAGENTBENCH_DESEQ_SAMPLE_CONDITIONS: tuple[tuple[str, str], ...] = (
    ("SRR1278968", "Plankton"),
    ("SRR1278969", "Plankton"),
    ("SRR1278970", "Plankton"),
    ("SRR1278971", "Biofilm"),
    ("SRR1278972", "Biofilm"),
    ("SRR1278973", "Biofilm"),
)


@dataclass(frozen=True)
class BenchmarkAssetRepairAction:
    """One deterministic benchmark asset repair that was applied.

    Attributes:
        repair_id: Stable machine-readable repair identifier.
        path: Repaired filesystem path.
        action: Stable repair action label.
        message: Human-readable description of the repair.
    """

    repair_id: str
    path: str
    action: str
    message: str


@dataclass(frozen=True)
class BenchmarkAssetRepairReport:
    """Summary of benchmark asset repairs for one data root.

    Attributes:
        matched_profile: Recognized benchmark profile id, or an empty string.
        changed: Whether any repair mutated on-disk assets.
        actions: Applied repair actions.
    """

    matched_profile: str
    changed: bool
    actions: tuple[BenchmarkAssetRepairAction, ...]


@dataclass(frozen=True)
class _BenchmarkAssetProfile:
    """One recognized benchmark asset profile."""

    profile_id: str
    task_id: str
    analysis_types: tuple[str, ...]
    repair_fn: Callable[[Path], tuple[BenchmarkAssetRepairAction, ...]]


def render_bioagentbench_deseq_sample_metadata() -> str:
    """Return canonical BioAgentBench DESeq sample metadata text.

    Returns:
        Canonical TSV payload for the official BioAgentBench DESeq task.
    """

    rows = ["sample\tcondition"]
    rows.extend(
        f"{sample}\t{condition}"
        for sample, condition in BIOAGENTBENCH_DESEQ_SAMPLE_CONDITIONS
    )
    return "\n".join(rows) + "\n"


def repair_benchmark_input_assets(
    *,
    data_root: Path,
    analysis_type: str = "",
) -> BenchmarkAssetRepairReport:
    """Repair known benchmark-owned task assets under one data root.

    Args:
        data_root: Candidate benchmark task data directory.
        analysis_type: Optional analysis type used to narrow profile matching.

    Returns:
        One repair report describing any deterministic benchmark asset repairs
        that were applied.
    """

    resolved_root = Path(data_root).expanduser().resolve(strict=False)
    normalized_analysis_type = str(analysis_type or "").strip()
    for profile in _KNOWN_BENCHMARK_ASSET_PROFILES:
        if not _profile_matches_data_root(
            profile,
            data_root=resolved_root,
            analysis_type=normalized_analysis_type,
        ):
            continue
        actions = profile.repair_fn(resolved_root)
        return BenchmarkAssetRepairReport(
            matched_profile=profile.profile_id,
            changed=bool(actions),
            actions=actions,
        )
    return BenchmarkAssetRepairReport(
        matched_profile="",
        changed=False,
        actions=(),
    )


def _profile_matches_data_root(
    profile: _BenchmarkAssetProfile,
    *,
    data_root: Path,
    analysis_type: str,
) -> bool:
    """Return whether one benchmark profile owns the provided data root."""

    if analysis_type and analysis_type not in profile.analysis_types:
        return False
    return _is_bioagentbench_task_data_root(data_root, task_id=profile.task_id)


def _is_bioagentbench_task_data_root(data_root: Path, *, task_id: str) -> bool:
    """Return whether a path is one canonical BioAgentBench task data root."""

    resolved_root = Path(data_root).expanduser().resolve(strict=False)
    if resolved_root.name != "data":
        return False
    task_root = resolved_root.parent
    tasks_root = task_root.parent
    bench_root = tasks_root.parent
    return (
        task_root.name == task_id
        and tasks_root.name == "tasks"
        and bench_root.name == "bioagent-bench"
    )


def _repair_bioagentbench_deseq_assets(
    data_root: Path,
) -> tuple[BenchmarkAssetRepairAction, ...]:
    """Repair canonical assets for the BioAgentBench DESeq task."""

    metadata_path = Path(data_root).expanduser().resolve(strict=False) / "sample_metadata.tsv"
    expected_text = render_bioagentbench_deseq_sample_metadata()
    current_text = _read_utf8_text(metadata_path)
    if _normalize_text(current_text) == expected_text:
        return ()

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(expected_text, encoding="utf-8")
    action = "restored" if current_text is None else "canonicalized"
    return (
        BenchmarkAssetRepairAction(
            repair_id="bioagentbench_deseq_sample_metadata_v1",
            path=str(metadata_path),
            action=action,
            message=(
                "Restored canonical BioAgentBench DESeq sample metadata."
                if action == "restored"
                else "Canonicalized BioAgentBench DESeq sample metadata back to the repo-defined benchmark content."
            ),
        ),
    )


def _read_utf8_text(path: Path) -> str | None:
    """Read one UTF-8 text file, returning None when unreadable or missing."""

    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _normalize_text(text: str | None) -> str | None:
    """Normalize line endings and terminal newline for stable text comparison."""

    if text is None:
        return None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


_KNOWN_BENCHMARK_ASSET_PROFILES: tuple[_BenchmarkAssetProfile, ...] = (
    _BenchmarkAssetProfile(
        profile_id="bioagentbench_deseq_v1",
        task_id="deseq",
        analysis_types=("rna_seq_differential_expression",),
        repair_fn=_repair_bioagentbench_deseq_assets,
    ),
)


__all__ = [
    "BIOAGENTBENCH_DESEQ_SAMPLE_CONDITIONS",
    "BenchmarkAssetRepairAction",
    "BenchmarkAssetRepairReport",
    "repair_benchmark_input_assets",
    "render_bioagentbench_deseq_sample_metadata",
]
