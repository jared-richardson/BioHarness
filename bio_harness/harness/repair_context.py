"""Focused repair context helpers for model-driven plan repair.

This module builds compact, evidence-backed context packets for repair prompts.
The packets are designed to help weaker local models by narrowing attention to
the failing step or local subgraph, while still preserving task-level protocol
and semantic constraints.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.metaharness_flags import (
    diagnostic_traces_enabled,
    nonmarkovian_repair_enabled,
    trace_advisories_enabled,
)
from bio_harness.core.protocol_grounding._shared import _discover_fastq_pairs
from bio_harness.core.scientific_tool_catalog import load_scientific_tool_catalog, scientific_tool_index
from bio_harness.workflows.template_io_base import (
    bash_output_hints_for_command,
    parse_path_tokens,
    structured_output_hints_for_step,
)


REPAIR_ADVISORIES_PATH = Path(__file__).with_name("repair_advisories.json")
_INPUT_KEY_TOKENS = (
    "input",
    "reads",
    "read",
    "reference",
    "annotation",
    "gtf",
    "gff",
    "fasta",
    "fastq",
    "vcf",
    "bam",
    "matrix",
    "metadata",
)
_OUTPUT_KEY_TOKENS = (
    "output",
    "out",
    "report",
    "results",
    "counts",
    "csv",
    "tsv",
    "vcf",
    "bam",
    "dir",
    "tree",
)
_LISTISH_PROTOCOL_KEYS = (
    "required_tools",
    "preferred_tools",
    "required_plan_signals",
    "missing_plan_signals",
    "required_output_signals",
)
_TRACE_TEXT_LIMIT = 8192
_RUN_STREAM_LINE_LIMIT = 200
_INPUT_LISTING_FILE_LIMIT = 20
_INPUT_LISTING_CHAR_LIMIT = 4096
_REPAIR_HISTORY_TEXT_LIMIT = 240
_REPAIR_HISTORY_DETAILS_LIMIT = 8
_SELECTED_DIR_HINT_LIMIT = 6
_PREV_COMPLETION_KEYS = (
    "tool_name",
    "success",
    "exit_code",
    "outputs",
    "output_paths",
    "output_dir",
    "step_id",
)
_ARTIFACT_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ARTIFACT_BRANCH_ALIAS_RE = re.compile(r"(?<![a-z0-9])(anc|ancestor)(?![a-z0-9])")
_ARTIFACT_BRANCH_INDEX_RE = re.compile(r"(?:evol(?:ved)?|isolate|mutant)[^0-9]*(\d+)")
_ARTIFACT_STAGE_TOKEN_MAP = {
    "annotate": "annotated",
    "annotated": "annotated",
    "annotation": "annotated",
    "ann": "annotated",
    "call": "raw",
    "caller": "raw",
    "calling": "raw",
    "contig": "contigs",
    "contigs": "contigs",
    "filter": "filtered",
    "filtered": "filtered",
    "freebayes": "raw",
    "haplotypecaller": "raw",
    "isec": "subtracted",
    "minus": "subtracted",
    "normalize": "normalized",
    "normalized": "normalized",
    "raw": "raw",
    "scaffold": "scaffolds",
    "scaffolds": "scaffolds",
    "shared": "shared",
    "subtract": "subtracted",
    "subtracted": "subtracted",
    "vcf": "vcf",
}
_ARTIFACT_IDENTITY_SKIP_TOKENS = frozenset(
    {
        "aligned",
        "align",
        "alignment",
        "alignments",
        "bam",
        "call",
        "caller",
        "calling",
        "count",
        "counts",
        "featurecounts",
        "filtered",
        "normalize",
        "normalized",
        "out",
        "raw",
        "report",
        "results",
        "sorted",
        "star",
        "subread",
        "subtract",
        "subtracted",
        "sortedbycoord",
        "table",
    }
)


def load_repair_advisories(path: Path | None = None) -> dict[str, Any]:
    """Load repair advisories from disk.

    Args:
        path: Optional override path. Defaults to the bundled catalog.

    Returns:
        Parsed advisory catalog with stable top-level keys.
    """
    source = path or REPAIR_ADVISORIES_PATH
    if not source.is_file():
        return {"version": 1, "analysis_advisories": {}, "tool_advisories": {}}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "analysis_advisories": {}, "tool_advisories": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "analysis_advisories": {}, "tool_advisories": {}}
    payload.setdefault("version", 1)
    payload["analysis_advisories"] = (
        dict(payload.get("analysis_advisories", {}))
        if isinstance(payload.get("analysis_advisories", {}), dict)
        else {}
    )
    payload["tool_advisories"] = (
        dict(payload.get("tool_advisories", {}))
        if isinstance(payload.get("tool_advisories", {}), dict)
        else {}
    )
    return payload


def save_repair_advisories(catalog: Mapping[str, Any], path: Path | None = None) -> Path:
    """Persist a repair advisory catalog.

    Args:
        catalog: Advisory payload to write.
        path: Optional override output path.

    Returns:
        The path written to disk.
    """
    destination = path or REPAIR_ADVISORIES_PATH
    normalized = load_repair_advisories(destination)
    if isinstance(catalog, Mapping):
        normalized.update({k: v for k, v in catalog.items()})
    destination.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def upsert_repair_advisory(
    catalog: Mapping[str, Any],
    *,
    scope: str,
    name: str,
    summary: str = "",
    repair_hints: list[str] | None = None,
    avoid_patterns: list[str] | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    """Upsert one advisory entry.

    Args:
        catalog: Existing advisory catalog.
        scope: Either ``analysis`` or ``tool``.
        name: Analysis type or tool name.
        summary: Short advisory summary.
        repair_hints: Concrete hints to guide repair.
        avoid_patterns: Known anti-patterns to avoid.
        source: Provenance label for the entry.

    Returns:
        Updated advisory catalog.

    Raises:
        ValueError: If *scope* or *name* is invalid.
    """
    scope_map = {
        "analysis": "analysis_advisories",
        "analysis_advisories": "analysis_advisories",
        "tool": "tool_advisories",
        "tool_advisories": "tool_advisories",
    }
    target_key = scope_map.get(str(scope).strip().lower(), "")
    entry_name = str(name).strip()
    if not target_key or not entry_name:
        raise ValueError("scope must be 'analysis' or 'tool', and name must be non-empty")

    updated = {"version": 1, "analysis_advisories": {}, "tool_advisories": {}}
    if isinstance(catalog, Mapping):
        updated.update({k: v for k, v in catalog.items()})
    updated["analysis_advisories"] = (
        dict(updated.get("analysis_advisories", {}))
        if isinstance(updated.get("analysis_advisories", {}), Mapping)
        else {}
    )
    updated["tool_advisories"] = (
        dict(updated.get("tool_advisories", {}))
        if isinstance(updated.get("tool_advisories", {}), Mapping)
        else {}
    )

    target = dict(updated.get(target_key, {}))
    existing = dict(target.get(entry_name, {})) if isinstance(target.get(entry_name, {}), dict) else {}
    existing["summary"] = str(summary).strip() or str(existing.get("summary", "")).strip()
    existing["repair_hints"] = _dedupe_strings(repair_hints or existing.get("repair_hints", []))
    existing["avoid_patterns"] = _dedupe_strings(avoid_patterns or existing.get("avoid_patterns", []))
    existing["source"] = str(source).strip() or str(existing.get("source", "")).strip()
    target[entry_name] = existing
    updated[target_key] = dict(sorted(target.items(), key=lambda item: item[0]))
    return updated


def build_repair_context(
    *,
    run: Mapping[str, Any],
    selected_dir: Path,
    data_root: Path | None = None,
    available_skills: list[Mapping[str, Any]] | None = None,
    failure_class: str,
    reason: str,
    validation: Mapping[str, Any] | None = None,
    focus_mode: str = "step_local",
) -> dict[str, Any]:
    """Build a focused repair packet for prompt conditioning.

    Args:
        run: Current harness run dictionary.
        selected_dir: Selected task output directory.
        data_root: Optional readonly benchmark/task input root.
        available_skills: Skill metadata visible to the planner.
        failure_class: Failure class driving the repair.
        reason: Human-readable failure reason.
        validation: Optional validation payload for the failed check.
        focus_mode: One of ``step_local``, ``subgraph_local``, or ``full_plan``.

    Returns:
        Compact structured repair context suitable for prompt inclusion.
    """
    plan_payload = run.get("final_plan", {})
    if not (isinstance(plan_payload, Mapping) and "plan" in plan_payload and isinstance(plan_payload.get("plan"), list)):
        plan_payload = run.get("plan", {})
    plan = plan_payload if isinstance(plan_payload, Mapping) else {}
    steps = plan.get("plan", []) if isinstance(plan.get("plan", []), list) else []
    analysis_spec = run.get("analysis_spec", {}) if isinstance(run.get("analysis_spec", {}), Mapping) else {}
    contract = run.get("plan_contract", {}) if isinstance(run.get("plan_contract", {}), Mapping) else {}
    step_statuses = list(run.get("step_statuses", [])) if isinstance(run.get("step_statuses", []), list) else []
    raw_next_step_idx = run.get("next_step_idx", None)
    next_step_idx = int(raw_next_step_idx) if raw_next_step_idx is not None else -1
    failed_step_number = _first_failed_step_number(step_statuses, next_step_idx, len(steps))
    if failed_step_number <= 0:
        raw_current_step_index = run.get("current_step_index", None)
        current_step_index = int(raw_current_step_index) if raw_current_step_index is not None else -1
        if 0 <= current_step_index < len(steps):
            failed_step_number = current_step_index + 1
    failed_step_index = failed_step_number - 1 if failed_step_number > 0 else -1
    focus_indices = _focus_indices(focus_mode, failed_step_number, len(steps))
    selected_dir_resolved = selected_dir.resolve(strict=False)

    focus_steps = [
        _summarize_step(
            step,
            step_number=index + 1,
            selected_dir=selected_dir_resolved,
        )
        for index, step in enumerate(steps)
        if index in focus_indices
    ]
    relevant_tools = _dedupe_strings(step.get("tool_name", "") for step in focus_steps)
    advisories = load_repair_advisories() if trace_advisories_enabled() else {}
    analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip()
    protocol_grounding = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec.get("protocol_grounding", {}), Mapping)
        else {}
    )
    selected_dir_producer_hints = _selected_dir_producer_hints(
        steps=steps,
        selected_dir=selected_dir_resolved,
        data_root=data_root.resolve(strict=False) if isinstance(data_root, Path) else None,
        validation=validation or {},
    )
    context = {
        "focus_mode": focus_mode,
        "focus_instruction": _focus_instruction(focus_mode, failed_step_number),
        "failure_class": str(failure_class).strip(),
        "failure_reason": str(reason).strip(),
        "failed_step_number": int(failed_step_number),
        "failure_signatures": _dedupe_strings(run.get("failure_signatures", [])),
        "focus_steps": focus_steps,
        "analysis_summary": {
            "analysis_type": analysis_type,
            "chosen_method": str(analysis_spec.get("chosen_method", "") or "").strip(),
            "preferred_tools": _dedupe_strings(analysis_spec.get("preferred_tools", [])),
            "discouraged_tools": _dedupe_strings(analysis_spec.get("discouraged_tools", [])),
            "acceptance_checks": _dedupe_strings(analysis_spec.get("acceptance_checks", []))[:6],
            "rerun_triggers": _dedupe_strings(analysis_spec.get("rerun_triggers", []))[:6],
        },
        "contract_summary": {
            "must_include_capabilities": _dedupe_strings(contract.get("must_include_capabilities", [])),
            "required_tool_hints": _dedupe_strings(contract.get("required_tool_hints", [])),
            "missing_capabilities": _dedupe_strings((validation or {}).get("missing_capabilities", [])),
            "missing_required_tool_hints": _dedupe_strings((validation or {}).get("missing_required_tool_hints", [])),
            "missing_tool_hints": _dedupe_strings((validation or {}).get("missing_tool_hints", [])),
            "direct_wrapper_issues": _dedupe_strings((validation or {}).get("direct_wrapper_issues", []))[:10],
            "artifact_role_issues": _dedupe_strings((validation or {}).get("artifact_role_issues", []))[:10],
            "selected_dir_producer_hints": selected_dir_producer_hints,
        },
        "protocol_summary": {
            key: _dedupe_strings(protocol_grounding.get(key, []))[:10]
            for key in _LISTISH_PROTOCOL_KEYS
            if protocol_grounding.get(key)
        },
        "validation_summary": _compact_validation(validation or {}),
        "parameter_hints": _parameter_hints_for_tools(analysis_spec, relevant_tools),
        "tool_knowledge": _tool_knowledge(
            relevant_tools,
            available_skills or [],
            advisories=advisories,
        ),
        "analysis_advisory": _analysis_advisory(analysis_type, advisories),
    }
    if diagnostic_traces_enabled():
        context["diagnostic_traces"] = _diagnostic_traces(
            run=run,
            selected_dir=selected_dir_resolved,
            steps=steps,
            failed_step_index=failed_step_index,
        )
    else:
        context["diagnostic_traces"] = {}
    if nonmarkovian_repair_enabled():
        prior_repair_attempts = _prior_repair_attempts(run)
        if prior_repair_attempts:
            context["prior_repair_attempts"] = prior_repair_attempts

    return context


def _selected_dir_producer_hints(
    *,
    steps: list[Any],
    selected_dir: Path,
    data_root: Path | None,
    validation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Summarize missing selected-dir producers with nearby upstream evidence.

    Args:
        steps: Candidate plan steps.
        selected_dir: Active selected output directory.
        validation: Latest contract-validation payload.

    Returns:
        Compact, branch-aware missing-producer hints for prompt conditioning.
    """

    issues = _selected_dir_missing_producer_issues(validation, selected_dir)
    if not issues:
        return []
    output_catalog = _planned_output_catalog(steps, selected_dir)
    readonly_fastq_identities = _readonly_fastq_identities(data_root)
    grouped_issues: dict[str, list[dict[str, str]]] = {}
    for issue in issues:
        grouped_issues.setdefault(issue["consumer"], []).append(issue)
    hints: list[dict[str, Any]] = []
    for consumer, consumer_issues in list(grouped_issues.items())[:_SELECTED_DIR_HINT_LIMIT]:
        missing_path = consumer_issues[0]["path"]
        missing_profile = _artifact_path_profile(missing_path)
        consumer_step_number = _consumer_step_number(steps, consumer)
        candidate_catalog = [
            entry
            for entry in output_catalog
            if consumer_step_number <= 0 or int(entry.get("step_number", 0)) < consumer_step_number
        ]
        nearest_outputs = _aggregate_nearest_outputs(
            issues=consumer_issues,
            output_catalog=candidate_catalog,
        )
        requested_identities = _dedupe_strings(
            _artifact_path_profile(issue["path"]).get("identity", "")
            for issue in consumer_issues
        )
        family = str(missing_profile.get("family", "") or "")
        produced_identities = _produced_identities_for_family(
            candidate_catalog,
            artifact_family=family,
        )
        missing_identities = [
            identity
            for identity in requested_identities
            if identity not in set(produced_identities)
        ]
        hints.append(
            {
                "consumer": consumer,
                "missing_input": _display_selected_path(missing_path, selected_dir),
                "branch_hint": missing_profile["branch"],
                "artifact_family": missing_profile["family"],
                "requested_identity_count": len(requested_identities),
                "planned_producer_identity_count": len(produced_identities),
                "requested_identities": requested_identities,
                "planned_producer_identities": produced_identities,
                "missing_identities": missing_identities,
                "readonly_fastq_identities": [
                    identity
                    for identity in readonly_fastq_identities
                    if identity in set(requested_identities)
                ],
                "nearest_upstream_outputs": [
                    {
                        "step_number": int(entry["step_number"]),
                        "tool_name": entry["tool_name"],
                        "path": entry["path_display"],
                    }
                    for entry in nearest_outputs
                ],
                "repair_instruction": _selected_dir_repair_instruction(
                    consumer=consumer,
                    missing_display=_display_selected_path(missing_path, selected_dir),
                    missing_profile=missing_profile,
                    issue_count=len(consumer_issues),
                    requested_identities=requested_identities,
                    produced_identities=produced_identities,
                    missing_identities=missing_identities,
                    readonly_fastq_identities=readonly_fastq_identities,
                    nearest_outputs=nearest_outputs,
                ),
            }
        )
    return hints


def _consumer_step_number(steps: list[Any], consumer: str) -> int:
    """Return the first matching step number for one ``tool.argument`` consumer."""

    tool_name, _, param_name = str(consumer or "").partition(".")
    tool_token = tool_name.strip()
    param_token = param_name.strip()
    if not (tool_token and param_token):
        return -1
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, Mapping):
            continue
        if str(step.get("tool_name", "") or "").strip() != tool_token:
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
        if param_token in args:
            return index
    return -1


def _aggregate_nearest_outputs(
    *,
    issues: list[dict[str, str]],
    output_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the nearest unique upstream outputs across one issue group."""

    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    seen: set[tuple[int, str]] = set()
    for issue in issues:
        missing_path = issue["path"]
        missing_profile = _artifact_path_profile(missing_path)
        for entry in _rank_output_candidates(
            missing_path=missing_path,
            missing_profile=missing_profile,
            output_catalog=output_catalog,
        ):
            step_number = int(entry.get("step_number", 0))
            path_display = str(entry.get("path_display", "") or "")
            key = (step_number, path_display)
            if key in seen:
                continue
            seen.add(key)
            score = _output_similarity_score(
                missing_path=missing_path,
                missing_profile=missing_profile,
                candidate_path=str(entry.get("path", "") or ""),
                candidate_profile=entry.get("profile", {}) if isinstance(entry.get("profile", {}), Mapping) else {},
            )
            scored.append((score, step_number, path_display, entry))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [entry for _, _, _, entry in scored[:3]]


def _produced_identities_for_family(
    output_catalog: list[dict[str, Any]],
    *,
    artifact_family: str,
) -> list[str]:
    """Return produced artifact identities for one output family."""

    identities: list[str] = []
    for entry in output_catalog:
        profile = entry.get("profile", {}) if isinstance(entry.get("profile", {}), Mapping) else {}
        if str(profile.get("family", "") or "") != str(artifact_family or ""):
            continue
        identity = str(profile.get("identity", "") or "")
        if identity:
            identities.append(identity)
    return _dedupe_strings(identities)


def _readonly_fastq_identities(data_root: Path | None) -> list[str]:
    """Return sample identities discovered from readonly FASTQ pairs."""

    if data_root is None:
        return []
    try:
        pair_map = _discover_fastq_pairs(data_root)
    except Exception:
        return []
    return _dedupe_strings(_artifact_identity(label) for label in pair_map.keys())


def _selected_dir_missing_producer_issues(
    validation: Mapping[str, Any],
    selected_dir: Path,
) -> list[dict[str, str]]:
    """Return selected-dir missing-producer issues from one validation payload."""

    selected_root = selected_dir.resolve(strict=False)
    issues: list[dict[str, str]] = []
    for raw_issue in _dedupe_strings(validation.get("artifact_role_issues", [])):
        consumer, violation_type, path_text = _parse_artifact_issue(raw_issue)
        if violation_type != "input_in_selected_dir_without_producer":
            continue
        normalized = _normalize_selected_path(path_text, selected_root)
        if not normalized or not _path_is_within(normalized, selected_root):
            continue
        issues.append(
            {
                "consumer": consumer,
                "path": normalized,
            }
        )
    return issues


def _parse_artifact_issue(issue: str) -> tuple[str, str, str]:
    """Split one stable artifact-role issue string into its parts."""

    head, sep, remainder = str(issue or "").partition(":")
    if not sep:
        return "", "", ""
    violation_type, sep, path_text = remainder.partition(":")
    if not sep:
        return head.strip(), violation_type.strip(), ""
    return head.strip(), violation_type.strip(), path_text.strip()


def _planned_output_catalog(steps: list[Any], selected_dir: Path) -> list[dict[str, Any]]:
    """Return selected-dir outputs emitted by the candidate plan."""

    selected_root = selected_dir.resolve(strict=False)
    catalog: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, Mapping):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip()
        if not tool_name:
            continue
        for output_path in _step_output_paths(step, selected_root):
            normalized = _normalize_selected_path(output_path, selected_root)
            if not normalized or not _path_is_within(normalized, selected_root):
                continue
            key = (index, tool_name, normalized)
            if key in seen:
                continue
            seen.add(key)
            catalog.append(
                {
                    "step_number": index,
                    "tool_name": tool_name,
                    "path": normalized,
                    "path_display": _display_selected_path(normalized, selected_root),
                    "profile": _artifact_path_profile(normalized),
                }
            )
    return catalog


def _step_output_paths(step: Mapping[str, Any], selected_dir: Path) -> list[str]:
    """Return deterministic output paths hinted by one step."""

    tool_name = str(step.get("tool_name", "") or "").strip()
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    hinted_paths: list[str] = []
    if tool_name == "bash_run":
        command = str(args.get("command", "") or "").strip()
        output_paths, _output_roots = bash_output_hints_for_command(command)
        hinted_paths.extend(str(path) for path in output_paths)
    else:
        output_paths, _output_roots = structured_output_hints_for_step(tool_name, dict(args))
        hinted_paths.extend(str(path) for path in output_paths)
        if tool_name in {"star_align", "star_2pass_align"}:
            output_prefix = str(args.get("output_prefix", "") or "").strip()
            if output_prefix:
                hinted_paths.append(f"{output_prefix}Aligned.out.bam")
                hinted_paths.append(f"{output_prefix}Aligned.sortedByCoord.out.bam")
    if not hinted_paths:
        hinted_paths.extend(_collect_argument_paths(args, output=True))
    return _dedupe_strings(_normalize_step_output_paths(hinted_paths, selected_dir))


def _normalize_step_output_paths(paths: list[str], selected_dir: Path) -> list[str]:
    """Normalize step output hints while preserving relative selected-dir paths."""

    normalized: list[str] = []
    selected_root = selected_dir.resolve(strict=False)
    for raw_path in paths:
        rendered = str(raw_path or "").strip()
        if not rendered:
            continue
        candidate = Path(rendered).expanduser()
        if candidate.is_absolute():
            normalized.append(str(candidate.resolve(strict=False)))
            continue
        normalized.append(str((selected_root / candidate).resolve(strict=False)))
    return normalized


def _rank_output_candidates(
    *,
    missing_path: str,
    missing_profile: Mapping[str, Any],
    output_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the nearest upstream output candidates for one missing path."""

    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in output_catalog:
        profile = entry.get("profile", {})
        score = _output_similarity_score(
            missing_path=missing_path,
            missing_profile=missing_profile,
            candidate_path=str(entry.get("path", "") or ""),
            candidate_profile=profile if isinstance(profile, Mapping) else {},
        )
        if score <= 0:
            continue
        scored.append((score, entry))
    scored.sort(
        key=lambda item: (
            -item[0],
            int(item[1].get("step_number", 0)),
            str(item[1].get("path_display", "")),
        )
    )
    return [entry for _, entry in scored[:3]]


def _output_similarity_score(
    *,
    missing_path: str,
    missing_profile: Mapping[str, Any],
    candidate_path: str,
    candidate_profile: Mapping[str, Any],
) -> int:
    """Return a lexical similarity score between missing and produced artifacts."""

    if not missing_path or not candidate_path:
        return 0
    score = 0
    if str(missing_profile.get("family", "")) and missing_profile.get("family") == candidate_profile.get("family"):
        score += 8
    missing_branch = str(missing_profile.get("branch", "") or "")
    candidate_branch = str(candidate_profile.get("branch", "") or "")
    if missing_branch and candidate_branch:
        score += 8 if missing_branch == candidate_branch else -6
    missing_name = Path(missing_path).name.lower()
    candidate_name = Path(candidate_path).name.lower()
    if missing_name == candidate_name:
        score += 12
    if _file_suffix(missing_path) == _file_suffix(candidate_path):
        score += 6
    missing_tokens = set(missing_profile.get("tokens", []))
    candidate_tokens = set(candidate_profile.get("tokens", []))
    shared_tokens = missing_tokens & candidate_tokens
    score += min(12, 2 * len(shared_tokens))
    missing_stage_tokens = set(missing_profile.get("stage_tokens", []))
    candidate_stage_tokens = set(candidate_profile.get("stage_tokens", []))
    score += min(9, 3 * len(missing_stage_tokens & candidate_stage_tokens))
    if Path(missing_path).parent.name.lower() == Path(candidate_path).parent.name.lower():
        score += 2
    return score


def _selected_dir_repair_instruction(
    *,
    consumer: str,
    missing_display: str,
    missing_profile: Mapping[str, Any],
    issue_count: int,
    requested_identities: list[str],
    produced_identities: list[str],
    missing_identities: list[str],
    readonly_fastq_identities: list[str],
    nearest_outputs: list[dict[str, Any]],
) -> str:
    """Render one concise repair instruction for a missing selected-dir input."""

    base = (
        f"No earlier step emits `{missing_display}`. Reuse an exact produced path or add an explicit "
        "upstream producer before this consumer; do not invent a renamed alias."
    )
    family = str(missing_profile.get("family", "") or "").strip()
    if issue_count > 1 and family:
        missing_text = ", ".join(missing_identities[:6]) if missing_identities else ""
        identity_clause = f" Missing identities: {missing_text}." if missing_text else ""
        base = (
            f"This consumer lists {issue_count} selected-dir {family} inputs, but earlier steps only produce "
            f"{len(produced_identities)} matching {family} identities.{identity_clause} "
            "Each listed input must come from an earlier producer or be removed from the consumer."
        )
        if family == "bam" and readonly_fastq_identities:
            relevant_fastqs = [identity for identity in readonly_fastq_identities if identity in set(requested_identities)]
            if relevant_fastqs:
                base += (
                    " When readonly FASTQ pairs exist for those identities, add one alignment producer per identity "
                    "before the counting step; do not point the counter at unproduced BAM aliases."
                )

    if not nearest_outputs:
        return base
    nearest_profile = nearest_outputs[0].get("profile", {})
    if not isinstance(nearest_profile, Mapping):
        return base
    missing_stage_tokens = set(missing_profile.get("stage_tokens", []))
    nearest_stage_tokens = set(nearest_profile.get("stage_tokens", []))
    unresolved_stages = sorted(missing_stage_tokens - nearest_stage_tokens)
    if unresolved_stages:
        stage_text = ", ".join(unresolved_stages[:3])
        return (
            f"{base} The nearest upstream outputs only cover earlier-stage artifacts. "
            f"If this consumer needs `{stage_text}`, add that producer step explicitly first."
        )
    if str(consumer).strip().lower() == "featurecounts_run.input_bams" and family == "bam":
        return (
            f"{base} Keep `featurecounts_run.input_bams` limited to BAMs that earlier alignment or merge steps "
            "actually emit."
        )
    return base


def _normalize_selected_path(path_text: str, selected_dir: Path) -> str:
    """Return one absolute selected-dir path string."""

    rendered = str(path_text or "").strip()
    if not rendered:
        return ""
    candidate = Path(rendered).expanduser()
    if not candidate.is_absolute():
        candidate = selected_dir / candidate
    return str(candidate.resolve(strict=False))


def _display_selected_path(path_text: str, selected_dir: Path) -> str:
    """Return one selected-dir path relative to the selected root when possible."""

    normalized = _normalize_selected_path(path_text, selected_dir)
    if not normalized:
        return ""
    candidate = Path(normalized)
    selected_root = selected_dir.resolve(strict=False)
    if _path_is_within(normalized, selected_root):
        try:
            return str(candidate.relative_to(selected_root))
        except Exception:
            return normalized
    return normalized


def _path_is_within(path_text: str, root: Path) -> bool:
    """Return whether one path resolves inside the selected directory."""

    try:
        Path(path_text).resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except Exception:
        return False


def _artifact_path_profile(path_text: str) -> dict[str, Any]:
    """Return a compact lexical profile for one artifact path."""

    lowered = str(path_text or "").strip().lower()
    tokens = _artifact_path_tokens(lowered)
    return {
        "family": _artifact_family(lowered),
        "branch": _artifact_branch(lowered),
        "identity": _artifact_identity(lowered),
        "stage_tokens": _artifact_stage_tokens(tokens),
        "tokens": tokens,
    }


def _artifact_identity(path_text: str) -> str:
    """Return a coarse artifact identity derived from one path or label."""

    token = Path(str(path_text or "").strip()).name.lower()
    if not token:
        return ""
    for suffix in (".gz", ".bgz"):
        if token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    for suffix in (
        ".vcf",
        ".bam",
        ".cram",
        ".csv",
        ".tsv",
        ".fasta",
        ".fa",
        ".fna",
        ".gff3",
        ".gff",
        ".gtf",
    ):
        if token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    raw_parts = [part for part in re.split(r"[^a-z0-9]+", token) if part]
    parts = [
        part
        for part in raw_parts
        if part not in _ARTIFACT_IDENTITY_SKIP_TOKENS
        and part not in _ARTIFACT_STAGE_TOKEN_MAP
    ]
    if not parts:
        return token
    return "_".join(parts)


def _artifact_path_tokens(path_text: str) -> list[str]:
    """Tokenize one artifact path for approximate producer matching."""

    raw_tokens = _ARTIFACT_TOKEN_RE.findall(str(path_text or "").strip().lower())
    normalized: list[str] = []
    for token in raw_tokens:
        if token in {"gz", "vcf", "bam", "fa", "fasta", "gff", "gtf"}:
            normalized.append(token)
            continue
        branch = _artifact_branch(token)
        if branch:
            normalized.append(branch)
            continue
        normalized.append(token)
    return normalized


def _artifact_family(path_text: str) -> str:
    """Return a coarse file family for one artifact path."""

    lowered = str(path_text or "").strip().lower()
    for suffix, family in (
        (".vcf.gz", "vcf"),
        (".vcf", "vcf"),
        (".bam", "bam"),
        (".cram", "bam"),
        (".fasta", "fasta"),
        (".fa", "fasta"),
        (".fna", "fasta"),
        (".gff3", "annotation"),
        (".gff", "annotation"),
        (".gtf", "annotation"),
        (".faa", "protein"),
        (".csv", "table"),
        (".tsv", "table"),
    ):
        if lowered.endswith(suffix):
            return family
    return ""


def _artifact_branch(path_text: str) -> str:
    """Return a branch-like label embedded in one artifact path."""

    lowered = str(path_text or "").strip().lower()
    if not lowered:
        return ""
    match = _ARTIFACT_BRANCH_INDEX_RE.search(lowered)
    if match:
        return f"evol{match.group(1)}"
    if _ARTIFACT_BRANCH_ALIAS_RE.search(lowered):
        return "ancestor"
    return ""


def _artifact_stage_tokens(tokens: list[str]) -> list[str]:
    """Return normalized stage tokens derived from path tokens."""

    stages: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized = _ARTIFACT_STAGE_TOKEN_MAP.get(token, "")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        stages.append(normalized)
    return stages


def _file_suffix(path_text: str) -> str:
    """Return a stable multi-part suffix for one artifact path."""

    lowered = str(path_text or "").strip().lower()
    if lowered.endswith(".vcf.gz"):
        return ".vcf.gz"
    return "".join(Path(lowered).suffixes[-2:]) or Path(lowered).suffix


def _diagnostic_traces(
    *,
    run: Mapping[str, Any],
    selected_dir: Path,
    steps: list[Any],
    failed_step_index: int,
) -> dict[str, Any]:
    stderr_text, stdout_text = _read_step_stream_tails(
        run=run,
        selected_dir=selected_dir,
        failed_step_index=failed_step_index,
    )
    return {
        "stderr": stderr_text,
        "stdout": stdout_text,
        "executed_command": _executed_command_summary(steps, failed_step_index),
        "prev_step_completion": _load_prev_step_completion(selected_dir, failed_step_index),
        "input_file_listing": _input_file_listing(selected_dir, steps, failed_step_index),
    }


def _read_step_stream_tails(
    *,
    run: Mapping[str, Any],
    selected_dir: Path,
    failed_step_index: int,
) -> tuple[str, str]:
    stderr_text = ""
    stdout_text = ""
    for candidate_dir in _candidate_step_dirs(selected_dir, failed_step_index):
        if not stderr_text:
            stderr_text = _read_file_tail(candidate_dir / "stderr.log")
        if not stdout_text:
            stdout_text = _read_file_tail(candidate_dir / "stdout.log")
        if stderr_text and stdout_text:
            break
    if not stderr_text:
        stderr_text = _stream_tail_text(run.get("stderr_tail", []))
    if not stdout_text:
        stdout_text = _stream_tail_text(run.get("stdout_tail", []))
    return stderr_text, stdout_text


def _candidate_step_dirs(selected_dir: Path, failed_step_index: int) -> list[Path]:
    if failed_step_index < 0:
        return [selected_dir]
    candidates = [
        selected_dir / f"step_{failed_step_index}",
        selected_dir / f"step_{failed_step_index + 1}",
        selected_dir,
    ]
    ordered: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return ordered


def _read_file_tail(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return _truncate_text(text, _TRACE_TEXT_LIMIT)


def _stream_tail_text(value: Any) -> str:
    if isinstance(value, str):
        return _truncate_text(value, _TRACE_TEXT_LIMIT)
    lines = _dedupe_preserve_order_strings(value)
    if not lines:
        return ""
    return _truncate_text("\n".join(lines[-_RUN_STREAM_LINE_LIMIT:]), _TRACE_TEXT_LIMIT)


def _executed_command_summary(steps: list[Any], failed_step_index: int) -> str:
    if failed_step_index < 0 or failed_step_index >= len(steps):
        return ""
    step = steps[failed_step_index]
    if not isinstance(step, Mapping):
        return ""
    tool_name = str(step.get("tool_name", "") or "").strip()
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    payload = {
        "tool_name": tool_name,
        "arguments": _compact_arguments(args),
    }
    return _truncate_text(json.dumps(payload, sort_keys=True), _TRACE_TEXT_LIMIT)


def _compact_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in arguments.items():
        compact[str(key)] = _compact_value(value)
    return compact


def _compact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _compact_value(inner)
            for key, inner in list(value.items())[:_REPAIR_HISTORY_DETAILS_LIMIT]
        }
    if isinstance(value, (list, tuple, set)):
        return [_compact_value(item) for item in list(value)[:_REPAIR_HISTORY_DETAILS_LIMIT]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return _truncate_text(value, _REPAIR_HISTORY_TEXT_LIMIT)
        return value
    return _truncate_text(str(value), _REPAIR_HISTORY_TEXT_LIMIT)


def _load_prev_step_completion(selected_dir: Path, failed_step_index: int) -> dict[str, Any]:
    if failed_step_index <= 0:
        return {}
    prev_index = failed_step_index - 1
    for candidate_dir in _candidate_step_dirs(selected_dir, prev_index):
        manifest_path = candidate_dir / ".step_completion.json"
        if not manifest_path.is_file():
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, Mapping):
            continue
        compact = {
            key: _compact_value(payload.get(key))
            for key in _PREV_COMPLETION_KEYS
            if key in payload
        }
        if compact:
            return compact
    return {}


def _input_file_listing(selected_dir: Path, steps: list[Any], failed_step_index: int) -> str:
    if failed_step_index < 0 or failed_step_index >= len(steps):
        return ""
    step = steps[failed_step_index]
    if not isinstance(step, Mapping):
        return ""
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    for raw_path in _collect_argument_paths(args, output=False):
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else selected_dir / candidate
        parent = resolved.parent
        if not parent.is_dir():
            continue
        try:
            listing = sorted(parent.iterdir(), key=lambda entry: entry.name.lower())
        except Exception:
            continue
        lines: list[str] = []
        for entry in listing[:_INPUT_LISTING_FILE_LIMIT]:
            if not entry.is_file():
                continue
            try:
                size = int(entry.stat().st_size)
            except Exception:
                size = 0
            lines.append(f"  {entry.name} ({size} bytes)")
        if lines:
            return _truncate_text("\n".join(lines), _INPUT_LISTING_CHAR_LIMIT)
    return ""


def _prior_repair_attempts(run: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_history = run.get("auto_repair_history", [])
    if not isinstance(raw_history, Iterable) or isinstance(raw_history, (str, bytes, Mapping)):
        return None
    attempts: list[dict[str, Any]] = []
    for entry in raw_history:
        compact = _compact_history_entry(entry)
        if compact:
            attempts.append(compact)
    if not attempts:
        return None
    return {
        "count": len(attempts),
        "attempts": attempts,
        "instruction": (
            "The following repair attempts have already been tried on this run and FAILED. "
            "Do NOT repeat the same strategies. Analyze why they failed and try a different approach."
        ),
    }


def _compact_history_entry(entry: Any) -> dict[str, Any] | None:
    if hasattr(entry, "model_dump"):
        try:
            entry = entry.model_dump()
        except Exception:
            entry = {"action": str(entry)}
    if isinstance(entry, Mapping):
        compact: dict[str, Any] = {}
        for key in ("ts", "run_id", "failure_class", "attempt", "action", "strategy", "details"):
            if key not in entry:
                continue
            compact[key] = _compact_value(entry.get(key))
        return compact or None
    token = str(entry or "").strip()
    if not token:
        return None
    return {"action": _truncate_text(token, _REPAIR_HISTORY_TEXT_LIMIT)}


def _truncate_text(text: str, limit: int) -> str:
    token = str(text or "")
    if limit <= 0 or len(token) <= limit:
        return token
    return token[-limit:]


def _dedupe_preserve_order_strings(values: Any) -> list[str]:
    if isinstance(values, (str, bytes, Mapping)):
        items = [values]
    elif isinstance(values, Iterable):
        items = list(values)
    else:
        items = [values]
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        token = str(item or "").rstrip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _dedupe_strings(values: Any) -> list[str]:
    if isinstance(values, (str, bytes)) or isinstance(values, Mapping):
        items = [values]
    elif isinstance(values, Iterable):
        items = list(values)
    else:
        items = [values]
    out: list[str] = []
    seen: set[str] = set()
    for value in items:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _first_failed_step_number(step_statuses: list[str], next_step_idx: int, total_steps: int) -> int:
    for index, status in enumerate(step_statuses):
        if str(status).strip().lower() == "failed":
            return index + 1
    if 0 <= next_step_idx < total_steps:
        return next_step_idx + 1
    return 0


def _focus_indices(focus_mode: str, failed_step_number: int, total_steps: int) -> set[int]:
    mode = str(focus_mode or "step_local").strip().lower()
    if total_steps <= 0:
        return set()
    if mode == "full_plan":
        return set(range(total_steps))
    center = max(1, min(total_steps, failed_step_number or 1)) - 1
    radius = 1 if mode == "step_local" else 2
    return {index for index in range(total_steps) if abs(index - center) <= radius}


def _focus_instruction(focus_mode: str, failed_step_number: int) -> str:
    if focus_mode == "full_plan":
        return "Repair the plan globally, but preserve valid structure and paths wherever possible."
    if focus_mode == "subgraph_local":
        return (
            "Repair the failed step and its immediate upstream/downstream handoff. "
            "Avoid changing unrelated earlier steps."
        )
    if failed_step_number > 0:
        return (
            f"Repair step {failed_step_number} locally first. "
            "Only expand the edit scope if the failure cannot be fixed in that local neighborhood."
        )
    return "Repair the smallest local plan region that resolves the failure."


def _summarize_step(step: Mapping[str, Any], *, step_number: int, selected_dir: Path) -> dict[str, Any]:
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    return {
        "step_number": step_number,
        "step_id": int(step.get("step_id", step_number) or step_number),
        "tool_name": str(step.get("tool_name", "") or "").strip(),
        "objective": str(step.get("objective", "") or "").strip()[:240],
        "input_paths": _collect_argument_paths(args, output=False),
        "output_paths": _collect_argument_paths(args, output=True),
        "artifact_state": _artifact_state(_collect_argument_paths(args, output=True), selected_dir),
        "command_excerpt": _command_excerpt(args),
        "argument_hints": _argument_hints(args),
    }


def _collect_argument_paths(args: Mapping[str, Any], *, output: bool) -> list[str]:
    paths: list[str] = []
    for key, value in args.items():
        key_l = str(key or "").strip().lower()
        has_input_token = any(token in key_l for token in _INPUT_KEY_TOKENS)
        has_output_token = any(token in key_l for token in _OUTPUT_KEY_TOKENS)
        if output:
            if key_l.startswith("input") or not has_output_token:
                continue
        else:
            if key_l.startswith("output") or not has_input_token:
                continue
        if not output and has_output_token and not key_l.startswith("input"):
            continue
        paths.extend(_flatten_pathlike_values(value))
    return _dedupe_strings(paths)[:8]


def _flatten_pathlike_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_pathlike_values(item))
        return out
    token = str(value or "").strip()
    if not token:
        return []
    parsed = parse_path_tokens(value)
    if len(parsed) > 1:
        return [item for item in parsed if str(item).strip()]
    if "/" in token or token.endswith((".csv", ".tsv", ".vcf", ".vcf.gz", ".bam", ".fa", ".fasta", ".gff", ".gtf", ".txt")):
        return [token]
    return []


def _artifact_state(paths: list[str], selected_dir: Path) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for raw_path in paths:
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else selected_dir / candidate
        if resolved.is_dir():
            try:
                non_empty = any(resolved.iterdir())
            except Exception:
                non_empty = False
            states.append(
                {
                    "path": str(resolved.resolve(strict=False)),
                    "exists": True,
                    "kind": "dir",
                    "non_empty": bool(non_empty),
                }
            )
            continue
        if resolved.is_file():
            size = 0
            try:
                size = int(resolved.stat().st_size)
            except Exception:
                size = 0
            states.append(
                {
                    "path": str(resolved.resolve(strict=False)),
                    "exists": True,
                    "kind": "file",
                    "non_empty": size > 0,
                    "size_bytes": size,
                }
            )
            continue
        states.append(
            {
                "path": str(resolved.resolve(strict=False)),
                "exists": False,
            }
        )
    return states


def _command_excerpt(args: Mapping[str, Any]) -> str:
    command = str(args.get("command", "") or "").strip()
    if not command:
        return ""
    if len(command) <= 280:
        return command
    return command[:277].rstrip() + "..."


def _argument_hints(args: Mapping[str, Any]) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    for key, value in args.items():
        key_l = str(key or "").strip().lower()
        if key_l == "command":
            continue
        if isinstance(value, (str, int, float, bool)):
            token = str(value)
            if len(token) <= 120 and "/" not in token:
                hints[str(key)] = value
    return hints


def _compact_validation(validation: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {"passed": bool(validation.get("passed", False))}
    for key in (
        "issues",
        "missing_required_tools",
        "missing_plan_signals",
        "missing_capabilities",
        "missing_required_tool_hints",
        "missing_tool_hints",
        "direct_wrapper_issues",
        "artifact_role_issues",
    ):
        values = _dedupe_strings(validation.get(key, []))
        if values:
            compact[key] = values[:10]
    return compact


def _parameter_hints_for_tools(analysis_spec: Mapping[str, Any], tools: list[str]) -> list[dict[str, Any]]:
    parameter_profile = (
        analysis_spec.get("parameter_profile", [])
        if isinstance(analysis_spec.get("parameter_profile", []), list)
        else []
    )
    tool_set = {str(tool).strip().lower() for tool in tools if str(tool).strip()}
    hints: list[dict[str, Any]] = []
    for row in parameter_profile:
        if not isinstance(row, Mapping):
            continue
        tool_name = str(row.get("tool_name", "") or "").strip()
        if tool_name.lower() not in tool_set:
            continue
        hints.append(
            {
                "tool_name": tool_name,
                "settings": row.get("settings", {}) if isinstance(row.get("settings", {}), Mapping) else {},
                "rationale": str(row.get("rationale", "") or "").strip(),
            }
        )
    return hints[:8]


def _tool_knowledge(
    relevant_tools: list[str],
    available_skills: list[Mapping[str, Any]],
    *,
    advisories: Mapping[str, Any],
) -> list[dict[str, Any]]:
    skill_index = {
        str(skill.get("name", "")).strip().lower(): skill
        for skill in available_skills
        if isinstance(skill, Mapping) and str(skill.get("name", "")).strip()
    }
    scientific_index = scientific_tool_index(load_scientific_tool_catalog())
    tool_advisories = (
        advisories.get("tool_advisories", {})
        if isinstance(advisories.get("tool_advisories", {}), Mapping)
        else {}
    )
    entries: list[dict[str, Any]] = []
    for tool_name in relevant_tools:
        skill = skill_index.get(tool_name.lower(), {})
        scientific = scientific_index.get(tool_name.lower(), {})
        parameters = skill.get("parameters", {}) if isinstance(skill.get("parameters", {}), Mapping) else {}
        required_args: list[str] = []
        optional_args: list[str] = []
        for param_name, param_details in parameters.items():
            if not str(param_name).strip():
                continue
            if isinstance(param_details, Mapping) and bool(param_details.get("required", False)):
                required_args.append(str(param_name).strip())
            else:
                optional_args.append(str(param_name).strip())
        if not required_args:
            required_args = [str(value).strip() for value in scientific.get("required_parameters", []) if str(value).strip()]
        if not optional_args:
            optional_args = [str(value).strip() for value in scientific.get("optional_parameters", []) if str(value).strip()]
        advisory = (
            tool_advisories.get(tool_name, {})
            if isinstance(tool_advisories.get(tool_name, {}), Mapping)
            else {}
        )
        entry = {
            "tool_name": tool_name,
            "support_tier": str(scientific.get("support_tier", "wrapped" if skill else "") or "").strip(),
            "description": str(skill.get("description", "") or scientific.get("description", "") or "").strip(),
            "when_to_use": str(skill.get("when_to_use", "") or scientific.get("when_to_use", "") or "").strip(),
            "when_not_to_use": str(skill.get("when_not_to_use", "") or scientific.get("when_not_to_use", "") or "").strip(),
            "required_args": sorted(required_args),
            "optional_args": sorted(optional_args)[:10],
            "executables": _dedupe_strings(scientific.get("executables", [])),
            "repo_alternatives": _dedupe_strings(scientific.get("repo_alternatives", [])),
            "repair_advisory": {
                "summary": str(advisory.get("summary", "") or "").strip(),
                "repair_hints": _dedupe_strings(advisory.get("repair_hints", [])),
                "avoid_patterns": _dedupe_strings(advisory.get("avoid_patterns", [])),
            }
            if advisory
            else {},
            "summary": str(advisory.get("summary", "") or "").strip(),
            "repair_hints": _dedupe_strings(advisory.get("repair_hints", [])),
            "avoid_patterns": _dedupe_strings(advisory.get("avoid_patterns", [])),
        }
        entries.append(entry)
    return entries


def _analysis_advisory(analysis_type: str, advisories: Mapping[str, Any]) -> dict[str, Any]:
    if not str(analysis_type or "").strip():
        return {}
    analysis_advisories = (
        advisories.get("analysis_advisories", {})
        if isinstance(advisories.get("analysis_advisories", {}), Mapping)
        else {}
    )
    advisory = (
        analysis_advisories.get(analysis_type, {})
        if isinstance(analysis_advisories.get(analysis_type, {}), Mapping)
        else {}
    )
    if not advisory:
        return {}
    return {
        "analysis_type": str(analysis_type or "").strip(),
        "summary": str(advisory.get("summary", "") or "").strip(),
        "repair_hints": _dedupe_strings(advisory.get("repair_hints", [])),
        "avoid_patterns": _dedupe_strings(advisory.get("avoid_patterns", [])),
    }
