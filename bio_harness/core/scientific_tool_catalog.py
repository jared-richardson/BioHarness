"""Bundled scientific tool catalog helpers.

The scientific tool catalog merges repo-supported wrappers from the generated
skill index with a curated supplemental catalog of common bioinformatics tools
and repo-local helper scripts. This keeps one normalized source of truth for:

- tools the harness can execute directly via wrappers
- repo helper scripts that are safe to mention explicitly
- common external tools that are useful repair/planning context, even when the
  repo does not yet expose a dedicated wrapper
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping


SCIENTIFIC_TOOL_CATALOG_PATH = Path(__file__).resolve().parents[1] / "capabilities" / "scientific_tools.json"
SKILL_INDEX_PATH = Path(__file__).resolve().parents[1] / "skills" / "definitions" / "index.json"

EXCLUDED_SCIENTIFIC_SKILLS = frozenset({"bash_run", "fallback_skill_builder"})
_SUFFIX_TOKENS = (
    "_align",
    "_call",
    "_annotate",
    "_run",
    "_workflow",
    "_style",
    "_assemble",
    "_count",
)
_SUPPORT_TIERS = {"wrapped", "helper_script", "catalog_only"}
_SKILL_ALIAS_OVERRIDES: dict[str, list[str]] = {
    "bedtools_coverage": ["bedtools coverage", "coveragebed"],
    "bedtools_genomecov": ["bedtools genomecov", "genomecov"],
    "bedtools_intersect": ["bedtools intersect", "intersectbed"],
    "cnv_cnvkit_style": ["cnvkit"],
    "fusion_star_fusion_style": ["star-fusion"],
    "gatk_haplotypecaller": ["haplotypecaller"],
    "gatk_mutect2_call": ["mutect2"],
    "immune_repertoire_mixcr_style": ["mixcr"],
    "metagenomics_kraken2_bracken_style": ["kraken2", "bracken"],
    "methylation_bismark_style": ["bismark"],
    "phylogenetics_iqtree_style": ["iqtree", "iqtree2"],
    "prodigal_annotate": ["prodigal"],
    "samtools_flagstat": ["samtools flagstat", "flagstat"],
    "samtools_idxstats": ["samtools idxstats", "idxstats"],
    "samtools_stats": ["samtools stats"],
    "sniffles_sv_call": ["sniffles"],
    "star_solo_count": ["starsolo"],
}
_SKILL_CAPABILITY_OVERRIDES: dict[str, list[str]] = {
    "fastqc_run": ["fastqc"],
}


def _normalize_capability_id(raw: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")


def _dedupe_strings(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = str(raw or "").strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(lowered)
    return out


def _clean_string(raw: Any) -> str:
    return str(raw or "").strip()


def _normalize_tool_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    name = _clean_string(entry.get("name", "")).lower()
    if not name:
        raise ValueError("scientific tool entries require a non-empty name")

    support_tier = _clean_string(entry.get("support_tier", "catalog_only")).lower()
    if support_tier not in _SUPPORT_TIERS:
        support_tier = "catalog_only"

    return {
        "name": name,
        "aliases": _dedupe_strings(list(entry.get("aliases", []) or [])),
        "support_tier": support_tier,
        "family": _clean_string(entry.get("family", "")).lower(),
        "description": _clean_string(entry.get("description", "")),
        "when_to_use": _clean_string(entry.get("when_to_use", "")),
        "when_not_to_use": _clean_string(entry.get("when_not_to_use", "")),
        "capabilities": _dedupe_strings(
            [_normalize_capability_id(value) for value in list(entry.get("capabilities", []) or [])]
        ),
        "analysis_categories": _dedupe_strings(list(entry.get("analysis_categories", []) or [])),
        "input_types": _dedupe_strings(list(entry.get("input_types", []) or [])),
        "output_types": _dedupe_strings(list(entry.get("output_types", []) or [])),
        "required_parameters": _dedupe_strings(list(entry.get("required_parameters", []) or [])),
        "optional_parameters": _dedupe_strings(list(entry.get("optional_parameters", []) or [])),
        "executables": _dedupe_strings(list(entry.get("executables", []) or [])),
        "repo_alternatives": _dedupe_strings(list(entry.get("repo_alternatives", []) or [])),
        "augment_capability_catalog": bool(entry.get("augment_capability_catalog", False)),
        "documentation_url": _clean_string(entry.get("documentation_url", "")),
        "source": _clean_string(entry.get("source", "")) or support_tier,
    }


def _merge_tool_entry(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in (
        "description",
        "when_to_use",
        "when_not_to_use",
        "family",
        "support_tier",
        "documentation_url",
        "source",
    ):
        value = _clean_string(override.get(key, ""))
        if value:
            merged[key] = value

    for key in (
        "aliases",
        "capabilities",
        "analysis_categories",
        "input_types",
        "output_types",
        "required_parameters",
        "optional_parameters",
        "executables",
        "repo_alternatives",
    ):
        merged[key] = _dedupe_strings(list(base.get(key, []) or []) + list(override.get(key, []) or []))

    merged["augment_capability_catalog"] = bool(
        override.get("augment_capability_catalog", base.get("augment_capability_catalog", False))
    )
    return _normalize_tool_entry(merged)


def load_curated_scientific_tool_catalog(path: Path | None = None) -> dict[str, Any]:
    """Load the curated supplemental scientific tool catalog from disk."""
    source = path or SCIENTIFIC_TOOL_CATALOG_PATH
    if not source.is_file():
        return {"version": 1, "tools": []}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "tools": []}
    if not isinstance(payload, Mapping):
        return {"version": 1, "tools": []}
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        tools = []
    return {"version": int(payload.get("version", 1)), "tools": tools}


def save_curated_scientific_tool_catalog(catalog: Mapping[str, Any], path: Path | None = None) -> Path:
    """Persist the curated supplemental scientific tool catalog."""
    destination = path or SCIENTIFIC_TOOL_CATALOG_PATH
    tools: list[dict[str, Any]] = []
    for raw in list(catalog.get("tools", []) or []) if isinstance(catalog, Mapping) else []:
        if not isinstance(raw, Mapping):
            continue
        tools.append(_normalize_tool_entry(raw))
    payload = {"version": int(catalog.get("version", 1)) if isinstance(catalog, Mapping) else 1, "tools": tools}
    destination.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return destination


def upsert_scientific_tool_entry(catalog: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    """Upsert one curated scientific tool entry."""
    normalized_entry = _normalize_tool_entry(entry)
    updated = load_curated_scientific_tool_catalog()
    if isinstance(catalog, Mapping):
        updated["version"] = int(catalog.get("version", updated.get("version", 1)))
        updated["tools"] = list(catalog.get("tools", updated.get("tools", [])) or [])

    merged_by_name: dict[str, dict[str, Any]] = {}
    for raw in list(updated.get("tools", []) or []):
        if not isinstance(raw, Mapping):
            continue
        normalized = _normalize_tool_entry(raw)
        merged_by_name[normalized["name"]] = normalized

    existing = merged_by_name.get(normalized_entry["name"], {})
    merged_by_name[normalized_entry["name"]] = (
        _merge_tool_entry(existing, normalized_entry) if existing else normalized_entry
    )
    updated["tools"] = [merged_by_name[name] for name in sorted(merged_by_name)]
    return updated


def _skill_aliases(skill_name: str, executables: list[str]) -> list[str]:
    aliases = list(_SKILL_ALIAS_OVERRIDES.get(skill_name, []))
    primary_executable = executables[0] if executables else ""
    if primary_executable and primary_executable != skill_name:
        aliases.append(primary_executable)

    base_name = skill_name
    for suffix in _SUFFIX_TOKENS:
        if base_name.endswith(suffix):
            base_name = base_name[: -len(suffix)]
            break
    if base_name and base_name != skill_name and base_name not in EXCLUDED_SCIENTIFIC_SKILLS:
        aliases.append(base_name)
    if base_name.startswith("gatk_"):
        aliases.append(base_name.split("_", 1)[1])
    return _dedupe_strings(aliases)


def _wrapped_tool_entries(skill_index_path: Path | None = None) -> list[dict[str, Any]]:
    source = skill_index_path or SKILL_INDEX_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return []
    skills = payload.get("skills", []) if isinstance(payload, Mapping) else []
    if not isinstance(skills, list):
        return []

    entries: list[dict[str, Any]] = []
    for raw in skills:
        if not isinstance(raw, Mapping):
            continue
        name = _clean_string(raw.get("name", "")).lower()
        if not name or name in EXCLUDED_SCIENTIFIC_SKILLS:
            continue

        parameters = raw.get("parameters", {})
        if not isinstance(parameters, Mapping):
            parameters = {}
        required_parameters = [
            str(param_name).strip()
            for param_name, spec in parameters.items()
            if str(param_name).strip() and isinstance(spec, Mapping) and bool(spec.get("required", False))
        ]
        optional_parameters = [
            str(param_name).strip()
            for param_name, spec in parameters.items()
            if str(param_name).strip() and not (isinstance(spec, Mapping) and bool(spec.get("required", False)))
        ]
        executables = _dedupe_strings(list(raw.get("tools_required", []) or []))
        entry = {
            "name": name,
            "aliases": _skill_aliases(name, executables),
            "support_tier": "wrapped",
            "family": _clean_string(raw.get("analysis_categories", [""])[0] if raw.get("analysis_categories") else ""),
            "description": _clean_string(raw.get("description", "")),
            "when_to_use": _clean_string(raw.get("when_to_use", "")),
            "when_not_to_use": _clean_string(raw.get("when_not_to_use", "")),
            "capabilities": list(raw.get("capabilities", []) or []) or list(_SKILL_CAPABILITY_OVERRIDES.get(name, [])),
            "analysis_categories": list(raw.get("analysis_categories", []) or []),
            "input_types": list(raw.get("input_types", []) or []),
            "output_types": list(raw.get("output_types", []) or []),
            "required_parameters": required_parameters,
            "optional_parameters": optional_parameters,
            "executables": executables,
            "augment_capability_catalog": True,
            "source": "wrapped_skill",
        }
        entries.append(_normalize_tool_entry(entry))
    return entries


def load_scientific_tool_catalog(
    path: Path | None = None,
    *,
    skill_index_path: Path | None = None,
) -> dict[str, Any]:
    """Load the merged scientific tool catalog."""
    curated = load_curated_scientific_tool_catalog(path)
    merged_by_name: dict[str, dict[str, Any]] = {}

    for entry in _wrapped_tool_entries(skill_index_path):
        merged_by_name[entry["name"]] = copy.deepcopy(entry)

    for raw in list(curated.get("tools", []) or []):
        if not isinstance(raw, Mapping):
            continue
        entry = _normalize_tool_entry(raw)
        existing = merged_by_name.get(entry["name"])
        merged_by_name[entry["name"]] = _merge_tool_entry(existing, entry) if existing else entry

    return {
        "version": int(curated.get("version", 1)),
        "tools": [merged_by_name[name] for name in sorted(merged_by_name)],
    }


def scientific_tool_index(
    catalog: Mapping[str, Any],
    *,
    include_aliases: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build a case-insensitive lookup index for the scientific tool catalog."""
    index: dict[str, dict[str, Any]] = {}
    for raw in list(catalog.get("tools", []) or []) if isinstance(catalog, Mapping) else []:
        if not isinstance(raw, Mapping):
            continue
        entry = _normalize_tool_entry(raw)
        index[entry["name"]] = entry
        if include_aliases:
            for alias in entry.get("aliases", []):
                index.setdefault(alias, entry)
    return index


def resolve_scientific_tool(name: str, catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve one scientific tool by name or alias."""
    token = _clean_string(name).lower()
    if not token:
        return {}
    source = catalog if isinstance(catalog, Mapping) else load_scientific_tool_catalog()
    return dict(scientific_tool_index(source).get(token, {}))
