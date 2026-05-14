"""Evolution-specific plan repair helpers for the E2E harness.

This module owns deterministic repairs for the official evolution benchmark and
related experimental-evolution workflows. Keeping these helpers separate from
the generic repair facade reduces the size and policy density of
``bio_harness.harness.plan_repair``.
"""

from __future__ import annotations

import copy
import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.artifact_role_validator import (
    summarize_artifact_role_violations,
    validate_artifact_role_invariants,
)
from bio_harness.core.protocol_grounding import (
    _build_normalize_vcf_command,
    _build_variant_filter_command,
)
from bio_harness.harness.config import SHARED_VARIANT_EXPORTER
from bio_harness.harness.contract_utils import (
    _discover_fastq_pair_map,
    _extract_fastq_sample_tag,
    _infer_evolution_step_sample_tag,
    _iter_pathlike_values,
    _looks_like_fasta_path,
    _sample_tag_kind,
)
from bio_harness.harness.plan_helpers import (
    _extract_csv_output_from_command,
    _normalize_steps,
    _renumber_plan_steps,
)
from bio_harness.harness.plan_repair_shared_variants import (
    _evolution_variant_repair_settings,
    _shared_variant_export_settings_from_analysis_spec,
)


def _wants_evolution_variant_workflow(request_text: str) -> bool:
    """Return whether a request targets the evolution variant benchmark family."""

    query_l = str(request_text or "").lower()
    return (
        ("evolution" in query_l or "evolved" in query_l or "ancestor" in query_l)
        and not any(term in query_l for term in ("differential expression", "splicing", "rna-seq", "transcript"))
    )


def _derive_step_path(root_value: str, leaf_name: str) -> str:
    """Build a deterministic child path from one workflow root."""

    root_text = str(root_value or "").strip()
    if not root_text:
        return ""
    root_path = Path(root_text).expanduser()
    combined = root_path / leaf_name
    if root_path.is_absolute():
        return str(combined.resolve(strict=False))
    return str(combined)


def _looks_like_assembly_reference(path_value: str) -> bool:
    """Return whether a FASTA path looks like an assembly-derived reference."""

    raw = str(path_value or "").strip()
    if not raw:
        return False
    name = Path(raw).name.lower()
    return _looks_like_fasta_path(raw) and any(token in name for token in ("contig", "scaffold", "assembly"))


def _ancestor_root_from_spades(spades_roots: list[tuple[str, str]]) -> str:
    """Pick the ancestor SPAdes output root from discovered assembly roots."""

    for root, sample_tag in spades_roots:
        if _sample_tag_kind(sample_tag) == "ancestor":
            return root
    return spades_roots[0][0] if spades_roots else ""


def _canonical_evolution_bam_path(selected_dir: Path, sample_tag: str) -> str:
    """Return the canonical BAM path for one evolved lineage.

    Fix #25 (2026-04-23, post-exp41): the emitted filename MUST match the
    ``_aligned.bam`` convention used by ``_bind_bacterial_evolution_variant_calling``
    in ``bio_harness/core/strict_artifact_binding_variant_binders.py`` — which
    sets ``evol1_bam = alignments/evol1_aligned.bam``. Previously this helper
    returned the bare ``{slug}.bam`` form, so the scientific-harness plan
    normalizer ran ``_repair_evolution_alignment_path_bindings`` AFTER the
    strict binder had already produced ``evol1_aligned.bam`` and silently
    rewrote it back to ``evol1.bam``. The bwa_mem_align wrapper then wrote the
    final BAM as ``evol1.bam`` on disk, but the NEXT step's strict binder still
    expected ``evol1_aligned.bam`` — causing the Fix #22b disk-existence
    pre-check to reject the candidate with "references inputs that are not
    available yet" and stalling the run permanently (exp41 failed at turn 7
    for exactly this reason). Ancestor steps were unaffected because this
    repair only runs for ``_sample_tag_kind == "evolved"`` samples, so the
    ``anc_aligned.bam`` convention survived intact. Aligning both canonical
    naming schemes on ``{slug}_aligned.bam`` is the minimal repair.
    """

    raw_tag = str(sample_tag).strip().lower()
    digits_match = re.search(r"(?:evol(?:ved)?|isolate|mutant)[^0-9]*(\d+)", raw_tag)
    if "anc" in raw_tag or "ancestor" in raw_tag:
        slug = "anc"
    elif digits_match:
        slug = f"evol{digits_match.group(1)}"
    else:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_tag) or "sample"
    return str((selected_dir / "alignments" / f"{slug}_aligned.bam").resolve(strict=False))


def _resolve_evolution_selected_dir(
    *,
    selected_dir: Path | None,
    ancestor_root: str,
) -> Path | None:
    """Return the selected-dir root needed for dependency-safe repairs."""

    if selected_dir is not None:
        return Path(selected_dir).expanduser().resolve(strict=False)
    root_text = str(ancestor_root or "").strip()
    if not root_text:
        return None
    return Path(root_text).expanduser().resolve(strict=False).parent


def _artifact_issue_strings_for_steps(
    steps: list[dict[str, Any]],
    *,
    selected_dir: Path,
) -> set[str]:
    """Return stable artifact-role issue strings for one step list."""

    violations = validate_artifact_role_invariants(
        {"plan": copy.deepcopy(steps)},
        selected_dir=selected_dir,
    )
    return set(summarize_artifact_role_violations(violations))


def _can_remove_step_without_new_artifact_issues(
    steps: list[dict[str, Any]],
    *,
    step_index: int,
    selected_dir: Path | None,
) -> bool:
    """Return whether removing one step preserves artifact-role safety.

    The guard is conservative: removal is allowed only when dropping the step
    does not introduce any new artifact-role violations compared with the
    current candidate plan.
    """

    if selected_dir is None or step_index < 0 or step_index >= len(steps):
        return False
    before = _artifact_issue_strings_for_steps(steps, selected_dir=selected_dir)
    remaining_steps = [copy.deepcopy(step) for idx, step in enumerate(steps) if idx != step_index]
    after = _artifact_issue_strings_for_steps(remaining_steps, selected_dir=selected_dir)
    return after.issubset(before)


def _repair_evolution_spades_reference_usage(
    plan: dict[str, Any],
    request_text: str,
    *,
    selected_dir: Path | None = None,
    allow_destructive_mutations: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Repair evolution plans that misuse evolved assemblies as references.

    Args:
        plan: Candidate executable plan.
        request_text: User request used to detect the evolution workflow family.
        selected_dir: Optional selected-dir root for dependency-safe checks.
        allow_destructive_mutations: Whether the repair may delete or insert
            workflow steps after reference normalization. Safe path rewrites
            remain enabled even when this is `False`.
    """

    if not _wants_evolution_variant_workflow(request_text):
        return plan, {"changed": False, "why": "request_not_evolution_variant_workflow"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    spades_roots: list[tuple[str, str]] = []
    replacements: list[dict[str, Any]] = []
    inserted_steps: list[dict[str, Any]] = []
    remove_step_indices: set[int] = set()
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if tool_name == "spades_assemble":
            output_dir = str(args.get("output_dir", "")).strip()
            if output_dir:
                sample_tag = ""
                for probe_key in ("reads_1", "reads_2", "output_dir"):
                    sample_tag = _extract_fastq_sample_tag(str(args.get(probe_key, "")).strip())
                    if sample_tag:
                        break
                spades_roots.append((output_dir, sample_tag))
            updated_args = dict(args)
            if "careful" not in updated_args:
                updated_args["careful"] = True
                step["arguments"] = updated_args
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "spades_assemble",
                        "argument": "careful",
                        "from": "",
                        "to": True,
                    }
                )
            continue

        if not spades_roots or not args:
            continue
        updated_args = dict(args)
        changed = False
        step_sample_tag = _infer_evolution_step_sample_tag(updated_args)
        step_sample_kind = _sample_tag_kind(step_sample_tag)
        ancestor_root = _ancestor_root_from_spades(spades_roots)
        resolved_selected_dir = _resolve_evolution_selected_dir(
            selected_dir=selected_dir,
            ancestor_root=ancestor_root,
        )
        ancestor_gff = _derive_step_path(ancestor_root, "genes.gff") if ancestor_root else ""
        ancestor_faa = _derive_step_path(ancestor_root, "genes.faa") if ancestor_root else ""
        ancestor_scaffolds = _derive_step_path(ancestor_root, "scaffolds.fasta") if ancestor_root else ""
        for key in ("reference_fasta", "input_fasta"):
            current = str(updated_args.get(key, "")).strip()
            if not current:
                continue
            current_name = Path(current).name.lower()
            current_resolved = str(Path(current).expanduser().resolve(strict=False))
            if current.endswith("/contigs.fasta") or current_name.endswith("_contigs.fasta") or current_name.endswith("_scaffolds.fasta"):
                replacement = ancestor_scaffolds
                if replacement and current != replacement:
                    updated_args[key] = replacement
                    changed = True
                    replacements.append(
                        {
                            "step_id": int(step.get("step_id", idx)),
                            "tool_name": str(step.get("tool_name", "")).strip(),
                            "argument": key,
                            "from": current,
                            "to": replacement,
                        }
                    )
                continue
            if not ancestor_root:
                continue
            under_spades_root = any(
                current_resolved.startswith(str(Path(root).expanduser().resolve(strict=False)))
                for root, _sample_tag in spades_roots
            )
            if not under_spades_root and not _looks_like_assembly_reference(current):
                continue
            replacement = ancestor_scaffolds
            if replacement and current != replacement:
                updated_args[key] = replacement
                changed = True
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": str(step.get("tool_name", "")).strip(),
                        "argument": key,
                        "from": current,
                        "to": replacement,
                    }
                )
        if tool_name == "prodigal_annotate" and ancestor_root:
            if allow_destructive_mutations and step_sample_kind == "evolved":
                remove_step_indices.add(idx - 1)
            else:
                if ancestor_scaffolds and updated_args.get("input_fasta") != ancestor_scaffolds:
                    updated_args["input_fasta"] = ancestor_scaffolds
                    changed = True
                    replacements.append(
                        {
                            "step_id": int(step.get("step_id", idx)),
                            "tool_name": "prodigal_annotate",
                            "argument": "input_fasta",
                            "from": str(args.get("input_fasta", "")).strip(),
                            "to": ancestor_scaffolds,
                        }
                    )
                if ancestor_gff and updated_args.get("output_gff") != ancestor_gff:
                    updated_args["output_gff"] = ancestor_gff
                    changed = True
                    replacements.append(
                        {
                            "step_id": int(step.get("step_id", idx)),
                            "tool_name": "prodigal_annotate",
                            "argument": "output_gff",
                            "from": str(args.get("output_gff", "")).strip(),
                            "to": ancestor_gff,
                        }
                    )
                if ancestor_faa and updated_args.get("output_faa") != ancestor_faa:
                    updated_args["output_faa"] = ancestor_faa
                    changed = True
                    replacements.append(
                        {
                            "step_id": int(step.get("step_id", idx)),
                            "tool_name": "prodigal_annotate",
                            "argument": "output_faa",
                            "from": str(args.get("output_faa", "")).strip(),
                            "to": ancestor_faa,
                        }
                    )
        if tool_name == "bwa_mem_align":
            if allow_destructive_mutations and step_sample_kind == "ancestor":
                if _can_remove_step_without_new_artifact_issues(
                    steps,
                    step_index=idx - 1,
                    selected_dir=resolved_selected_dir,
                ):
                    remove_step_indices.add(idx - 1)
            elif ancestor_scaffolds and updated_args.get("reference_fasta") != ancestor_scaffolds:
                updated_args["reference_fasta"] = ancestor_scaffolds
                changed = True
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "bwa_mem_align",
                        "argument": "reference_fasta",
                        "from": str(args.get("reference_fasta", "")).strip(),
                        "to": ancestor_scaffolds,
                    }
                )
        if tool_name == "freebayes_call":
            if allow_destructive_mutations and step_sample_kind == "ancestor":
                if _can_remove_step_without_new_artifact_issues(
                    steps,
                    step_index=idx - 1,
                    selected_dir=resolved_selected_dir,
                ):
                    remove_step_indices.add(idx - 1)
            else:
                if ancestor_scaffolds and updated_args.get("reference_fasta") != ancestor_scaffolds:
                    updated_args["reference_fasta"] = ancestor_scaffolds
                    changed = True
                    replacements.append(
                        {
                            "step_id": int(step.get("step_id", idx)),
                            "tool_name": "freebayes_call",
                            "argument": "reference_fasta",
                            "from": str(args.get("reference_fasta", "")).strip(),
                            "to": ancestor_scaffolds,
                        }
                    )
                if str(updated_args.get("ploidy", "")).strip() != "1":
                    updated_args["ploidy"] = 1
                    changed = True
                    replacements.append(
                        {
                            "step_id": int(step.get("step_id", idx)),
                            "tool_name": "freebayes_call",
                            "argument": "ploidy",
                            "from": str(args.get("ploidy", "")).strip(),
                            "to": 1,
                        }
                    )
        if tool_name == "snpeff_annotate" and ancestor_root:
            if allow_destructive_mutations and step_sample_kind == "ancestor":
                if _can_remove_step_without_new_artifact_issues(
                    steps,
                    step_index=idx - 1,
                    selected_dir=resolved_selected_dir,
                ):
                    remove_step_indices.add(idx - 1)
            if updated_args.get("reference_fasta") != ancestor_scaffolds:
                updated_args["reference_fasta"] = ancestor_scaffolds
                changed = True
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "snpeff_annotate",
                        "argument": "reference_fasta",
                        "from": str(args.get("reference_fasta", "")).strip(),
                        "to": ancestor_scaffolds,
                    }
                )
            if updated_args.get("annotation_gff") != ancestor_gff:
                updated_args["annotation_gff"] = ancestor_gff
                changed = True
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "snpeff_annotate",
                        "argument": "annotation_gff",
                        "from": str(args.get("annotation_gff", "")).strip(),
                        "to": ancestor_gff,
                    }
                )
            genome_db = str(updated_args.get("genome_db", "")).strip()
            if genome_db in {"", "ecoli", "e_coli", "bacteria_ancestor"}:
                updated_args["genome_db"] = "ecoli_custom"
                changed = True
                replacements.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": "snpeff_annotate",
                        "argument": "genome_db",
                        "from": genome_db,
                        "to": "ecoli_custom",
                    }
                )
        if changed:
            step["arguments"] = updated_args

    ancestor_root = _ancestor_root_from_spades(spades_roots)
    if allow_destructive_mutations and ancestor_root:
        needs_prodigal = any(
            idx not in remove_step_indices and str(step.get("tool_name", "")).strip().lower() == "snpeff_annotate"
            for idx, step in enumerate(steps)
            if isinstance(step, dict)
        )
        has_ancestor_prodigal = any(
            idx not in remove_step_indices
            and str(step.get("tool_name", "")).strip().lower() == "prodigal_annotate"
            and _sample_tag_kind(
                _infer_evolution_step_sample_tag(
                    step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
                )
            )
            != "evolved"
            for idx, step in enumerate(steps)
            if isinstance(step, dict)
        )
        if needs_prodigal and not has_ancestor_prodigal:
            inserted_steps.append(
                {
                    "tool_name": "prodigal_annotate",
                    "arguments": {
                        "input_fasta": _derive_step_path(ancestor_root, "scaffolds.fasta"),
                        "output_gff": _derive_step_path(ancestor_root, "genes.gff"),
                        "output_faa": _derive_step_path(ancestor_root, "genes.faa"),
                    },
                    "step_id": 0,
                }
            )

    removed_step_ids: list[int] = []
    if allow_destructive_mutations:
        referenced_roots: set[str] = set()
        for step in steps:
            if not isinstance(step, dict):
                continue
            if str(step.get("tool_name", "")).strip().lower() == "spades_assemble":
                continue
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            for value in args.values():
                for item in _iter_pathlike_values(value):
                    item_resolved = str(Path(str(item)).expanduser().resolve(strict=False))
                    for root, _sample_tag in spades_roots:
                        root_resolved = str(Path(root).expanduser().resolve(strict=False))
                        if item_resolved.startswith(root_resolved):
                            referenced_roots.add(root)
        filtered_steps: list[dict[str, Any]] = []
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            if (idx - 1) in remove_step_indices:
                removed_step_ids.append(int(step.get("step_id", idx)))
                continue
            if str(step.get("tool_name", "")).strip().lower() != "spades_assemble":
                filtered_steps.append(step)
                continue
            output_dir = str(
                (step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}).get("output_dir", "")
            ).strip()
            sample_tag = _extract_fastq_sample_tag(
                str((step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}).get("reads_1", "")).strip()
            )
            sample_kind = _sample_tag_kind(sample_tag)
            if sample_kind == "evolved":
                removed_step_ids.append(int(step.get("step_id", idx)))
                continue
            if output_dir and output_dir != ancestor_root and output_dir not in referenced_roots:
                removed_step_ids.append(int(step.get("step_id", idx)))
                continue
            filtered_steps.append(step)
        steps = filtered_steps

        if inserted_steps:
            insert_at = 0
            for idx, step in enumerate(steps):
                tool_name = str(step.get("tool_name", "")).strip().lower() if isinstance(step, dict) else ""
                if tool_name == "spades_assemble":
                    insert_at = idx + 1
            steps[insert_at:insert_at] = inserted_steps
            replacements.append(
                {
                    "step_id": 0,
                    "tool_name": "prodigal_annotate",
                    "argument": "insert_step",
                    "from": "",
                    "to": "ancestor_gene_prediction",
                }
            )

        if removed_step_ids:
            replacements.append(
                {
                    "step_id": 0,
                    "tool_name": "spades_assemble",
                    "argument": "remove_unused_steps",
                    "from": removed_step_ids,
                    "to": [],
                }
            )

    if not replacements:
        return plan, {"changed": False, "why": "no_evolution_spades_repairs"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }


def _repair_evolution_missing_variant_branches(
    plan: dict[str, Any],
    *,
    request_text: str,
    selected_dir: Path,
    data_root: Path,
    analysis_spec: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Expand incomplete evolution plans into the full per-lineage variant path."""

    if not _wants_evolution_variant_workflow(request_text):
        return plan, {"changed": False, "why": "request_not_evolution_variant_workflow"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    pair_map = _discover_fastq_pair_map(data_root)
    if not pair_map:
        return plan, {"changed": False, "why": "no_fastq_pairs_discovered"}

    reference_label = ""
    evolved_labels: list[str] = []
    for label in sorted(pair_map):
        if _sample_tag_kind(label) == "ancestor" and not reference_label:
            reference_label = label
            continue
        if _sample_tag_kind(label) == "evolved":
            evolved_labels.append(label)
    if not reference_label:
        reference_label = sorted(pair_map)[0]
    if not evolved_labels:
        evolved_labels = [label for label in sorted(pair_map) if label != reference_label]
    if len(evolved_labels) < 2:
        return plan, {"changed": False, "why": "insufficient_evolved_samples", "sample_labels": evolved_labels}
    evolved_labels = evolved_labels[:2]

    freebayes_steps = [
        step for step in steps if isinstance(step, dict) and str(step.get("tool_name", "")).strip().lower() == "freebayes_call"
    ]
    bwa_steps = [
        step for step in steps if isinstance(step, dict) and str(step.get("tool_name", "")).strip().lower() == "bwa_mem_align"
    ]
    evolved_bwa_count = sum(
        1
        for step in bwa_steps
        if _sample_tag_kind(
            _infer_evolution_step_sample_tag(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {})
        )
        == "evolved"
    )
    evolved_freebayes_count = sum(
        1
        for step in freebayes_steps
        if _sample_tag_kind(
            _infer_evolution_step_sample_tag(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {})
        )
        == "evolved"
    )
    has_multi_bam_caller = any(
        isinstance((step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}).get("input_bam"), (list, tuple, set))
        for step in freebayes_steps
    )
    if not has_multi_bam_caller and evolved_bwa_count >= len(evolved_labels) and evolved_freebayes_count >= len(evolved_labels):
        return plan, {"changed": False, "why": "evolution_variant_branches_already_present"}

    ancestor_spades_step: dict[str, Any] | None = None
    annotation_step: dict[str, Any] | None = None
    reference_fasta = ""
    annotation_gff = ""
    snpeff_genome_db = "ecoli_custom"
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if tool_name == "spades_assemble":
            sample_tag = _infer_evolution_step_sample_tag(args)
            if _sample_tag_kind(sample_tag) == "ancestor" or ancestor_spades_step is None:
                ancestor_spades_step = {"tool_name": "spades_assemble", "arguments": dict(args)}
                output_dir = str(args.get("output_dir", "")).strip()
                if output_dir:
                    reference_fasta = str((Path(output_dir).expanduser() / "scaffolds.fasta").resolve(strict=False))
        elif tool_name in {"prodigal_annotate", "prokka_annotate"} and annotation_step is None:
            annotation_step = {"tool_name": tool_name, "arguments": dict(args)}
            annotation_gff = str(args.get("output_gff", "") or "").strip()
        elif tool_name == "snpeff_annotate":
            snpeff_genome_db = str(args.get("genome_db", "")).strip() or snpeff_genome_db
            if not annotation_gff:
                annotation_gff = str(args.get("annotation_gff", "") or "").strip()
            if not reference_fasta:
                reference_fasta = str(args.get("reference_fasta", "") or "").strip()

    if not ancestor_spades_step:
        return plan, {"changed": False, "why": "no_ancestor_assembly_step"}
    if not reference_fasta:
        return plan, {"changed": False, "why": "no_reference_fasta_for_evolution_repair"}

    if annotation_step is None:
        annotation_root = selected_dir / "annotation"
        annotation_gff = str((annotation_root / "genes.gff").resolve(strict=False))
        annotation_faa = str((annotation_root / "genes.faa").resolve(strict=False))
        annotation_step = {
            "tool_name": "prodigal_annotate",
            "arguments": {
                "input_fasta": reference_fasta,
                "output_gff": annotation_gff,
                "output_faa": annotation_faa,
            },
        }
    elif not annotation_gff:
        annotation_gff = str((selected_dir / "annotation" / "genes.gff").resolve(strict=False))
        annotation_args = dict(annotation_step.get("arguments", {}) if isinstance(annotation_step.get("arguments", {}), dict) else {})
        annotation_args["output_gff"] = annotation_gff
        annotation_step["arguments"] = annotation_args

    desired_bam_paths: dict[str, str] = {}
    for step in freebayes_steps:
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        for raw in _iter_pathlike_values(args.get("input_bam")):
            sample_tag = _extract_fastq_sample_tag(raw)
            if _sample_tag_kind(sample_tag) != "evolved":
                continue
            desired_bam_paths.setdefault(sample_tag, raw)
    for step in bwa_steps:
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        raw_output_bam = str(args.get("output_bam", "")).strip()
        if not raw_output_bam:
            continue
        sample_tag = _infer_evolution_step_sample_tag(args)
        if _sample_tag_kind(sample_tag) != "evolved":
            continue
        desired_bam_paths.setdefault(sample_tag, raw_output_bam)

    output_csv = str((selected_dir / "final" / "variants_shared.csv").resolve(strict=False))
    for step in steps:
        if not isinstance(step, dict) or str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        candidate_csv = _extract_csv_output_from_command(str(args.get("command", "")).strip())
        if candidate_csv:
            output_csv = candidate_csv
            break

    export_settings = _shared_variant_export_settings_from_analysis_spec(analysis_spec)
    repair_settings = _evolution_variant_repair_settings(analysis_spec)
    new_steps: list[dict[str, Any]] = [
        {"tool_name": "spades_assemble", "arguments": dict(ancestor_spades_step.get("arguments", {}))},
        {"tool_name": str(annotation_step.get("tool_name", "")).strip(), "arguments": dict(annotation_step.get("arguments", {}))},
    ]
    comparison_vcfs: list[str] = []
    created_steps: list[dict[str, Any]] = []

    for label in evolved_labels:
        sample_pair = pair_map.get(label, {})
        reads_1 = str(sample_pair.get("r1", "") or sample_pair.get("reads_1", "")).strip()
        reads_2 = str(sample_pair.get("r2", "") or sample_pair.get("reads_2", "")).strip()
        if not (reads_1 and reads_2):
            return plan, {"changed": False, "why": "missing_sample_pair_reads", "sample_label": label}
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label).strip().lower()) or "sample"
        bam_path = desired_bam_paths.get(label) or str((selected_dir / "alignments" / f"{slug}_sorted.bam").resolve(strict=False))
        unmapped_bam = str((selected_dir / "alignments" / f"{slug}.unmapped.bam").resolve(strict=False))
        raw_vcf = str((selected_dir / "variants" / f"{slug}.raw.vcf").resolve(strict=False))
        filtered_vcf = str((selected_dir / "variants" / f"{slug}.filtered.vcf.gz").resolve(strict=False))
        annotated_vcf = str((selected_dir / "variants" / f"{slug}.annotated.vcf").resolve(strict=False))
        normalized_vcf = str((selected_dir / "variants" / f"{slug}.normalized.vcf.gz").resolve(strict=False))
        comparison_vcfs.append(normalized_vcf if export_settings["normalize_before_compare"] else annotated_vcf)
        created_steps.extend(
            [
                {
                    "tool_name": "bwa_mem_align",
                    "arguments": {
                        "reference_fasta": reference_fasta,
                        "reads_1": reads_1,
                        "reads_2": reads_2,
                        "output_bam": bam_path,
                        "threads": 8,
                        "postprocess_mode": repair_settings["postprocess_mode"],
                        "output_unmapped_bam": unmapped_bam,
                    },
                },
                {
                    "tool_name": "freebayes_call",
                    "arguments": {
                        "reference_fasta": reference_fasta,
                        "input_bam": bam_path,
                        "output_vcf": raw_vcf,
                        "ploidy": 1,
                    },
                },
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": _build_variant_filter_command(raw_vcf, filtered_vcf, repair_settings["filter_expression"]),
                    },
                },
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {
                        "genome_db": snpeff_genome_db,
                        "reference_fasta": reference_fasta,
                        "annotation_gff": annotation_gff,
                        "input_vcf": filtered_vcf,
                        "output_vcf": annotated_vcf,
                    },
                },
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": _build_normalize_vcf_command(annotated_vcf, normalized_vcf, reference_fasta),
                    },
                },
            ]
        )

    export_cmd = (
        f"python {shlex.quote(str(SHARED_VARIANT_EXPORTER))} "
        f"--input-vcf-a {shlex.quote(comparison_vcfs[0])} "
        f"--input-vcf-b {shlex.quote(comparison_vcfs[1])} "
        f"--output-csv {shlex.quote(output_csv)} "
        f"--min-impact {shlex.quote(export_settings['min_impact'])} "
        f"--status {shlex.quote(export_settings['status'])} "
        f"--header-case {shlex.quote(export_settings['header_case'])}"
    )
    if export_settings["dedupe_by_gene"]:
        export_cmd += " --dedupe-by-gene"

    new_steps.extend(created_steps)
    new_steps.append({"tool_name": "bash_run", "arguments": {"command": export_cmd}})

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = new_steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "repaired_evolution_missing_variant_branches",
        "reference_label": reference_label,
        "sample_labels": evolved_labels,
        "comparison_vcfs": comparison_vcfs,
        "output_csv": output_csv,
        "diff_summary": {"replacement_count": len(created_steps) + 1},
    }


def _repair_evolution_alignment_path_bindings(
    plan: dict[str, Any],
    *,
    request_text: str,
    selected_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize evolution alignment and caller BAM bindings to canonical paths."""

    if not _wants_evolution_variant_workflow(request_text):
        return plan, {"changed": False, "why": "request_not_evolution_variant_workflow"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    replacements: list[dict[str, Any]] = []
    evolved_bam_paths: dict[str, str] = {}
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or str(step.get("tool_name", "")).strip().lower() != "bwa_mem_align":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        sample_tag = _infer_evolution_step_sample_tag(args)
        if _sample_tag_kind(sample_tag) != "evolved":
            continue
        canonical_bam = _canonical_evolution_bam_path(selected_dir, sample_tag)
        evolved_bam_paths[sample_tag] = canonical_bam
        current_bam = str(args.get("output_bam", "")).strip()
        if not current_bam or current_bam == canonical_bam:
            continue
        step["arguments"] = {**args, "output_bam": canonical_bam}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bwa_mem_align",
                "argument": "output_bam",
                "from": current_bam,
                "to": canonical_bam,
            }
        )

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or str(step.get("tool_name", "")).strip().lower() != "freebayes_call":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        sample_tag = _infer_evolution_step_sample_tag(args)
        if _sample_tag_kind(sample_tag) != "evolved":
            continue
        canonical_bam = evolved_bam_paths.get(sample_tag) or _canonical_evolution_bam_path(selected_dir, sample_tag)
        current_bam = str(args.get("input_bam", "")).strip()
        if not current_bam or current_bam == canonical_bam:
            continue
        step["arguments"] = {**args, "input_bam": canonical_bam}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "freebayes_call",
                "argument": "input_bam",
                "from": current_bam,
                "to": canonical_bam,
            }
        )

    if not replacements:
        return plan, {"changed": False, "why": "no_evolution_alignment_path_repairs"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "repaired_evolution_alignment_path_bindings",
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }
