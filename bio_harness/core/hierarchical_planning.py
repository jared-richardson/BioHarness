"""Workflow normalization schemas and strict-plan rebinding helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from bio_harness.core.benchmark_policy import is_bioagentbench_planning_strict_policy
from bio_harness.core.request_output_intent import is_file_like_output_path
from bio_harness.core.strict_artifact_binding import bind_step_spec_for_benchmark_policy
from bio_harness.core.tool_registry import default_tool_registry


class WorkflowStep(BaseModel):
    """Normalized workflow skeleton step emitted by the planner."""

    step_id: int = Field(description="Unique step identifier within the workflow.")
    tool_name: str = Field(description="Tool selected for this workflow step.")
    objective: str = Field(default="", description="Short statement of what this step should accomplish.")
    depends_on: List[int] = Field(default_factory=list, description="Step IDs that must finish before this step.")
    branch_id: str = Field(default="", description="Optional branch label for parallel comparands or cohorts.")
    parameter_hints: Dict[str, Any] = Field(default_factory=dict, description="Step-local parameter hints.")
    downstream_constraints: List[str] = Field(
        default_factory=list,
        description="Short notes about what downstream steps require from this step.",
    )


class WorkflowSpecSchema(BaseModel):
    """Top-level workflow skeleton schema for hierarchical planning."""

    thought_process: str = Field(
        default="No thought process provided by model.",
        description="Brief reasoning for the workflow shape.",
    )
    workflow: List[WorkflowStep] = Field(description="Ordered workflow skeleton with dependencies.")
    global_constraints: List[str] = Field(
        default_factory=list,
        description="Constraints that must remain consistent across the whole workflow.",
    )
    final_deliverables: List[str] = Field(
        default_factory=list,
        description="Expected end products or deliverable filenames.",
    )


class StepExecutionSpecSchema(BaseModel):
    """Concrete execution schema for a single expanded workflow step."""

    step_id: int = Field(description="Unique step identifier.")
    tool_name: str = Field(description="Tool to execute for this step.")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Concrete arguments for the tool.")
    produces: List[str] = Field(default_factory=list, description="Artifacts this step is expected to produce.")
    assumptions: List[str] = Field(default_factory=list, description="Assumptions required for compatibility.")
    downstream_considerations: List[str] = Field(
        default_factory=list,
        description="Notes that matter for downstream compatibility.",
    )


_BRANCH_TOKEN_RE = re.compile(r"\b(?:anc(?:estor)?|evol\d+|sample\d+|line\d+)\b", re.IGNORECASE)
_UNDECLARED_OUTPUT_HINT_KEYS = (
    "output_file",
    "output_csv",
    "output_tsv",
    "final_csv",
    "final_tsv",
    "final_json",
    "final_output",
)


def _branch_tokens(*parts: str) -> set[str]:
    """Extract branch-identifying tokens from planner-authored text."""

    tokens: set[str] = set()
    for part in parts:
        for match in _BRANCH_TOKEN_RE.findall(str(part or "")):
            tokens.add(match.lower())
    return tokens


def _infer_evolution_branch_ids(steps: List[Dict[str, Any]]) -> List[str]:
    """Infer concrete evolution branch IDs from workflow-step metadata."""

    branch_ids: List[str] = []
    seen: set[str] = set()
    for step in steps:
        tokens = _branch_tokens(
            str(step.get("branch_id", "")),
            str(step.get("objective", "")),
            " ".join(step.get("downstream_constraints", []) or []),
        )
        for token in sorted(tokens):
            if not token.startswith("evol") or token in seen:
                continue
            seen.add(token)
            branch_ids.append(token)
    return branch_ids


def _sanitize_parameter_hints_for_deliverables(
    *,
    tool_name: str,
    parameter_hints: Dict[str, Any],
    final_deliverables: List[str],
) -> tuple[Dict[str, Any], List[str]]:
    """Move invented final-output hints into workflow ``final_deliverables``."""

    registry = default_tool_registry()
    declared = set(registry.parameter_schema_for(tool_name))
    cleaned = dict(parameter_hints or {})
    deliverables = list(final_deliverables or [])
    for key in list(cleaned):
        hint_key = str(key).strip()
        lowered = hint_key.lower()
        if not hint_key or hint_key in declared:
            continue
        if lowered not in _UNDECLARED_OUTPUT_HINT_KEYS and not lowered.startswith("final_"):
            continue
        value = str(cleaned.get(key, "") or "").strip()
        if not value or not is_file_like_output_path(value):
            continue
        deliverables.append(value)
        cleaned.pop(key, None)
    deduped: List[str] = []
    seen: set[str] = set()
    for item in deliverables:
        token = str(item).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return cleaned, deduped


def _coerce_workflow_step_list(value: Any) -> List[Dict[str, Any]]:
    """Return planner-authored workflow steps from common container shapes."""

    if isinstance(value, list):
        return [dict(step) for step in value if isinstance(step, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("steps", "workflow", "plan", "plan_outline"):
        nested = value.get(key, [])
        if isinstance(nested, list):
            return [dict(step) for step in nested if isinstance(step, dict)]
    return []


def _coerce_parameter_hints(value: Any) -> Dict[str, Any]:
    """Normalize compact model-authored parameter hints into a mapping."""

    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    return {"note": text} if text else {}


def normalize_workflow_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a planner-authored workflow skeleton into harness form.

    Args:
        raw: Raw workflow payload from the planner.

    Returns:
        A normalized workflow dictionary with concrete step IDs, dependency
        wiring, and branch-aware expansion for repeated evolution steps.
    """

    if not isinstance(raw, dict):
        return {
            "thought_process": "No thought process provided by model.",
            "workflow": [],
            "global_constraints": [],
            "final_deliverables": [],
        }
    out: Dict[str, Any] = dict(raw)
    out.setdefault("thought_process", "No thought process provided by model.")
    raw_steps = _coerce_workflow_step_list(out.get("workflow", []))
    # Some LLMs (e.g. Qwen 3.6) emit the step list under `plan` instead of
    # `workflow` even when the schema/seed uses `workflow`. Accept either key
    # so a consistent-but-mis-keyed response is not lost to an empty workflow.
    if not raw_steps:
        for alt_key in ("plan", "steps", "plan_outline"):
            alt_steps = _coerce_workflow_step_list(out.get(alt_key, []))
            if alt_steps:
                raw_steps = alt_steps
                break
    final_deliverables = [str(x).strip() for x in out.get("final_deliverables", []) if str(x).strip()]

    prepared_steps: List[Dict[str, Any]] = []
    for idx, step in enumerate(raw_steps, start=1):
        if not isinstance(step, dict):
            continue
        row = dict(step)
        try:
            step_id = int(row.get("step_id", idx))
        except Exception:
            step_id = idx
        tool_name = str(row.get("tool_name") or row.get("tool") or "").strip()
        if not tool_name:
            continue
        depends_on = row.get("depends_on", [])
        if not isinstance(depends_on, list):
            depends_on = []
        dep_ids: List[int] = []
        for dep in depends_on:
            try:
                dep_ids.append(int(dep))
            except Exception:
                continue
        parameter_hints = _coerce_parameter_hints(row.get("parameter_hints", {}))
        parameter_hints, final_deliverables = _sanitize_parameter_hints_for_deliverables(
            tool_name=tool_name,
            parameter_hints=parameter_hints,
            final_deliverables=final_deliverables,
        )
        downstream_constraints = row.get("downstream_constraints", [])
        if not isinstance(downstream_constraints, list):
            downstream_constraints = []
        prepared_steps.append(
            {
                "original_step_id": step_id,
                "tool_name": tool_name,
                "objective": str(row.get("objective", "")).strip(),
                "depends_on_raw": dep_ids,
                "branch_id": str(row.get("branch_id", "")).strip(),
                "parameter_hints": parameter_hints,
                "downstream_constraints": [str(x).strip() for x in downstream_constraints if str(x).strip()],
            }
        )

    evolution_branch_ids = _infer_evolution_branch_ids(prepared_steps)
    expanded_steps: List[Dict[str, Any]] = []
    for step in prepared_steps:
        tool_name_l = str(step.get("tool_name", "")).strip().lower()
        objective_l = str(step.get("objective", "")).strip().lower()
        step_tokens = _branch_tokens(
            str(step.get("branch_id", "")),
            str(step.get("objective", "")),
            " ".join(step.get("downstream_constraints", []) or []),
        )
        sample_tokens = {token for token in step_tokens if token.startswith(("evol", "sample", "line"))}
        if (
            tool_name_l == "snpeff_annotate"
            and not sample_tokens
            and len(evolution_branch_ids) >= 2
            and any(
                token in objective_l
                for token in (
                    "ancestor-subtracted",
                    "subtracted evolved",
                    "subtracted variants",
                    "subtracted vcf",
                    "minus-ancestor",
                    "evolved variants",
                    "evolved callsets",
                )
            )
        ):
            for branch_id in evolution_branch_ids:
                cloned = dict(step)
                cloned["branch_id"] = branch_id
                expanded_steps.append(cloned)
            continue
        expanded_steps.append(step)
    prepared_steps = expanded_steps

    duplicate_groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for step in prepared_steps:
        key = (int(step.get("original_step_id", 0)), str(step.get("tool_name", "")).strip().lower())
        duplicate_groups.setdefault(key, []).append(step)

    filtered_steps: List[Dict[str, Any]] = []
    for step in prepared_steps:
        key = (int(step.get("original_step_id", 0)), str(step.get("tool_name", "")).strip().lower())
        siblings = duplicate_groups.get(key, [])
        step_tokens = _branch_tokens(
            str(step.get("branch_id", "")),
            str(step.get("objective", "")),
            " ".join(step.get("downstream_constraints", []) or []),
        )
        has_specific_sibling = any(
            sibling is not step
            and _branch_tokens(
                str(sibling.get("branch_id", "")),
                str(sibling.get("objective", "")),
                " ".join(sibling.get("downstream_constraints", []) or []),
            )
            for sibling in siblings
        )
        if len(siblings) > 1 and not step_tokens and has_specific_sibling:
            continue
        filtered_steps.append(step)

    normalized_steps: List[Dict[str, Any]] = []
    prev_step_id: int | None = None
    original_to_rows: Dict[int, List[Dict[str, Any]]] = {}
    for idx, step in enumerate(filtered_steps, start=1):
        original_step_id = int(step.get("original_step_id", idx))
        current_tokens = _branch_tokens(
            str(step.get("branch_id", "")),
            str(step.get("objective", "")),
            " ".join(step.get("downstream_constraints", []) or []),
        )
        dep_ids: List[int] = []
        for dep in step.get("depends_on_raw", []) or []:
            try:
                dep_original_id = int(dep)
            except Exception:
                continue
            candidates = list(original_to_rows.get(dep_original_id, []))
            if not candidates:
                continue
            if len(candidates) == 1:
                dep_ids.append(int(candidates[0]["step_id"]))
                continue
            concrete_matches = [
                candidate for candidate in candidates
                if current_tokens.intersection(
                    _branch_tokens(
                        str(candidate.get("branch_id", "")),
                        str(candidate.get("objective", "")),
                        " ".join(candidate.get("downstream_constraints", []) or []),
                    )
                )
            ]
            selected = concrete_matches or candidates
            dep_ids.extend(int(candidate["step_id"]) for candidate in selected)
        if not dep_ids and prev_step_id is not None:
            dep_ids = [prev_step_id]
        dep_ids = list(dict.fromkeys(dep_ids))
        normalized_row = {
            "step_id": idx,
            "tool_name": str(step.get("tool_name", "")).strip(),
            "objective": str(step.get("objective", "")).strip(),
            "depends_on": dep_ids,
            "branch_id": str(step.get("branch_id", "")).strip(),
            "parameter_hints": step.get("parameter_hints", {}),
            "downstream_constraints": step.get("downstream_constraints", []),
        }
        normalized_steps.append(normalized_row)
        original_to_rows.setdefault(original_step_id, []).append(normalized_row)
        prev_step_id = idx

    out["workflow"] = normalized_steps
    out["global_constraints"] = [str(x).strip() for x in out.get("global_constraints", []) if str(x).strip()]
    out["final_deliverables"] = final_deliverables
    return out


_STEP_EXPANSION_PARAMETER_ALIASES: Dict[str, Dict[str, str]] = {
    "snpeff_annotate": {
        "database": "genome_db",
        "db": "genome_db",
        "gff": "annotation_gff",
        "gff_path": "annotation_gff",
        "fasta": "reference_fasta",
        "reference": "reference_fasta",
        "reference_genome": "reference_fasta",
        "config": "config_dir",
    },
}


def _planner_parameter_aliases(
    tool_name: str,
    declared_parameters: set[str],
) -> Dict[str, str]:
    """Return planner-side parameter aliases for one expanded step."""

    aliases = dict(_STEP_EXPANSION_PARAMETER_ALIASES.get(str(tool_name).strip(), {}))
    if "genome_db" in declared_parameters:
        aliases.setdefault("database", "genome_db")
        aliases.setdefault("db", "genome_db")
    return {
        str(alias).strip(): str(canonical).strip()
        for alias, canonical in aliases.items()
        if str(alias).strip()
        and str(canonical).strip()
        and str(canonical).strip() in declared_parameters
    }


def _salvage_freebayes_cli_arguments(execution_spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return wrapper arguments from a CLI-style FreeBayes execution spec."""

    raw_arguments = execution_spec.get("arguments", {})
    if not isinstance(raw_arguments, list):
        return {}
    argv = [str(item).strip() for item in raw_arguments if str(item).strip()]
    if not argv:
        return {}
    salvaged: Dict[str, Any] = {}
    positional: list[str] = []
    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token in {"-f", "--fasta-reference"} and idx + 1 < len(argv):
            salvaged["reference_fasta"] = argv[idx + 1]
            idx += 2
            continue
        if token in {"-p", "--ploidy"} and idx + 1 < len(argv):
            try:
                salvaged["ploidy"] = int(argv[idx + 1])
            except Exception:
                salvaged["ploidy"] = argv[idx + 1]
            idx += 2
            continue
        if token.startswith("-"):
            idx += 1
            continue
        positional.append(token)
        idx += 1
    if positional:
        salvaged["input_bam"] = positional[-1]
    output_redirect = str(execution_spec.get("output_redirect", "")).strip()
    if output_redirect:
        output_key = "output_vcf_gz" if output_redirect.endswith(".gz") else "output_vcf"
        salvaged[output_key] = output_redirect
    return salvaged


def _salvage_cli_execution_spec_arguments(
    expected_tool: str,
    execution_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """Return wrapper arguments salvaged from CLI-style execution specs."""

    if str(expected_tool).strip() == "freebayes_call":
        return _salvage_freebayes_cli_arguments(execution_spec)
    return {}


def normalize_step_execution_spec(
    raw: Dict[str, Any],
    *,
    expected_step_id: int,
    expected_tool_name: str,
) -> Dict[str, Any]:
    """Normalize an expanded execution step to the harness execution schema.

    Args:
        raw: Raw step-expansion payload from the planner.
        expected_step_id: Step ID assigned by the harness for this expansion.
        expected_tool_name: Tool name expected for the expanded step.

    Returns:
        A normalized execution-step dictionary with a stable arguments mapping
        and list-valued metadata fields.
    """

    if not isinstance(raw, dict):
        return {
            "step_id": int(expected_step_id),
            "tool_name": str(expected_tool_name).strip(),
            "arguments": {},
            "produces": [],
            "assumptions": [],
            "downstream_considerations": [],
        }
    out: Dict[str, Any] = dict(raw)
    out["step_id"] = int(expected_step_id)
    expected_tool = str(expected_tool_name).strip()
    declared_parameters = set(default_tool_registry().parameter_schema_for(expected_tool))
    raw_tool = str(out.get("tool_name", "")).strip()
    out["tool_name"] = expected_tool
    execution_spec = out.get("execution_spec", {})
    if not isinstance(execution_spec, dict):
        execution_spec = {}
    execution_tool = str(execution_spec.get("tool_name") or execution_spec.get("tool") or "").strip()
    if execution_tool and not raw_tool:
        raw_tool = execution_tool
    arguments = out.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}
    top_level_parameters = out.get("parameters", {})
    if not isinstance(top_level_parameters, dict):
        top_level_parameters = {}
    top_level_parameter_hints = out.get("parameter_hints", {})
    if not isinstance(top_level_parameter_hints, dict):
        top_level_parameter_hints = {}
    top_level_input_files = out.get("input_files", {})
    if not isinstance(top_level_input_files, dict):
        top_level_input_files = {}
    top_level_inputs = out.get("inputs", {})
    if not isinstance(top_level_inputs, dict):
        top_level_inputs = {}
    top_level_outputs = out.get("outputs", {})
    if not isinstance(top_level_outputs, dict):
        top_level_outputs = {}
    top_level_output_files = out.get("output_files", {})
    if not isinstance(top_level_output_files, dict):
        top_level_output_files = {}
    nested_arguments = execution_spec.get("arguments", {})
    nested_inputs = execution_spec.get("inputs", {})
    nested_outputs = execution_spec.get("outputs", {})
    nested_cli_arguments = _salvage_cli_execution_spec_arguments(expected_tool, execution_spec)
    merged_alias_arguments: dict[str, Any] = {}
    planner_aliases = _planner_parameter_aliases(expected_tool, declared_parameters)
    for source in (
        top_level_input_files,
        top_level_inputs,
        top_level_outputs,
        top_level_output_files,
        top_level_parameters,
        top_level_parameter_hints,
    ):
        if not source:
            continue
        for key, value in source.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            canonical_key = key_text
            if canonical_key not in declared_parameters:
                canonical_key = planner_aliases.get(key_text, "")
            if canonical_key and canonical_key in declared_parameters:
                merged_alias_arguments[canonical_key] = value
    merged_nested_arguments: dict[str, Any] = {}
    if isinstance(nested_inputs, dict) and nested_inputs:
        merged_nested_arguments.update(nested_inputs)
    if isinstance(nested_outputs, dict) and nested_outputs:
        merged_nested_arguments.update(nested_outputs)
    if isinstance(nested_arguments, dict) and nested_arguments:
        merged_nested_arguments.update(nested_arguments)
    if nested_cli_arguments:
        merged_nested_arguments.update(nested_cli_arguments)
    if merged_alias_arguments:
        merged_arguments = dict(merged_alias_arguments)
        merged_arguments.update(arguments)
        arguments = merged_arguments
    if merged_nested_arguments:
        merged_arguments = dict(merged_nested_arguments)
        merged_arguments.update(arguments)
        arguments = merged_arguments
    bash_alias = raw_tool.lower() in {"bash", "sh", "shell", "shell_script"}
    if expected_tool == "bash_run":
        command = str(
            out.get("command", "")
            or out.get("execution_command", "")
            or execution_spec.get("command", "")
            or ""
        ).strip()
        script = str(out.get("script", "") or execution_spec.get("script", "") or "").strip()
        arg_command = str(arguments.get("command", "") or "").strip()
        arg_script = str(arguments.get("script", "") or "").strip()
        if not arg_command:
            promoted = command or arg_script or script
            if promoted:
                arguments = dict(arguments)
                arguments["command"] = promoted
        if "script" in arguments:
            arguments = dict(arguments)
            arguments.pop("script", None)
    if declared_parameters and (raw_tool and raw_tool != expected_tool and not (expected_tool == "bash_run" and bash_alias)):
        arguments = {
            key: value
            for key, value in arguments.items()
            if str(key).strip() in declared_parameters
        }
    elif declared_parameters:
        arguments = {
            key: value
            for key, value in arguments.items()
            if str(key).strip() in declared_parameters
        }
    out["arguments"] = arguments
    out["produces"] = [str(x).strip() for x in out.get("produces", []) if str(x).strip()]
    out["assumptions"] = [str(x).strip() for x in out.get("assumptions", []) if str(x).strip()]
    out["downstream_considerations"] = [
        str(x).strip()
        for x in out.get("downstream_considerations", [])
        if str(x).strip()
    ]
    return out


def workflow_spec_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    workflow: List[Dict[str, Any]] = []
    prev_step_id: int | None = None
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        try:
            step_id = int(step.get("step_id", idx))
        except Exception:
            step_id = idx
        tool_name = str(step.get("tool_name", "")).strip()
        if not tool_name:
            continue
        workflow.append(
            {
                "step_id": step_id,
                "tool_name": tool_name,
                "objective": f"Preserve or repair the existing {tool_name} step.",
                "depends_on": [prev_step_id] if prev_step_id is not None else [],
                "branch_id": "",
                "parameter_hints": {},
                "downstream_constraints": [],
            }
        )
        prev_step_id = step_id
    return {
        "thought_process": str(
            plan.get(
                "thought_process",
                "Preserve the valid plan prefix and repair only what is needed.",
            )
            or ""
        ),
        "workflow": workflow,
        "global_constraints": [],
        "final_deliverables": [],
    }


def assemble_executable_plan(
    workflow_spec: Dict[str, Any],
    step_specs: List[Dict[str, Any]],
    *,
    analysis_spec: Dict[str, Any] | None = None,
    seed_plan: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized_workflow = normalize_workflow_spec(workflow_spec)
    strict_policy = is_bioagentbench_planning_strict_policy((analysis_spec or {}).get("benchmark_policy"))
    seed_steps = {}
    if isinstance(seed_plan, dict):
        for step in seed_plan.get("plan", []) if isinstance(seed_plan.get("plan", []), list) else []:
            if not isinstance(step, dict):
                continue
            try:
                key = int(step.get("step_id"))
            except Exception:
                continue
            seed_steps[key] = step

    step_map: Dict[int, Dict[str, Any]] = {}
    for step in step_specs:
        if not isinstance(step, dict):
            continue
        try:
            step_id = int(step.get("step_id"))
        except Exception:
            continue
        step_map[step_id] = step

    assembled_steps: List[Dict[str, Any]] = []
    for idx, wf_step in enumerate(normalized_workflow.get("workflow", []), start=1):
        if not isinstance(wf_step, dict):
            continue
        step_id = int(wf_step.get("step_id", idx))
        tool_name = str(wf_step.get("tool_name", "")).strip()
        chosen = step_map.get(step_id)
        if not isinstance(chosen, dict):
            chosen = seed_steps.get(step_id, {})
        args = chosen.get("arguments", {}) if isinstance(chosen.get("arguments", {}), dict) else {}
        if not args and step_id in seed_steps:
            seed_args = seed_steps[step_id].get("arguments", {})
            if isinstance(seed_args, dict):
                args = dict(seed_args)
        assembled_step = {
            "step_id": step_id,
            "tool_name": str(chosen.get("tool_name", "")).strip() or tool_name,
            "arguments": dict(args),
        }
        if strict_policy or str((analysis_spec or {}).get("analysis_type", "") or "").strip():
            assembled_step = bind_step_spec_for_benchmark_policy(
                step_spec=assembled_step,
                workflow_step=wf_step,
                analysis_spec=analysis_spec,
            )
            assembled_step["step_id"] = step_id
            assembled_step["tool_name"] = str(assembled_step.get("tool_name", "")).strip() or tool_name
        assembled_steps.append(assembled_step)

    return {
        "thought_process": str(
            normalized_workflow.get("thought_process", "No thought process provided by model.")
            or "No thought process provided by model."
        ),
        "plan": assembled_steps,
    }


def should_use_hierarchical_planning(
    *,
    planner_mode: str,
    user_query: str,
    analysis_spec: Dict[str, Any] | None = None,
) -> bool:
    mode = str(planner_mode or "auto").strip().lower()
    if mode == "hierarchical":
        return True
    if mode in {"direct", "off"}:
        return False

    query_l = str(user_query or "").lower()
    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    protocol_grounding = (
        (analysis_spec or {}).get("protocol_grounding", {})
        if isinstance((analysis_spec or {}).get("protocol_grounding", {}), dict)
        else {}
    )
    if "repairing an executable bioinformatics plan" in query_l:
        return True
    if "return only json with keys `thought_process` and `plan`" in query_l and "current plan" in query_l:
        return True
    if bool(protocol_grounding.get("grounded", False)):
        return True
    return analysis_type in {
        "bacterial_evolution_variant_calling",
        "rna_seq_differential_expression",
        "alternative_splicing",
        "transcript_quantification",
    }
