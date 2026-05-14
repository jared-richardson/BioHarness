"""Stage-aware validation and conservative preexecution repair for plans."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
import logging
import shlex
from typing import Any, Iterable, Mapping

from bio_harness.core.stage_semantics import (
    canonicalize_bash_command_for_stage_dedupe,
    classify_artifact_identity,
    classify_path_stage,
)
from bio_harness.core.tool_registry import (
    ToolRegistry,
    render_expected_output_path,
)

logger = logging.getLogger(__name__)

_VCF_LIKE_STAGES: frozenset[str] = frozenset(
    {"raw", "filtered", "subtracted", "annotated", "normalized"}
)
_TRANSITION_RELEVANT_STAGES: frozenset[str] = frozenset(
    {"aligned", "raw", "filtered", "subtracted", "annotated", "normalized", "shared", "counts", "expression", "indexed"}
)
_COMMAND_FAMILY_TOKENS: tuple[tuple[str, str], ...] = (
    ("bcftools norm", "bcftools_norm"),
    ("tabix", "tabix"),
    ("bcftools isec", "bcftools_isec"),
    ("export_shared_variants_csv.py", "export_shared_variants_csv"),
)
_PATH_TOKEN_HINTS: tuple[str, ...] = (
    ".vcf",
    ".vcf.gz",
    ".vcf.bgz",
    ".bam",
    ".csv",
    ".tsv",
    ".fasta",
    ".fa",
    ".fna",
    ".tbi",
    ".bai",
    ".csi",
)


@dataclass(frozen=True, slots=True)
class StageKey:
    """One stage-qualified artifact identity."""

    identity: str
    stage: str


@dataclass(frozen=True, slots=True)
class StageIssue:
    """One stage-DAG validation issue."""

    issue: str
    step_id: int | None = None
    stage: str = ""
    identity: str = ""
    related_step_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable view of the issue."""

        payload: dict[str, Any] = {"issue": self.issue}
        if self.step_id is not None:
            payload["step_id"] = self.step_id
        if self.stage:
            payload["stage"] = self.stage
        if self.identity:
            payload["identity"] = self.identity
        if self.related_step_id is not None:
            payload["related_step_id"] = self.related_step_id
        if self.details:
            payload.update(self.details)
        return payload


@dataclass(frozen=True, slots=True)
class StepStageInfo:
    """Resolved stage consumption and production for one plan step."""

    step_id: int
    tool_name: str
    consumes: frozenset[StageKey] = frozenset()
    produces: frozenset[StageKey] = frozenset()
    invalid_transitions: frozenset[StageKey] = frozenset()
    canonical_bash_command: str = ""
    consumed_paths: dict[StageKey, tuple[str, ...]] = field(default_factory=dict)
    produced_paths: dict[StageKey, tuple[str, ...]] = field(default_factory=dict)
    parse_failed: bool = False


@dataclass(frozen=True, slots=True)
class RepairResult:
    """Result of one single-pass stage-DAG repair attempt."""

    plan: dict[str, Any]
    repair_applied: bool
    removed_step_ids: tuple[int, ...] = ()
    moved_step_ids: tuple[int, ...] = ()
    rebinds: tuple[dict[str, Any], ...] = ()
    unresolved_issues: tuple[StageIssue, ...] = ()

    def as_sidecar(self) -> dict[str, Any]:
        """Return persisted repair provenance."""

        return {
            "repair_applied": self.repair_applied,
            "removed_step_ids": list(self.removed_step_ids),
            "moved_step_ids": list(self.moved_step_ids),
            "rebinds": list(self.rebinds),
            "unresolved_issues": [issue.as_dict() for issue in self.unresolved_issues],
        }


def _normalize_plan_steps(plan: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return a normalized list of plan steps."""

    if isinstance(plan, Mapping):
        raw_steps = plan.get("plan", [])
    else:
        raw_steps = plan
    if not isinstance(raw_steps, list):
        return []
    return [dict(step) for step in raw_steps if isinstance(step, Mapping)]


def _normalize_plan_dict(plan: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return one mutable plan dictionary."""

    if isinstance(plan, Mapping):
        payload = deepcopy(dict(plan))
        payload["plan"] = _normalize_plan_steps(plan)
        return payload
    return {"plan": _normalize_plan_steps(plan)}


def _path_stage_key(path: str) -> StageKey | None:
    """Classify one artifact path into a stage key when possible."""

    stage = classify_path_stage(path)
    identity = classify_artifact_identity(path)
    if not stage or not identity:
        return None
    return StageKey(identity=identity, stage=stage)


def _finalize_paths(bucket: dict[StageKey, set[str]]) -> dict[StageKey, tuple[str, ...]]:
    """Freeze one mutable stage-path bucket."""

    return {
        key: tuple(sorted(paths))
        for key, paths in bucket.items()
        if paths
    }


def _bash_path_tokens(command: str) -> tuple[list[str], bool]:
    """Return candidate path tokens extracted from one bash command."""

    try:
        tokens = shlex.split(command, posix=True)
    except Exception:
        return [], True
    candidates = [
        str(token).strip()
        for token in tokens
        if str(token).strip()
        and (
            classify_path_stage(str(token).strip()) is not None
            or any(str(token).strip().lower().endswith(suffix) for suffix in _PATH_TOKEN_HINTS)
        )
    ]
    return candidates, False


def _recognized_command_families(command: str) -> frozenset[str]:
    """Return recognized command families present in one bash step."""

    lowered = str(command or "").lower()
    families = {
        family_name
        for token, family_name in _COMMAND_FAMILY_TOKENS
        if token in lowered
    }
    return frozenset(families)


def _infer_bash_stage_info(
    step: Mapping[str, Any],
) -> StepStageInfo:
    """Infer stage consumption and production for one bash step."""

    step_id = int(step.get("step_id", 0) or 0)
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    command = str(args.get("command", "") or "").strip()
    canonical_command = canonicalize_bash_command_for_stage_dedupe(command)
    families = _recognized_command_families(command)
    if not families:
        return StepStageInfo(step_id=step_id, tool_name="bash_run", canonical_bash_command=canonical_command)

    tokens, parse_failed = _bash_path_tokens(command)
    if parse_failed:
        logger.warning("Stage-DAG bash parsing failed for step_id=%s; treating as stage-neutral", step_id)
        return StepStageInfo(
            step_id=step_id,
            tool_name="bash_run",
            canonical_bash_command=canonical_command,
            parse_failed=True,
        )

    consumed_paths: dict[StageKey, set[str]] = {}
    produced_paths: dict[StageKey, set[str]] = {}
    for token in tokens:
        key = _path_stage_key(token)
        if key is None:
            continue
        if "bcftools_norm" in families:
            if key.stage in _VCF_LIKE_STAGES - {"normalized"}:
                consumed_paths.setdefault(key, set()).add(token)
            if key.stage == "normalized":
                produced_paths.setdefault(key, set()).add(token)
        if "bcftools_isec" in families:
            if key.stage in _VCF_LIKE_STAGES:
                consumed_paths.setdefault(key, set()).add(token)
            if key.stage in {"subtracted", "shared"}:
                produced_paths.setdefault(key, set()).add(token)
        if "export_shared_variants_csv" in families:
            if key.stage in {"annotated", "normalized"}:
                consumed_paths.setdefault(key, set()).add(token)
            if key.stage == "shared":
                produced_paths.setdefault(key, set()).add(token)
        if "tabix" in families and key.stage in {"annotated", "normalized", "subtracted", "filtered", "raw"}:
            consumed_paths.setdefault(key, set()).add(token)

    return StepStageInfo(
        step_id=step_id,
        tool_name="bash_run",
        consumes=frozenset(consumed_paths),
        produces=frozenset(produced_paths),
        canonical_bash_command=canonical_command,
        consumed_paths=_finalize_paths(consumed_paths),
        produced_paths=_finalize_paths(produced_paths),
        parse_failed=False,
    )


def _expected_output_paths_for_step(
    step: Mapping[str, Any],
    *,
    registry: ToolRegistry | None,
    tool_name: str,
) -> list[str]:
    """Return explicit and inferred output paths for one structured tool step."""

    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    output_paths: list[str] = []
    if registry is None:
        for key, value in args.items():
            key_name = str(key or "").strip().lower()
            if not isinstance(value, str) or not value.strip():
                continue
            if key_name.startswith("output_") or key_name in {"output", "output_dir", "output_vcf", "output_bam"}:
                output_paths.append(str(value).strip())
        return output_paths
    for key in registry.output_argument_keys_for(tool_name):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            output_paths.append(str(value).strip())

    expected_by_key = registry.expected_output_files_by_key_for(tool_name)
    for key, filenames in expected_by_key.items():
        root = args.get(key)
        if not isinstance(root, str) or not root.strip():
            continue
        for relative_name in filenames:
            rendered = render_expected_output_path(
                key=key,
                output_root=str(root).strip(),
                relative_name=relative_name,
            )
            if rendered:
                output_paths.append(rendered)
    return output_paths


def infer_step_stage_info(
    step: Mapping[str, Any],
    registry: ToolRegistry | None = None,
) -> StepStageInfo:
    """Infer consumed and produced stage keys for one plan step.

    Args:
        step: Candidate step mapping.
        registry: Optional runtime tool registry. When omitted, inference stays
            suffix-based and uses only argument-name heuristics for structured
            inputs and outputs.

    Returns:
        Structured stage information for the step.
    """

    step_id = int(step.get("step_id", 0) or 0)
    tool_name = str(step.get("tool_name", "") or "").strip()
    if not tool_name:
        return StepStageInfo(step_id=step_id, tool_name="")
    if tool_name.lower() == "bash_run":
        return _infer_bash_stage_info(step)

    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    consumes_declared = set(registry.consumes_stages_for(tool_name)) if registry is not None else set()
    produces_declared = set(registry.produces_stages_for(tool_name)) if registry is not None else set()

    consumed_paths: dict[StageKey, set[str]] = {}
    produced_paths: dict[StageKey, set[str]] = {}
    invalid_transitions: set[StageKey] = set()

    input_keys = (
        registry.input_keys_for(tool_name)
        if registry is not None
        else [
            str(key).strip()
            for key, value in args.items()
            if isinstance(value, str)
            and str(value).strip()
            and (
                str(key).strip().lower().startswith("input_")
                or str(key).strip().lower() in {"input", "input_vcf", "input_bam", "reference_fasta", "annotation_gff"}
            )
        ]
    )
    for key in input_keys:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        stage_key = _path_stage_key(value)
        if stage_key is None:
            continue
        if consumes_declared and stage_key.stage not in consumes_declared:
            if stage_key.stage in _TRANSITION_RELEVANT_STAGES:
                invalid_transitions.add(stage_key)
            continue
        consumed_paths.setdefault(stage_key, set()).add(str(value).strip())

    for output_path in _expected_output_paths_for_step(step, registry=registry, tool_name=tool_name):
        stage_key = _path_stage_key(output_path)
        if stage_key is None:
            continue
        if produces_declared and stage_key.stage not in produces_declared:
            continue
        produced_paths.setdefault(stage_key, set()).add(output_path)

    return StepStageInfo(
        step_id=step_id,
        tool_name=tool_name,
        consumes=frozenset(consumed_paths),
        produces=frozenset(produced_paths),
        invalid_transitions=frozenset(invalid_transitions),
        consumed_paths=_finalize_paths(consumed_paths),
        produced_paths=_finalize_paths(produced_paths),
    )


def _latest_producer_indices(step_infos: list[StepStageInfo]) -> dict[StageKey, int]:
    """Return the latest producer index for each stage key."""

    latest: dict[StageKey, int] = {}
    for index, info in enumerate(step_infos):
        for key in info.produces:
            latest[key] = index
    return latest


def _find_cycle_nodes(adjacency: dict[int, set[int]]) -> list[list[int]]:
    """Return cycle node groups from one adjacency map."""

    visited: set[int] = set()
    active: set[int] = set()
    stack: list[int] = []
    cycles: list[list[int]] = []

    def visit(node: int) -> None:
        if node in active:
            if node in stack:
                start = stack.index(node)
                cycles.append(stack[start:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        active.add(node)
        stack.append(node)
        for target in adjacency.get(node, set()):
            visit(target)
        stack.pop()
        active.remove(node)

    for node in sorted(adjacency):
        visit(node)
    return cycles


def _dependency_edges(step_infos: list[StepStageInfo]) -> tuple[dict[int, set[int]], list[StageIssue]]:
    """Build stable dependency edges and report any cycles."""

    latest_producer = _latest_producer_indices(step_infos)
    adjacency: dict[int, set[int]] = {index: set() for index in range(len(step_infos))}
    for consumer_index, info in enumerate(step_infos):
        for key in info.consumes:
            if key in info.produces:
                continue
            producer_index = latest_producer.get(key)
            if producer_index is None or producer_index == consumer_index:
                continue
            adjacency[producer_index].add(consumer_index)

    cycles = _find_cycle_nodes(adjacency)
    issues = [
        StageIssue(
            issue="cycle_detected",
            step_id=step_infos[cycle[0]].step_id if cycle else None,
            details={"cycle_step_ids": [step_infos[index].step_id for index in cycle]},
        )
        for cycle in cycles
        if cycle
    ]
    return adjacency, issues


def validate_stage_dag(
    plan: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    registry: ToolRegistry | None = None,
) -> list[StageIssue]:
    """Validate stage producer/consumer ordering for one plan.

    Args:
        plan: Plan dictionary or direct list of step mappings.
        registry: Optional runtime tool registry.

    Returns:
        Stable list of stage-DAG issues.
    """

    steps = _normalize_plan_steps(plan)
    step_infos = [infer_step_stage_info(step, registry) for step in steps]
    issues: list[StageIssue] = []

    first_duplicate_by_command: dict[str, int] = {}
    for index, info in enumerate(step_infos):
        if info.tool_name != "bash_run" or not info.canonical_bash_command:
            continue
        first_index = first_duplicate_by_command.setdefault(info.canonical_bash_command, index)
        if first_index != index:
            issues.append(
                StageIssue(
                    issue="duplicate_equivalent_step",
                    step_id=info.step_id,
                    related_step_id=step_infos[first_index].step_id,
                )
            )

    producer_indices_by_key: dict[StageKey, list[int]] = defaultdict(list)
    for index, info in enumerate(step_infos):
        for key in info.produces:
            producer_indices_by_key[key].append(index)
    produced_stages = {key.stage for key in producer_indices_by_key}

    for index, info in enumerate(step_infos):
        for key in sorted(info.invalid_transitions, key=lambda item: (item.identity, item.stage)):
            issues.append(
                StageIssue(
                    issue="invalid_stage_transition",
                    step_id=info.step_id,
                    stage=key.stage,
                    identity=key.identity,
                )
            )
        for key in sorted(info.consumes, key=lambda item: (item.identity, item.stage)):
            if key in info.produces:
                continue
            producer_indices = producer_indices_by_key.get(key, [])
            earlier = [producer for producer in producer_indices if producer < index]
            later = [producer for producer in producer_indices if producer > index]
            if earlier:
                continue
            if later:
                latest_later = later[-1]
                issues.append(
                    StageIssue(
                        issue="consumer_before_producer",
                        step_id=info.step_id,
                        stage=key.stage,
                        identity=key.identity,
                        related_step_id=step_infos[latest_later].step_id,
                    )
                )
            else:
                if key.stage in produced_stages:
                    issues.append(
                        StageIssue(
                            issue="missing_stage_producer",
                            step_id=info.step_id,
                            stage=key.stage,
                            identity=key.identity,
                        )
                    )

    _adjacency, cycle_issues = _dependency_edges(step_infos)
    issues.extend(cycle_issues)
    return sorted(
        issues,
        key=lambda issue: (
            issue.step_id if issue.step_id is not None else -1,
            issue.issue,
            issue.identity,
            issue.stage,
            issue.related_step_id if issue.related_step_id is not None else -1,
        ),
    )


def _dedupe_bash_steps(
    steps: list[dict[str, Any]],
    step_infos: list[StepStageInfo],
) -> tuple[list[dict[str, Any]], tuple[int, ...]]:
    """Remove safe duplicate bash steps while preserving first occurrences."""

    first_index_by_command: dict[str, int] = {}
    removable_indices: set[int] = set()
    for index, info in enumerate(step_infos):
        if info.tool_name != "bash_run" or not info.canonical_bash_command:
            continue
        first_index = first_index_by_command.setdefault(info.canonical_bash_command, index)
        if first_index == index:
            continue
        if not info.produces:
            continue
        earlier_info = step_infos[first_index]
        if not info.produces.issubset(earlier_info.produces):
            continue
        removable_indices.add(index)

    removed_ids = tuple(
        int(steps[index].get("step_id", 0) or 0)
        for index in sorted(removable_indices)
    )
    deduped_steps = [
        deepcopy(step)
        for index, step in enumerate(steps)
        if index not in removable_indices
    ]
    return deduped_steps, removed_ids


def _stable_topological_reorder(
    steps: list[dict[str, Any]],
    step_infos: list[StepStageInfo],
) -> tuple[list[dict[str, Any]], tuple[int, ...], tuple[StageIssue, ...]]:
    """Perform one stable topological reorder when possible."""

    adjacency, cycle_issues = _dependency_edges(step_infos)
    if cycle_issues:
        return [deepcopy(step) for step in steps], (), tuple(cycle_issues)

    indegree = {index: 0 for index in range(len(steps))}
    for sources in adjacency.values():
        for target in sources:
            indegree[target] += 1

    ready = sorted(index for index, degree in indegree.items() if degree == 0)
    ordered: list[int] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(adjacency.get(current, set())):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()

    if len(ordered) != len(steps):
        remaining = [
            step_infos[index].step_id
            for index in range(len(steps))
            if index not in set(ordered)
        ]
        return [deepcopy(step) for step in steps], (), (
            StageIssue(
                issue="cycle_detected",
                details={"cycle_step_ids": remaining},
            ),
        )

    reordered_steps = [deepcopy(steps[index]) for index in ordered]
    moved_ids = tuple(
        int(steps[index].get("step_id", 0) or 0)
        for new_index, index in enumerate(ordered)
        if new_index != index
    )
    return reordered_steps, moved_ids, ()


def _producer_paths_by_key(step_infos: list[StepStageInfo]) -> dict[StageKey, list[tuple[int, str]]]:
    """Return all concrete produced paths grouped by stage key."""

    producers: dict[StageKey, list[tuple[int, str]]] = defaultdict(list)
    for info in step_infos:
        for key, paths in info.produced_paths.items():
            for path in paths:
                producers[key].append((info.step_id, path))
    return producers


def _structured_rebinds(
    steps: list[dict[str, Any]],
    *,
    registry: ToolRegistry,
) -> tuple[list[dict[str, Any]], tuple[dict[str, Any], ...]]:
    """Rebind non-bash structured path arguments by whole-value replacement."""

    rebound_steps = [deepcopy(step) for step in steps]
    step_infos = [infer_step_stage_info(step, registry) for step in rebound_steps]
    producer_paths = _producer_paths_by_key(step_infos)
    rebinds: list[dict[str, Any]] = []

    for index, step in enumerate(rebound_steps):
        tool_name = str(step.get("tool_name", "") or "").strip()
        if not tool_name or tool_name.lower() == "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
        if not isinstance(args, dict):
            continue
        for arg_name in registry.input_keys_for(tool_name):
            value = args.get(arg_name)
            if not isinstance(value, str) or not value.strip():
                continue
            key = _path_stage_key(value)
            if key is None:
                continue
            candidates = producer_paths.get(key, [])
            unique_paths = sorted({path for _step_id, path in candidates})
            if len(unique_paths) != 1:
                continue
            replacement = unique_paths[0]
            if replacement == value:
                continue
            args[arg_name] = replacement
            rebound_steps[index]["arguments"] = args
            producer_step_id = candidates[0][0]
            rebinds.append(
                {
                    "step_id": int(step.get("step_id", 0) or 0),
                    "argument": arg_name,
                    "old_value": value,
                    "new_value": replacement,
                    "stage": key.stage,
                    "identity": key.identity,
                    "producer_step_id": producer_step_id,
                }
            )
    return rebound_steps, tuple(rebinds)


def repair_stage_dag(
    plan: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    registry: ToolRegistry | None = None,
) -> RepairResult:
    """Apply one conservative stage-DAG repair pass.

    Args:
        plan: Plan dictionary or direct list of step mappings.
        registry: Optional runtime tool registry.

    Returns:
        Repair result with the repaired plan and any unresolved issues.
    """

    repaired_plan = _normalize_plan_dict(plan)
    steps = repaired_plan.get("plan", [])
    if not isinstance(steps, list):
        steps = []
        repaired_plan["plan"] = steps

    step_infos = [infer_step_stage_info(step, registry) for step in steps]
    steps, removed_step_ids = _dedupe_bash_steps(steps, step_infos)

    step_infos = [infer_step_stage_info(step, registry) for step in steps]
    steps, moved_step_ids, cycle_issues = _stable_topological_reorder(steps, step_infos)

    if registry is not None:
        steps, rebinds = _structured_rebinds(steps, registry=registry)
    else:
        rebinds = ()
    repaired_plan["plan"] = steps

    unresolved = tuple(validate_stage_dag(repaired_plan, registry=registry))
    if cycle_issues:
        combined: list[StageIssue] = list(unresolved)
        for cycle_issue in cycle_issues:
            if cycle_issue not in combined:
                combined.append(cycle_issue)
        unresolved = tuple(
            sorted(
                combined,
                key=lambda issue: (
                    issue.step_id if issue.step_id is not None else -1,
                    issue.issue,
                    issue.identity,
                    issue.stage,
                ),
            )
        )

    repair_applied = bool(removed_step_ids or moved_step_ids or rebinds)
    return RepairResult(
        plan=repaired_plan,
        repair_applied=repair_applied,
        removed_step_ids=removed_step_ids,
        moved_step_ids=moved_step_ids,
        rebinds=rebinds,
        unresolved_issues=unresolved,
    )


__all__ = [
    "RepairResult",
    "StageIssue",
    "StageKey",
    "StepStageInfo",
    "infer_step_stage_info",
    "repair_stage_dag",
    "validate_stage_dag",
]
