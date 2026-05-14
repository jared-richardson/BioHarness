"""Analysis-workflow plan repairs for the E2E harness.

This module keeps deterministic repairs for differential-expression, variant
annotation, and multi-model pathway plans out of the general plan-repair
facade. The helpers here preserve existing behavior while giving those policy
clusters their own focused home.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.core.protocol_grounding import _compile_rna_seq_de_plan
from bio_harness.harness.config import PROJECT_ROOT
from bio_harness.harness.plan_helpers import (
    _normalize_steps,
    _renumber_plan_steps,
)
from bio_harness.harness.plan_repair_pathway_workflows import (
    _looks_like_inline_multi_model_compare_pathways_command,
    _repair_multi_model_compare_pathways_commands,
)


@dataclass(frozen=True)
class _DirectWrapperArtifactCandidate:
    """Resolved adjacent wrapper artifact candidate used for inspection repair."""

    tool_name: str
    param_name: str
    path: Path


def _repair_variant_annotation_impact_filter(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rewrite planner-made impact filters to the stable SnpSift form."""

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    annotated_vcf = ""
    changed_steps: list[int] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if tool_name == "snpeff_annotate":
            annotated_vcf = str(args.get("output_vcf", "") or "").strip()
            continue
        if tool_name != "bash_run":
            continue
        command = str(args.get("command", "") or "").strip()
        command_l = command.lower()
        if "bcftools filter" not in command_l:
            continue
        if not any(token in command_l for token in ("info/eff", "info/ann", "info/impact", "ann[*].impact", "eff[*].impact")):
            continue
        try:
            command_tokens = shlex.split(command)
        except ValueError:
            command_tokens = []
        output_path = ""
        output_match = re.search(r">\s*(\S+)\s*$", command)
        if output_match:
            output_path = output_match.group(1).strip().strip("'\"")
        if not output_path:
            for token_idx, token in enumerate(command_tokens):
                if token == "-o" and token_idx + 1 < len(command_tokens):
                    output_path = command_tokens[token_idx + 1].strip().strip("'\"")
                    break
        if not output_path:
            continue
        source_vcf = annotated_vcf
        if not source_vcf:
            vcf_candidates: list[str] = []
            skip_next = False
            for token in command_tokens:
                if skip_next:
                    skip_next = False
                    continue
                if token in {"-i", "-e", "-s", "-S", "-m", "-M", "-r", "-R", "-t", "-T", "-o", "-O"}:
                    skip_next = True
                    continue
                token_l = token.lower()
                if token_l.endswith(".vcf") or token_l.endswith(".vcf.gz"):
                    vcf_candidates.append(token.strip().strip("'\""))
            for candidate in vcf_candidates:
                if candidate != output_path:
                    source_vcf = candidate
                    break
        if not source_vcf:
            continue
        repaired_command = (
            f"mkdir -p {shlex.quote(str(Path(output_path).expanduser().parent))} && "
            f"SnpSift filter \"(ANN[*].IMPACT = 'HIGH') || (ANN[*].IMPACT = 'MODERATE')\" "
            f"{shlex.quote(source_vcf)} > {shlex.quote(output_path)}"
        )
        step["arguments"] = {**args, "command": repaired_command}
        changed_steps.append(int(step.get("step_id", idx)))

    if not changed_steps:
        return plan, {"changed": False, "why": "no_variant_annotation_impact_filter_repairs"}
    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "variant_annotation_impact_filter_repaired",
        "changed_steps": changed_steps,
    }


def _repair_deseq_bash_run_to_skill(
    plan: dict[str, Any],
    *,
    selected_dir: Path,
    analysis_spec: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replace inline R DESeq scripts with the stable ``deseq2_run`` wrapper."""

    if not isinstance(analysis_spec, dict):
        return plan, {"changed": False, "why": "analysis_spec_missing"}
    if str(analysis_spec.get("analysis_type", "") or "").strip().lower() != "rna_seq_differential_expression":
        return plan, {"changed": False, "why": "analysis_type_not_rna_seq_differential_expression"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    replacements: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "").strip()
        command_l = command.lower()
        if "library(deseq2)" not in command_l and "deseqdatasetfrommatrix" not in command_l:
            continue

        counts_match = re.search(r'read\.table\("([^"]+gene_counts[^"]*)"', command)
        metadata_match = re.search(r'read\.table\("([^"]+metadata[^"]*)"', command)
        contrast_match = re.search(
            r'results\(dds,\s*contrast=c\("condition",\s*"([^"]+)",\s*"([^"]+)"\)\)',
            command,
        )
        counts_matrix = str(counts_match.group(1)).strip() if counts_match else str(
            (selected_dir / "counts" / "gene_counts.txt").resolve(strict=False)
        )
        metadata_table = str(metadata_match.group(1)).strip() if metadata_match else str(
            (selected_dir / "metadata.tsv").resolve(strict=False)
        )
        if contrast_match:
            contrast = f"condition_{contrast_match.group(1)}_vs_{contrast_match.group(2)}"
        else:
            contrast = "condition_treatment_vs_control"
        steps[idx - 1] = {
            "tool_name": "deseq2_run",
            "arguments": {
                "script_path": str(PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "deseq2_wrapper.R"),
                "counts_matrix": counts_matrix,
                "metadata_table": metadata_table,
                "design_formula": "~ condition",
                "contrast": contrast,
                "output_dir": str((selected_dir / "deseq2_results").resolve(strict=False)),
            },
            "step_id": int(step.get("step_id", idx)),
        }
        replacements.append({"step_id": int(step.get("step_id", idx)), "mode": "bash_run_to_deseq2_run"})

    if not replacements:
        return plan, {"changed": False, "why": "no_deseq_bash_run_found"}

    patched = dict(plan)
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "repaired_deseq_bash_run_to_skill",
        "replacements": replacements,
    }


def _repair_direct_wrapper_helper_bash_run(
    plan: dict[str, Any],
    *,
    selected_dir: Path,
    analysis_spec: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Drop helper shell steps that only scaffold direct-wrapper execution paths."""

    if not isinstance(analysis_spec, dict):
        return plan, {"changed": False, "why": "analysis_spec_missing"}
    execution_contract = (
        analysis_spec.get("execution_contract", {})
        if isinstance(analysis_spec.get("execution_contract", {}), dict)
        else {}
    )
    if str(execution_contract.get("execution_mode", "") or "").strip().lower() != "direct_wrapper":
        return plan, {"changed": False, "why": "execution_mode_not_direct_wrapper"}

    compatible_tools = {
        str(tool).strip().lower()
        for tool in (execution_contract.get("compatible_tools", []) or [])
        if str(tool).strip()
    }
    explicit_intent = (
        analysis_spec.get("explicit_execution_intent", {})
        if isinstance(analysis_spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    compatible_tools.update(
        str(tool).strip().lower()
        for tool in (explicit_intent.get("locked_tools", []) or [])
        if str(tool).strip()
    )
    if not compatible_tools:
        return plan, {"changed": False, "why": "no_direct_wrapper_tools"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    repaired_steps: list[dict[str, Any]] = []
    removed_steps: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            repaired_steps.append(step)
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        if tool_name != "bash_run":
            repaired_steps.append(step)
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "").strip()
        if not command:
            repaired_steps.append(step)
            continue
        neighbor_tools = _adjacent_tool_names(steps, idx)
        if not compatible_tools.intersection(neighbor_tools):
            repaired_steps.append(step)
            continue
        if _is_direct_wrapper_mkdir_helper(command, selected_dir) or _is_direct_wrapper_deliverable_move(
            command,
            selected_dir,
        ):
            removed_steps.append(
                {
                    "step_id": int(step.get("step_id", idx + 1) or idx + 1),
                    "command": command,
                }
            )
            continue
        repaired_steps.append(step)

    if not removed_steps:
        return plan, {"changed": False, "why": "no_direct_wrapper_helper_bash_run_found"}

    patched = dict(plan)
    patched["plan"] = repaired_steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "removed_direct_wrapper_helper_bash_run",
        "removed_steps": removed_steps,
    }


def _repair_direct_wrapper_inspection_bash_run(
    plan: dict[str, Any],
    *,
    selected_dir: Path,
    analysis_spec: dict[str, Any] | None,
    request_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebind simple artifact-inspection shell steps to adjacent wrapper outputs."""

    if not isinstance(analysis_spec, dict):
        return plan, {"changed": False, "why": "analysis_spec_missing"}
    execution_contract = (
        analysis_spec.get("execution_contract", {})
        if isinstance(analysis_spec.get("execution_contract", {}), dict)
        else {}
    )
    if str(execution_contract.get("execution_mode", "") or "").strip().lower() != "direct_wrapper":
        return plan, {"changed": False, "why": "execution_mode_not_direct_wrapper"}

    compatible_tools = {
        str(tool).strip().lower()
        for tool in (execution_contract.get("compatible_tools", []) or [])
        if str(tool).strip()
    }
    explicit_intent = (
        analysis_spec.get("explicit_execution_intent", {})
        if isinstance(analysis_spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    compatible_tools.update(
        str(tool).strip().lower()
        for tool in (explicit_intent.get("locked_tools", []) or [])
        if str(tool).strip()
    )
    if not compatible_tools:
        return plan, {"changed": False, "why": "no_direct_wrapper_tools"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    replacements: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "").strip()
        if not _looks_like_artifact_inspection_command(command):
            continue
        path_tokens = _extract_command_path_tokens(command)
        if not path_tokens:
            continue
        candidates = _adjacent_direct_wrapper_artifacts(
            steps,
            idx=idx,
            compatible_tools=compatible_tools,
            selected_dir=selected_dir,
        )
        if not candidates:
            continue
        updated_command = command
        step_replacements: list[dict[str, Any]] = []
        for raw_token, resolved_path in path_tokens:
            if not _inspection_target_eligible_for_rebind(
                resolved_path,
                selected_dir=selected_dir,
            ):
                continue
            candidate = _choose_inspection_artifact_candidate(
                target_path=resolved_path,
                request_text=request_text,
                candidates=candidates,
            )
            if candidate is None:
                continue
            updated_command = updated_command.replace(raw_token, str(candidate.path))
            step_replacements.append(
                {
                    "from": raw_token,
                    "to": str(candidate.path),
                    "tool_name": candidate.tool_name,
                    "param_name": candidate.param_name,
                }
            )
        if not step_replacements or updated_command == command:
            continue
        step["arguments"] = {**args, "command": updated_command}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx + 1) or idx + 1),
                "replacements": step_replacements,
            }
        )

    if not replacements:
        return plan, {"changed": False, "why": "no_direct_wrapper_inspection_bash_run_found"}

    patched = dict(plan)
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "repaired_direct_wrapper_inspection_bash_run",
        "replacements": replacements,
    }


def _repair_rna_seq_de_plan_with_assay_compiler(
    plan: dict[str, Any],
    *,
    selected_dir: Path,
    data_root: Path,
    analysis_spec: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild malformed RNA-seq DE plans with the assay compiler when needed."""

    if not isinstance(analysis_spec, dict):
        return plan, {"changed": False, "why": "analysis_spec_missing"}
    if str(analysis_spec.get("analysis_type", "") or "").strip().lower() != "rna_seq_differential_expression":
        return plan, {"changed": False, "why": "analysis_type_not_rna_seq_differential_expression"}

    steps = _normalize_steps(plan)

    tool_names = [str(step.get("tool_name", "")).strip().lower() for step in steps if isinstance(step, dict)]
    star_steps = [step for step in steps if isinstance(step, dict) and str(step.get("tool_name", "")).strip().lower() == "star_align"]
    featurecounts_steps = [
        step for step in steps if isinstance(step, dict) and str(step.get("tool_name", "")).strip().lower() == "featurecounts_run"
    ]
    pair_count = len(sorted(data_root.glob("*_1.fastq"))) + len(sorted(data_root.glob("*_1.fastq.gz")))

    reasons: list[str] = []
    if not steps:
        reasons.append("plan_missing")
    if "dexseq_run" in tool_names:
        reasons.append("uses_dexseq_run")
    if "deseq2_run" not in tool_names:
        reasons.append("missing_deseq2_run")
    if pair_count >= 2 and len(star_steps) < pair_count:
        reasons.append("insufficient_star_align_steps")
    if featurecounts_steps:
        for step in featurecounts_steps:
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            annotation_gtf = str(args.get("annotation_gtf", "") or "").strip().lower()
            if "metadata" in Path(annotation_gtf).name:
                reasons.append("featurecounts_annotation_points_to_metadata")
                break
    for step in star_steps:
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        genome_dir = str(args.get("genome_dir", "") or "").strip()
        if genome_dir and Path(genome_dir).resolve(strict=False) == data_root.resolve(strict=False):
            reasons.append("star_genome_dir_points_to_data_root")
            break

    reasons = sorted(set(reasons))
    if not reasons:
        return plan, {"changed": False, "why": "no_rna_seq_de_repair_needed"}

    compiled, compile_meta = _compile_rna_seq_de_plan(
        plan=plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
        data_root=data_root,
    )
    if not compile_meta.get("changed", False):
        return plan, {
            "changed": False,
            "why": "rna_seq_de_compiler_unavailable",
            "reasons": reasons,
            "compile_meta": compile_meta,
        }

    return compiled, {
        "changed": True,
        "why": "repaired_rna_seq_de_plan_with_assay_compiler",
        "reasons": reasons,
        "compile_meta": compile_meta,
    }


def _adjacent_tool_names(steps: list[dict[str, Any]], idx: int) -> set[str]:
    """Return the nearest non-bash tool names around one step index."""

    neighbors: set[str] = set()
    for cursor in range(idx - 1, -1, -1):
        tool_name = str(steps[cursor].get("tool_name", "")).strip().lower()
        if tool_name:
            neighbors.add(tool_name)
        if tool_name and tool_name != "bash_run":
            break
    for cursor in range(idx + 1, len(steps)):
        tool_name = str(steps[cursor].get("tool_name", "")).strip().lower()
        if tool_name:
            neighbors.add(tool_name)
        if tool_name and tool_name != "bash_run":
            break
    return neighbors


def _adjacent_direct_wrapper_artifacts(
    steps: list[dict[str, Any]],
    *,
    idx: int,
    compatible_tools: set[str],
    selected_dir: Path,
) -> list[_DirectWrapperArtifactCandidate]:
    """Return adjacent direct-wrapper artifact candidates for one shell step."""

    candidates: list[_DirectWrapperArtifactCandidate] = []
    for cursor in (idx - 1, idx + 1):
        if cursor < 0 or cursor >= len(steps):
            continue
        step = steps[cursor]
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        if tool_name not in compatible_tools:
            continue
        candidates.extend(
            _direct_wrapper_artifact_candidates_for_step(
                step,
                selected_dir=selected_dir,
            )
        )
    return candidates


def _direct_wrapper_artifact_candidates_for_step(
    step: dict[str, Any],
    *,
    selected_dir: Path,
) -> list[_DirectWrapperArtifactCandidate]:
    """Return concrete output artifact candidates for one direct-wrapper step."""

    tool_name = str(step.get("tool_name", "")).strip().lower()
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
    registry = default_tool_registry()
    candidates: list[_DirectWrapperArtifactCandidate] = []
    seen: set[tuple[str, str]] = set()
    expected_by_key = registry.expected_output_files_by_key_for(tool_name)
    output_keys = list(registry.output_argument_keys_for(tool_name))
    output_keys.extend(registry.execution_output_parameters_for(tool_name))
    for param_name in output_keys:
        raw_value = str(args.get(param_name, "") or "").strip()
        if not raw_value:
            continue
        base_path = Path(raw_value).expanduser()
        if not base_path.is_absolute():
            base_path = (selected_dir / base_path).resolve(strict=False)
        else:
            base_path = base_path.resolve(strict=False)
        expected_rel_names = expected_by_key.get(param_name, [])
        if expected_rel_names:
            for rel_name in expected_rel_names:
                candidate_path = (base_path / rel_name).resolve(strict=False)
                key = (param_name, str(candidate_path))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    _DirectWrapperArtifactCandidate(
                        tool_name=tool_name,
                        param_name=str(param_name),
                        path=candidate_path,
                    )
                )
            continue
        key = (param_name, str(base_path))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            _DirectWrapperArtifactCandidate(
                tool_name=tool_name,
                param_name=str(param_name),
                path=base_path,
            )
        )
    return candidates


def _looks_like_artifact_inspection_command(command: str) -> bool:
    """Return whether one shell command looks like a simple file preview."""

    text = str(command or "").strip()
    if not text:
        return False
    if any(token in text for token in ("&&", "||", ">", ">>", "|")):
        return False
    try:
        tokens = shlex.split(text)
    except ValueError:
        return False
    if not tokens:
        return False
    return tokens[0] in {"head", "tail", "cat", "sed"}


def _extract_command_path_tokens(command: str) -> list[tuple[str, Path]]:
    """Return raw and resolved path tokens referenced by one shell command."""

    try:
        tokens = shlex.split(str(command or "").strip())
    except ValueError:
        return []
    path_tokens: list[tuple[str, Path]] = []
    for token in tokens:
        raw = str(token or "").strip()
        if not raw or not _looks_like_path_token(raw):
            continue
        path_tokens.append((raw, Path(raw).expanduser().resolve(strict=False)))
    return path_tokens


def _choose_inspection_artifact_candidate(
    *,
    target_path: Path,
    request_text: str,
    candidates: list[_DirectWrapperArtifactCandidate],
) -> _DirectWrapperArtifactCandidate | None:
    """Return the best adjacent artifact candidate for one inspection path."""

    request_tokens = _normalized_name_tokens(request_text)
    target_tokens = _normalized_name_tokens(target_path.name)
    best: _DirectWrapperArtifactCandidate | None = None
    best_score = 0
    for candidate in candidates:
        if candidate.path == target_path:
            return None
        score = 0
        if candidate.path.parent == target_path.parent:
            score += 3
        if candidate.path.suffix == target_path.suffix:
            score += 2
        candidate_tokens = _normalized_name_tokens(candidate.path.name)
        overlap = len(candidate_tokens.intersection(target_tokens))
        score += overlap * 4
        score += len(candidate_tokens.intersection(request_tokens)) * 2
        if _request_prefers_candidate(request_text, candidate):
            score += 4
        if score > best_score:
            best = candidate
            best_score = score
    if best is None or best_score < 6:
        return None
    return best


def _normalized_name_tokens(text: str) -> set[str]:
    """Return normalized semantic tokens for artifact matching."""

    tokens: set[str] = set()
    for raw_token in re.split(r"[^a-z0-9]+", str(text or "").lower()):
        token = raw_token.strip()
        if not token:
            continue
        if any(char.isdigit() for char in token):
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        if token in {"tmp", "run", "result", "results", "output", "outputs", "table", "file"}:
            continue
        tokens.add(token)
    return tokens


def _request_prefers_candidate(
    request_text: str,
    candidate: _DirectWrapperArtifactCandidate,
) -> bool:
    """Return whether request wording explicitly prefers one artifact candidate."""

    request_l = str(request_text or "").lower()
    path_name = candidate.path.name.lower()
    param_name = candidate.param_name.lower()
    if "abundance" in request_l and ("abundance" in path_name or "abundance" in param_name):
        return True
    if "gtf" in request_l and candidate.path.suffix.lower() == ".gtf":
        return True
    if "marker" in request_l and "marker" in path_name:
        return True
    if "cluster" in request_l and "cluster" in path_name:
        return True
    if "deseq" in request_l and "deseq" in path_name:
        return True
    return False


def _path_within_selected_dir(candidate: Path, selected_dir: Path) -> bool:
    """Return whether one candidate path resolves inside the selected dir."""

    try:
        candidate.relative_to(selected_dir.resolve(strict=False))
        return True
    except ValueError:
        return False


def _inspection_target_eligible_for_rebind(
    candidate: Path,
    *,
    selected_dir: Path,
) -> bool:
    """Return whether one inspection target should be rebound to wrapper output.

    Existing real files outside the selected directory are preserved so the
    repair does not hijack deliberate artifact inspection. Nonexistent
    workspace-style placeholder outputs remain eligible because they are the
    common planner failure mode for direct-wrapper follow-up steps.
    """

    resolved_candidate = candidate.resolve(strict=False)
    if _path_within_selected_dir(resolved_candidate, selected_dir):
        return True
    if resolved_candidate.exists():
        return False
    for ancestor in (selected_dir.resolve(strict=False), *selected_dir.resolve(strict=False).parents):
        try:
            resolved_candidate.relative_to(ancestor)
            return True
        except ValueError:
            continue
    return str(resolved_candidate.parent.name).strip().lower() in {
        "output",
        "outputs",
        "result",
        "results",
        "final",
    }


def _looks_like_path_token(token: str) -> bool:
    """Return whether one shell token looks like a filesystem path."""

    text = str(token or "").strip()
    if not text:
        return False
    if text.startswith("-"):
        return False
    if any(marker in text for marker in ("/", "./", "../", "~")):
        return True
    return text.lower().endswith(
        (
            ".csv",
            ".tsv",
            ".json",
            ".gtf",
            ".h5ad",
            ".txt",
        )
    )


def _is_direct_wrapper_mkdir_helper(command: str, selected_dir: Path) -> bool:
    """Return whether a shell step only creates wrapper-owned output directories."""

    segments = _split_simple_shell_segments(command)
    if not segments:
        return False
    return all(_is_mkdir_segment(segment, selected_dir) for segment in segments)


def _is_direct_wrapper_deliverable_move(command: str, selected_dir: Path) -> bool:
    """Return whether a shell step only copies one wrapper output into a deliverable path."""

    segments = _split_simple_shell_segments(command)
    if not segments:
        return False
    move_segment = segments[-1]
    if len(segments) > 1 and not all(
        _is_mkdir_segment(segment, selected_dir) for segment in segments[:-1]
    ):
        return False
    return _is_copy_or_move_segment(move_segment, selected_dir)


def _split_simple_shell_segments(command: str) -> list[str]:
    """Split one simple shell command on ``&&`` boundaries."""

    return [segment.strip() for segment in str(command or "").split("&&") if segment.strip()]


def _is_mkdir_segment(command: str, selected_dir: Path) -> bool:
    """Return whether one shell segment is a selected-dir ``mkdir -p`` helper."""

    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) < 3 or tokens[0] != "mkdir" or tokens[1] != "-p":
        return False
    paths = tokens[2:]
    return bool(paths) and all(_path_within_selected_dir(path, selected_dir) for path in paths)


def _is_copy_or_move_segment(command: str, selected_dir: Path) -> bool:
    """Return whether one shell segment is a selected-dir copy or move helper."""

    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) != 3 or tokens[0] not in {"mv", "cp"}:
        return False
    source_path = Path(tokens[1]).expanduser()
    dest_path = Path(tokens[2]).expanduser()
    if not _path_within_selected_dir(str(source_path), selected_dir):
        return False
    if not _path_within_selected_dir(str(dest_path), selected_dir):
        return False
    return dest_path.suffix.lower() in {".csv", ".tsv", ".json", ".gtf"}


def _path_within_selected_dir(path_text: str, selected_dir: Path) -> bool:
    """Return whether one candidate path stays inside the selected directory."""

    try:
        candidate = Path(path_text).expanduser().resolve(strict=False)
        selected = selected_dir.expanduser().resolve(strict=False)
        if candidate.is_relative_to(selected):
            return True
        return _masked_selected_dir_match(candidate, selected)
    except OSError:
        return False


def _masked_selected_dir_match(candidate: Path, selected_dir: Path) -> bool:
    """Return whether one path matches the selected-dir prefix after date masking."""

    candidate_parts = _masked_path_parts(candidate)
    selected_parts = _masked_path_parts(selected_dir)
    if len(candidate_parts) < len(selected_parts):
        return False
    return candidate_parts[: len(selected_parts)] == selected_parts


def _masked_path_parts(path: Path) -> tuple[str, ...]:
    """Return one path's parts with run-date tokens normalized."""

    return tuple(
        re.sub(r"(?<!\d)20\d{6}(?!\d)", "<DATE>", part)
        for part in path.parts
    )

__all__ = [
    "_repair_direct_wrapper_inspection_bash_run",
    "_repair_direct_wrapper_helper_bash_run",
    "_looks_like_inline_multi_model_compare_pathways_command",
    "_repair_deseq_bash_run_to_skill",
    "_repair_multi_model_compare_pathways_commands",
    "_repair_rna_seq_de_plan_with_assay_compiler",
    "_repair_variant_annotation_impact_filter",
]
