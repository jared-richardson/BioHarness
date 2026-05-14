from __future__ import annotations

from typing import Any, Dict

from pathlib import Path

from bio_harness.workflows.template_canonicalization_support import (
    empty_canonicalization_result as _empty_canonicalization_result,
    summarize_plan_diff as _summarize_plan_diff,
)
from bio_harness.workflows.template_command_rewrites import (
    extract_manifest_redirect as _extract_manifest_redirect,
    extract_star_genomegenerate as _extract_star_genomegenerate,
    normalize_rmats_command as _normalize_rmats_command,
    rewrite_rmats_to_wrapper as _rewrite_rmats_to_wrapper,
    rewrite_star_alignreads_command as _rewrite_star_alignreads_command,
    strip_destructive_segments as _strip_destructive_segments,
)
from bio_harness.workflows.template_io_support import (
    DEFAULT_STAR_INDEX_CACHE_ROOT,
    STRUCTURED_READ_PAIR_TOOLS,
    STRUCTURED_STAR_GENOME_DIR_TOOLS,
    alignment_bam_hints_for_step as _alignment_bam_hints_for_step,
    annotation_reference_kind as _annotation_reference_kind,
    bash_output_hints_for_command as _bash_output_hints_for_command,
    dedupe_preserve_order as _dedupe_preserve_order,
    extend_output_hints as _extend_output_hints,
    extract_sample_hint as _extract_sample_hint,
    get_fastq_pair_map as _get_fastq_pair_map,
    new_canonicalization_state as _new_canonicalization_state,
    normalize_structured_argument_aliases as _normalize_structured_argument_aliases,
    normalize_structured_output_path as _normalize_structured_output_path,
    path_matches_planned_outputs as _path_matches_planned_outputs,
    path_within_root as _path_within_root,
    pick_reference_file as _pick_reference_file,
    pick_star_index_dir as _pick_star_index_dir,
    repair_fastqc_input_files as _repair_fastqc_input_files,
    repair_alignment_dependent_bam_input as _repair_alignment_dependent_bam_input,
    repair_featurecounts_input_bams as _repair_featurecounts_input_bams,
    resolve_fastq_pair_from_hints as _resolve_fastq_pair_from_hints,
    rewrite_bash_reference_flags as _rewrite_bash_reference_flags,
    rewrite_output_dependency_path as _rewrite_output_dependency_path,
    script_command as _script_command,
    structured_output_hints_for_step as _structured_output_hints_for_step,
)
from bio_harness.workflows.template_plan_builders import (
    build_bootstrap_execution_plan,
    build_splicing_execution_plan,
    export_plan_run_scripts,
)

__all__ = [
    "build_bootstrap_execution_plan",
    "build_splicing_execution_plan",
    "canonicalize_execution_plan",
    "export_plan_run_scripts",
]


def _normalize_declared_hint_path(path: Path, *, selected_dir: str) -> Path:
    """Normalize one declared output hint into the active selected directory."""

    selected = str(selected_dir or "").strip()
    if not selected:
        return path.resolve(strict=False) if path.is_absolute() else path
    selected_path = Path(selected).expanduser().resolve(strict=False)
    if not path.is_absolute():
        if str(path) in {".", "./"}:
            return selected_path
        return (selected_path / path).resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    if _path_within_root(path_resolved, selected_path):
        return path_resolved
    return (selected_path / path_resolved.name).resolve(strict=False)


def _collect_declared_output_hints(
    steps: list[dict[str, Any]],
    *,
    selected_dir: str,
) -> tuple[list[Path], list[Path]]:
    """Collect plan-owned output hints before per-step normalization."""

    output_paths: list[Path] = []
    output_roots: list[Path] = []
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            continue
        tool_name = str(raw_step.get("tool_name", "")).strip()
        args = raw_step.get("arguments", {}) if isinstance(raw_step.get("arguments", {}), dict) else {}
        if tool_name == "bash_run":
            command = str(args.get("command", "")).strip()
            if not command:
                continue
            step_paths, step_roots = _bash_output_hints_for_command(command)
            output_paths.extend(
                _normalize_declared_hint_path(path, selected_dir=selected_dir)
                for path in step_paths
            )
            output_roots.extend(
                _normalize_declared_hint_path(path, selected_dir=selected_dir)
                for path in step_roots
            )
            continue
        scratch_state = _new_canonicalization_state()
        normalized_args: dict[str, Any] = {}
        for key, value in args.items():
            if isinstance(value, str):
                normalized_value, _ = _normalize_structured_output_path(
                    key,
                    value,
                    selected_dir=selected_dir,
                    state=scratch_state,
                )
                normalized_args[key] = normalized_value
                continue
            normalized_args[key] = value
        step_paths, step_roots = _structured_output_hints_for_step(tool_name, normalized_args)
        output_paths.extend(path.resolve(strict=False) if path.is_absolute() else path for path in step_paths)
        output_roots.extend(path.resolve(strict=False) if path.is_absolute() else path for path in step_roots)
    return output_paths, output_roots


def _owned_reference_outputs(state: dict[str, Any]) -> tuple[list[Path], list[Path]]:
    """Return the full set of plan-owned output hints known to canonicalization."""

    return (
        [
            *state.get("declared_output_paths", []),
            *state.get("planned_output_paths", []),
        ],
        [
            *state.get("declared_output_roots", []),
            *state.get("planned_output_roots", []),
        ],
    )


def _should_preserve_reference_path(
    path_text: str,
    *,
    selected_dir: str,
    state: dict[str, Any],
) -> bool:
    """Return whether a reference-like path should survive canonicalization."""

    raw = str(path_text or "").strip()
    if not raw:
        return False
    planned_paths, planned_roots = _owned_reference_outputs(state)
    if _path_matches_planned_outputs(raw, planned_paths, planned_roots):
        return True
    try:
        candidate = Path(raw).expanduser()
    except (RuntimeError, ValueError):
        return False
    if candidate.is_absolute():
        candidate_resolved = candidate.resolve(strict=False)
        selected = str(selected_dir or "").strip()
        if selected:
            selected_path = Path(selected).expanduser().resolve(strict=False)
            if _path_within_root(candidate_resolved, selected_path):
                return True
        try:
            if candidate.exists():
                return True
        except OSError:
            return False
    return False


def _normalize_structured_step(
    step: dict[str, Any],
    args: dict[str, Any],
    *,
    tool_name: str,
    data_root: str,
    selected_dir: str,
    state: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    changed_step = False
    args, alias_changed = _normalize_structured_argument_aliases(tool_name, args)
    changed_step = changed_step or alias_changed
    rewritten_args: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str):
            rewritten_value, dependency_changed = _rewrite_output_dependency_path(value, state)
            if dependency_changed:
                changed_step = True
                value = rewritten_value
            normalized_output, output_changed = _normalize_structured_output_path(
                key,
                value,
                selected_dir=selected_dir,
                state=state,
            )
            if output_changed:
                changed_step = True
                value = normalized_output
        rewritten_args[key] = value
    args = rewritten_args
    if tool_name in STRUCTURED_STAR_GENOME_DIR_TOOLS:
        genome_dir = str(args.get("genome_dir", "")).strip()
        if genome_dir:
            replacement_genome_dir = _pick_star_index_dir(genome_dir, data_root=data_root)
            if replacement_genome_dir and replacement_genome_dir != genome_dir:
                args["genome_dir"] = replacement_genome_dir
                changed_step = True
    if tool_name in STRUCTURED_READ_PAIR_TOOLS:
        reads_1 = str(args.get("reads_1", "")).strip()
        reads_2 = str(args.get("reads_2", "")).strip()
        if reads_1 and reads_2:
            reads_1_exists = Path(reads_1).expanduser().exists()
            reads_2_exists = Path(reads_2).expanduser().exists()
            if (not reads_1_exists or not reads_2_exists) and data_root:
                sample_hint = _extract_sample_hint(reads_1, reads_2, str(args.get("output_prefix", "")))
                replacement_r1, replacement_r2 = _resolve_fastq_pair_from_hints(
                    _get_fastq_pair_map(state, data_root),
                    sample_hint=sample_hint,
                    requested_reads_1=reads_1,
                    requested_reads_2=reads_2,
                )
                if replacement_r1 and replacement_r2:
                    args["reads_1"] = replacement_r1
                    args["reads_2"] = replacement_r2
                    changed_step = True
    if tool_name == "fastqc_run":
        input_file = str(args.get("input_file", "")).strip()
        if input_file and data_root:
            repaired_input, repaired = _repair_fastqc_input_files(
                input_file,
                data_root=data_root,
                pair_map=_get_fastq_pair_map(state, data_root),
            )
            if repaired and repaired_input != input_file:
                args["input_file"] = repaired_input
                changed_step = True
    gtf_keys = ("annotation_gtf", "gtf_path", "gtf")
    for gtf_key in gtf_keys:
        gtf_path = str(args.get(gtf_key, "")).strip()
        if not gtf_path:
            continue
        if _should_preserve_reference_path(
            gtf_path,
            selected_dir=selected_dir,
            state=state,
        ):
            continue
        replacement_gtf = _pick_reference_file(
            gtf_path,
            kind=_annotation_reference_kind(gtf_path),
            data_root=data_root,
        )
        if replacement_gtf and replacement_gtf != gtf_path:
            args[gtf_key] = replacement_gtf
            changed_step = True
    fasta_keys = ("fasta", "reference_fasta", "genome_fasta", "genome_fasta_file")
    for fasta_key in fasta_keys:
        fasta_path = str(args.get(fasta_key, "")).strip()
        if not fasta_path:
            continue
        if _should_preserve_reference_path(
            fasta_path,
            selected_dir=selected_dir,
            state=state,
        ):
            continue
        replacement_fa = _pick_reference_file(fasta_path, kind="fasta", data_root=data_root)
        if replacement_fa and replacement_fa != fasta_path:
            args[fasta_key] = replacement_fa
            changed_step = True
    if tool_name == "featurecounts_run":
        repaired_bams, repaired = _repair_featurecounts_input_bams(
            args.get("input_bams"),
            alignment_bam_hints=state["alignment_bam_hints"],
        )
        if repaired:
            args["input_bams"] = repaired_bams
            changed_step = True
    if tool_name == "gatk_mutect2_call":
        tumor_bam, repaired_tumor = _repair_alignment_dependent_bam_input(
            str(args.get("tumor_bam", "")).strip(),
            alignment_bam_hints=state["alignment_bam_hints"],
            sample_tokens=(str(args.get("tumor_sample", "")).strip(), "tumor"),
        )
        normal_bam, repaired_normal = _repair_alignment_dependent_bam_input(
            str(args.get("normal_bam", "")).strip(),
            alignment_bam_hints=state["alignment_bam_hints"],
            sample_tokens=(str(args.get("normal_sample", "")).strip(), "normal"),
        )
        if repaired_tumor:
            args["tumor_bam"] = tumor_bam
            changed_step = True
        if repaired_normal:
            args["normal_bam"] = normal_bam
            changed_step = True
    elif tool_name in {"gatk_haplotypecaller", "bcftools_call", "freebayes_call", "sniffles_sv_call", "varscan_call"}:
        repaired_bam, repaired = _repair_alignment_dependent_bam_input(
            str(args.get("input_bam", "")).strip(),
            alignment_bam_hints=state["alignment_bam_hints"],
        )
        if repaired:
            args["input_bam"] = repaired_bam
            changed_step = True
    if changed_step:
        step["canonicalized_to"] = "structured_io_resolution"
    step["arguments"] = args
    state["alignment_bam_hints"].extend(_alignment_bam_hints_for_step(tool_name, args))
    state["alignment_bam_hints"] = _dedupe_preserve_order(state["alignment_bam_hints"])
    step_paths, step_roots = _structured_output_hints_for_step(tool_name, args)
    _extend_output_hints(state, step_paths, step_roots)
    return step, changed_step


def _normalize_bash_step(
    step: dict[str, Any],
    args: dict[str, Any],
    *,
    data_root: str,
    selected_dir: str,
    state: dict[str, Any],
) -> tuple[dict[str, Any] | None, int, list[str], int]:
    command = str(args.get("command", "")).strip()
    if not command:
        step["arguments"] = args
        return step, 0, [], 0

    output_paths, output_roots = _bash_output_hints_for_command(command)
    _extend_output_hints(state, output_paths, output_roots)

    star_cmd = _extract_star_genomegenerate(command)
    if star_cmd:
        fasta_ref = star_cmd["fasta"]
        if not _should_preserve_reference_path(
            star_cmd["fasta"],
            selected_dir=selected_dir,
            state=state,
        ):
            fasta_ref = _pick_reference_file(star_cmd["fasta"], kind="fasta", data_root=data_root) or star_cmd["fasta"]
        gtf_ref = star_cmd["gtf"]
        if not _should_preserve_reference_path(
            star_cmd["gtf"],
            selected_dir=selected_dir,
            state=state,
        ):
            gtf_ref = _pick_reference_file(star_cmd["gtf"], kind="gtf", data_root=data_root) or star_cmd["gtf"]
        args["command"] = _script_command(
            "build_star_index.sh",
            star_cmd["genome_dir"],
            fasta_ref,
            gtf_ref,
            star_cmd["threads"],
            DEFAULT_STAR_INDEX_CACHE_ROOT,
            star_cmd["sjdb_overhang"],
        )
        step["arguments"] = args
        step["canonicalized_to"] = "pipeline_scripts/build_star_index.sh"
        return step, 1, [], 0

    if ("find " in command and "fastq" in command.lower()) and ">" in command:
        manifest_out = _extract_manifest_redirect(command)
        if manifest_out and data_root:
            args["command"] = _script_command("fastq_manifest.sh", data_root, manifest_out)
            step["arguments"] = args
            step["canonicalized_to"] = "pipeline_scripts/fastq_manifest.sh"
            return step, 1, [], 0

    stripped_command, removed_segments = _strip_destructive_segments(command)
    if not stripped_command:
        return None, 0, removed_segments, 1

    changed_step = stripped_command != command
    rewritten_align_command, align_changed = _rewrite_star_alignreads_command(stripped_command)
    if align_changed:
        stripped_command = rewritten_align_command
        changed_step = True
        step["canonicalized_to"] = "star_align_reads_safe_defaults"
    rewritten_rmats_command, rmats_changed = _normalize_rmats_command(stripped_command)
    if rmats_changed:
        stripped_command = rewritten_rmats_command
        changed_step = True
        step.setdefault("canonicalized_to", "rmats_cli_normalization")
    rewritten_refs_command, refs_changed = _rewrite_bash_reference_flags(
        stripped_command,
        data_root=data_root,
        preserve_reference_path=lambda candidate: _should_preserve_reference_path(
            candidate,
            selected_dir=selected_dir,
            state=state,
        ),
    )
    if refs_changed:
        stripped_command = rewritten_refs_command
        changed_step = True
        step.setdefault("canonicalized_to", "bash_reference_resolution")
    rewritten_rmats_wrapper_command, rmats_wrapper_changed = _rewrite_rmats_to_wrapper(stripped_command)
    if rmats_wrapper_changed:
        stripped_command = rewritten_rmats_wrapper_command
        changed_step = True
        step["canonicalized_to"] = "pipeline_scripts/run_rmats_if_needed.sh"
    args["command"] = stripped_command
    step["arguments"] = args
    step_paths, step_roots = _structured_output_hints_for_step("bash_run", args)
    _extend_output_hints(state, step_paths, step_roots)
    return step, (1 if changed_step else 0), removed_segments, 0


def canonicalize_execution_plan(
    plan_json: Dict[str, Any],
    *,
    data_root: str = "",
    selected_dir: str = "",
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    original = dict(plan_json or {})
    steps = original.get("plan", []) if isinstance(original, dict) else []
    if not isinstance(steps, list):
        return _empty_canonicalization_result(original)

    normalized_steps: list[dict[str, Any]] = []
    replaced_count = 0
    removed_segments: list[str] = []
    removed_steps = 0
    state = _new_canonicalization_state()
    declared_paths, declared_roots = _collect_declared_output_hints(
        [step for step in steps if isinstance(step, dict)],
        selected_dir=selected_dir,
    )
    state["declared_output_paths"] = declared_paths
    state["declared_output_roots"] = declared_roots

    for idx, raw_step in enumerate(steps, start=1):
        if not isinstance(raw_step, dict):
            continue
        step = dict(raw_step)
        step["step_id"] = int(step.get("step_id", idx))
        args = dict(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {})
        tool_name = str(step.get("tool_name", "")).strip()
        if tool_name != "bash_run":
            normalized_step, changed_step = _normalize_structured_step(
                step,
                args,
                tool_name=tool_name,
                data_root=data_root,
                selected_dir=selected_dir,
                state=state,
            )
            if changed_step:
                replaced_count += 1
            normalized_steps.append(normalized_step)
            continue

        normalized_step, step_replacements, step_removed_segments, step_removed_steps = _normalize_bash_step(
            step,
            args,
            data_root=data_root,
            selected_dir=selected_dir,
            state=state,
        )
        replaced_count += step_replacements
        removed_segments.extend(step_removed_segments)
        removed_steps += step_removed_steps
        if normalized_step is None:
            continue
        normalized_steps.append(normalized_step)

    for idx, step in enumerate(normalized_steps, start=1):
        step["step_id"] = idx

    normalized_plan = {
        **original,
        "plan": normalized_steps,
        "canonicalization": {
            "template": "generic_plan_canonicalization_v1",
            "replaced_steps": replaced_count,
            "removed_steps": removed_steps,
            "removed_destructive_segments": removed_segments[:12],
        },
    }
    diff_summary = _summarize_plan_diff(original, normalized_plan)
    changed = bool(replaced_count or removed_steps)
    meta = {
        "changed": changed,
        "reason": "normalized_to_reusable_scripts" if changed else "already_canonical",
        "replaced_steps": replaced_count,
        "removed_steps": removed_steps,
        "removed_destructive_segments": removed_segments[:12],
        "diff_summary": diff_summary,
    }
    return normalized_plan, meta
