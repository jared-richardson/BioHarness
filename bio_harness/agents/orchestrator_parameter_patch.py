from __future__ import annotations

from typing import Any


PARAMETER_KNOWLEDGE_BASE: dict[tuple[str, str], dict[str, Any]] = {
    ("freebayes_call", "bacterial"): {"ploidy": 1},
    ("freebayes_call", "haploid"): {"ploidy": 1},
    ("spades_assemble", "bacterial"): {"careful": True},
    ("spades_assemble", "evolution"): {"careful": True},
    ("bwa_mem_align", "variant"): {"postprocess_mode": "fixmate_markdup_q20"},
    ("bwa_mem_align", "evolution"): {"postprocess_mode": "fixmate_markdup_q20"},
    ("star_align", "rna_seq"): {"twopassMode": "Basic"},
    ("star_align", "differential"): {"twopassMode": "Basic"},
    ("featurecounts_run", "paired"): {"is_paired_end": True, "count_read_pairs": True},
    ("salmon_quant", "transcript"): {"validateMappings": True},
    ("salmon_quant", "quantif"): {"validateMappings": True, "library_type": "A"},
}


def post_plan_parameter_patch(
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    knowledge_base: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Patch missing/default parameters in generated plans using deterministic knowledge."""

    if not isinstance(plan, dict):
        return plan
    steps = plan.get("plan", [])
    if not isinstance(steps, list):
        return plan

    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    query_context = analysis_type
    patched = False
    kb = knowledge_base or PARAMETER_KNOWLEDGE_BASE

    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {})
        if not isinstance(args, dict):
            continue

        for (kb_tool, kb_context), kb_params in kb.items():
            if tool_name != kb_tool:
                continue
            if kb_context not in query_context:
                continue
            for key, value in kb_params.items():
                if key not in args:
                    args[key] = value
                    patched = True

        step["arguments"] = args

    if patched:
        plan["plan"] = steps
    return plan
