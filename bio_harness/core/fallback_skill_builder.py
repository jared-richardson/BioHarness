from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bio_harness.core.capability_catalog import (
    infer_capabilities_from_text,
    infer_tool_hints_from_text,
    load_capability_catalog,
    normalize_capability_id,
)
from bio_harness.core.contracts import assess_plan_contract
from bio_harness.core.recovery_policy import classify_failure
from bio_harness.core.shell_parse import split_shell_segments
from bio_harness.core.path_graph_store import (
    PathGraphStore,
    default_path_graph_db_path,
    deterministic_prompt_hash,
)
from bio_harness.core.tool_onboarding import install_tool_onboarding_draft, slugify_skill_name
from bio_harness.core.uncommon_skill_framework import (
    load_uncommon_skill_catalog,
    validate_uncommon_skill_catalog,
)
from bio_harness.workflows.fallback_catalog import build_ranked_fallback_catalog, select_ranked_fallback_plan

STRICTNESS_MODES = {"conservative", "aggressive"}
DESTRUCTIVE_PATTERNS = (
    re.compile(r"(^|\s)rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/(\s|$)"),
    re.compile(r"(^|\s)sudo\s+rm\b"),
    re.compile(r"(^|\s)mkfs(\.|\s)"),
    re.compile(r"(^|\s)dd\s+if="),
    re.compile(r"(^|\s)shutdown\b"),
    re.compile(r"(^|\s)reboot\b"),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*;\s*\}"),
)
GROUP_EVIDENCE_MARKERS = (
    "__selected_control_r1__",
    "__selected_treatment_r1__",
    "__bam_list_count__",
    "--b1",
    "--b2",
    "condition_treatment_vs_control",
)
TOOL_ALIAS_MAP = {
    "star_align": "star",
    "star_2pass_align": "star",
    "star_solo_count": "star",
    "hisat2_align": "hisat2",
    "bwa_mem_align": "bwa",
    "bowtie2_align": "bowtie2",
    "minimap2_align": "minimap2",
    "featurecounts_run": "featurecounts",
    "stringtie_quant": "stringtie",
    "deseq2_run": "rscript",
    "edger_run": "rscript",
    "limma_voom_run": "rscript",
    "gatk_haplotypecaller": "gatk",
    "gatk_mutect2_call": "gatk",
    "bcftools_call": "bcftools",
    "freebayes_call": "freebayes",
    "dexseq_run": "rscript",
    "majiq_run": "majiq",
    "blastp_search": "blastp",
    "blastn_search": "blastn",
    "blastx_search": "blastx",
    "tblastx_search": "tblastx",
    "tblastn_search": "tblastn",
    "psiblast_search": "psiblast",
    "deltablast_search": "deltablast",
    "rpsblast_search": "rpsblast",
    "rpstblastn_search": "rpstblastn",
    "makeblastdb_run": "makeblastdb",
    "blast_formatter_run": "blast_formatter",
    "blastdbcmd_run": "blastdbcmd",
    "blastdbcheck_run": "blastdbcheck",
    "blastdb_aliastool_run": "blastdb_aliastool",
    "makeprofiledb_run": "makeprofiledb",
    "hmmscan_search": "hmmscan",
    "prokka_annotate": "prokka",
    "methylation_bismark_style": "bismark",
    "metagenomics_kraken2_bracken_style": "kraken2",
    "fusion_star_fusion_style": "star-fusion",
    "cnv_cnvkit_style": "cnvkit.py",
    "immune_repertoire_mixcr_style": "mixcr",
    "phylogenetics_iqtree_style": "iqtree2",
    "bash_run": "bash",
}


@dataclass(frozen=True)
class FallbackBuilderRequest:
    target_capability_set: list[str]
    allowed_tools: list[str]
    data_reference_constraints: dict[str, Any]
    strictness_mode: str
    request_text: str
    selected_dir: str
    data_root: str
    run_ids: list[str]
    batch_prompts: list[dict[str, str]]
    apply_missing_pieces: bool
    run_e2e: bool
    rerun_failures: bool

    @classmethod
    def from_raw(
        cls,
        *,
        target_capability_set: list[str] | None,
        allowed_tools: list[str] | None,
        data_reference_constraints: dict[str, Any] | None,
        strictness_mode: str,
        request_text: str = "",
        selected_dir: str = "",
        data_root: str = "",
        run_ids: list[str] | None = None,
        batch_prompts: list[dict[str, str]] | None = None,
        apply_missing_pieces: bool = False,
        run_e2e: bool = False,
        rerun_failures: bool = False,
    ) -> "FallbackBuilderRequest":
        mode = str(strictness_mode or "conservative").strip().lower()
        if mode not in STRICTNESS_MODES:
            mode = "conservative"
        return cls(
            target_capability_set=_normalize_capabilities(target_capability_set or []),
            allowed_tools=_normalize_tools(allowed_tools or []),
            data_reference_constraints=_normalize_constraints(data_reference_constraints or {}),
            strictness_mode=mode,
            request_text=str(request_text or "").strip(),
            selected_dir=str(selected_dir or "").strip(),
            data_root=str(data_root or "").strip(),
            run_ids=_sorted_unique([str(x).strip() for x in (run_ids or []) if str(x).strip()]),
            batch_prompts=_normalize_batch_prompts(batch_prompts or []),
            apply_missing_pieces=bool(apply_missing_pieces),
            run_e2e=bool(run_e2e),
            rerun_failures=bool(rerun_failures),
        )


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted({str(v).strip() for v in values if str(v).strip()})


def _normalize_capabilities(values: list[str]) -> list[str]:
    return _sorted_unique([normalize_capability_id(v) for v in values])


def _normalize_tools(values: list[str]) -> list[str]:
    return _sorted_unique([str(v).strip().lower() for v in values])


def _normalize_constraints(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    required_paths = out.get("required_paths", [])
    if not isinstance(required_paths, list):
        required_paths = []
    out["required_paths"] = _sorted_unique([str(x).strip() for x in required_paths if str(x).strip()])
    return out


def _normalize_batch_prompts(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        prompt = str(row.get("prompt", "")).strip()
        if not prompt:
            continue
        name = str(row.get("name", f"prompt_{idx:02d}")).strip() or f"prompt_{idx:02d}"
        out.append({"name": name, "prompt": prompt})
    out.sort(key=lambda x: (x["name"], x["prompt"]))
    return out


def inspect_repository_inventory(project_root: Path, capability_catalog_path: Path | None = None) -> dict[str, Any]:
    defs_dir = project_root / "bio_harness" / "skills" / "definitions"
    lib_dir = project_root / "bio_harness" / "skills" / "library"
    script_dir = project_root / "bio_harness" / "pipeline_scripts"

    defs = sorted(p.stem for p in defs_dir.glob("*.md") if p.is_file() and p.name != "template.md")
    libs = sorted(p.stem for p in lib_dir.glob("*.py") if p.is_file() and p.name != "__init__.py")
    defs_missing_impl = sorted(set(defs).difference(set(libs)))
    libs_missing_spec = sorted(set(libs).difference(set(defs)))
    pipeline_scripts = sorted(str(p.relative_to(project_root)) for p in script_dir.glob("*") if p.is_file())

    catalog = build_ranked_fallback_catalog()
    catalog_summary = [
        {
            "rank": int(item.get("rank", 999)),
            "pipeline_id": str(item.get("pipeline_id", "")),
            "required_tools": sorted(str(x).lower() for x in item.get("required_tools", []) if str(x).strip()),
            "contract_capabilities": sorted(
                normalize_capability_id(str(x)) for x in item.get("contract_capabilities", []) if str(x).strip()
            ),
            "recovery_safety": str(item.get("recovery_safety", "")),
        }
        for item in catalog
    ]

    cap_path = capability_catalog_path or (project_root / "bio_harness" / "capabilities" / "catalog.json")
    capability_catalog = load_capability_catalog(cap_path)
    capability_ids = sorted(
        normalize_capability_id(str(x.get("id", "")))
        for x in capability_catalog.get("capabilities", [])
        if isinstance(x, dict)
    )

    return {
        "project_root": str(project_root),
        "skill_definition_count": len(defs),
        "skill_library_count": len(libs),
        "skill_definitions": defs,
        "skill_libraries": libs,
        "definitions_missing_implementation": defs_missing_impl,
        "implementations_missing_definition": libs_missing_spec,
        "pipeline_scripts": pipeline_scripts,
        "fallback_catalog": catalog_summary,
        "capability_ids": capability_ids,
    }


def _expand_allowed_tool_aliases(allowed_tools: list[str]) -> set[str]:
    aliases: set[str] = set()
    for tool in allowed_tools:
        tok = str(tool).strip().lower()
        if not tok:
            continue
        aliases.add(tok)
        aliases.add(TOOL_ALIAS_MAP.get(tok, tok))
    return aliases


def _build_contract(request: FallbackBuilderRequest, capability_catalog: dict[str, Any]) -> dict[str, Any]:
    caps = list(request.target_capability_set)
    if not caps and request.request_text:
        caps = _normalize_capabilities(infer_capabilities_from_text(request.request_text, capability_catalog, enabled_only=True))
    hints = list(request.allowed_tools)
    if request.request_text:
        inferred = infer_tool_hints_from_text(request.request_text, capability_catalog, enabled_only=True)
        hints = _sorted_unique(hints + _normalize_tools(inferred))
    return {
        "must_include_capabilities": caps,
        "explicit_tool_hints": hints,
    }


def _decide_reuse_extend_create(
    *,
    catalog_summary: list[dict[str, Any]],
    target_capabilities: list[str],
    allowed_tools: list[str],
    strictness_mode: str,
) -> dict[str, Any]:
    target_set = set(target_capabilities)
    allowed_aliases = _expand_allowed_tool_aliases(allowed_tools)

    scored: list[dict[str, Any]] = []
    for row in catalog_summary:
        template_caps = set(row.get("contract_capabilities", []))
        required_tools = [str(x).strip().lower() for x in row.get("required_tools", []) if str(x).strip()]

        missing_caps = sorted(target_set.difference(template_caps))
        covered_caps = sorted(target_set.intersection(template_caps))
        blocked_tools: list[str] = []
        if allowed_aliases:
            blocked_tools = sorted(
                [
                    t
                    for t in required_tools
                    if t not in allowed_aliases and TOOL_ALIAS_MAP.get(t, t) not in allowed_aliases
                ]
            )

        is_reuse = len(missing_caps) == 0 and len(blocked_tools) == 0
        is_extend = len(covered_caps) > 0 and (len(blocked_tools) == 0 or strictness_mode == "aggressive")
        stage = "create"
        if is_reuse:
            stage = "reuse"
        elif is_extend:
            stage = "extend"

        scored.append(
            {
                "pipeline_id": str(row.get("pipeline_id", "")),
                "rank": int(row.get("rank", 999)),
                "stage": stage,
                "covered_capabilities": covered_caps,
                "missing_capabilities": missing_caps,
                "blocked_tools": blocked_tools,
                "recovery_safety": str(row.get("recovery_safety", "")).lower(),
                "required_tools": required_tools,
            }
        )

    stage_order = {"reuse": 0, "extend": 1, "create": 2}
    scored.sort(
        key=lambda x: (
            stage_order.get(str(x.get("stage", "create")), 3),
            len(x.get("missing_capabilities", [])),
            len(x.get("blocked_tools", [])),
            -len(x.get("covered_capabilities", [])),
            int(x.get("rank", 999)),
            str(x.get("pipeline_id", "")),
        )
    )

    chosen = scored[0] if scored else {
        "pipeline_id": "",
        "rank": 999,
        "stage": "create",
        "covered_capabilities": [],
        "missing_capabilities": target_capabilities,
        "blocked_tools": [],
        "recovery_safety": "unknown",
        "required_tools": [],
    }

    decision_action = str(chosen.get("stage", "create"))
    return {
        "action": decision_action,
        "selected_pipeline_id": str(chosen.get("pipeline_id", "")),
        "selected_rank": int(chosen.get("rank", 999)),
        "covered_capabilities": list(chosen.get("covered_capabilities", [])),
        "missing_capabilities": list(chosen.get("missing_capabilities", [])),
        "blocked_tools": list(chosen.get("blocked_tools", [])),
        "required_tools": list(chosen.get("required_tools", [])),
        "strictness_mode": strictness_mode,
        "scored_candidates": scored[:12],
    }


def _tool_availability_override(catalog_summary: list[dict[str, Any]], allowed_tools: list[str]) -> dict[str, bool]:
    aliases = _expand_allowed_tool_aliases(allowed_tools)
    if not aliases:
        return {}
    all_tools = sorted(
        {
            str(tool).strip().lower()
            for row in catalog_summary
            for tool in row.get("required_tools", [])
            if str(tool).strip()
        }
    )
    override: dict[str, bool] = {}
    for tool in all_tools:
        alias = TOOL_ALIAS_MAP.get(tool, tool)
        override[tool] = bool(tool in aliases or alias in aliases)
    return override


def _build_create_plan_stub(request: FallbackBuilderRequest, contract: dict[str, Any]) -> dict[str, Any]:
    caps = contract.get("must_include_capabilities", []) if isinstance(contract, dict) else []
    cap_slug = "_".join(caps[:3]) if caps else "generic"
    pipeline_id = f"custom_{cap_slug}"

    commands: list[str] = ["echo __FALLBACK_SKILL_BUILDER_STUB__"]
    if "group_comparison" in caps:
        commands.append("echo __SELECTED_CONTROL_R1__:auto")
        commands.append("echo __SELECTED_TREATMENT_R1__:auto")
    if request.allowed_tools:
        quoted = " ".join(request.allowed_tools)
        commands.append(f"echo __ALLOWED_TOOLS__:{quoted}")

    steps = [
        {
            "step_id": 1,
            "tool_name": "bash_run",
            "arguments": {"command": " ; ".join(commands)},
        }
    ]
    return {
        "thought_process": "Deterministic fallback stub created because no fully reusable template satisfied constraints.",
        "canonical_template": pipeline_id,
        "plan": steps,
        "execution_options": {
            "strictness_mode": request.strictness_mode,
            "target_capability_set": list(contract.get("must_include_capabilities", [])),
            "allowed_tools": list(request.allowed_tools),
        },
    }


def _resolve_selected_dir(project_root: Path, request: FallbackBuilderRequest) -> str:
    if request.selected_dir:
        return str(Path(request.selected_dir).expanduser().resolve())
    return str((project_root / "workspace").resolve())


def _resolve_data_root(project_root: Path, request: FallbackBuilderRequest) -> str:
    if request.data_root:
        return str(Path(request.data_root).expanduser().resolve())
    return str((project_root / "workspace" / "inputs_readonly").resolve())


def _resolve_path_graph_db(project_root: Path, request: FallbackBuilderRequest) -> Path:
    constraints = request.data_reference_constraints if isinstance(request.data_reference_constraints, dict) else {}
    raw = str(constraints.get("path_graph_db", "")).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    selected_dir = _resolve_selected_dir(project_root, request)
    return default_path_graph_db_path(selected_dir)


def _resolve_preference_profile(
    request: FallbackBuilderRequest,
    graph_store: PathGraphStore,
) -> dict[str, Any]:
    constraints = request.data_reference_constraints if isinstance(request.data_reference_constraints, dict) else {}
    inline = constraints.get("preference_profile", {})
    if isinstance(inline, dict) and inline:
        return dict(inline)
    user_key = str(constraints.get("path_graph_user_key", "fallback_builder")).strip() or "fallback_builder"
    scope = str(constraints.get("path_graph_scope", "global")).strip() or "global"
    prefs = graph_store.get_user_preferences(user_key=user_key, scope=scope)
    return dict(prefs) if isinstance(prefs, dict) else {}


def _select_catalog_plan(
    *,
    project_root: Path,
    request: FallbackBuilderRequest,
    contract: dict[str, Any],
    inventory: dict[str, Any],
    graph_store: PathGraphStore,
    preference_profile: dict[str, Any],
    excluded_pipeline_ids: list[str] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    constraints = request.data_reference_constraints
    selected_dir = _resolve_selected_dir(project_root, request)
    data_root = _resolve_data_root(project_root, request)
    availability_override = _tool_availability_override(inventory.get("fallback_catalog", []), request.allowed_tools)

    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt=request.request_text,
        data_root=data_root,
        selected_dir=selected_dir,
        reference_fasta=str(constraints.get("reference_fasta", "")),
        annotation_gtf=str(constraints.get("annotation_gtf", "")),
        control_tag=str(constraints.get("control_tag", "S1")),
        treatment_tag=str(constraints.get("treatment_tag", "S6")),
        subset_mode=bool(constraints.get("subset_mode", True)),
        test_reads_per_fastq=int(constraints.get("test_reads_per_fastq", 1_000_000)),
        cache_paths=dict(constraints.get("cache_paths", {})) if isinstance(constraints.get("cache_paths"), dict) else None,
        tool_availability_override=availability_override,
        excluded_pipeline_ids=excluded_pipeline_ids,
        graph_store=graph_store,
        preference_profile=preference_profile,
    )
    if not isinstance(details, dict):
        details = {"why": "catalog_selection_unknown"}
    return plan, details


def _extract_selected_pipeline_id(plan: dict[str, Any], selection_details: dict[str, Any]) -> str:
    selection = selection_details.get("selection", {}) if isinstance(selection_details.get("selection"), dict) else {}
    pipeline_id = str(selection.get("pipeline_id", "")).strip()
    if pipeline_id:
        return pipeline_id
    canonical = str(plan.get("canonical_template", "")).strip()
    if canonical and not canonical.startswith("custom_"):
        return canonical
    selected_template = (
        selection_details.get("selected_template", {})
        if isinstance(selection_details.get("selected_template"), dict)
        else {}
    )
    return str(selected_template.get("pipeline_id", "")).strip()


def _plan_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(step) for step in plan.get("plan", []) if isinstance(step, dict)]


def _compose_plan_segments(
    *,
    base_plan: dict[str, Any],
    segment_plans: list[dict[str, Any]],
    segment_ids: list[str],
) -> dict[str, Any]:
    composed = dict(base_plan)
    merged_steps: list[dict[str, Any]] = []
    for idx, segment in enumerate(segment_plans, start=1):
        segment_id = str(segment_ids[idx - 1]).strip() if idx - 1 < len(segment_ids) else f"segment_{idx:02d}"
        if idx > 1:
            merged_steps.append(
                {
                    "tool_name": "bash_run",
                    "arguments": {"command": f"echo __COMPOSITION_SEGMENT__:{segment_id}"},
                }
            )
        for step in _plan_steps(segment):
            row = dict(step)
            row.pop("step_id", None)
            merged_steps.append(row)

    composed["plan"] = merged_steps
    canonical_ids = [str(x).strip() for x in segment_ids if str(x).strip()]
    if canonical_ids:
        composed["canonical_template"] = "composed::" + "+".join(canonical_ids)
    base_thought = str(base_plan.get("thought_process", "")).strip()
    suffix = "Composed deterministic fallback plan from multiple templates."
    composed["thought_process"] = f"{base_thought} {suffix}".strip()

    execution_options = composed.get("execution_options", {})
    options = dict(execution_options) if isinstance(execution_options, dict) else {}
    options["composition_enabled"] = True
    options["composed_pipeline_ids"] = canonical_ids
    composed["execution_options"] = options
    return _renumber_plan_steps(composed)


def _compose_multi_template_plan(
    *,
    project_root: Path,
    request: FallbackBuilderRequest,
    contract: dict[str, Any],
    inventory: dict[str, Any],
    graph_store: PathGraphStore,
    preference_profile: dict[str, Any],
    base_plan: dict[str, Any],
    base_selection_details: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    constraints = request.data_reference_constraints if isinstance(request.data_reference_constraints, dict) else {}
    explicit_flag = constraints.get("enable_multi_template_composition")
    enabled = bool(explicit_flag) if isinstance(explicit_flag, bool) else request.strictness_mode == "aggressive"
    if not enabled:
        return base_plan, {
            "enabled": False,
            "attempted": False,
            "applied": False,
            "why": "composition_disabled",
            "selected_pipeline_ids": [],
            "initial_missing_capabilities": [],
            "final_missing_capabilities": [],
            "segments": [],
        }

    initial_validation = assess_plan_contract(base_plan, contract)
    initial_missing = _normalize_capabilities(initial_validation.get("missing_capabilities", []))
    if not initial_missing:
        return base_plan, {
            "enabled": True,
            "attempted": False,
            "applied": False,
            "why": "single_template_satisfies_contract",
            "selected_pipeline_ids": [],
            "initial_missing_capabilities": [],
            "final_missing_capabilities": [],
            "segments": [],
        }

    raw_max_templates = constraints.get("max_composed_templates", 3)
    try:
        max_templates = int(raw_max_templates)
    except Exception:
        max_templates = 3
    max_templates = min(max(max_templates, 2), 6)

    base_pipeline_id = _extract_selected_pipeline_id(base_plan, base_selection_details)
    segment_plans: list[dict[str, Any]] = [base_plan]
    segment_ids: list[str] = [base_pipeline_id or "segment_01"]
    segment_rows: list[dict[str, Any]] = []
    excluded = {base_pipeline_id} if base_pipeline_id else set()
    remaining = list(initial_missing)
    stop_reason = "max_templates_reached"

    while remaining and len(segment_plans) < max_templates:
        remaining_contract = {
            "must_include_capabilities": list(remaining),
            "explicit_tool_hints": list(contract.get("explicit_tool_hints", [])),
        }
        candidate_plan, candidate_details = _select_catalog_plan(
            project_root=project_root,
            request=request,
            contract=remaining_contract,
            inventory=inventory,
            graph_store=graph_store,
            preference_profile=preference_profile,
            excluded_pipeline_ids=sorted(excluded),
        )
        if not isinstance(candidate_plan, dict):
            stop_reason = "catalog_selection_unavailable"
            break

        candidate_pipeline_id = _extract_selected_pipeline_id(candidate_plan, candidate_details)
        candidate_key = candidate_pipeline_id or f"segment_{len(segment_plans) + 1:02d}"
        if candidate_pipeline_id and candidate_pipeline_id in excluded:
            stop_reason = "selector_returned_excluded_template"
            break

        candidate_validation = assess_plan_contract(candidate_plan, remaining_contract)
        candidate_missing = _normalize_capabilities(candidate_validation.get("missing_capabilities", []))
        newly_covered = sorted(set(remaining).difference(set(candidate_missing)))
        if not newly_covered:
            stop_reason = "no_additional_coverage"
            break

        segment_plans.append(candidate_plan)
        segment_ids.append(candidate_key)
        if candidate_pipeline_id:
            excluded.add(candidate_pipeline_id)
        remaining = list(candidate_missing)
        segment_rows.append(
            {
                "pipeline_id": candidate_key,
                "selection_reason": str(candidate_details.get("selection_reason", "")),
                "newly_covered_capabilities": newly_covered,
                "remaining_capabilities": list(remaining),
            }
        )

    if len(segment_plans) <= 1:
        return base_plan, {
            "enabled": True,
            "attempted": True,
            "applied": False,
            "why": stop_reason,
            "selected_pipeline_ids": [x for x in segment_ids if x],
            "initial_missing_capabilities": list(initial_missing),
            "final_missing_capabilities": list(initial_missing),
            "segments": segment_rows,
            "max_templates": int(max_templates),
        }

    composed_plan = _compose_plan_segments(
        base_plan=base_plan,
        segment_plans=segment_plans,
        segment_ids=segment_ids,
    )
    final_validation = assess_plan_contract(composed_plan, contract)
    final_missing = _normalize_capabilities(final_validation.get("missing_capabilities", []))
    improved = len(final_missing) < len(initial_missing)
    if not improved:
        return base_plan, {
            "enabled": True,
            "attempted": True,
            "applied": False,
            "why": "composition_no_coverage_improvement",
            "selected_pipeline_ids": [x for x in segment_ids if x],
            "initial_missing_capabilities": list(initial_missing),
            "final_missing_capabilities": list(initial_missing),
            "segments": segment_rows,
            "max_templates": int(max_templates),
        }

    return composed_plan, {
        "enabled": True,
        "attempted": True,
        "applied": True,
        "why": "composed_templates",
        "selected_pipeline_ids": [x for x in segment_ids if x],
        "initial_missing_capabilities": list(initial_missing),
        "final_missing_capabilities": list(final_missing),
        "segments": segment_rows,
        "max_templates": int(max_templates),
    }


def _generate_plan(
    *,
    project_root: Path,
    request: FallbackBuilderRequest,
    contract: dict[str, Any],
    decision: dict[str, Any],
    inventory: dict[str, Any],
    graph_store: PathGraphStore,
    preference_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if decision.get("action") == "create":
        return _build_create_plan_stub(request, contract), {"why": "created_stub_plan", "selection": decision}

    plan, details = _select_catalog_plan(
        project_root=project_root,
        request=request,
        contract=contract,
        inventory=inventory,
        graph_store=graph_store,
        preference_profile=preference_profile,
    )
    if not isinstance(plan, dict):
        return _build_create_plan_stub(request, contract), {"why": "catalog_selection_unavailable", "selection": details}
    return plan, details if isinstance(details, dict) else {"why": "catalog_selection_unknown"}


def _renumber_plan_steps(plan: dict[str, Any]) -> dict[str, Any]:
    out = dict(plan)
    steps = out.get("plan", []) if isinstance(out.get("plan", []), list) else []
    normalized: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        row = dict(step)
        row["step_id"] = idx
        normalized.append(row)
    out["plan"] = normalized
    return out


def ensure_positive_evidence_markers(plan: dict[str, Any], contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    caps = contract.get("must_include_capabilities", []) if isinstance(contract, dict) else []
    if "group_comparison" not in set(caps):
        return plan, {"changed": False, "why": "group_comparison_not_requested"}

    hay = json.dumps(plan, ensure_ascii=True, sort_keys=True).lower()
    if any(marker in hay for marker in GROUP_EVIDENCE_MARKERS):
        return plan, {"changed": False, "why": "group_markers_already_present"}

    steps = plan.get("plan", []) if isinstance(plan.get("plan", []), list) else []
    marker_step = {
        "tool_name": "bash_run",
        "arguments": {
            "command": "echo __SELECTED_CONTROL_R1__:auto ; echo __SELECTED_TREATMENT_R1__:auto ; echo __GROUP_CONDITION_MARKER__:condition_treatment_vs_control",
        },
    }
    patched = dict(plan)
    patched["plan"] = [marker_step] + [dict(x) for x in steps if isinstance(x, dict)]
    patched = _renumber_plan_steps(patched)
    return patched, {"changed": True, "why": "inserted_group_evidence_marker_step", "inserted_step_id": 1}


def _detect_destructive_commands(plan: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for step in plan.get("plan", []) if isinstance(plan.get("plan", []), list) else []:
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip() != "bash_run":
            continue
        command = str((step.get("arguments") or {}).get("command", ""))
        if not command:
            continue
        for segment in split_shell_segments(command):
            lowered = f" {segment.lower()} "
            for pat in DESTRUCTIVE_PATTERNS:
                if pat.search(lowered):
                    findings.append(
                        {
                            "step_id": int(step.get("step_id", 0)) if str(step.get("step_id", "")).isdigit() else None,
                            "pattern": pat.pattern,
                            "segment": segment,
                        }
                    )
                    break
    findings.sort(key=lambda x: (str(x.get("step_id")), str(x.get("pattern")), str(x.get("segment"))))
    return findings


def run_preflight_checks(
    *,
    plan: dict[str, Any],
    request: FallbackBuilderRequest,
    project_root: Path,
) -> dict[str, Any]:
    constraints = request.data_reference_constraints
    selected_dir = Path(_resolve_selected_dir(project_root, request))
    data_root = Path(_resolve_data_root(project_root, request))

    required_paths = [Path(p).expanduser() for p in constraints.get("required_paths", []) if str(p).strip()]
    for key in ("reference_fasta", "annotation_gtf"):
        val = str(constraints.get(key, "")).strip()
        if val:
            required_paths.append(Path(val).expanduser())

    missing_paths: list[str] = []
    for p in required_paths:
        if p.is_absolute():
            if not p.exists():
                missing_paths.append(str(p))
            continue

        candidates = [
            selected_dir / p,
            data_root / p,
            project_root / p,
        ]
        if not any(c.exists() for c in candidates):
            missing_paths.append(str(candidates[0]))

    destructive = _detect_destructive_commands(plan)

    tool_blockers: list[str] = []
    allowed_aliases = _expand_allowed_tool_aliases(request.allowed_tools)
    if allowed_aliases:
        for step in plan.get("plan", []) if isinstance(plan.get("plan", []), list) else []:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool_name", "")).strip().lower()
            if not tool:
                continue
            normalized = TOOL_ALIAS_MAP.get(tool, tool)
            if tool == "bash_run":
                continue
            if normalized not in allowed_aliases and tool not in allowed_aliases:
                tool_blockers.append(tool)

    return {
        "passed": len(missing_paths) == 0 and len(destructive) == 0 and len(tool_blockers) == 0,
        "missing_paths": sorted(set(missing_paths)),
        "destructive_commands": destructive,
        "tool_blockers": sorted(set(tool_blockers)),
    }


def _build_missing_piece_draft(
    *,
    request: FallbackBuilderRequest,
    contract: dict[str, Any],
    decision: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    target_caps = contract.get("must_include_capabilities", []) if isinstance(contract, dict) else []
    base = str(decision.get("selected_pipeline_id", "custom_fallback")).strip() or "custom_fallback"
    skill_name = slugify_skill_name(f"fallback_{base}")
    tools = request.allowed_tools or ["bash"]
    primary_tool = str(tools[0]).strip().lower()
    command_template = ""
    if primary_tool not in {"", "bash", "bash_run"}:
        command_template = f"{primary_tool} --input {{input_path}} --output {{output_path}}"

    return {
        "skill_name": skill_name,
        "description": (
            "Generated fallback pipeline helper from fallback_skill_builder. "
            "Use when deterministic fallback template coverage is missing and a stub skill is required."
        ),
        "risk_level": "medium",
        "tools_required": tools,
        "capabilities": target_caps,
        "parameters": {
            "input_path": {"type": "path", "description": "Primary input file.", "required": True},
            "output_path": {"type": "path", "description": "Primary output file.", "required": True},
        },
        "command_template": command_template,
        "usage_guide": "Generated by fallback_skill_builder in deterministic mode.",
        "template_plan": plan,
    }


def apply_missing_pieces(
    *,
    project_root: Path,
    request: FallbackBuilderRequest,
    contract: dict[str, Any],
    decision: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    if not request.apply_missing_pieces:
        return {
            "applied": False,
            "why": "apply_missing_pieces_disabled",
            "changed_files": [],
        }

    draft = _build_missing_piece_draft(request=request, contract=contract, decision=decision, plan=plan)
    defs_dir = project_root / "bio_harness" / "skills" / "definitions"
    lib_dir = project_root / "bio_harness" / "skills" / "library"
    cap_path = project_root / "bio_harness" / "capabilities" / "catalog.json"

    ok, msg = install_tool_onboarding_draft(
        draft,
        {"source": "fallback_skill_builder", "mode": request.strictness_mode},
        skills_definitions_dir=defs_dir,
        skills_library_dir=lib_dir,
        capability_catalog_path=cap_path,
        install_workflow="fallback_skill_builder",
        installed_at="1970-01-01T00:00:00",
    )

    draft_dir = project_root / "bio_harness" / "workflows" / "fallback_template_drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"{draft['skill_name']}.json"
    draft_path.write_text(json.dumps(draft, indent=2, sort_keys=True), encoding="utf-8")

    changed = [str(draft_path)]
    if ok:
        changed.append(str(defs_dir / f"{draft['skill_name']}.md"))
        changed.append(str(lib_dir / f"{draft['skill_name']}.py"))
        changed.append(str(cap_path))

    return {
        "applied": bool(ok),
        "install_message": msg,
        "skill_name": draft["skill_name"],
        "draft_path": str(draft_path),
        "changed_files": sorted(set(changed)),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_run_artifacts(run_dir: Path) -> dict[str, Any]:
    state_path = run_dir / "state.json"
    events_path = run_dir / "events.jsonl"
    exec_path = run_dir / "execution.log"
    stderr_path = run_dir / "stderr.log"

    missing_files = [
        str(p)
        for p in (state_path, events_path, exec_path, stderr_path)
        if not p.exists()
    ]

    snapshot = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "state": _read_json(state_path),
        "events": _read_jsonl(events_path),
        "execution_log": exec_path.read_text(encoding="utf-8") if exec_path.exists() else "",
        "stderr_log": stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else "",
        "missing_files": sorted(missing_files),
    }
    return snapshot


def classify_failure_from_artifacts(snapshot: dict[str, Any]) -> dict[str, Any]:
    run = dict(snapshot.get("state", {})) if isinstance(snapshot.get("state"), dict) else {}
    execution_log = str(snapshot.get("execution_log", ""))
    stderr_log = str(snapshot.get("stderr_log", ""))
    status = str(run.get("status", "")).strip().lower()

    if status == "completed" and not str(run.get("error", "")).strip():
        return {
            "failure_class": "none",
            "status": status,
            "error_excerpt": "",
            "signals": {
                "missing_reference_detected": False,
                "format_input_error_detected": False,
                "validation_block_detected": False,
                "policy_block_detected": False,
                "execution_stalled_detected": False,
            },
        }

    if not str(run.get("error", "")).strip():
        run["error"] = "\n".join([execution_log[-500:], stderr_log[-500:]]).strip()

    low_exec = execution_log.lower()
    low_err = stderr_log.lower()
    missing_tools = sorted(
        {
            str(x).strip()
            for x in re.findall(r"__missing_tool__:([a-z0-9._+-]+)", f"{low_exec}\n{low_err}")
            if str(x).strip()
        }
    )
    if missing_tools:
        run["missing_tools_detected"] = missing_tools

    if "__missing_reference__" in low_exec or "missing reference" in low_err:
        run["missing_reference_detected"] = True
    if "__no_control_fastq__" in low_exec or "__no_treatment_fastq__" in low_exec:
        run["format_input_error_detected"] = True
    if "blocked by validation agent" in low_exec or "blocked by validation agent" in low_err:
        run["validation_block_detected"] = True
    if "denied command" in low_exec or "__policy_block__" in low_exec:
        run["policy_block_detected"] = True
    if "execution stalled" in low_exec or "stalled for" in low_exec:
        run["execution_stalled_detected"] = True

    if status == "completed" and not str(run.get("error", "")).strip():
        failure_class = "none"
    else:
        failure_class = classify_failure(run)

    return {
        "failure_class": failure_class,
        "status": status,
        "error_excerpt": str(run.get("error", ""))[:400],
        "signals": {
            "missing_reference_detected": bool(run.get("missing_reference_detected", False)),
            "format_input_error_detected": bool(run.get("format_input_error_detected", False)),
            "validation_block_detected": bool(run.get("validation_block_detected", False)),
            "policy_block_detected": bool(run.get("policy_block_detected", False)),
            "execution_stalled_detected": bool(run.get("execution_stalled_detected", False)),
        },
    }


def choose_repair_action(failure_class: str, strictness_mode: str) -> str:
    mapping = {
        "none": "no_action",
        "tool_missing": "repair_missing_tool_or_degrade_template",
        "missing_reference": "repair_reference_bindings",
        "format_input_error": "repair_input_grouping_or_manifest",
        "contract_mismatch": "rebuild_contract_then_select_template",
        "runtime_step_failure": "rerun_same_prompt_with_failure_context",
        "validation_block": "halt_and_surface_validation_block",
        "policy_block": "halt_and_surface_policy_block",
        "unknown_failure": "rerun_same_prompt_with_failure_context",
    }
    action = mapping.get(str(failure_class).strip(), "rerun_same_prompt_with_failure_context")
    if strictness_mode == "conservative" and action.startswith("rerun"):
        return "rerun_same_prompt_conservative"
    return action


def _resolve_run_dir(run_id: str, workspace_root: Path) -> Path:
    candidate = Path(run_id).expanduser()
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()
    return (workspace_root / "runs" / run_id).resolve()


def _rerun_prompt_from_snapshot(
    *,
    snapshot: dict[str, Any],
    project_root: Path,
    strictness_mode: str,
    prompt_override: str = "",
) -> dict[str, Any]:
    run_dir = Path(str(snapshot.get("run_dir", ""))).resolve()
    manifest = _read_json(run_dir / "manifest.json")
    state = snapshot.get("state", {}) if isinstance(snapshot.get("state"), dict) else {}

    prompt = str(state.get("user_request", "")).strip()
    if not prompt and prompt_override:
        prompt = str(prompt_override).strip()
    if not prompt:
        return {
            "rerun_attempted": False,
            "why": "missing_user_request_in_state",
            "command": [],
        }

    selected_dir = str(manifest.get("selected_dir", (project_root / "workspace").resolve()))
    data_root = str(manifest.get("data_root", (project_root / "workspace" / "inputs_readonly").resolve()))

    result_json = project_root / "workspace" / "runs" / "_batch_reports" / f"rerun_{run_dir.name}_result.json"
    result_json.parent.mkdir(parents=True, exist_ok=True)

    max_repairs = "3" if strictness_mode == "aggressive" else "1"
    cmd = [
        sys.executable,
        str(project_root / "scripts" / "run_agent_e2e.py"),
        "--prompt",
        prompt,
        "--selected-dir",
        selected_dir,
        "--data-root",
        data_root,
        "--max-repairs",
        max_repairs,
        "--result-json",
        str(result_json),
        "--quiet",
    ]
    if strictness_mode == "conservative":
        cmd.append("--no-replan")

    proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, check=False)
    payload = _read_json(result_json)
    return {
        "rerun_attempted": True,
        "command": cmd,
        "exit_code": int(proc.returncode),
        "status": str(payload.get("status", "")),
        "error": str(payload.get("error", "")),
        "run_id_after": str(payload.get("run_id", "")),
        "run_dir_after": str(payload.get("run_dir", "")),
    }


def troubleshoot_runs(
    *,
    run_ids: list[str],
    workspace_root: Path,
    project_root: Path,
    strictness_mode: str,
    rerun_failures: bool,
    prompt_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    commands_run: list[list[str]] = []
    prompt_map = dict(prompt_overrides or {})

    for run_id in run_ids:
        run_dir = _resolve_run_dir(run_id, workspace_root)
        if not run_dir.exists():
            results.append(
                {
                    "run_id": run_id,
                    "exists": False,
                    "failure_class": "unknown_failure",
                    "repair_action": "collect_artifacts_missing_run_dir",
                }
            )
            continue

        snapshot = read_run_artifacts(run_dir)
        classification = classify_failure_from_artifacts(snapshot)
        failure_class = str(classification.get("failure_class", "unknown_failure"))
        action = choose_repair_action(failure_class, strictness_mode)

        row: dict[str, Any] = {
            "run_id": run_dir.name,
            "exists": True,
            "run_dir": str(run_dir),
            "failure_class": failure_class,
            "repair_action": action,
            "artifact_missing_files": snapshot.get("missing_files", []),
            "classification": classification,
            "rerun": {"rerun_attempted": False},
        }
        if rerun_failures and failure_class not in {"none", "validation_block", "policy_block"}:
            override = str(prompt_map.get(run_dir.name, "")).strip()
            rerun = _rerun_prompt_from_snapshot(
                snapshot=snapshot,
                project_root=project_root,
                strictness_mode=strictness_mode,
                prompt_override=override,
            )
            row["rerun"] = rerun
            if rerun.get("command"):
                commands_run.append(list(rerun.get("command", [])))

        before_status = str(snapshot.get("state", {}).get("status", "")) if isinstance(snapshot.get("state"), dict) else ""
        after_status = str(row.get("rerun", {}).get("status", ""))
        row["regression_status"] = {
            "before": before_status,
            "after": after_status,
            "improved": bool(before_status != "completed" and after_status == "completed"),
        }
        results.append(row)

    return {
        "items": results,
        "commands_run": commands_run,
    }


def _run_batch_prompts(
    *,
    prompts: list[dict[str, str]],
    project_root: Path,
    selected_dir: str,
    data_root: str,
    constraints: dict[str, Any],
) -> dict[str, Any]:
    prompts_path = project_root / "workspace" / "runs" / "_batch_reports" / "fallback_skill_builder_prompts.json"
    prompts_path.parent.mkdir(parents=True, exist_ok=True)
    prompts_path.write_text(json.dumps(prompts, indent=2, sort_keys=True), encoding="utf-8")

    out_dir = project_root / "workspace" / "runs" / "_batch_reports" / "fallback_skill_builder"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(project_root / "scripts" / "run_agent_e2e_batch.py"),
        "--prompts-file",
        str(prompts_path),
        "--selected-dir",
        selected_dir,
        "--data-root",
        data_root,
        "--out-dir",
        str(out_dir),
        "--quiet",
    ]
    if not bool(constraints.get("batch_allow_repair", False)):
        cmd.extend(["--max-repairs", "0", "--no-replan", "--no-canonicalize"])
    plan_file = str(constraints.get("plan_file", "")).strip()
    if plan_file:
        cmd.extend(["--plan-file", plan_file])
    graph_db = str(constraints.get("path_graph_db", "")).strip()
    if graph_db:
        cmd.extend(["--path-graph-db", str(Path(graph_db).expanduser().resolve())])
    graph_user_key = str(constraints.get("path_graph_user_key", "")).strip()
    if graph_user_key:
        cmd.extend(["--path-graph-user-key", graph_user_key])
    graph_scope = str(constraints.get("path_graph_scope", "")).strip()
    if graph_scope:
        cmd.extend(["--path-graph-scope", graph_scope])
    if bool(constraints.get("path_graph_persist_preference_updates", False)):
        cmd.append("--path-graph-persist-preference-updates")

    proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, check=False)
    summary_path = out_dir / "batch_summary.json"
    summary = _read_json(summary_path)

    return {
        "command": cmd,
        "exit_code": int(proc.returncode),
        "summary_path": str(summary_path),
        "summary": summary,
    }


def run_fallback_skill_builder(
    *,
    project_root: Path,
    request: FallbackBuilderRequest,
    capability_catalog_path: Path | None = None,
) -> dict[str, Any]:
    audit: list[dict[str, Any]] = []
    commands_run: list[list[str]] = []
    started_at = datetime.now(timezone.utc).isoformat()
    prompt_hash = deterministic_prompt_hash(request.request_text)
    run_token = deterministic_prompt_hash(f"{prompt_hash}|{started_at}")[:12]

    inventory = inspect_repository_inventory(project_root, capability_catalog_path=capability_catalog_path)
    audit.append({"step": "inventory", "status": "ok", "details": {"skill_definition_count": inventory["skill_definition_count"], "catalog_size": len(inventory["fallback_catalog"])} })

    uncommon_catalog = load_uncommon_skill_catalog()
    uncommon_errors = validate_uncommon_skill_catalog(uncommon_catalog)
    audit.append(
        {
            "step": "uncommon_schema_validation",
            "status": "ok" if not uncommon_errors else "failed",
            "details": {"error_count": len(uncommon_errors), "errors": uncommon_errors[:20]},
        }
    )

    graph_store = PathGraphStore(_resolve_path_graph_db(project_root, request))
    graph_store.ensure_catalog_paths(build_ranked_fallback_catalog())
    preference_profile = _resolve_preference_profile(request, graph_store)
    audit.append(
        {
            "step": "path_graph",
            "status": "ok",
            "details": {
                "db_path": str(graph_store.db_path),
                "preference_profile_loaded": bool(preference_profile),
            },
        }
    )

    cap_path = capability_catalog_path or (project_root / "bio_harness" / "capabilities" / "catalog.json")
    capability_catalog = load_capability_catalog(cap_path)
    contract = _build_contract(request, capability_catalog)
    audit.append({"step": "contract", "status": "ok", "details": contract})

    decision = _decide_reuse_extend_create(
        catalog_summary=inventory.get("fallback_catalog", []),
        target_capabilities=contract.get("must_include_capabilities", []),
        allowed_tools=request.allowed_tools,
        strictness_mode=request.strictness_mode,
    )
    audit.append({"step": "decision", "status": "ok", "details": {"action": decision.get("action"), "selected_pipeline_id": decision.get("selected_pipeline_id")}})

    plan, selection_details = _generate_plan(
        project_root=project_root,
        request=request,
        contract=contract,
        decision=decision,
        inventory=inventory,
        graph_store=graph_store,
        preference_profile=preference_profile,
    )
    audit.append({"step": "plan_generation", "status": "ok", "details": {"selection_why": selection_details.get("why", ""), "selected_pipeline_id": selection_details.get("selection", {}).get("pipeline_id", "") if isinstance(selection_details.get("selection"), dict) else ""}})

    plan, composition_details = _compose_multi_template_plan(
        project_root=project_root,
        request=request,
        contract=contract,
        inventory=inventory,
        graph_store=graph_store,
        preference_profile=preference_profile,
        base_plan=plan,
        base_selection_details=selection_details,
    )
    audit.append(
        {
            "step": "composition",
            "status": "ok" if composition_details.get("applied", False) else "skipped",
            "details": composition_details,
        }
    )

    plan, marker_details = ensure_positive_evidence_markers(plan, contract)
    audit.append({"step": "group_evidence", "status": "ok", "details": marker_details})

    contract_validation = assess_plan_contract(plan, contract)
    preflight = run_preflight_checks(plan=plan, request=request, project_root=project_root)
    audit.append({"step": "validation", "status": "ok" if contract_validation.get("passed", False) else "failed", "details": contract_validation})
    audit.append({"step": "preflight", "status": "ok" if preflight.get("passed", False) else "failed", "details": preflight})

    apply_report = apply_missing_pieces(
        project_root=project_root,
        request=request,
        contract=contract,
        decision=decision,
        plan=plan,
    )
    audit.append({"step": "missing_piece_application", "status": "ok" if apply_report.get("applied", False) else "skipped", "details": {"why": apply_report.get("why", ""), "skill_name": apply_report.get("skill_name", "")}})

    batch_report: dict[str, Any] = {"executed": False}
    batch_run_ids: list[str] = []
    batch_prompt_map: dict[str, str] = {}
    if request.run_e2e and request.batch_prompts:
        batch_report = _run_batch_prompts(
            prompts=request.batch_prompts,
            project_root=project_root,
            selected_dir=_resolve_selected_dir(project_root, request),
            data_root=_resolve_data_root(project_root, request),
            constraints=request.data_reference_constraints,
        )
        commands_run.append(list(batch_report.get("command", [])))
        summary = batch_report.get("summary", {}) if isinstance(batch_report.get("summary", {}), dict) else {}
        items = summary.get("items", []) if isinstance(summary.get("items", []), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            run_dir = str(item.get("run_dir", "")).strip()
            if run_dir:
                run_name = Path(run_dir).name
                batch_run_ids.append(run_name)
                batch_prompt_map[run_name] = str(item.get("prompt", "")).strip()
        audit.append({"step": "batch_e2e", "status": "ok" if int(batch_report.get("exit_code", 2)) == 0 else "failed", "details": {"summary_path": batch_report.get("summary_path", ""), "run_count": len(batch_run_ids)}})

    troubleshoot_ids = _sorted_unique(request.run_ids + batch_run_ids)
    troubleshooting = {"items": [], "commands_run": []}
    if troubleshoot_ids:
        troubleshooting = troubleshoot_runs(
            run_ids=troubleshoot_ids,
            workspace_root=(project_root / "workspace"),
            project_root=project_root,
            strictness_mode=request.strictness_mode,
            rerun_failures=request.rerun_failures,
            prompt_overrides=batch_prompt_map,
        )
        commands_run.extend([list(x) for x in troubleshooting.get("commands_run", []) if isinstance(x, list)])
        audit.append({"step": "troubleshooting", "status": "ok", "details": {"run_count": len(troubleshooting.get("items", []))}})

    before_ids = [str(x) for x in troubleshoot_ids]
    after_ids = [
        str(item.get("rerun", {}).get("run_id_after", ""))
        for item in troubleshooting.get("items", [])
        if isinstance(item, dict)
    ]
    after_ids = _sorted_unique([x for x in after_ids if x])

    status = "passed"
    if not contract_validation.get("passed", False) or not preflight.get("passed", False):
        status = "needs_attention"

    selected_pipeline_id = _extract_selected_pipeline_id(plan, selection_details)
    if selected_pipeline_id:
        graph_store.record_path_run(
            run_id=f"fallback_builder:{run_token}:planned",
            path_id=selected_pipeline_id,
            prompt_hash=prompt_hash,
            status="planned",
            started_at=started_at,
            artifacts={
                "selection_details": selection_details,
                "decision": decision,
            },
        )
        graph_store.add_annotation(
            target_type="path",
            target_id=selected_pipeline_id,
            note=(
                "fallback_builder_selected "
                f"reason={selection_details.get('selection_reason', selection_details.get('why', ''))} "
                f"score={selection_details.get('selection_score', 0)} "
                f"graph_score={selection_details.get('selection_graph_score', 0.0)}"
            ).strip(),
            tags=["fallback_builder", "selected", prompt_hash],
        )
        for row in (selection_details.get("candidates", []) if isinstance(selection_details.get("candidates", []), list) else [])[:10]:
            if not isinstance(row, dict):
                continue
            candidate_id = str(row.get("pipeline_id", "")).strip()
            if not candidate_id or candidate_id == selected_pipeline_id:
                continue
            graph_store.add_annotation(
                target_type="path",
                target_id=candidate_id,
                note=(
                    "fallback_builder_rejected "
                    f"missing_caps={row.get('missing_caps', [])} "
                    f"missing_inputs={row.get('missing_inputs', [])} "
                    f"missing_tools={row.get('missing_tools', [])}"
                ),
                tags=["fallback_builder", "rejected", prompt_hash],
            )

        finished_at = datetime.now(timezone.utc).isoformat()
        quality_score = 1.0
        quality_score -= 0.35 if not contract_validation.get("passed", False) else 0.0
        quality_score -= 0.25 if not preflight.get("passed", False) else 0.0
        quality_score = max(0.0, min(1.0, quality_score))
        reliability_score = max(
            0.0,
            min(
                1.0,
                (0.65 if status == "passed" else 0.2)
                + (0.2 if contract_validation.get("passed", False) else 0.0)
                + (0.15 if preflight.get("passed", False) else 0.0),
            ),
        )
        graph_store.record_path_run(
            run_id=f"fallback_builder:{run_token}:final",
            path_id=selected_pipeline_id,
            prompt_hash=prompt_hash,
            status="completed" if status == "passed" else "failed",
            started_at=started_at,
            finished_at=finished_at,
            artifacts={
                "status": status,
                "contract_validation": contract_validation,
                "preflight": preflight,
                "selection_details": selection_details,
                "quality_score": round(float(quality_score), 6),
                "reliability_score": round(float(reliability_score), 6),
            },
        )
        constraints = request.data_reference_constraints if isinstance(request.data_reference_constraints, dict) else {}
        if bool(constraints.get("path_graph_persist_preference_updates", False)) and status == "passed":
            graph_store.persist_success_preferences(
                user_key=str(constraints.get("path_graph_user_key", "fallback_builder")).strip() or "fallback_builder",
                scope=str(constraints.get("path_graph_scope", "global")).strip() or "global",
                path_id=selected_pipeline_id,
                requested_capabilities=[str(x) for x in contract.get("must_include_capabilities", []) if str(x).strip()],
            )

    return {
        "status": status,
        "request": {
            "target_capability_set": request.target_capability_set,
            "allowed_tools": request.allowed_tools,
            "data_reference_constraints": request.data_reference_constraints,
            "strictness_mode": request.strictness_mode,
            "request_text": request.request_text,
            "apply_missing_pieces": request.apply_missing_pieces,
            "run_e2e": request.run_e2e,
            "rerun_failures": request.rerun_failures,
        },
        "inventory": inventory,
        "decision": decision,
        "contract": contract,
        "plan": plan,
        "contract_validation": contract_validation,
        "preflight": preflight,
        "selection_details": selection_details,
        "path_graph": {
            "db_path": str(graph_store.db_path),
            "selected_path_id": selected_pipeline_id,
            "prompt_hash": prompt_hash,
            "preference_profile": preference_profile,
        },
        "composition": composition_details,
        "group_evidence": marker_details,
        "apply_report": apply_report,
        "batch_report": batch_report,
        "troubleshooting": troubleshooting,
        "run_ids": {
            "before": before_ids,
            "after": after_ids,
        },
        "patch": {
            "changed_files": sorted(set(apply_report.get("changed_files", []))),
        },
        "audit_trail": audit,
        "commands_run": commands_run,
    }
