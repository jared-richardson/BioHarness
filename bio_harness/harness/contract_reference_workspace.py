"""Workspace-scoped reference lookup and repair helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bio_harness.core.analysis_spec_support import is_direct_skill_smoke_query
from bio_harness.core.request_scope import infer_request_data_root
from bio_harness.harness.config import PROJECT_ROOT
from bio_harness.harness.contract_reference_detection import (
    _explicit_requested_reference_paths,
    _looks_like_fasta_path,
    _looks_like_task_local_generated_reference,
    _looks_like_transcriptome_fasta_path,
    _planned_converted_gtf_path,
    _preserve_current_reference_path,
)
from bio_harness.harness.contract_reference_indexing import (
    _find_prebuilt_quant_index,
    _stable_index_base_for_tool,
    _stable_quant_index_path_for_tool,
)
from bio_harness.harness.path_utils import _path_within_root
from bio_harness.harness.plan_helpers import _normalize_steps, _renumber_plan_steps


def _workspace_reference_alias_candidates(kind: str) -> tuple[list[str], tuple[str, ...]]:
    """Return alias names and suffixes for one workspace reference kind."""

    if kind == "gtf":
        return ["mouse_gtf", "gtf"], (".gtf", ".gtf.gz")
    if kind == "transcriptome":
        return ["transcriptome", "cdna", "transcripts", "mrna"], (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")
    return ["mouse_fasta", "mouse_fa", "fasta", "fa"], (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")


def _workspace_search_roots(selected_dir: Path, data_root: Path) -> list[Path]:
    """Return normalized workspace roots searched for staged references."""

    roots = [selected_dir / "inputs_readonly", selected_dir / "references", data_root, selected_dir]
    dedup_roots: list[Path] = []
    seen_roots: set[str] = set()
    for root in roots:
        try:
            resolved = Path(root).expanduser().resolve(strict=False)
        except Exception:
            resolved = Path(root).expanduser()
        key = str(resolved)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        dedup_roots.append(resolved)
    return dedup_roots


def _find_workspace_reference(
    kind: str,
    request_text: str,
    selected_dir: Path,
    data_root: Path,
) -> str:
    """Return the best staged workspace reference matching one kind."""

    alias_names, suffixes = _workspace_reference_alias_candidates(kind)
    dedup_roots = _workspace_search_roots(selected_dir, data_root)

    lower = str(request_text or "").lower()
    for alias in alias_names:
        if alias not in lower:
            continue
        for root in dedup_roots:
            candidate = root / alias
            if candidate.exists() or candidate.is_symlink():
                return str(candidate)

    for root in dedup_roots:
        if not root.exists():
            continue
        for alias in alias_names:
            candidate = root / alias
            if candidate.exists() or candidate.is_symlink():
                return str(candidate)

    matches: list[Path] = []
    for root in dedup_roots:
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*")
        except Exception:
            continue
        for entry in iterator:
            if not (entry.exists() or entry.is_symlink()):
                continue
            name_l = entry.name.lower()
            if name_l.endswith(suffixes):
                matches.append(entry)
    if kind == "transcriptome":
        matches = [entry for entry in matches if _looks_like_transcriptome_fasta_path(entry)]
    if not matches:
        return ""
    ranked = sorted(
        matches,
        key=lambda path: (
            1 if kind == "fasta" and _looks_like_transcriptome_fasta_path(path) else 0,
            0 if any(alias in path.name.lower() for alias in alias_names) else 1,
            len(str(path)),
        ),
    )
    return str(ranked[0])


def _repair_requested_references_and_index_bases_in_plan(
    plan: dict[str, Any],
    selected_dir: Path,
    data_root: Path,
    request_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Repair staged references and derived index paths in one plan."""

    if is_direct_skill_smoke_query(request_text):
        return plan, {"changed": False, "why": "direct_skill_smoke_preserves_explicit_requested_paths"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    explicit_requested_refs = _explicit_requested_reference_paths(request_text)
    request_root_text = infer_request_data_root(request_text, project_root=PROJECT_ROOT)
    request_data_root = Path(request_root_text).resolve(strict=False) if request_root_text else None
    search_roots = _workspace_search_roots(selected_dir, data_root)

    resolved_gtf = explicit_requested_refs["gtf"] or _find_workspace_reference("gtf", request_text, selected_dir, data_root)
    if not resolved_gtf:
        planned_gtf = _planned_converted_gtf_path(plan)
        if planned_gtf:
            resolved_gtf = planned_gtf
    resolved_fasta = explicit_requested_refs["fasta"] or _find_workspace_reference("fasta", request_text, selected_dir, data_root)
    resolved_transcriptome = explicit_requested_refs["transcriptome"] or _find_workspace_reference("transcriptome", request_text, selected_dir, data_root)
    replacements: list[dict[str, Any]] = []

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if not args:
            continue
        tool_name = str(step.get("tool_name", "")).strip()
        updated_args = dict(args)
        step_changed = False

        current_gtf = str(updated_args.get("annotation_gtf", "")).strip()
        preserve_current_gtf = _preserve_current_reference_path(
            current_path=current_gtf,
            explicit_requested_path=explicit_requested_refs["gtf"],
            selected_dir=selected_dir,
            data_root=data_root,
            request_data_root=request_data_root,
        )
        if resolved_gtf and current_gtf and current_gtf != resolved_gtf and not preserve_current_gtf:
            updated_args["annotation_gtf"] = resolved_gtf
            replacements.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": tool_name,
                    "argument": "annotation_gtf",
                    "from": current_gtf,
                    "to": resolved_gtf,
                }
            )
            step_changed = True

        current_fasta = str(updated_args.get("reference_fasta", "")).strip()
        preserve_task_local_reference = _looks_like_task_local_generated_reference(current_fasta, selected_dir)
        preserve_current_fasta = _preserve_current_reference_path(
            current_path=current_fasta,
            explicit_requested_path=explicit_requested_refs["fasta"],
            selected_dir=selected_dir,
            data_root=data_root,
            request_data_root=request_data_root,
            preserve_task_local=preserve_task_local_reference,
        )
        if resolved_fasta and current_fasta and current_fasta != resolved_fasta and not preserve_current_fasta:
            updated_args["reference_fasta"] = resolved_fasta
            replacements.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": tool_name,
                    "argument": "reference_fasta",
                    "from": current_fasta,
                    "to": resolved_fasta,
                }
            )
            step_changed = True

        current_transcriptome = str(updated_args.get("transcriptome_fasta", "")).strip()
        preserve_task_local_transcriptome = _looks_like_task_local_generated_reference(current_transcriptome, selected_dir)
        preserve_current_transcriptome = _preserve_current_reference_path(
            current_path=current_transcriptome,
            explicit_requested_path=explicit_requested_refs["transcriptome"],
            selected_dir=selected_dir,
            data_root=data_root,
            request_data_root=request_data_root,
            preserve_task_local=preserve_task_local_transcriptome,
        )
        if tool_name in {"kallisto_quant", "salmon_quant"} and resolved_transcriptome:
            if not current_transcriptome:
                updated_args["transcriptome_fasta"] = resolved_transcriptome
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": tool_name,
                        "argument": "transcriptome_fasta",
                        "from": current_transcriptome,
                        "to": resolved_transcriptome,
                    }
                )
                step_changed = True
            elif current_transcriptome != resolved_transcriptome and not preserve_current_transcriptome:
                updated_args["transcriptome_fasta"] = resolved_transcriptome
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": tool_name,
                        "argument": "transcriptome_fasta",
                        "from": current_transcriptome,
                        "to": resolved_transcriptome,
                    }
                )
                step_changed = True

        effective_reference = str(updated_args.get("reference_fasta", "")).strip()
        stable_index = _stable_index_base_for_tool(tool_name, selected_dir, effective_reference)
        current_index = str(updated_args.get("index_base", "")).strip()
        if stable_index and current_index != stable_index:
            updated_args["index_base"] = stable_index
            replacements.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": tool_name,
                    "argument": "index_base",
                    "from": current_index,
                    "to": stable_index,
                }
            )
            step_changed = True

        effective_transcriptome = str(updated_args.get("transcriptome_fasta", "")).strip()
        stable_quant_index = _stable_quant_index_path_for_tool(tool_name, selected_dir, effective_transcriptome)
        prebuilt_quant_index = _find_prebuilt_quant_index(tool_name, effective_transcriptome, search_roots)
        if tool_name == "kallisto_quant" and stable_quant_index:
            current_quant_index = str(updated_args.get("index_path", "")).strip()
            preferred_quant_index = prebuilt_quant_index or stable_quant_index
            if (
                not current_quant_index
                or current_quant_index == effective_transcriptome
                or _looks_like_fasta_path(current_quant_index)
                or not _path_within_root(current_quant_index, selected_dir)
            ):
                updated_args["index_path"] = preferred_quant_index
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": tool_name,
                        "argument": "index_path",
                        "from": current_quant_index,
                        "to": preferred_quant_index,
                    }
                )
                step_changed = True
        if tool_name == "salmon_quant" and stable_quant_index:
            current_quant_index = str(updated_args.get("index_dir", "")).strip()
            if (
                not current_quant_index
                or current_quant_index == effective_transcriptome
                or _looks_like_fasta_path(current_quant_index)
                or not _path_within_root(current_quant_index, selected_dir)
            ):
                updated_args["index_dir"] = stable_quant_index
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": tool_name,
                        "argument": "index_dir",
                        "from": current_quant_index,
                        "to": stable_quant_index,
                    }
                )
                step_changed = True
            if not str(updated_args.get("library_type", "")).strip():
                updated_args["library_type"] = "A"
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": tool_name,
                        "argument": "library_type",
                        "from": "",
                        "to": "A",
                    }
                )
                step_changed = True

        if step_changed:
            step["arguments"] = updated_args

    if not replacements:
        return plan, {
            "changed": False,
            "why": "no_reference_or_index_repairs",
            "request_data_root": str(request_data_root) if request_data_root is not None else "",
            "explicit_requested_references": explicit_requested_refs,
            "resolved_gtf": resolved_gtf,
            "resolved_fasta": resolved_fasta,
            "resolved_transcriptome_fasta": resolved_transcriptome,
        }

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "request_data_root": str(request_data_root) if request_data_root is not None else "",
        "explicit_requested_references": explicit_requested_refs,
        "resolved_gtf": resolved_gtf,
        "resolved_fasta": resolved_fasta,
        "resolved_transcriptome_fasta": resolved_transcriptome,
        "diff_summary": {"replacement_count": len(replacements)},
    }
