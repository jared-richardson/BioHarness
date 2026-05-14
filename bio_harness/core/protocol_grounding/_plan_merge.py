"""Plan merging and patching utilities.

Provides step-matching, argument-merging, and plan-patching logic used when
a deterministic template compiler guides an LLM-generated plan.
"""
from __future__ import annotations

import re
from typing import Any

from bio_harness.core.protocol_grounding._shared import (
    SIGNAL_EQUIVALENCES,
    _renumber_plan,
)


def _tools_equivalent(tool_a: str, tool_b: str) -> bool:
    """Check if two tool names refer to the same tool."""
    a, b = tool_a.strip().lower(), tool_b.strip().lower()
    if a == b:
        return True
    for group in SIGNAL_EQUIVALENCES.values():
        lower_group = [g.lower() for g in group]
        if a in lower_group and b in lower_group:
            return True
    _ANNOTATION_EQUIVS = {"prokka_annotate", "prodigal_annotate"}
    if a in _ANNOTATION_EQUIVS and b in _ANNOTATION_EQUIVS:
        return True
    return False


def _classify_bash_purpose(command: str) -> set[str]:
    """Classify a bash_run command by its purpose."""
    c = command.lower()
    purposes: set[str] = set()
    if any(kw in c for kw in ("vcffilter", "bcftools filter", "bcftools view -i", "snpsift filter", "snpsift")):
        purposes.add("variant_filter")
    if "bcftools norm" in c:
        purposes.add("normalize")
    if any(kw in c for kw in ("export_shared_variants", "shared_variant", "variants_shared")):
        purposes.add("export")
    if any(kw in c for kw in ("snpeff build", "snpeff.config")):
        purposes.add("snpeff_setup")
    if any(kw in c for kw in ("awk ", "grep ", "sed ", "cut ")) and ".vcf" in c:
        purposes.add("export")
    if not purposes:
        purposes.add("other")
    return purposes


def _match_steps(
    llm_steps: list[dict[str, Any]],
    template_steps: list[dict[str, Any]],
) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """Greedy forward matching of LLM steps to template steps.

    Returns (matches, used_llm_indices, used_template_indices).
    Each match is (llm_idx, template_idx).
    """
    matches: list[tuple[int, int]] = []
    used_llm: set[int] = set()
    used_template: set[int] = set()

    for t_idx, t_step in enumerate(template_steps):
        if not isinstance(t_step, dict):
            continue
        t_tool = str(t_step.get("tool_name", "")).lower()

        for l_idx, l_step in enumerate(llm_steps):
            if l_idx in used_llm or not isinstance(l_step, dict):
                continue
            l_tool = str(l_step.get("tool_name", "")).lower()

            if not _tools_equivalent(l_tool, t_tool):
                continue

            if t_tool == "bash_run":
                t_purposes = _classify_bash_purpose(
                    str((t_step.get("arguments") or {}).get("command", ""))
                )
                l_purposes = _classify_bash_purpose(
                    str((l_step.get("arguments") or {}).get("command", ""))
                )
                if not (t_purposes & l_purposes):
                    continue

            matches.append((l_idx, t_idx))
            used_llm.add(l_idx)
            used_template.add(t_idx)
            break

    return matches, used_llm, used_template


def _merge_step_arguments(
    llm_step: dict[str, Any],
    template_step: dict[str, Any],
) -> dict[str, Any]:
    """Merge: use template args as base, keep extra LLM args that don't conflict."""
    merged = dict(template_step)
    llm_args = dict((llm_step.get("arguments") or {}) if isinstance(llm_step.get("arguments"), dict) else {})
    template_args = dict((template_step.get("arguments") or {}) if isinstance(template_step.get("arguments"), dict) else {})

    # Keys that belong on the step envelope, not inside arguments.
    _STEP_ENVELOPE_KEYS = frozenset({
        "step_id", "tool_name", "purpose", "step_purpose",
        "deliverables", "expected_files", "validation_method",
        "success_criteria", "canonicalized_to",
    })
    final_args = dict(template_args)
    for key, value in llm_args.items():
        if key not in final_args and key not in _STEP_ENVELOPE_KEYS:
            final_args[key] = value
    merged["arguments"] = final_args

    if llm_step.get("purpose") and not template_step.get("purpose"):
        merged["purpose"] = llm_step["purpose"]
    return merged


def _patch_llm_plan_with_template(
    llm_plan: dict[str, Any],
    template_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Patch an LLM-generated plan using a deterministic template as reference.

    Instead of wholesale replacing the LLM plan, this function:
    1. Matches LLM steps to template steps by tool_name
    2. For matched steps: patches arguments (file paths, critical params) from template
    3. For unmatched template steps: inserts them at the correct position
    4. Preserves extra LLM steps the template doesn't have
    5. Falls back to full template only if LLM plan has <30% overlap
    """
    llm_steps = list(
        s for s in (llm_plan.get("plan", []) if isinstance(llm_plan, dict) else [])
        if isinstance(s, dict)
    )
    template_steps = list(
        s for s in (template_plan.get("plan", []) if isinstance(template_plan, dict) else [])
        if isinstance(s, dict)
    )

    if not llm_steps:
        return template_plan, {
            "changed": True,
            "why": "llm_plan_empty_using_template",
            "strategy": "full_template",
        }

    matches, used_llm, used_template = _match_steps(llm_steps, template_steps)
    overlap_ratio = len(matches) / max(len(template_steps), 1)

    if overlap_ratio < 0.3:
        result = dict(template_plan)
        result["thought_process"] = str(llm_plan.get("thought_process", "")).strip()
        return _renumber_plan(result), {
            "changed": True,
            "why": "llm_plan_too_different",
            "strategy": "full_template_with_llm_thought",
            "overlap_ratio": round(overlap_ratio, 3),
            "matched_steps": len(matches),
            "template_steps": len(template_steps),
        }

    # Build merged plan using template ordering.
    # For each template slot: use merged (LLM+template) step if matched,
    # otherwise insert template step directly.
    match_map_t_to_l = {t_idx: l_idx for l_idx, t_idx in matches}
    merged_steps: list[dict[str, Any]] = []

    for t_idx, t_step in enumerate(template_steps):
        l_idx = match_map_t_to_l.get(t_idx)
        if l_idx is not None:
            merged_steps.append(_merge_step_arguments(llm_steps[l_idx], t_step))
        else:
            merged_steps.append(dict(t_step))

    # Preserve extra LLM steps (e.g., QC, additional analysis) that weren't
    # matched.  Filter out bash_run steps that duplicate functionality already
    # covered by dedicated skill steps in the template (e.g., snpEff build
    # when snpeff_annotate is present, or samtools index when bwa_mem_align
    # already handles indexing).
    template_tool_set = {
        str(s.get("tool_name", "")).lower() for s in template_steps if isinstance(s, dict)
    }
    _REDUNDANT_BASH_KEYWORDS = {
        "snpeff_annotate": ["snpeff build", "snpeff.config", "snpeff_config"],
        "bwa_mem_align": ["bwa index", "bwa-mem2 index", "samtools faidx"],
        "freebayes_call": ["samtools faidx", "samtools index"],
        "deseq2_run": ["deseq2", "deseqdatasetfrommatrix", "library(deseq2)"],
        "edger_run": ["library(edger)", "edger", "dgelist"],
        "limma_voom_run": ["library(limma)", "limma", "voom"],
        "salmon_quant": ["salmon index", "salmon quant"],
        "kallisto_quant": ["kallisto index", "kallisto quant"],
        "featurecounts_run": ["featurecounts", "subread"],
        "gatk_haplotypecaller": ["gatk haplotypecaller", "gatk createsequencedictionary", "gatk addorreplacereadgroups"],
    }
    # Catch-all: inline R/Python scripts are redundant when dedicated skill steps exist
    _R_SKILL_TOOLS = frozenset({
        "deseq2_run", "edger_run", "limma_voom_run", "dexseq_run", "rmats_run",
        "seurat_rscript_workflow",
    })
    _PYTHON_SKILL_TOOLS = frozenset({
        "scanpy_workflow", "sc_count_and_cluster",
    })
    _has_r_skill = bool(template_tool_set & _R_SKILL_TOOLS)
    _has_python_skill = bool(template_tool_set & _PYTHON_SKILL_TOOLS)

    # Collect output paths generated by template steps so we can filter
    # extra LLM steps that write to the same destination (would overwrite
    # correct template output with inferior LLM-generated output).
    _template_output_paths: set[str] = set()
    for t_step in template_steps:
        t_args = (t_step.get("arguments") or {}) if isinstance(t_step.get("arguments"), dict) else {}
        for key in ("output_vcf", "output_dir", "output_file", "output_bam", "output_tsv", "output_csv"):
            val = str(t_args.get(key, "")).strip()
            if val:
                _template_output_paths.add(val)
        # Also extract redirect targets from bash_run commands: ... > /path/to/file
        if str(t_step.get("tool_name", "")).lower() == "bash_run":
            t_cmd = str(t_args.get("command", ""))
            _redirect_match = re.search(r'>\s*(\S+)\s*$', t_cmd)
            if _redirect_match:
                _template_output_paths.add(_redirect_match.group(1))

    extra_llm: list[dict[str, Any]] = []
    filtered_extra = 0
    for i in range(len(llm_steps)):
        if i in used_llm:
            continue
        step = llm_steps[i]
        tool = str(step.get("tool_name", "")).lower()
        if tool == "bash_run":
            cmd = str((step.get("arguments") or {}).get("command", "")).lower()
            cmd_raw = str((step.get("arguments") or {}).get("command", ""))
            redundant = False
            # Pass 1: keyword-based filtering for specific tool duplicates
            for covered_tool, keywords in _REDUNDANT_BASH_KEYWORDS.items():
                if covered_tool in template_tool_set and any(kw in cmd for kw in keywords):
                    redundant = True
                    break
            # Pass 2: catch-all for *inline* R scripts when R skill step exists.
            # Only match Rscript -e (inline code) or R -e / R --no-save with
            # library() calls.  Do NOT match `Rscript some_script.R` which
            # could be legitimate preprocessing (e.g., adapter trimming, format
            # conversion) that the R skill step doesn't cover.
            if not redundant and _has_r_skill:
                is_inline_r = (
                    "rscript -e" in cmd      # Rscript -e 'library(...); ...'
                    or "rscript --vanilla -e" in cmd
                    or ("library(" in cmd and ("r -e" in cmd or "r --no-save" in cmd
                                               or "rscript -e" in cmd))
                )
                # Also match heredoc-style R scripts: Rscript <<EOF / Rscript - <<
                if not is_inline_r:
                    is_inline_r = (
                        ("rscript" in cmd or "r --no-save" in cmd)
                        and ("<<" in cmd or "library(" in cmd and ";" in cmd)
                    )
                if is_inline_r:
                    redundant = True
            # Pass 3: catch-all for inline Python scripts when Python skill step exists
            if not redundant and _has_python_skill:
                if ("python3 -c" in cmd or "python -c" in cmd) and "import " in cmd:
                    redundant = True
            # Pass 4: filter bash commands that redirect to the same output
            # path as a template step (e.g., LLM grep overwriting SnpSift output,
            # or LLM awk overwriting salmon quant.sf conversion).
            if not redundant and _template_output_paths:
                _redir = re.search(r'>\s*(\S+)\s*$', cmd_raw)
                if _redir and _redir.group(1) in _template_output_paths:
                    redundant = True
            if redundant:
                filtered_extra += 1
                continue
        extra_llm.append(step)

    # Append extra LLM steps AFTER all template steps.  These are typically
    # post-processing / deliverable-formatting steps that depend on template
    # step outputs.  Placing them before template steps (the previous
    # behaviour) caused failures when they referenced files not yet produced.
    #
    # Compilers can set "_self_contained": True on the template plan to
    # indicate that the template produces the final deliverable and extra
    # LLM steps should be discarded (they would be harmful or redundant).
    _self_contained = bool(template_plan.get("_self_contained", False))
    if _self_contained:
        filtered_extra += len(extra_llm)
        extra_llm = []
    if extra_llm:
        merged_steps.extend(extra_llm)

    result = dict(llm_plan) if isinstance(llm_plan, dict) else {}
    result["plan"] = merged_steps
    thought = str(result.get("thought_process", "")).strip()
    suffix = (
        f"[Plan guided by protocol template: {len(matches)} steps matched, "
        f"{len(template_steps) - len(matches)} inserted from template, "
        f"{len(extra_llm)} extra LLM steps preserved.]"
    )
    result["thought_process"] = f"{thought} {suffix}".strip()

    return _renumber_plan(result), {
        "changed": True,
        "why": "llm_plan_patched_with_template",
        "strategy": "guided_patch",
        "overlap_ratio": round(overlap_ratio, 3),
        "matched_steps": len(matches),
        "inserted_from_template": len(template_steps) - len(matches),
        "extra_llm_steps_preserved": len(extra_llm),
        "extra_llm_steps_filtered": filtered_extra,
    }
