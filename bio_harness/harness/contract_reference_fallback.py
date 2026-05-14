"""Fallback reference resolution helpers for template and repair paths."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.harness.config import PROJECT_ROOT, READONLY_LINKS_ROOT, WORKSPACE_ROOT
from bio_harness.harness.contract_reference_detection import (
    _extract_reference_paths_from_plan,
    _pick_reference_paths_from_text,
)
from bio_harness.harness.path_utils import _path_within_any_root


def _find_reference_candidate(kind: str) -> str:
    """Return the best globally discovered reference candidate for one kind."""

    roots = [
        WORKSPACE_ROOT / "references",
        PROJECT_ROOT / "references",
        READONLY_LINKS_ROOT,
        WORKSPACE_ROOT,
    ]
    suffixes = (".gtf", ".gtf.gz") if kind == "gtf" else (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")
    preferred_markers = ("mouse", "mm", "grcm", "gencode")
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not (path.is_file() or path.is_symlink()):
                continue
            name_l = path.name.lower()
            target_name = ""
            try:
                target_name = path.resolve(strict=True).name.lower()
            except Exception:
                target_name = ""
            if name_l.endswith(suffixes) or (target_name and target_name.endswith(suffixes)):
                candidates.append(path)
    if not candidates:
        return ""
    scored = sorted(
        candidates,
        key=lambda path: (
            0 if any(marker in path.name.lower() for marker in preferred_markers) else 1,
            len(path.name),
        ),
    )
    return str(scored[0])


def _find_reference_candidate_in_roots(kind: str, roots: list[Path] | tuple[Path, ...]) -> str:
    """Return the best reference candidate from an explicit root allowlist."""

    suffixes = (".gtf", ".gtf.gz") if kind == "gtf" else (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")
    for root in roots:
        try:
            root_path = Path(root).expanduser().resolve(strict=False)
        except Exception:
            continue
        if not root_path.exists():
            continue
        matches: list[Path] = []
        for path in root_path.rglob("*"):
            if not (path.is_file() or path.is_symlink()):
                continue
            name_l = path.name.lower()
            target_name = ""
            try:
                target_name = path.resolve(strict=True).name.lower()
            except Exception:
                target_name = ""
            if name_l.endswith(suffixes) or (target_name and target_name.endswith(suffixes)):
                matches.append(path)
        if matches:
            ranked = sorted(matches, key=lambda path: (len(path.name), len(str(path))))
            return str(ranked[0].resolve(strict=False))
    return ""


def _find_alias_reference(kind: str, request_text: str) -> str:
    """Return an alias-style reference path such as ``mouse_gtf`` or ``mouse_fasta``."""

    lower = (request_text or "").lower()
    if kind == "gtf":
        alias_names = ["mouse_gtf", "gtf"]
    else:
        alias_names = ["mouse_fasta", "mouse_fa", "fasta", "fa"]

    for alias in alias_names:
        if alias not in lower:
            continue
        candidate = READONLY_LINKS_ROOT / alias
        if candidate.exists() or candidate.is_symlink():
            return str(candidate)

    if READONLY_LINKS_ROOT.exists():
        for entry in READONLY_LINKS_ROOT.iterdir():
            name_l = entry.name.lower()
            if kind == "gtf" and "gtf" in name_l and (entry.exists() or entry.is_symlink()):
                return str(entry)
            if kind == "fasta" and any(token in name_l for token in ("fasta", "fa", "genome")) and (entry.exists() or entry.is_symlink()):
                return str(entry)
    return ""


def _resolve_reference_paths(request_text: str) -> tuple[str, str, str]:
    """Resolve candidate GTF and FASTA paths from the request and workspace."""

    explicit_gtf, explicit_fasta = _pick_reference_paths_from_text(request_text)
    gtf = ""
    fasta = ""
    reason_parts: list[str] = []
    lower = (request_text or "").lower()
    alias_gtf_requested = ("mouse_gtf" in lower) or bool(re.search(r"\bgtf\b", lower))
    alias_fasta_requested = ("mouse_fasta" in lower) or ("mouse_fa" in lower) or bool(re.search(r"\bfasta\b", lower))

    if alias_gtf_requested:
        gtf = _find_alias_reference("gtf", request_text) or _find_reference_candidate("gtf")
        if gtf:
            reason_parts.append("gtf_alias_or_scan")
    if alias_fasta_requested:
        fasta = _find_alias_reference("fasta", request_text) or _find_reference_candidate("fasta")
        if fasta:
            reason_parts.append("fasta_alias_or_scan")

    if not gtf and explicit_gtf:
        gtf = explicit_gtf
        reason_parts.append("gtf_explicit")
    if not fasta and explicit_fasta:
        fasta = explicit_fasta
        reason_parts.append("fasta_explicit")

    if not gtf:
        gtf = _find_reference_candidate("gtf")
        if gtf:
            reason_parts.append("gtf_local_scan")
    if not fasta:
        fasta = _find_reference_candidate("fasta")
        if fasta:
            reason_parts.append("fasta_local_scan")

    reason = ",".join(reason_parts) if reason_parts else "unresolved"
    return gtf, fasta, reason


def _resolve_reference_paths_for_template_fallback(
    request_text: str,
    *,
    data_root: Path,
    selected_dir: Path,
    official_benchmark_policy: bool,
) -> tuple[str, str, str]:
    """Resolve reference paths for deterministic template fallback mode."""

    if not official_benchmark_policy:
        return _resolve_reference_paths(request_text)

    allowed_roots = [
        Path(data_root).expanduser().resolve(strict=False),
        Path(selected_dir).expanduser().resolve(strict=False),
    ]
    explicit_gtf, explicit_fasta = _pick_reference_paths_from_text(request_text)
    gtf = ""
    fasta = ""
    reason_parts: list[str] = []

    if explicit_gtf and _path_within_any_root(explicit_gtf, allowed_roots):
        gtf = explicit_gtf
        reason_parts.append("gtf_explicit_within_official_roots")
    if explicit_fasta and _path_within_any_root(explicit_fasta, allowed_roots):
        fasta = explicit_fasta
        reason_parts.append("fasta_explicit_within_official_roots")

    if not gtf:
        gtf = _find_reference_candidate_in_roots("gtf", allowed_roots)
        if gtf:
            reason_parts.append("gtf_official_root_scan")
    if not fasta:
        fasta = _find_reference_candidate_in_roots("fasta", allowed_roots)
        if fasta:
            reason_parts.append("fasta_official_root_scan")

    reason = ",".join(reason_parts) if reason_parts else "unresolved_within_official_roots"
    return gtf, fasta, reason


def _repair_missing_references_in_plan(plan: dict[str, Any], missing_refs: list[str], request_text: str) -> dict[str, Any]:
    """Patch missing reference paths in a plan using alias and scan fallback."""

    if not missing_refs:
        return {"changed": False, "replacements": []}
    replacements: list[dict[str, str]] = []
    steps = (plan or {}).get("plan", [])
    plan_refs = _extract_reference_paths_from_plan(plan)
    for missing in missing_refs:
        missing_l = str(missing).lower()
        kind = "gtf" if (missing_l.endswith(".gtf") or missing_l.endswith(".gtf.gz") or missing_l == "gtf" or "gtf" in Path(missing_l).name) else "fasta"

        if missing_l in {"gtf", "fasta"}:
            old_candidates = [
                ref
                for ref in plan_refs
                if (kind == "gtf" and str(ref).lower().endswith((".gtf", ".gtf.gz")))
                or (kind == "fasta" and str(ref).lower().endswith((".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")))
            ]
        else:
            old_candidates = [str(missing)]

        candidate = _find_alias_reference(kind, request_text) or _find_reference_candidate(kind)
        if not candidate or not Path(candidate).exists():
            continue
        for old in old_candidates:
            new = str(candidate)
            if old == new:
                continue
            changed_any = False
            for step in steps:
                if not isinstance(step, dict):
                    continue
                tool = str(step.get("tool_name", ""))
                args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
                if tool == "bash_run":
                    command = str(args.get("command", ""))
                    if old in command:
                        args["command"] = command.replace(old, new)
                        changed_any = True
            if changed_any:
                replacements.append({"old": old, "new": new, "kind": kind})
    return {"changed": bool(replacements), "replacements": replacements}
