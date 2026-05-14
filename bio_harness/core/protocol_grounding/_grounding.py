"""Protocol grounding extraction and assessment.

Contains the core logic for discovering protocol files, extracting grounding
constraints from benchmark recipes, and assessing whether an LLM-generated
plan satisfies those constraints.
"""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any

from bio_harness.core.benchmark_policy import (
    SCIENTIFIC_HARNESS_POLICY,
    is_blind_bioagentbench_policy,
)
from bio_harness.core.cystic_fibrosis_scaffold import is_cystic_fibrosis_scaffold_command
from bio_harness.core.execution_mode import build_execution_contract
from bio_harness.core.protocol_grounding._shared import (
    DEFAULT_SHARED_VARIANT_COLUMNS,
    DESEQ_METADATA_FILENAMES,
    PROTOCOL_FILENAMES,
    SIGNAL_EQUIVALENCES,
    VARIANT_CALL_TOOLS,
    _dedupe,
    _signal_present_in_text,
)
from bio_harness.core.request_scope import infer_explicit_requested_skill
from bio_harness.core.tool_env import requirement_available
from bio_harness.core.protocol_grounding._plan_merge import _classify_bash_purpose


def _normalize_metadata_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or "").strip().lower())


def _discover_sample_groups_from_metadata(data_root: Path, labels: list[str]) -> tuple[dict[str, str], str]:
    label_lookup = {_normalize_metadata_token(label): label for label in labels}
    for candidate_name in DESEQ_METADATA_FILENAMES:
        candidate = data_root / candidate_name
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            with candidate.open("r", encoding="utf-8", newline="") as handle:
                first_line = handle.readline()
                handle.seek(0)
                delimiter = "," if first_line.count(",") > first_line.count("\t") else "\t"
                reader = csv.DictReader(handle, delimiter=delimiter)
                if not reader.fieldnames:
                    continue
                fields = {str(field).strip().lower(): field for field in reader.fieldnames if str(field).strip()}
                sample_field = next((fields[key] for key in ("sample", "sample_id", "sample_name") if key in fields), "")
                condition_field = next((fields[key] for key in ("condition", "group", "phenotype") if key in fields), "")
                if not sample_field or not condition_field:
                    continue
                sample_groups: dict[str, str] = {}
                for row in reader:
                    sample_value = str(row.get(sample_field, "") or "").strip()
                    condition_value = str(row.get(condition_field, "") or "").strip()
                    if not sample_value or not condition_value:
                        continue
                    matched_label = label_lookup.get(_normalize_metadata_token(sample_value), "")
                    if matched_label:
                        sample_groups[matched_label] = condition_value
                if sample_groups:
                    return sample_groups, str(candidate.resolve(strict=False))
        except Exception:
            continue
    return {}, ""


def _infer_deseq_contrast(sample_groups: dict[str, str], labels: list[str]) -> tuple[str, str]:
    ordered_groups: list[str] = []
    seen: set[str] = set()
    for label in labels:
        group = str(sample_groups.get(label, "") or "").strip()
        if not group or group in seen:
            continue
        ordered_groups.append(group)
        seen.add(group)
    if len(ordered_groups) >= 2:
        control_aliases = {"control", "untreated", "wildtype", "wt", "baseline", "plankton", "planktonic"}
        treatment_aliases = {"treatment", "treated", "case", "disease", "biofilm"}
        control_group = next((group for group in ordered_groups if _normalize_metadata_token(group) in control_aliases), "")
        treatment_group = next((group for group in ordered_groups if _normalize_metadata_token(group) in treatment_aliases), "")
        if not control_group:
            control_group = ordered_groups[0]
        if not treatment_group:
            treatment_group = next((group for group in ordered_groups if group != control_group), ordered_groups[-1])
        if treatment_group and control_group and treatment_group != control_group:
            return control_group, treatment_group
    return "control", "treatment"


def _task_tokens_from_paths(*paths: Path) -> list[str]:
    tokens: list[str] = []
    for path in paths:
        parts = [str(part).strip() for part in Path(path).parts if str(part).strip()]
        for idx, part in enumerate(parts):
            low = part.lower()
            if low in {"tasks", "runs"} and idx + 1 < len(parts):
                tokens.append(parts[idx + 1])
        if parts:
            tokens.append(parts[-1])
    return _dedupe([token for token in tokens if token and token not in {".", "/"}])


def _discover_reference_annotation_path(selected_dir: Path, data_root: Path) -> str:
    for search_root in [
        data_root.parent / "references",
        selected_dir.parent / "references",
        data_root.parent.parent / "references",
        Path("workspace/references").resolve(),
    ]:
        if not search_root.exists():
            continue
        for candidate in sorted(search_root.glob("*")):
            name = candidate.name.lower()
            if candidate.is_file() and name.endswith((".gtf", ".gff", ".gff3", ".gtf.gz", ".gff.gz", ".gff3.gz")):
                return str(candidate.resolve(strict=False))
    return ""


def discover_protocol_files(
    *,
    user_query: str,
    selected_dir: Path,
    data_root: Path,
    project_root: Path,
    benchmark_policy: str = SCIENTIFIC_HARNESS_POLICY,
) -> list[Path]:
    """Discover protocol/reference files relevant to the analysis.

    Searches *data_root*, its sibling ``references/`` directory, and
    *selected_dir* for FASTA, GFF/GTF, VCF, BED, and other reference files
    that inform template compilation and plan grounding.

    Returns:
        Ordered list of discovered :class:`Path` objects (duplicates removed).
    """
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        resolved = path.expanduser().resolve(strict=False)
        key = str(resolved)
        if key in seen or not resolved.exists() or not resolved.is_file():
            return
        seen.add(key)
        candidates.append(resolved)

    search_roots = [
        selected_dir,
        selected_dir.parent,
        selected_dir.parent.parent,
        data_root,
        data_root.parent,
        data_root.parent.parent,
    ]
    for root in search_roots:
        root = Path(root).expanduser().resolve(strict=False)
        if not root.exists() or not root.is_dir():
            continue
        for name in PROTOCOL_FILENAMES:
            _add(root / name)

    task_tokens = _task_tokens_from_paths(selected_dir, data_root)
    if not is_blind_bioagentbench_policy(benchmark_policy) and _external_protocol_grounding_enabled():
        external_root = (project_root / "external").resolve(strict=False)
        if external_root.exists():
            for token in task_tokens:
                pattern_roots = list(external_root.glob(f"*/tasks/{token}"))
                for root in pattern_roots:
                    for name in PROTOCOL_FILENAMES:
                        _add(root / name)

        results_root = data_root.parent / "results"
        if results_root.exists() and results_root.is_dir():
            for csv_path in sorted(results_root.glob("*.csv"))[:4]:
                _add(csv_path)

    return candidates[:16]


def _external_protocol_grounding_enabled() -> bool:
    scope = os.getenv("BIO_HARNESS_PROTOCOL_GROUNDING_SCOPE", "").strip().lower()
    if scope in {"local", "local_only", "task_local"}:
        return False
    disabled = os.getenv("BIO_HARNESS_DISABLE_EXTERNAL_PROTOCOL_GROUNDING", "")
    return disabled.strip().lower() not in {"1", "true", "yes", "on"}


def _read_excerpt(path: Path, max_chars: int = 120000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def _append_param_hint(target: list[dict[str, Any]], tool_name: str, settings: dict[str, Any], rationale: str) -> None:
    if not tool_name or not settings:
        return
    target.append(
        {
            "tool_name": str(tool_name).strip(),
            "settings": dict(settings),
            "rationale": str(rationale or "").strip(),
        }
    )


def _merge_param_hints(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool_name", "")).strip()
        settings = item.get("settings", {})
        if not tool or not isinstance(settings, dict):
            continue
        row = merged.setdefault(tool, {"tool_name": tool, "settings": {}, "rationale": ""})
        row["settings"].update(settings)
        rationale = str(item.get("rationale", "")).strip()
        if rationale:
            existing = str(row.get("rationale", "")).strip()
            row["rationale"] = rationale if not existing else f"{existing} {rationale}".strip()
    return list(merged.values())


def _result_file_header_constraints(
    protocol_files: list[Path],
    *,
    preferred_names: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    output_columns: list[str] = []
    notes: list[str] = []
    preferred = [str(name).strip().lower() for name in (preferred_names or []) if str(name).strip()]
    csv_files = [path for path in protocol_files if path.suffix.lower() == ".csv"]
    if preferred:
        prioritized: list[Path] = []
        remaining: list[Path] = []
        for path in csv_files:
            name_l = path.name.lower()
            if name_l in preferred or any(token in name_l for token in preferred):
                prioritized.append(path)
            else:
                remaining.append(path)
        csv_files = prioritized + remaining
    for path in csv_files:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
        except Exception:
            continue
        cleaned = [str(col or "").strip() for col in header if str(col or "").strip()]
        if cleaned:
            output_columns = cleaned
            notes.append(f"Deliverable columns should match {', '.join(cleaned[:8])}.")
            break
    return output_columns, notes


def _benchmark_protocol_profile(
    *,
    analysis_type: str,
    task_name: str,
    protocol_files: list[Path],
    user_query: str,
) -> dict[str, Any]:
    task_name_l = str(task_name or "").strip().lower()
    query_l = str(user_query or "").lower()
    if analysis_type != "bacterial_evolution_variant_calling":
        return {}
    if task_name_l and task_name_l != "evolution" and "evolution" not in query_l and "evolved" not in query_l:
        return {}

    recipe_text = "\n".join(_read_excerpt(path, max_chars=30000) for path in protocol_files if path.name == "run_script.sh")
    recipe_l = recipe_text.lower()
    if not recipe_l:
        return {}

    uses_benchmark_recipe = all(token in recipe_l for token in ("spades.py", "freebayes", "snpeff"))
    if not uses_benchmark_recipe:
        return {}

    return {
        "profile_id": "bioagent_bench_evolution_shared_v1",
        "benchmark_family": "bioagent_bench",
        "analytical_method": "freebayes_call",
        "reference_strategy": "assembled_ancestor_scaffolds",
        "requires_shared_comparison": True,
        "sample_roles": {"reference": "ancestor", "comparators": ["evolved", "evolved"]},
        "annotation_strategy": {
            "tool_name": "prokka_annotate",
            "fallback_tool_name": "prodigal_annotate",
            "kingdom": "Bacteria",
            "genus": "Escherichia",
            "species": "coli",
        },
        "post_alignment_policy": {
            "mode": "fixmate_markdup_q20",
            "remove_duplicates": True,
            "mapq_min": 20,
        },
        "variant_filter": {
            "tool_signal": "vcffilter",
            # Fix #18: INFO/-qualify AO (bcftools errors with "ambiguous"
            # when FreeBayes defines both INFO/AO and FORMAT/AO). The other
            # fields are INFO-only in FreeBayes but we qualify them too for
            # forward compatibility with future FORMAT duplicates.
            "expression": "QUAL > 1 & QUAL / INFO/AO > 10 & INFO/SAF > 0 & INFO/SAR > 0 & INFO/RPR > 1 & INFO/RPL > 1",
        },
        "shared_variant_policy": {
            "normalize_before_compare": True,
            "compare_mode": "intersection",
            "dedupe_by_gene": True,
            "min_impact": "MODERATE",
            "accepted_impacts": ["HIGH", "MODERATE"],
        },
        "export_profile": {
            "filename": "variants_shared.csv",
            "header_case": "upper",
            "status": "shared",
            "dedupe_by_gene": True,
            "min_impact": "MODERATE",
            "output_columns": list(DEFAULT_SHARED_VARIANT_COLUMNS),
        },
        "calibration_axes": [
            "shared_variant_normalization",
            "annotation_namespace",
            "vcf_filter_string",
            "post_alignment_dedup_mapq",
        ],
    }


def _normalize_skeleton_tool_hint(tool_hint: str) -> str:
    """Return a stable signal token for a tool hint embedded in a skeleton."""

    token = str(tool_hint or "").strip().lower()
    if not token:
        return ""
    if token.startswith("spades.py --meta"):
        return "spades"
    return token


def _excluded_signal_hints_for_analysis(
    analysis_type: str,
    *,
    official_benchmark_policy: bool,
) -> frozenset[str]:
    """Return skeleton signal hints that are advisory in official mode."""

    if not official_benchmark_policy:
        return frozenset()
    excluded_hints: dict[str, frozenset[str]] = {
        "germline_variant_calling": frozenset({"hap.py"}),
        "variant_annotation": frozenset({"snpsift"}),
        "multi_model_dge_pathway": frozenset({"python"}),
        "metagenomics_classification": frozenset({"cp"}),
        "viral_metagenomics": frozenset({"fastp", "samtools", "python3"}),
    }
    return excluded_hints.get(analysis_type, frozenset())


def _collect_skeleton_requirements(
    *,
    skeleton: list[Any],
    available_skill_names: set[str],
    excluded_signal_hints: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Extract required tools and signal hints from a seeded plan skeleton."""

    required_tools: list[str] = []
    required_signals: list[str] = []
    for entry in skeleton:
        tool_name = ""
        if isinstance(entry, (list, tuple)) and len(entry) >= 1:
            tool_name = str(entry[0]).strip()
        elif isinstance(entry, dict):
            tool_name = str(entry.get("tool_name", "")).strip()
        if tool_name and tool_name != "bash_run":
            if tool_name in available_skill_names:
                required_tools.append(tool_name)
            required_signals.append(tool_name.lower())
        if isinstance(entry, (list, tuple)) and len(entry) >= 3:
            params = entry[2] if isinstance(entry[2], dict) else {}
        elif isinstance(entry, dict):
            params = entry.get("default_params", {}) or {}
        else:
            params = {}
        tool_hint = _normalize_skeleton_tool_hint(params.get("tool", ""))
        if tool_hint and tool_hint not in excluded_signal_hints:
            required_signals.append(tool_hint)
    return _dedupe(required_tools), _dedupe(required_signals)


def _resolve_execution_contract_for_grounding(
    *,
    analysis_type: str,
    user_query: str,
    available_skill_names: set[str],
    analysis_spec: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the execution contract that should drive generic grounding."""

    if isinstance(analysis_spec, dict):
        existing = analysis_spec.get("execution_contract", {})
        if isinstance(existing, dict) and existing:
            return dict(existing)
    explicit_intent = (
        analysis_spec.get("explicit_execution_intent", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    chosen_method = str((analysis_spec or {}).get("chosen_method", "") or "").strip()
    if not chosen_method:
        explicit_skill = infer_explicit_requested_skill(user_query, list(available_skill_names))
        if explicit_skill:
            chosen_method = explicit_skill
            if not explicit_intent:
                explicit_intent = {"locked_tools": [explicit_skill]}
    return build_execution_contract(
        analysis_type=analysis_type,
        user_query=user_query,
        chosen_method=chosen_method,
        contract=contract,
        explicit_execution_intent=explicit_intent,
        available_skill_names=sorted(available_skill_names),
    )


def _generic_direct_wrapper_grounding(
    *,
    analysis_type: str,
    execution_contract: dict[str, Any],
    available_skill_names: set[str],
    chosen_method: str,
) -> dict[str, Any]:
    """Build compatibility grounding for direct-wrapper execution modes."""

    input_mode = str(execution_contract.get("input_mode", "") or "").strip()
    compatible_tools = [
        str(tool).strip()
        for tool in (execution_contract.get("compatible_tools", []) or [])
        if str(tool).strip() and (not available_skill_names or str(tool).strip() in available_skill_names)
    ]
    locked_tools = [
        str(tool).strip()
        for tool in (execution_contract.get("locked_tools", []) or [])
        if str(tool).strip() and (not available_skill_names or str(tool).strip() in available_skill_names)
    ]
    requested_tools = [
        str(tool).strip()
        for tool in (execution_contract.get("required_tools", []) or [])
        if str(tool).strip() and (not available_skill_names or str(tool).strip() in available_skill_names)
    ]
    required_tools = list(locked_tools or requested_tools)
    if not required_tools and chosen_method and (not compatible_tools or chosen_method in compatible_tools):
        required_tools = [chosen_method]
    if not required_tools and len(compatible_tools) == 1:
        required_tools = [compatible_tools[0]]
    if not compatible_tools and required_tools:
        compatible_tools = list(required_tools)
    binding_rules = [
        (
            f"When inputs are already in {input_mode or 'direct-wrapper'} form, preserve a compatible "
            "single-wrapper workflow instead of expanding to upstream template stages."
        ),
    ]
    if required_tools:
        binding_rules.append(
            "Do not substitute sibling wrappers when the selected direct wrapper already matches the provided inputs."
        )
    return {
        "grounded": True,
        "task_name": analysis_type,
        "analysis_type": analysis_type,
        "analysis_family": str(execution_contract.get("analysis_family", "") or analysis_type).strip(),
        "input_mode": input_mode,
        "execution_mode": "direct_wrapper",
        "compatible_tools": compatible_tools,
        "required_tools": required_tools,
        "preferred_tools": compatible_tools,
        "required_plan_signals": list(required_tools),
        "binding_rules": binding_rules,
        "source_files": [],
        "analytical_method": chosen_method or (required_tools[0] if required_tools else ""),
    }


def _generic_analysis_grounding(
    analysis_type: str,
    user_query: str = "",
    available_skill_names: list[str] | None = None,
    benchmark_policy: str = SCIENTIFIC_HARNESS_POLICY,
    analysis_spec: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build grounding constraints from the analysis type when no protocol files exist.

    This ensures every analysis type gets structural anchoring even for novel
    prompts that don't have benchmark protocol files.
    """
    available = {str(name).strip() for name in (available_skill_names or []) if str(name).strip()}

    # Import here to avoid circular dependency (analysis_spec -> protocol_grounding)
    try:
        from bio_harness.core.analysis_spec import _profile_seed
        explicit_skill = infer_explicit_requested_skill(user_query, list(available))
        seed_query = user_query if explicit_skill else ""
        seed = _profile_seed(analysis_type, seed_query, list(available))
    except Exception:
        return {}

    execution_contract = _resolve_execution_contract_for_grounding(
        analysis_type=analysis_type,
        user_query=user_query,
        available_skill_names=available,
        analysis_spec=analysis_spec,
        contract=contract,
    )
    chosen_method = str((analysis_spec or {}).get("chosen_method", "") or "").strip()
    if not chosen_method:
        chosen_method = str(seed.get("chosen_method", "") or "").strip()
    if (
        not chosen_method
        and str(execution_contract.get("execution_mode", "") or "").strip() == "direct_wrapper"
        and user_query
    ):
        try:
            chosen_method = str(_profile_seed(analysis_type, user_query, list(available)).get("chosen_method", "") or "").strip()
        except Exception:
            chosen_method = ""
    if str(execution_contract.get("execution_mode", "") or "").strip() == "direct_wrapper":
        direct_grounding = _generic_direct_wrapper_grounding(
            analysis_type=analysis_type,
            execution_contract=execution_contract,
            available_skill_names=available,
            chosen_method=chosen_method,
        )
        if any(
            direct_grounding.get(key)
            for key in ("required_tools", "preferred_tools", "required_plan_signals", "compatible_tools")
        ):
            return direct_grounding

    skeleton = seed.get("plan_skeleton", [])
    if not skeleton:
        return {}

    official_benchmark_policy = is_blind_bioagentbench_policy(benchmark_policy)
    required_tools, required_signals = _collect_skeleton_requirements(
        skeleton=skeleton,
        available_skill_names=available,
        excluded_signal_hints=_excluded_signal_hints_for_analysis(
            analysis_type,
            official_benchmark_policy=official_benchmark_policy,
        ),
    )

    if not required_tools and not required_signals:
        return {}

    return {
        "grounded": True,
        "task_name": analysis_type,
        "analysis_type": analysis_type,
        "analysis_family": str(execution_contract.get("analysis_family", "") or analysis_type).strip(),
        "input_mode": str(execution_contract.get("input_mode", "") or "").strip(),
        "execution_mode": str(execution_contract.get("execution_mode", "") or "compiled_pipeline").strip(),
        "compatible_tools": [
            str(tool).strip()
            for tool in (execution_contract.get("compatible_tools", []) or [])
            if str(tool).strip()
        ],
        "required_tools": _dedupe(required_tools),
        "required_plan_signals": _dedupe(required_signals),
        "source_files": [],
        "binding_rules": [
            f"This plan follows the {analysis_type} analysis template.",
            "Include all steps from the plan skeleton in the correct order.",
        ],
    }


def extract_protocol_grounding(
    *,
    user_query: str,
    analysis_type: str,
    selected_dir: Path,
    data_root: Path,
    project_root: Path,
    available_skill_names: list[str] | None = None,
    benchmark_policy: str = SCIENTIFIC_HARNESS_POLICY,
    analysis_spec: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build protocol grounding metadata from plan skeletons and data files.

    Combines the analysis-type plan skeleton with discovered protocol files
    to produce a grounding dict containing ``required_tools``,
    ``required_plan_signals``, and ``binding_rules`` that guide the LLM
    planner and template compilers.

    Returns:
        Grounding dict (may be empty if no skeleton or files are found).
    """
    if analysis_type == "direct_skill_smoke":
        return {}

    available = {str(name).strip() for name in (available_skill_names or []) if str(name).strip()}
    protocol_files = discover_protocol_files(
        user_query=user_query,
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        benchmark_policy=benchmark_policy,
    )
    if not protocol_files:
        grounding = _generic_analysis_grounding(
            analysis_type,
            user_query,
            available_skill_names,
            benchmark_policy=benchmark_policy,
            analysis_spec=analysis_spec,
            contract=contract,
        )
        if (
            grounding
            and analysis_type == "rna_seq_differential_expression"
            and str(grounding.get("execution_mode", "") or "").strip() != "direct_wrapper"
        ):
            annotation_path = _discover_reference_annotation_path(selected_dir, data_root).lower()
            if annotation_path.endswith((".gff", ".gff3", ".gff.gz", ".gff3.gz")):
                grounding["required_tools"] = [
                    tool for tool in grounding.get("required_tools", [])
                    if str(tool).strip() != "star_align"
                ]
                grounding["required_plan_signals"] = [
                    signal for signal in grounding.get("required_plan_signals", [])
                    if str(signal).strip().lower() != "star_align"
                ]
                if "subread_align" in available:
                    grounding["required_tools"] = _dedupe(list(grounding.get("required_tools", []) or []) + ["subread_align"])
                    grounding["required_plan_signals"] = _dedupe(
                        list(grounding.get("required_plan_signals", []) or []) + ["subread_align", "featurecounts_run"]
                    )
                grounding["binding_rules"] = _dedupe(
                    list(grounding.get("binding_rules", []) or [])
                    + ["When only GFF annotation is available, a Subjunc plus featureCounts path is acceptable."]
                )
        return grounding

    preferred_tools: list[str] = []
    required_tools: list[str] = []
    discouraged_tools: list[str] = []
    required_plan_signals: list[str] = []
    binding_rules: list[str] = []
    notes: list[str] = []
    parameter_profile: list[dict[str, Any]] = []
    query_l = str(user_query or "").lower()

    for path in protocol_files:
        text = _read_excerpt(path)
        lowered = text.lower()
        if not lowered:
            continue

        if "spades.py" in lowered and "--careful" in lowered:
            preferred_tools.append("spades_assemble")
            required_tools.append("spades_assemble")
            required_plan_signals.append("spades")
            _append_param_hint(
                parameter_profile,
                "spades_assemble",
                {"careful": True},
                "Task-local recipe uses SPAdes careful mode.",
            )
        if "freebayes" in lowered:
            preferred_tools.append("freebayes_call")
            required_tools.append("freebayes_call")
            required_plan_signals.append("freebayes")
            if re.search(r"freebayes\b[^\n]*\s-p\s+1\b", lowered):
                _append_param_hint(
                    parameter_profile,
                    "freebayes_call",
                    {"ploidy": 1},
                    "Task-local recipe uses haploid FreeBayes calling.",
                )
        if "vcffilter" in lowered:
            required_plan_signals.append("vcffilter")
            binding_rules.append("Include an explicit post-caller variant filtering step before annotation/export.")
            if requirement_available("vcffilter"):
                notes.append("Local environment has `vcffilter`; a dedicated variant-filter step is viable.")
        if "snpeff" in lowered:
            preferred_tools.append("snpeff_annotate")
            required_tools.append("snpeff_annotate")
            required_plan_signals.append("snpeff")
        if "prokka" in lowered:
            preferred_tools.append("prokka_annotate")
            required_tools.append("prokka_annotate")
        if "prodigal" in lowered:
            preferred_tools.append("prodigal_annotate")
        if "salmon quant" in lowered:
            preferred_tools.append("salmon_quant")
            required_tools.append("salmon_quant")
            if "--validatemappings" in lowered:
                _append_param_hint(
                    parameter_profile,
                    "salmon_quant",
                    {"validateMappings": True},
                    "Task-local recipe enables validateMappings.",
                )
        if "kallisto quant" in lowered:
            preferred_tools.append("kallisto_quant")
        if "featurecounts" in lowered:
            preferred_tools.append("featurecounts_run")
            if re.search(r"featurecounts\b[^\n]*\s-p(\s|$)", lowered):
                _append_param_hint(
                    parameter_profile,
                    "featurecounts_run",
                    {"count_read_pairs": True},
                    "Task-local recipe uses paired-end counting.",
                )
        if "deseq2" in lowered:
            preferred_tools.append("deseq2_run")
        if any(token in lowered for token in ("results/variants_shared.csv", "variants_shared.csv")):
            binding_rules.append("Final deliverable must be a shared-variant CSV matching the benchmark naming/column semantics.")
            if not available or "shared_variants_export_run" in available:
                preferred_tools.append("shared_variants_export_run")
                required_tools.append("shared_variants_export_run")
                required_plan_signals.append("shared_variants_export_run")
        if "/assembly/scaffolds.fasta" in lowered or "scaffolds.fasta" in lowered:
            binding_rules.append("Use assembled scaffolds as the working reference if the local recipe does so.")
        if "gene" in lowered and "impact" in lowered and "effect" in lowered and "status" in lowered:
            notes.append("Final export should preserve gene, impact, effect, and status columns.")

    task_tokens = _task_tokens_from_paths(selected_dir, data_root)
    task_name = task_tokens[0] if task_tokens else ""
    benchmark_profile = _benchmark_protocol_profile(
        analysis_type=analysis_type,
        task_name=task_name,
        protocol_files=protocol_files,
        user_query=user_query,
    )
    preferred_result_names = []
    if isinstance(benchmark_profile.get("export_profile", {}), dict):
        preferred_result_names.append(str(benchmark_profile.get("export_profile", {}).get("filename", "")).strip())
    preferred_result_names.append("variants_shared.csv")
    output_columns, output_notes = _result_file_header_constraints(protocol_files, preferred_names=preferred_result_names)
    if not output_columns and isinstance(benchmark_profile.get("export_profile", {}), dict):
        output_columns = [
            str(col).strip()
            for col in (benchmark_profile.get("export_profile", {}).get("output_columns", []) or [])
            if str(col).strip()
        ]
        if output_columns:
            output_notes.append(f"Deliverable columns should match {', '.join(output_columns[:8])}.")
    notes.extend(output_notes)

    preferred_tools = [tool for tool in _dedupe(preferred_tools) if not available or tool in available]
    required_tools = [tool for tool in _dedupe(required_tools) if not available or tool in available]
    discouraged_tools = [tool for tool in _dedupe(discouraged_tools) if not available or tool in available]
    parameter_profile = [
        item
        for item in _merge_param_hints(parameter_profile)
        if not available or str(item.get("tool_name", "")).strip() in available
    ]

    requires_shared_comparison = False
    min_variant_branches = 0
    if analysis_type == "bacterial_evolution_variant_calling":
        if (
            ("shared" in query_l and any(token in query_l for token in ("isolate", "isolates", "line", "lines")))
            or "variants_shared.csv" in " ".join(str(path).lower() for path in protocol_files)
        ):
            requires_shared_comparison = True
            min_variant_branches = 2
            binding_rules.append("Shared-variant tasks require separate variant branches for at least two evolved comparands before intersection/export.")
        benchmark_export = (
            benchmark_profile.get("export_profile", {})
            if isinstance(benchmark_profile.get("export_profile", {}), dict)
            else {}
        )
        export_filename = str(benchmark_export.get("filename", "") or "").strip()
        if export_filename == "variants_shared.csv" and (
            not available or "shared_variants_export_run" in available
        ):
            preferred_tools.append("shared_variants_export_run")
            required_tools.append("shared_variants_export_run")
            required_plan_signals.append("shared_variants_export_run")

    grounding = {
        "grounded": True,
        "task_name": task_name,
        "analysis_type": analysis_type,
        "benchmark_policy": benchmark_policy,
        "source_files": [str(path) for path in protocol_files],
        "required_tools": _dedupe(required_tools),
        "preferred_tools": _dedupe(preferred_tools),
        "discouraged_tools": discouraged_tools,
        "required_plan_signals": _dedupe(required_plan_signals),
        "parameter_profile": parameter_profile,
        "binding_rules": _dedupe(binding_rules),
        "output_columns": output_columns,
        "notes": _dedupe(notes),
        "requires_shared_comparison": requires_shared_comparison,
        "min_variant_branches": min_variant_branches,
        "analytical_method": str(benchmark_profile.get("analytical_method", "")).strip(),
        "benchmark_profile": benchmark_profile,
    }
    execution_contract = _resolve_execution_contract_for_grounding(
        analysis_type=analysis_type,
        user_query=user_query,
        available_skill_names=available,
        analysis_spec=analysis_spec,
        contract=contract,
    )
    if execution_contract:
        grounding.setdefault(
            "analysis_family",
            str(execution_contract.get("analysis_family", "") or analysis_type).strip(),
        )
        grounding.setdefault(
            "input_mode",
            str(execution_contract.get("input_mode", "") or "").strip(),
        )
        grounding.setdefault(
            "execution_mode",
            str(execution_contract.get("execution_mode", "") or "").strip(),
        )
        grounding.setdefault(
            "compatible_tools",
            [
                str(tool).strip()
                for tool in (execution_contract.get("compatible_tools", []) or [])
                if str(tool).strip()
            ],
        )
    if not any(grounding.get(key) for key in ("required_tools", "preferred_tools", "required_plan_signals", "parameter_profile", "binding_rules", "output_columns", "notes")):
        return {}
    return grounding


def analysis_patch_from_protocol(
    grounding: dict[str, Any],
    *,
    available_skill_names: list[str] | None = None,
    analysis_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert protocol grounding into an analysis-spec patch.

    Extracts tool requirements, parameter profiles, and binding rules from
    *grounding* and formats them as a dict suitable for merging into the
    analysis spec that drives plan generation. When an existing analysis spec
    carries locked explicit execution intent, the patch preserves that wrapper
    choice instead of overwriting it with generic grounding defaults.
    """
    if not isinstance(grounding, dict) or not grounding:
        return {}
    available = {str(name).strip() for name in (available_skill_names or []) if str(name).strip()}

    def _filter_tools(values: list[str]) -> list[str]:
        cleaned = _dedupe([str(v).strip() for v in values if str(v).strip()])
        if available:
            cleaned = [value for value in cleaned if value in available]
        return cleaned

    def _merge_profile_entries(*profile_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge parameter-profile entries without dropping seed-specific hints."""

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in profile_groups:
            for entry in group:
                if not isinstance(entry, dict):
                    continue
                key = json.dumps(entry, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(dict(entry))
        return merged

    patch: dict[str, Any] = {"protocol_grounding": grounding}
    explicit_intent = (
        analysis_spec.get("explicit_execution_intent", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    locked_tools = _filter_tools(list(explicit_intent.get("locked_tools", []) or []))
    current_preferred_tools = _filter_tools(
        list((analysis_spec or {}).get("preferred_tools", []) or [])
    )
    current_chosen_method = str((analysis_spec or {}).get("chosen_method", "") or "").strip()
    source_provenance = [str(path) for path in grounding.get("source_files", []) if str(path).strip()]
    if source_provenance:
        patch["source_provenance"] = source_provenance
    preferred_tools = _filter_tools(list(grounding.get("preferred_tools", []) or []))
    if preferred_tools:
        patch["preferred_tools"] = (
            _dedupe(locked_tools + current_preferred_tools + preferred_tools)
            if locked_tools
            else _dedupe(current_preferred_tools + preferred_tools)
        )
    compatible_tools = _filter_tools(list(grounding.get("compatible_tools", []) or []))
    if compatible_tools:
        patch["preferred_tools"] = _dedupe(
            locked_tools
            + current_preferred_tools
            + compatible_tools
            + list(patch.get("preferred_tools", []) or [])
        )
    discouraged_tools = _filter_tools(list(grounding.get("discouraged_tools", []) or []))
    if discouraged_tools:
        patch["discouraged_tools"] = discouraged_tools
    current_parameter_profile = list((analysis_spec or {}).get("parameter_profile", []) or [])
    parameter_profile = list(grounding.get("parameter_profile", []) or [])
    merged_parameter_profile = _merge_profile_entries(
        current_parameter_profile,
        parameter_profile,
    )
    if merged_parameter_profile:
        patch["parameter_profile"] = merged_parameter_profile
    acceptance_checks = list(grounding.get("notes", []) or [])
    if acceptance_checks:
        patch["acceptance_checks"] = acceptance_checks
    analytical_method = str(grounding.get("analytical_method", "")).strip()
    benchmark_profile = grounding.get("benchmark_profile", {}) if isinstance(grounding.get("benchmark_profile", {}), dict) else {}
    if not analytical_method:
        analytical_method = str(benchmark_profile.get("analytical_method", "")).strip()
    required_tools = _filter_tools(list(grounding.get("required_tools", []) or []))
    if required_tools:
        if locked_tools:
            patch["preferred_tools"] = _dedupe(
                locked_tools
                + current_preferred_tools
                + list(patch.get("preferred_tools", []) or [])
                + required_tools
            )
            patch["chosen_method"] = current_chosen_method or locked_tools[0]
        else:
            patch["preferred_tools"] = _dedupe(required_tools + list(patch.get("preferred_tools", []) or []))
            if analytical_method:
                patch["chosen_method"] = analytical_method
            elif not patch.get("chosen_method"):
                patch["chosen_method"] = required_tools[0]
    elif analytical_method:
        if locked_tools:
            patch["preferred_tools"] = _dedupe(
                locked_tools
                + current_preferred_tools
                + list(patch.get("preferred_tools", []) or [])
            )
            patch["chosen_method"] = current_chosen_method or locked_tools[0]
        else:
            patch["chosen_method"] = analytical_method
    elif locked_tools:
        patch["preferred_tools"] = _dedupe(
            locked_tools
            + current_preferred_tools
            + list(patch.get("preferred_tools", []) or [])
        )
        patch["chosen_method"] = current_chosen_method or locked_tools[0]
    elif compatible_tools and len(compatible_tools) == 1:
        patch["chosen_method"] = compatible_tools[0]
    binding_rules = [str(rule).strip() for rule in (grounding.get("binding_rules", []) or []) if str(rule).strip()]
    if binding_rules:
        patch["context_facts"] = binding_rules
    return patch


def assess_protocol_grounding(plan: dict[str, Any], analysis_spec: dict[str, Any] | None) -> dict[str, Any]:
    """Check whether *plan* satisfies the protocol grounding requirements.

    Verifies that all ``required_tools`` appear as plan step tool names and
    all ``required_plan_signals`` appear somewhere in the serialised plan.

    Returns:
        Assessment dict with ``passed`` (bool), ``missing_required_tools``,
        ``missing_plan_signals``, ``issues``, and ``source_files``.
    """
    grounding = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("protocol_grounding", {}), dict)
        else {}
    )
    if not grounding:
        return {"passed": True, "missing_required_tools": [], "missing_plan_signals": [], "issues": [], "source_files": []}

    plan_text = ""
    try:
        plan_text = json.dumps(plan or {}, ensure_ascii=True).lower()
    except Exception:
        plan_text = str(plan or "").lower()

    required_tools = [str(x).strip() for x in (grounding.get("required_tools", []) or []) if str(x).strip()]
    benchmark_profile = grounding.get("benchmark_profile", {}) if isinstance(grounding.get("benchmark_profile", {}), dict) else {}
    annotation_strategy = (
        benchmark_profile.get("annotation_strategy", {})
        if isinstance(benchmark_profile.get("annotation_strategy", {}), dict)
        else {}
    )
    annotation_tool = str(annotation_strategy.get("tool_name", "") or "").strip().lower()
    fallback_annotation_tool = str(annotation_strategy.get("fallback_tool_name", "") or "").strip().lower()
    missing_required_tools = []
    for tool_name in required_tools:
        normalized_tool = tool_name.lower()
        if normalized_tool == annotation_tool and fallback_annotation_tool:
            if (
                f'"tool_name": "{normalized_tool}"' in plan_text
                or normalized_tool in plan_text
                or f'"tool_name": "{fallback_annotation_tool}"' in plan_text
                or fallback_annotation_tool in plan_text
            ):
                continue
        if f'"tool_name": "{normalized_tool}"' not in plan_text and normalized_tool not in plan_text:
            missing_required_tools.append(tool_name)

    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        steps = []

    missing_signals = []
    for signal in [str(x).strip().lower() for x in (grounding.get("required_plan_signals", []) or []) if str(x).strip()]:
        if signal == "snpsift":
            has_variant_filter_step = False
            for step in steps:
                if not isinstance(step, dict):
                    continue
                tool_name = str(step.get("tool_name", "")).strip().lower()
                if tool_name != "bash_run":
                    continue
                args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
                command = str(args.get("command", "")).strip()
                if "variant_filter" in _classify_bash_purpose(command):
                    has_variant_filter_step = True
                    break
            if has_variant_filter_step:
                continue
        if _signal_present_in_text(signal, plan_text):
            continue
        missing_signals.append(signal)

    issues: list[dict[str, Any]] = []
    analytical_method = str(grounding.get("analytical_method", "")).strip().lower()
    if not analytical_method:
        analytical_method = str(benchmark_profile.get("analytical_method", "")).strip().lower()
    chosen_method = str(analysis_spec.get("chosen_method", "")).strip().lower() if isinstance(analysis_spec, dict) else ""
    branch_method = analytical_method or chosen_method
    min_variant_branches = int(grounding.get("min_variant_branches", 0) or 0)
    if min_variant_branches > 0 and branch_method:
        # Count dedicated skill steps matching the branch method
        chosen_method_steps = [
            step for step in steps
            if isinstance(step, dict) and str(step.get("tool_name", "")).strip().lower() == branch_method
        ]
        # Also count bash_run steps whose command contains the variant caller binary
        branch_equivalents = SIGNAL_EQUIVALENCES.get(branch_method, [branch_method])
        for step in steps:
            if not isinstance(step, dict) or str(step.get("tool_name", "")).strip().lower() != "bash_run":
                continue
            cmd = str((step.get("arguments") or {}).get("command", "")).lower()
            if any(eq.lower() in cmd for eq in branch_equivalents):
                chosen_method_steps.append(step)
        if len(chosen_method_steps) < min_variant_branches:
            issues.append(
                {
                    "issue": "insufficient_comparison_branches",
                    "expected_min": min_variant_branches,
                    "observed": len(chosen_method_steps),
                    "chosen_method": branch_method,
                }
            )

    if branch_method and "vcffilter" in [str(x).strip().lower() for x in (grounding.get("required_plan_signals", []) or [])]:
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            tool_name = str(step.get("tool_name", "")).strip().lower()
            if tool_name not in VARIANT_CALL_TOOLS or tool_name == branch_method:
                continue
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            rendered = " ".join(str(v or "").strip().lower() for v in args.values())
            if "filter" not in rendered and "filtered" not in rendered:
                continue
            issues.append(
                {
                    "issue": "secondary_variant_caller_in_filter_stage",
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": tool_name,
                    "chosen_method": branch_method,
                }
            )
            break

    benchmark_export = benchmark_profile.get("export_profile", {}) if isinstance(benchmark_profile.get("export_profile", {}), dict) else {}
    expected_header_case = str(benchmark_export.get("header_case", "")).strip().lower()
    expected_annotation_tool = str((benchmark_profile.get("annotation_strategy", {}) or {}).get("tool_name", "")).strip().lower()
    fallback_annotation_tool = str((benchmark_profile.get("annotation_strategy", {}) or {}).get("fallback_tool_name", "")).strip().lower()
    if expected_annotation_tool:
        present_tools = {
            str(step.get("tool_name", "")).strip().lower()
            for step in steps
            if isinstance(step, dict) and str(step.get("tool_name", "")).strip()
        }
        # Genome annotation tools are functionally equivalent for validation
        _ANNOTATION_EQUIVALENTS = {
            "prokka_annotate": {"prokka_annotate", "prodigal_annotate"},
            "prodigal_annotate": {"prodigal_annotate", "prokka_annotate"},
        }
        acceptable = _ANNOTATION_EQUIVALENTS.get(expected_annotation_tool, {expected_annotation_tool})
        if fallback_annotation_tool:
            acceptable = acceptable | _ANNOTATION_EQUIVALENTS.get(fallback_annotation_tool, {fallback_annotation_tool})
        if not (present_tools & acceptable):
            issues.append({"issue": "missing_benchmark_annotation_stage", "expected_tool": expected_annotation_tool})
    if expected_header_case == "upper":
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or str(step.get("tool_name", "")).strip().lower() != "bash_run":
                continue
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            command_l = str(args.get("command", "")).strip().lower()
            if "export_shared_variants_csv.py" not in command_l:
                continue
            if "--header-case upper" not in command_l:
                issues.append({"issue": "shared_variant_export_header_case_mismatch", "step_id": int(step.get("step_id", idx))})
            break

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        command_l = command.lower()
        if is_cystic_fibrosis_scaffold_command(command, analysis_spec=analysis_spec):
            continue
        parses_vcf_with_shell = (
            ".vcf" in command_l
            and any(token in command_l for token in ("awk ", "grep ", "sed ", "cut "))
            and any(token in command_l for token in ("impact=", "ann", "gene_name", "function", "cog", ".csv"))
        )
        parses_vcf_with_inline_python = (
            ".vcf" in command_l
            and any(token in command_l for token in ("import vcf", "pysam.variantfile", "csv.dictwriter", "writerows("))
        )
        if parses_vcf_with_shell or parses_vcf_with_inline_python:
            issues.append(
                {
                    "issue": "brittle_structured_variant_export",
                    "step_id": int(step.get("step_id", idx)),
                }
            )
            break

    return {
        "passed": not missing_required_tools and not missing_signals and not issues,
        "missing_required_tools": missing_required_tools,
        "missing_plan_signals": missing_signals,
        "issues": issues,
        "source_files": list(grounding.get("source_files", []) or []),
        "task_name": str(grounding.get("task_name", "") or "").strip(),
    }
