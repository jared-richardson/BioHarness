"""Branch-stage progress summaries for stepwise agent planning.

The stepwise planner can race one sample branch ahead when protocol progress
only reports high-level missing tools. This module derives a deterministic
branch-by-stage matrix from the accepted prefix and renders that state as a
planner hint. It does not synthesize or insert scientific workflow steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from bio_harness.core.strict_artifact_binding_benchmark_helpers import _benchmark_task_data_dir
from bio_harness.core.strict_artifact_binding_paths import _build_rna_seq_de_paths


_EVOLUTION_STAGE_ORDER = (
    "aligned_bam",
    "raw_vcf",
    "filtered_vcf",
    "ancestor_subtracted_vcf",
    "annotated_vcf",
    "normalized_vcf",
)
_ANCESTOR_STAGES = frozenset({"aligned_bam", "raw_vcf", "filtered_vcf"})
_EVOLVED_STAGE_TO_TOOL = {
    "aligned_bam": "bwa_mem_align",
    "raw_vcf": "freebayes_call",
    "filtered_vcf": "bcftools_filter_run",
    "ancestor_subtracted_vcf": "bcftools_isec_run",
    "annotated_vcf": "snpeff_annotate",
    "normalized_vcf": "bcftools_norm_run",
}
_ANCESTOR_STAGE_TO_TOOL = {
    "aligned_bam": "bwa_mem_align",
    "raw_vcf": "freebayes_call",
    "filtered_vcf": "bcftools_filter_run",
}
_TOOL_TO_STAGE = {
    "bwa_mem_align": "aligned_bam",
    "bowtie2_align": "aligned_bam",
    "minimap2_align": "aligned_bam",
    "freebayes_call": "raw_vcf",
    "bcftools_call": "raw_vcf",
    "gatk_haplotypecaller": "raw_vcf",
    "bcftools_filter_run": "filtered_vcf",
    "vcffilter": "filtered_vcf",
    "bcftools_isec_run": "ancestor_subtracted_vcf",
    "snpeff_annotate": "annotated_vcf",
    "vep_annotate": "annotated_vcf",
    "bcftools_norm_run": "normalized_vcf",
}
_DOWNSTREAM_TOOLS_REQUIRING_COMPLETE_FRONTIER = frozenset(
    {
        "featurecounts_run",
        "shared_variants_export_run",
    }
)
_RNA_SEQ_DE_STAGE_ORDER = ("aligned_bam",)
_RNA_SEQ_DE_STAGE_TO_TOOL = {"aligned_bam": "subread_align"}
_EVOL_RE = re.compile(r"(?<![a-z0-9])(?:evol(?:ved)?|line|isolate|mutant)[\s_-]*(\d+)(?![a-z0-9])")
_ANCESTOR_RE = re.compile(r"(?<![a-z0-9])(?:anc|ancestor)(?![a-z0-9])")
_READ_MATE_SUFFIX_RE = re.compile(r"(?:[_\-.](?:r?[12]|read[12]))(?:\.f(?:ast)?q(?:\.gz)?)?$", re.IGNORECASE)


@dataclass(frozen=True)
class BranchStageCell:
    """One incomplete branch-stage frontier cell.

    Attributes:
        branch_id: Branch label, such as ``ancestor`` or ``evol2``.
        stage: Canonical stage name.
        suggested_tool: Preferred wrapper for the missing stage.
    """

    branch_id: str
    stage: str
    suggested_tool: str

    def to_mapping(self) -> dict[str, str]:
        """Return this cell as a JSON-compatible mapping."""

        return {
            "branch_id": self.branch_id,
            "stage": self.stage,
            "suggested_tool": self.suggested_tool,
        }


def summarize_branch_stage_progress(
    *,
    steps: list[dict[str, Any]],
    statuses: list[str],
    analysis_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize branch-stage progress for a stepwise prefix.

    Args:
        steps: Accepted plan-prefix steps.
        statuses: Step execution statuses parallel to ``steps``.
        analysis_spec: Current analysis specification.

    Returns:
        JSON-compatible branch-stage matrix summary. Unsupported analysis types
        return an empty mapping.
    """

    spec = analysis_spec if isinstance(analysis_spec, dict) else {}
    analysis_type = str(spec.get("analysis_type", "") or "").strip().lower()
    if analysis_type == "rna_seq_differential_expression":
        return _summarize_rna_seq_de_sample_progress(
            steps=steps,
            statuses=statuses,
            analysis_spec=spec,
        )
    if analysis_type != "bacterial_evolution_variant_calling":
        return {}

    completed = _completed_stages_by_branch(steps=steps, statuses=statuses)
    branches = _expected_evolution_branches(
        analysis_spec=spec,
        completed_branches=sorted(completed),
    )
    matrix = {
        branch: {
            stage: stage in completed.get(branch, set())
            for stage in _required_stages_for_branch(branch)
        }
        for branch in branches
    }
    frontier = _frontier_cells(matrix)
    next_cell = frontier[0] if frontier else None
    complete = not frontier
    return {
        "analysis_type": "bacterial_evolution_variant_calling",
        "complete": complete,
        "started": _matrix_has_completed_cell(matrix),
        "branches": branches,
        "stage_order": list(_EVOLUTION_STAGE_ORDER),
        "matrix": matrix,
        "next_cell": next_cell.to_mapping() if next_cell else {},
        "frontier": [cell.to_mapping() for cell in frontier],
    }


def render_branch_stage_progress_hint(progress: dict[str, Any]) -> str:
    """Render one compact branch-stage planner hint.

    Args:
        progress: Summary returned by ``summarize_branch_stage_progress``.

    Returns:
        Human-readable hint, or an empty string when no frontier is available.
    """

    if not isinstance(progress, dict) or bool(progress.get("complete", False)):
        return ""
    next_cell = progress.get("next_cell")
    if not isinstance(next_cell, dict) or not next_cell:
        return ""
    branch = str(next_cell.get("branch_id", "") or "").strip()
    stage = str(next_cell.get("stage", "") or "").strip()
    tool = str(next_cell.get("suggested_tool", "") or "").strip()
    if not branch or not stage or not tool:
        return ""

    if str(progress.get("analysis_type", "") or "") == "rna_seq_differential_expression":
        siblings = []
        frontier = progress.get("frontier")
        if isinstance(frontier, list):
            for item in frontier[1:4]:
                if not isinstance(item, dict):
                    continue
                sibling = str(item.get("branch_id", "") or "").strip()
                sibling_stage = str(item.get("stage", "") or "").strip()
                if sibling and sibling_stage:
                    siblings.append(f"{sibling}:{sibling_stage}")
        sibling_text = f" Other incomplete cells at this frontier: {', '.join(siblings)}." if siblings else ""
        return (
            "Sample-stage progress frontier: next incomplete cell is "
            f"sample={branch}, stage={stage}; suggested next tool is `{tool}`. "
            "Complete sample-local alignment before feature counting or "
            "differential-expression aggregation."
            + sibling_text
        )

    siblings = []
    frontier = progress.get("frontier")
    if isinstance(frontier, list):
        for item in frontier[1:4]:
            if not isinstance(item, dict):
                continue
            sibling_branch = str(item.get("branch_id", "") or "").strip()
            sibling_stage = str(item.get("stage", "") or "").strip()
            if sibling_branch and sibling_stage:
                siblings.append(f"{sibling_branch}:{sibling_stage}")
    sibling_text = f" Other incomplete cells at this frontier: {', '.join(siblings)}." if siblings else ""
    return (
        "Branch-stage progress frontier: next incomplete cell is "
        f"branch={branch}, stage={stage}; suggested next tool is `{tool}`. "
        "Complete this branch-local stage before downstream annotation, "
        "normalization, or shared export."
        + sibling_text
    )


def assess_candidate_branch_stage_frontier(
    *,
    steps: list[dict[str, Any]],
    statuses: list[str],
    analysis_spec: dict[str, Any] | None,
    candidate_step: dict[str, Any],
) -> dict[str, Any]:
    """Assess whether one candidate advances the current branch-stage frontier.

    Args:
        steps: Accepted plan-prefix steps.
        statuses: Execution statuses parallel to ``steps``.
        analysis_spec: Current analysis specification.
        candidate_step: Planner-proposed next executable step.

    Returns:
        JSON-compatible assessment. ``passed`` is ``False`` only when a
        branch-local evolution frontier is active and the candidate races to a
        different branch stage or downstream aggregate operation.
    """

    progress = summarize_branch_stage_progress(
        steps=steps,
        statuses=statuses,
        analysis_spec=analysis_spec,
    )
    if (
        not isinstance(progress, dict)
        or not progress
        or bool(progress.get("complete", False))
        or not bool(progress.get("started", False))
    ):
        return {"passed": True, "progress": progress}

    frontier = [
        item
        for item in progress.get("frontier", [])
        if isinstance(item, dict)
    ]
    if not frontier:
        return {"passed": True, "progress": progress}

    candidate_cell = branch_stage_cell_for_step(candidate_step)
    tool_name = str(candidate_step.get("tool_name", "") or "").strip()
    if not candidate_cell:
        if tool_name not in _DOWNSTREAM_TOOLS_REQUIRING_COMPLETE_FRONTIER:
            return {"passed": True, "progress": progress}
        observed = {"tool_name": tool_name, "branch_id": "", "stage": ""}
    else:
        observed = {
            "tool_name": tool_name,
            "branch_id": candidate_cell.branch_id,
            "stage": candidate_cell.stage,
        }

    if candidate_cell and _candidate_matches_frontier(candidate_cell, frontier):
        return {
            "passed": True,
            "progress": progress,
            "observed": observed,
        }

    expected_text = _render_frontier_cells(frontier)
    next_cell = progress.get("next_cell", {})
    expected_tool = (
        str(next_cell.get("suggested_tool", "") or "").strip()
        if isinstance(next_cell, dict)
        else ""
    )
    frontier_label = (
        "sample-stage frontier"
        if str(progress.get("analysis_type", "") or "") == "rna_seq_differential_expression"
        else "branch-stage frontier"
    )
    reason = (
        f"Candidate does not advance the current {frontier_label}. "
        f"Expected one of: {expected_text}. "
        "Observed candidate "
        f"branch={observed.get('branch_id') or '<unknown>'}, "
        f"stage={observed.get('stage') or '<unknown>'}, "
        f"tool={observed.get('tool_name') or '<unknown>'}. "
    )
    if expected_tool:
        reason += f"Expected branch-stage tool: `{expected_tool}`. "
    if str(progress.get("analysis_type", "") or "") == "rna_seq_differential_expression":
        reason += "Emit the sample-local alignment step before feature counting or DE aggregation."
    else:
        reason += (
            "Emit the branch-local frontier step before downstream annotation, "
            "normalization, or shared export."
        )
    return {
        "passed": False,
        "reason": reason,
        "progress": progress,
        "expected": frontier,
        "observed": observed,
    }


def branch_stage_cell_for_step(step: dict[str, Any]) -> BranchStageCell | None:
    """Infer the branch-stage cell produced by one candidate step.

    Args:
        step: Candidate or accepted workflow step.

    Returns:
        Inferred cell, or ``None`` when the step does not produce a tracked
        branch-local evolution stage.
    """

    if not isinstance(step, dict):
        return None
    tool_name = str(step.get("tool_name", "") or "").strip().lower()
    stage = _stage_for_step(tool_name=tool_name, step=step)
    if not stage:
        return None
    if tool_name == "subread_align":
        branch = _sample_id_for_bound_subread_step(step)
        suggested = "subread_align"
    else:
        branch = _branch_for_step(step)
        suggested = _suggested_tool(branch_id=branch, stage=stage)
    if not branch:
        return None
    return BranchStageCell(
        branch_id=branch,
        stage=stage,
        suggested_tool=suggested or tool_name,
    )


def _expected_evolution_branches(
    *,
    analysis_spec: dict[str, Any],
    completed_branches: list[str],
) -> list[str]:
    protocol = analysis_spec.get("protocol_grounding", {})
    protocol_dict = protocol if isinstance(protocol, dict) else {}
    min_variant_branches = int(protocol_dict.get("min_variant_branches", 0) or 0)
    observed_evol_numbers = [
        int(match.group(1))
        for branch in completed_branches
        for match in [re.search(r"evol(\d+)$", branch)]
        if match
    ]
    evolved_count = max([2, min_variant_branches, *observed_evol_numbers])
    branches = ["ancestor"] + [f"evol{index}" for index in range(1, evolved_count + 1)]
    extra = [branch for branch in completed_branches if branch not in branches]
    return branches + extra


def _required_stages_for_branch(branch_id: str) -> tuple[str, ...]:
    if branch_id == "ancestor":
        return tuple(stage for stage in _EVOLUTION_STAGE_ORDER if stage in _ANCESTOR_STAGES)
    return _EVOLUTION_STAGE_ORDER


def _frontier_cells(matrix: dict[str, dict[str, bool]]) -> list[BranchStageCell]:
    for stage in _EVOLUTION_STAGE_ORDER:
        cells: list[BranchStageCell] = []
        for branch, stages in matrix.items():
            if stage not in stages:
                continue
            if bool(stages.get(stage, False)):
                continue
            cells.append(
                BranchStageCell(
                    branch_id=branch,
                    stage=stage,
                    suggested_tool=_suggested_tool(branch_id=branch, stage=stage),
                )
            )
        if cells:
            return cells
    return []


def _rna_seq_de_frontier_cells(matrix: dict[str, dict[str, bool]]) -> list[BranchStageCell]:
    for stage in _RNA_SEQ_DE_STAGE_ORDER:
        cells: list[BranchStageCell] = []
        for sample_id, stages in matrix.items():
            if bool(stages.get(stage, False)):
                continue
            cells.append(
                BranchStageCell(
                    branch_id=sample_id,
                    stage=stage,
                    suggested_tool=_RNA_SEQ_DE_STAGE_TO_TOOL[stage],
                )
            )
        if cells:
            return cells
    return []


def _matrix_has_completed_cell(matrix: dict[str, dict[str, bool]]) -> bool:
    return any(
        bool(completed)
        for stages in matrix.values()
        for completed in stages.values()
    )


def _candidate_matches_frontier(
    candidate_cell: BranchStageCell,
    frontier: list[dict[str, Any]],
) -> bool:
    for cell in frontier:
        branch = str(cell.get("branch_id", "") or "").strip()
        stage = str(cell.get("stage", "") or "").strip()
        tool = str(cell.get("suggested_tool", "") or "").strip()
        if candidate_cell.branch_id != branch or candidate_cell.stage != stage:
            continue
        if tool and candidate_cell.suggested_tool != tool:
            continue
        return True
    return False


def _render_frontier_cells(frontier: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for cell in frontier[:4]:
        branch = str(cell.get("branch_id", "") or "").strip() or "<unknown>"
        stage = str(cell.get("stage", "") or "").strip() or "<unknown>"
        tool = str(cell.get("suggested_tool", "") or "").strip() or "<unknown>"
        rendered.append(f"branch={branch}, stage={stage}, tool={tool}")
    if len(frontier) > 4:
        rendered.append(f"... (+{len(frontier) - 4} more)")
    return "; ".join(rendered)


def _suggested_tool(*, branch_id: str, stage: str) -> str:
    if branch_id == "ancestor":
        return _ANCESTOR_STAGE_TO_TOOL.get(stage, "")
    return _EVOLVED_STAGE_TO_TOOL.get(stage, "")


def _completed_stages_by_branch(
    *,
    steps: list[dict[str, Any]],
    statuses: list[str],
) -> dict[str, set[str]]:
    completed: dict[str, set[str]] = {}
    for index, step in enumerate(steps):
        if index >= len(statuses) or str(statuses[index]).strip().lower() != "completed":
            continue
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        stage = _stage_for_step(tool_name=tool_name, step=step)
        if not stage:
            continue
        branch = _branch_for_step(step)
        if not branch:
            continue
        completed.setdefault(branch, set()).add(stage)
    return completed


def _stage_for_step(*, tool_name: str, step: dict[str, Any]) -> str:
    if tool_name == "bash_run":
        return _stage_for_bash_step(step)
    if tool_name == "subread_align":
        return "aligned_bam"
    return _TOOL_TO_STAGE.get(tool_name, "")


def _summarize_rna_seq_de_sample_progress(
    *,
    steps: list[dict[str, Any]],
    statuses: list[str],
    analysis_spec: dict[str, Any],
) -> dict[str, Any]:
    paths = _rna_seq_de_paths_from_analysis_spec(analysis_spec)
    if paths is None:
        return {}

    sample_ids = [Path(path).stem for path in paths.bam_paths if Path(path).stem]
    if not sample_ids:
        return {}

    completed = _completed_rna_seq_de_stages_by_sample(
        steps=steps,
        statuses=statuses,
        sample_ids=sample_ids,
    )
    matrix = {
        sample_id: {
            stage: stage in completed.get(sample_id, set())
            for stage in _RNA_SEQ_DE_STAGE_ORDER
        }
        for sample_id in sample_ids
    }
    frontier = _rna_seq_de_frontier_cells(matrix)
    next_cell = frontier[0] if frontier else None
    return {
        "analysis_type": "rna_seq_differential_expression",
        "complete": not frontier,
        "started": _matrix_has_completed_cell(matrix),
        "branches": sample_ids,
        "stage_order": list(_RNA_SEQ_DE_STAGE_ORDER),
        "matrix": matrix,
        "next_cell": next_cell.to_mapping() if next_cell else {},
        "frontier": [cell.to_mapping() for cell in frontier],
    }


def _rna_seq_de_paths_from_analysis_spec(analysis_spec: dict[str, Any]):
    selected_text = str(analysis_spec.get("selected_dir", "") or "").strip()
    data_root_text = str(analysis_spec.get("requested_data_root", "") or "").strip()
    selected_dir = Path(selected_text).expanduser() if selected_text else None
    data_root = Path(data_root_text).expanduser() if data_root_text else None
    if data_root is None and selected_dir is not None:
        data_root = _benchmark_task_data_dir(selected_dir)
    if selected_dir is None or data_root is None:
        return None
    return _build_rna_seq_de_paths(
        selected_dir=selected_dir,
        data_root=data_root,
    )


def _completed_rna_seq_de_stages_by_sample(
    *,
    steps: list[dict[str, Any]],
    statuses: list[str],
    sample_ids: list[str],
) -> dict[str, set[str]]:
    completed: dict[str, set[str]] = {}
    for index, step in enumerate(steps):
        if index >= len(statuses) or str(statuses[index]).strip().lower() != "completed":
            continue
        if str(step.get("tool_name", "") or "").strip().lower() != "subread_align":
            continue
        sample_id = _rna_seq_de_sample_for_step(step, sample_ids)
        if sample_id:
            completed.setdefault(sample_id, set()).add("aligned_bam")
    return completed


def _rna_seq_de_sample_for_step(step: dict[str, Any], sample_ids: list[str]) -> str:
    text = " ".join(
        [
            str(step.get("branch_id", "") or ""),
            str(step.get("sample_name", "") or ""),
            str(step.get("sample_id", "") or ""),
            str(step.get("objective", "") or ""),
            " ".join(_flatten_argument_strings(step.get("arguments", {}))),
        ]
    ).lower()
    for sample_id in sample_ids:
        if sample_id.lower() in text:
            return sample_id
    return ""


def _sample_id_for_bound_subread_step(step: dict[str, Any]) -> str:
    args = step.get("arguments", {})
    if not isinstance(args, dict):
        return ""
    output_bam = str(args.get("output_bam", "") or "").strip()
    if output_bam:
        return Path(output_bam).stem
    for key in ("reads_1", "reads_2"):
        value = str(args.get(key, "") or "").strip()
        if not value:
            continue
        stem = Path(value).name
        return _READ_MATE_SUFFIX_RE.sub("", stem)
    return ""


def _stage_for_bash_step(step: dict[str, Any]) -> str:
    args = step.get("arguments", {})
    command = ""
    if isinstance(args, dict):
        command = str(args.get("command", "") or "").lower()
    objective = str(step.get("objective", "") or "").lower()
    text = f"{objective} {command}"
    if "bcftools isec" in text and ("subtract" in text or "ancestor" in text or " anc" in text):
        return "ancestor_subtracted_vcf"
    if "bcftools norm" in text:
        return "normalized_vcf"
    if "snpeff" in text:
        return "annotated_vcf"
    if "vcffilter" in text or "bcftools filter" in text:
        return "filtered_vcf"
    return ""


def _branch_for_step(step: dict[str, Any]) -> str:
    fields: list[str] = [
        str(step.get("branch_id", "") or ""),
        str(step.get("sample_name", "") or ""),
        str(step.get("objective", "") or ""),
    ]
    args = step.get("arguments", {})
    if isinstance(args, dict):
        fields.extend(_flatten_argument_strings(args))
    text = " ".join(fields).lower()
    evol_matches = _EVOL_RE.findall(text)
    if evol_matches:
        return f"evol{evol_matches[-1]}"
    if _ANCESTOR_RE.search(text):
        return "ancestor"
    return ""


def _flatten_argument_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        flattened: list[str] = []
        for item in value.values():
            flattened.extend(_flatten_argument_strings(item))
        return flattened
    if isinstance(value, list):
        flattened = []
        for item in value:
            flattened.extend(_flatten_argument_strings(item))
        return flattened
    return []
