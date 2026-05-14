"""Reference-path extraction and request-scoped detection helpers."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.request_scope import extract_request_paths
from bio_harness.harness.config import PROJECT_ROOT
from bio_harness.harness.path_utils import _path_within_any_root, _path_within_root
from bio_harness.harness.plan_helpers import _normalize_steps
from bio_harness.harness.stream_utils import _extract_paths_from_text

_TRANSCRIPTOME_FASTA_MARKERS = ("transcriptome", "cdna", "transcript", "transcripts", "mrna")
_REFERENCE_CONTEXT_WINDOW = 96


def _extract_reference_paths_from_plan(plan: dict[str, Any]) -> list[str]:
    """Extract explicit reference-like paths from one plan."""

    refs: list[str] = []
    ext_suffixes = (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz", ".gtf", ".gtf.gz")
    alias_names = {"mouse_fasta", "mouse_fa", "mouse_gtf"}
    flagged_reference_args = {
        "-f",
        "-t",
        "--gtf",
        "--gtf_path",
        "--annotation_gtf",
        "--annotation",
        "--sjdbgtffile",
        "--fasta",
        "--reference_fasta",
        "--genome_fasta",
        "--genome_fasta_file",
        "--genomefastafiles",
    }

    def _normalize_token(token: str) -> str:
        return token.strip().strip("\"'").rstrip("];,")

    def _maybe_add_reference(token: str) -> None:
        text = _normalize_token(token)
        text_l = text.lower()
        base = Path(text).name.lower()
        if base in alias_names or (text.startswith("/") and text_l.endswith(ext_suffixes)):
            refs.append(text)

    for step in plan.get("plan", []):
        if not isinstance(step, dict):
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        for key in ("reference_fasta", "annotation_gtf", "transcriptome_fasta"):
            value = str(args.get(key, "")).strip()
            if value:
                _maybe_add_reference(value)
        if step.get("tool_name") != "bash_run":
            continue
        command = str(args.get("command", ""))
        if not command.strip():
            continue
        try:
            tokens = shlex.split(command, posix=True)
        except Exception:
            tokens = []
        for idx, token in enumerate(tokens):
            if str(token).strip().lower() not in flagged_reference_args:
                continue
            if idx + 1 < len(tokens):
                _maybe_add_reference(tokens[idx + 1])

    deduped: list[str] = []
    seen: set[str] = set()
    for item in refs:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _looks_like_fasta_path(path_value: str) -> bool:
    """Return whether a path text looks like a FASTA file."""

    name = Path(str(path_value or "").strip()).name.lower()
    return name.endswith((".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz"))


def _looks_like_transcriptome_fasta_path(path_value: str | Path) -> bool:
    """Return whether a FASTA path looks transcriptome-like rather than genomic."""

    raw = str(path_value or "").strip()
    if not _looks_like_fasta_path(raw):
        return False
    name_l = Path(raw).name.lower()
    return any(marker in name_l for marker in _TRANSCRIPTOME_FASTA_MARKERS)


def _looks_like_task_local_generated_reference(path_value: str, selected_dir: Path) -> bool:
    """Return whether a reference-like path points at a selected-dir local artifact."""

    raw = str(path_value or "").strip()
    if not raw or not _looks_like_fasta_path(raw):
        return False
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return _path_within_root(str(candidate), selected_dir)
    if "/" not in raw and "\\" not in raw:
        return False
    return _path_within_root(str((selected_dir / candidate).resolve(strict=False)), selected_dir)


def _planned_converted_gtf_path(plan: dict[str, Any]) -> str:
    """Return the planned converted GTF output from a bash conversion step, if any."""

    steps = _normalize_steps(plan)
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "").strip()
        if "gff3_to_gtf.py" not in command:
            continue
        try:
            tokens = shlex.split(command)
        except Exception:
            continue
        if len(tokens) >= 4:
            return str(tokens[-1]).strip()
    return ""


def _explicit_requested_reference_paths(request_text: str) -> dict[str, str]:
    """Extract explicitly requested reference paths from one request."""

    references = {"gtf": "", "fasta": "", "transcriptome": ""}
    request_text_l = str(request_text or "").lower()
    extracted: list[str] = []
    for candidate in extract_request_paths(request_text, project_root=PROJECT_ROOT):
        expanded = candidate.expanduser()
        if expanded.is_absolute():
            extracted.append(os.path.abspath(str(expanded)))
        else:
            extracted.append(str(expanded.resolve(strict=False)))

    for resolved in extracted:
        lowered = resolved.lower()
        if not references["gtf"] and lowered.endswith((".gtf", ".gtf.gz")):
            if _path_has_request_context_marker(request_text_l, resolved, kind="gtf"):
                references["gtf"] = resolved
                continue
        if not references["transcriptome"] and _looks_like_transcriptome_fasta_path(resolved):
            if _path_has_request_context_marker(request_text_l, resolved, kind="transcriptome"):
                references["transcriptome"] = resolved
                continue
        if not references["fasta"] and _looks_like_fasta_path(resolved):
            if _path_has_request_context_marker(request_text_l, resolved, kind="fasta"):
                references["fasta"] = resolved

    for resolved in extracted:
        lowered = resolved.lower()
        if not references["gtf"] and lowered.endswith((".gtf", ".gtf.gz")):
            references["gtf"] = resolved
            continue
        if not references["transcriptome"] and _looks_like_transcriptome_fasta_path(resolved):
            references["transcriptome"] = resolved
            continue
        if not references["fasta"] and _looks_like_fasta_path(resolved):
            references["fasta"] = resolved
    return references


def _path_has_request_context_marker(
    request_text_l: str,
    resolved_path: str,
    *,
    kind: str,
) -> bool:
    """Return whether a prompt path is introduced by a role-specific marker."""

    hay = str(request_text_l or "")
    needles = [str(resolved_path or "").strip(), Path(str(resolved_path or "")).name]
    markers_by_kind = {
        "fasta": ("reference", "reference genome", "reference fasta"),
        "gtf": ("annotation", "annotation gtf", "reference annotation"),
        "transcriptome": ("transcriptome", "cdna", "transcripts"),
    }
    markers = markers_by_kind.get(kind, ())
    if not markers:
        return False

    for needle in needles:
        needle_l = str(needle or "").strip().lower()
        if not needle_l:
            continue
        idx = hay.find(needle_l)
        while idx >= 0:
            before = hay[max(0, idx - _REFERENCE_CONTEXT_WINDOW) : idx]
            if any(marker in before for marker in markers):
                return True
            idx = hay.find(needle_l, idx + 1)
    return False


def _preserve_current_reference_path(
    *,
    current_path: str,
    explicit_requested_path: str,
    selected_dir: Path,
    data_root: Path,
    request_data_root: Path | None,
    preserve_task_local: bool = False,
) -> bool:
    """Return whether an existing reference path should survive repair."""

    current_text = str(current_path or "").strip()
    if not current_text:
        return False
    if preserve_task_local:
        return True
    if explicit_requested_path:
        return current_text == explicit_requested_path

    roots: list[Path] = [selected_dir, data_root]
    if request_data_root is not None:
        roots.append(request_data_root)
    return _path_within_any_root(current_text, tuple(roots))


def _pick_reference_paths_from_text(text: str) -> tuple[str, str]:
    """Return explicit GTF and FASTA paths mentioned in one request."""

    gtf = ""
    fasta = ""
    for path in _extract_paths_from_text(text):
        path_l = path.lower()
        if (path_l.endswith(".gtf") or path_l.endswith(".gtf.gz")) and Path(path).expanduser().exists():
            gtf = path
        if path_l.endswith((".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")) and Path(path).expanduser().exists():
            fasta = path
    return gtf, fasta
