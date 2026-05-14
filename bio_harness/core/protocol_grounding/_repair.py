"""Deterministic protocol repair dispatcher.

Routes an LLM-generated plan through the appropriate template compiler
based on the analysis type, applies parameter profiles and knowledge-base
defaults, and returns the repaired plan.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.core.protocol_grounding._plan_merge import _tools_equivalent
from bio_harness.core.protocol_grounding._shared import (
    _apply_parameter_knowledge_base,
    _apply_parameter_profile,
)
from bio_harness.core.protocol_grounding._shared import _locked_argument_values_for_tool, _normalize_steps, _renumber_plan


def _collapse_repeated_thought_process(text: str) -> str:
    """Collapse whole-string repeated thought chunks into one copy."""

    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized:
        return ""
    tokens = normalized.split(" ")
    token_count = len(tokens)
    for chunk_len in range(1, (token_count // 2) + 1):
        if token_count % chunk_len:
            continue
        chunk = tokens[:chunk_len]
        repeats = token_count // chunk_len
        if chunk * repeats == tokens:
            return " ".join(chunk)
    return normalized


def _merge_thought_process(
    llm_thought: str,
    template_thought: str,
) -> str:
    """Return a stable merged thought string without repeated template prefixes."""

    llm_text = _collapse_repeated_thought_process(llm_thought)
    template_text = _collapse_repeated_thought_process(template_thought)
    if not llm_text:
        return template_text
    if not template_text:
        return llm_text
    if llm_text == template_text:
        return llm_text
    if template_text in llm_text:
        return llm_text
    if llm_text in template_text:
        return template_text
    return _collapse_repeated_thought_process(f"{llm_text} {template_text}")


def _use_full_template(
    llm_plan: dict[str, Any],
    compiled_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use the compiled template plan directly, preserving LLM thought process.

    Template compilers produce internally-consistent plans with correct paths.
    Merging with the LLM plan via guided_patch can introduce path mismatches
    (e.g. bwa_mem_align outputs to LLM path while freebayes_call reads from
    template path).  Using the full template avoids these inconsistencies.
    """
    result = dict(compiled_plan)
    llm_thought = str(llm_plan.get("thought_process", "")).strip()
    template_thought = str(compiled_plan.get("thought_process", "")).strip()
    merged_thought = _merge_thought_process(llm_thought, template_thought)
    if merged_thought:
        result["thought_process"] = merged_thought
    return _renumber_plan(result), {
        "changed": True,
        "why": "full_template_replacement",
        "strategy": "full_template",
        "template_steps": len(compiled_plan.get("plan", [])),
    }


# ---------------------------------------------------------------------------
# Registry of analysis types that have deterministic template compilers.
# ---------------------------------------------------------------------------

TEMPLATE_COMPILER_TYPES: frozenset[str] = frozenset({
    "bacterial_evolution_variant_calling",
    "rna_seq_differential_expression",
    "transcript_quantification",
    "metagenomics_classification",
    "single_cell_rna_seq",
    "germline_variant_calling",
    "variant_annotation",
    "comparative_genomics",
    "viral_metagenomics",
    "multi_model_dge_pathway",
    "phylogenetics",
})


def _locked_tools(analysis_spec: dict[str, Any] | None) -> set[str]:
    """Return explicitly locked tool names from the analysis spec."""

    intent = (
        analysis_spec.get("explicit_execution_intent", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    return {
        str(tool).strip().lower()
        for tool in (intent.get("locked_tools", []) or [])
        if str(tool).strip()
    }


def _plan_tools(plan: dict[str, Any]) -> list[str]:
    """Return normalized tool names from one execution plan."""

    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return []
    return [
        str(step.get("tool_name", "")).strip().lower()
        for step in steps
        if isinstance(step, dict) and str(step.get("tool_name", "")).strip()
    ]


def _contains_tool_subsequence(observed: list[str], expected: list[str]) -> bool:
    """Return whether *expected* appears in *observed* as an ordered subsequence."""

    if not observed or not expected:
        return False
    expected_idx = 0
    for tool_name in observed:
        if expected_idx >= len(expected):
            break
        if _tools_equivalent(tool_name, expected[expected_idx]):
            expected_idx += 1
    return expected_idx == len(expected)


def _locked_intent_satisfied(plan: dict[str, Any], analysis_spec: dict[str, Any] | None) -> bool:
    """Return whether *plan* still satisfies explicit locked wrapper intent."""

    intent = (
        analysis_spec.get("explicit_execution_intent", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    locked = _locked_tools(analysis_spec)
    if not locked:
        return False
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return False
    locked_args = intent.get("locked_argument_values", {})
    if not isinstance(locked_args, dict):
        locked_args = {}
    matched_tools: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        if tool_name not in locked:
            continue
        arguments = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        expected_args = locked_args.get(tool_name, {})
        if isinstance(expected_args, dict):
            violated = False
            for key, value in expected_args.items():
                if arguments.get(str(key).strip()) != value:
                    violated = True
                    break
            if violated:
                continue
        matched_tools.add(tool_name)
    return matched_tools == locked


def _preserve_current_plan(
    current: dict[str, Any],
    compiled: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
) -> bool:
    """Return whether the current plan should survive compiler normalization."""

    locked = _locked_tools(analysis_spec)
    if not locked or not _locked_intent_satisfied(current, analysis_spec):
        return False
    return _contains_tool_subsequence(_plan_tools(current), _plan_tools(compiled))


def _apply_compiler_result(
    *,
    current: dict[str, Any],
    compiled: dict[str, Any],
    compile_meta: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    meta_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply one compiler result while preserving locked explicit-wrapper plans."""

    if not compile_meta.get("changed", False):
        return current
    if _preserve_current_plan(current, compiled, analysis_spec):
        meta_rows.append(
            {
                "changed": False,
                "why": "llm_plan_already_satisfies_locked_execution_intent",
                "strategy": "preserve_locked_plan",
            }
        )
        return current
    guided, guide_meta = _use_full_template(current, compiled)
    meta_rows.append(guide_meta)
    meta_rows.append({"_full_template": compiled, "_compile_meta": compile_meta})
    return guided


def _normalize_canonical_output_filenames(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize direct-wrapper output basenames to canonical registry defaults.

    This pass only rewrites planner-authored basenames for output parameters with
    declared canonical filenames. Explicitly locked user filenames are preserved.
    """

    registry = default_tool_registry()
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    changes: list[str] = []
    for step in steps:
        tool_name = str(step.get("tool_name", "") or "").strip()
        if not tool_name:
            continue
        canonical_map = registry.canonical_output_filenames_for(tool_name)
        if not canonical_map:
            continue
        locked_args = _locked_argument_values_for_tool(analysis_spec, tool_name)
        arguments = step.get("arguments", {})
        if not isinstance(arguments, dict) or not arguments:
            continue
        patched_args = dict(arguments)
        step_changed = False
        for key, raw_canonical in canonical_map.items():
            param_name = str(key or "").strip()
            canonical_name = (
                str(raw_canonical).strip()
                if not isinstance(raw_canonical, list)
                else ""
            )
            if not param_name or not canonical_name:
                continue
            locked_value = locked_args.get(param_name)
            locked_root = ""
            locked_file = ""
            if isinstance(locked_value, str) and locked_value.strip():
                locked_path = Path(locked_value.strip())
                if locked_path.suffix:
                    locked_file = str(locked_path)
                else:
                    locked_root = str(locked_path)
            current_value = str(patched_args.get(param_name, "") or "").strip()
            if not current_value:
                continue
            current_path = Path(current_value)
            if locked_file:
                new_value = locked_file
            elif locked_root:
                new_value = str(Path(locked_root) / canonical_name)
            elif not current_path.suffix:
                new_value = str(current_path / canonical_name)
            elif current_path.name != canonical_name:
                new_value = str(current_path.with_name(canonical_name))
            else:
                continue
            if new_value == current_value:
                continue
            patched_args[param_name] = new_value
            changes.append(f"{tool_name}.{param_name}:{current_value}->{new_value}")
            step_changed = True
        if step_changed:
            step["arguments"] = patched_args

    if not changes:
        return plan, {"changed": False, "why": "no_canonical_output_filename_changes"}
    patched = dict(plan)
    patched["plan"] = steps
    return _renumber_plan(patched), {
        "changed": True,
        "why": "canonical_output_filenames",
        "changes": changes,
    }


def deterministic_protocol_repair(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply deterministic template-guided repair to *plan*.

    Dispatches to the appropriate template compiler based on
    ``analysis_spec["analysis_type"]``, patches the LLM plan with template
    knowledge, and applies parameter profile / knowledge-base defaults.

    Args:
        plan: The LLM-generated plan dict.
        analysis_spec: Analysis specification containing ``analysis_type``,
            ``protocol_grounding``, and ``parameter_profile``.
        selected_dir: The output/workspace directory.
        data_root: The input data directory.

    Returns:
        Tuple of (repaired_plan, metadata_dict).
    """
    grounding = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("protocol_grounding", {}), dict)
        else {}
    )
    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip()
    if not grounding and analysis_type not in TEMPLATE_COMPILER_TYPES:
        return plan, {"changed": False, "why": "no_protocol_grounding"}

    current = plan
    meta_rows: list[dict[str, Any]] = []

    patched, meta = _apply_parameter_profile(
        current,
        list((analysis_spec or {}).get("parameter_profile", []) or []),
        preserve_existing_values_for_tools=_locked_tools(analysis_spec),
    )
    if meta.get("changed", False):
        current = patched
        meta_rows.append(meta)

    if analysis_type == "bacterial_evolution_variant_calling":
        from bio_harness.core.protocol_grounding._compiler_evolution import _compile_bacterial_evolution_shared_plan
        compiled, compile_meta = _compile_bacterial_evolution_shared_plan(
            plan=current,
            analysis_spec=analysis_spec,
            selected_dir=selected_dir,
            data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "rna_seq_differential_expression":
        from bio_harness.core.protocol_grounding._compiler_rna_seq import _compile_rna_seq_de_plan
        compiled, compile_meta = _compile_rna_seq_de_plan(
            plan=current,
            analysis_spec=analysis_spec,
            selected_dir=selected_dir,
            data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "transcript_quantification":
        from bio_harness.core.protocol_grounding._compiler_transcript import _compile_transcript_quant_plan
        compiled, compile_meta = _compile_transcript_quant_plan(
            plan=current,
            analysis_spec=analysis_spec,
            selected_dir=selected_dir,
            data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "metagenomics_classification":
        from bio_harness.core.protocol_grounding._compiler_metagenomics import _compile_metagenomics_plan
        compiled, compile_meta = _compile_metagenomics_plan(
            plan=current, analysis_spec=analysis_spec,
            selected_dir=selected_dir, data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "single_cell_rna_seq":
        from bio_harness.core.protocol_grounding._compiler_single_cell import _compile_single_cell_plan
        compiled, compile_meta = _compile_single_cell_plan(
            plan=current, analysis_spec=analysis_spec,
            selected_dir=selected_dir, data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "germline_variant_calling":
        from bio_harness.core.protocol_grounding._compiler_germline import _compile_germline_variant_calling_plan
        compiled, compile_meta = _compile_germline_variant_calling_plan(
            plan=current, analysis_spec=analysis_spec,
            selected_dir=selected_dir, data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "variant_annotation":
        from bio_harness.core.protocol_grounding._compiler_annotation import _compile_variant_annotation_plan
        compiled, compile_meta = _compile_variant_annotation_plan(
            plan=current, analysis_spec=analysis_spec,
            selected_dir=selected_dir, data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "phylogenetics":
        from bio_harness.core.protocol_grounding._compiler_phylogenetics import _compile_phylogenetics_plan
        compiled, compile_meta = _compile_phylogenetics_plan(
            plan=current, analysis_spec=analysis_spec,
            selected_dir=selected_dir, data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "comparative_genomics":
        from bio_harness.core.protocol_grounding._compiler_comparative import _compile_comparative_genomics_plan
        compiled, compile_meta = _compile_comparative_genomics_plan(
            plan=current, analysis_spec=analysis_spec,
            selected_dir=selected_dir, data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "viral_metagenomics":
        from bio_harness.core.protocol_grounding._compiler_viral import _compile_viral_metagenomics_plan
        compiled, compile_meta = _compile_viral_metagenomics_plan(
            plan=current, analysis_spec=analysis_spec,
            selected_dir=selected_dir, data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    elif analysis_type == "multi_model_dge_pathway":
        from bio_harness.core.protocol_grounding._compiler_dge import _compile_multi_model_dge_plan
        compiled, compile_meta = _compile_multi_model_dge_plan(
            plan=current, analysis_spec=analysis_spec,
            selected_dir=selected_dir, data_root=data_root,
        )
        current = _apply_compiler_result(
            current=current,
            compiled=compiled,
            compile_meta=compile_meta,
            analysis_spec=analysis_spec,
            meta_rows=meta_rows,
        )

    # Apply global parameter knowledge base as a safety net (fills missing
    # defaults without overwriting existing values).
    kb_patched, kb_meta = _apply_parameter_knowledge_base(current)
    if kb_meta.get("changed", False):
        current = kb_patched
        meta_rows.append(kb_meta)

    canonical_patched, canonical_meta = _normalize_canonical_output_filenames(
        current,
        analysis_spec=analysis_spec,
    )
    if canonical_meta.get("changed", False):
        current = canonical_patched
        meta_rows.append(canonical_meta)

    changed = any(
        row.get("changed", False)
        for row in meta_rows
        if not row.get("_full_template")
    )
    return current, {
        "changed": changed,
        "repairs": [r for r in meta_rows if not r.get("_full_template")],
        "why": "deterministic_protocol_repair_applied" if changed else "no_deterministic_protocol_repair",
        "_full_template": next(
            (r["_full_template"] for r in meta_rows if r.get("_full_template")),
            None,
        ),
    }
