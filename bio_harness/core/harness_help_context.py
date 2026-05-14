"""Build deterministic Bio-Harness help context for user-facing model guidance.

This module creates a compact, repo-grounded guide describing:

- what Bio-Harness can execute directly via wrapped skills
- which capability categories are well covered
- how support tiers differ between wrapped tools and catalog-only references
- how to stage inputs, download trusted resources, and extend the harness

The guide is designed for two surfaces:

1. deterministic context appended to model prompts
2. CLI or documentation helpers that print the same grounded summary
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from bio_harness.core.capability_catalog import load_capability_catalog
from bio_harness.core.scientific_tool_catalog import load_scientific_tool_catalog
from bio_harness.core.skill_retrieval import build_skill_retrieval_record, search_skill_records


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_CATALOG_PATH = PROJECT_ROOT / "bio_harness" / "capabilities" / "catalog.json"
_EXCLUDED_HELP_SKILLS = frozenset({"bash_run", "fallback_skill_builder"})
_HELP_QUERY_PHRASES = (
    "what can you do",
    "what do you do",
    "what programs",
    "what wrapped programs",
    "what tools",
    "what capabilities",
    "by category",
    "make a skill",
    "create a skill",
    "new skill",
    "add a capability",
    "add capability",
    "capability entry",
    "add a tool",
    "tool families",
    "catalog-only support",
    "wrapped vs catalog-only",
    "stage inputs",
    "stage files",
    "local input files",
    "download a paper",
    "download a manual",
    "trusted manual",
    "trusted paper",
    "trusted download",
    "set up ollama",
    "setup ollama",
    "set up model",
    "setup model",
    "model setup",
    "backend setup",
    "pull model",
    "model not available",
    "why is the model unavailable",
    "get started",
    "first run",
    "bio-harness help",
    "harness help",
)
_HELP_QUERY_TOKENS = {
    "capabilities",
    "capability",
    "programs",
    "wrapped",
    "catalog-only",
    "skills",
    "skill",
    "tools",
    "tool",
    "families",
    "manuals",
    "manual",
    "papers",
    "paper",
    "downloads",
    "download",
    "stage",
    "inputs",
    "workspace",
    "backend",
    "model",
    "ollama",
    "setup",
    "bootstrap",
    "doctor",
}
_HELP_ACTION_TOKENS = {"what", "how", "add", "create", "make", "download", "stage", "show", "list"}
_DEFAULT_SECTION_LIMIT = 10
_DEFAULT_EXAMPLE_LIMIT = 5
_COMPACT_SECTION_LIMIT = 8
_COMPACT_EXAMPLE_LIMIT = 4
_FOCUSED_HELP_LIMIT = 5
_GLOBAL_HELP_PHRASES = (
    "what capabilities",
    "by category",
    "what can you do",
    "what wrapped programs",
    "tool families",
    "get started",
    "first run",
    "create a skill",
    "new skill",
    "add a capability",
    "stage inputs",
    "trusted manual",
    "trusted download",
)


def _humanize_token(raw: str) -> str:
    """Return a title-cased label for underscore-delimited tokens."""
    token = str(raw or "").strip().replace("-", "_")
    if not token:
        return "General"
    return token.replace("_", " ").strip().title()


def _normalize_skill_rows(skills: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize loaded skill metadata into a stable help-oriented shape."""
    rows: list[dict[str, Any]] = []
    for name, raw in sorted(skills.items()):
        if str(name).strip().lower() in _EXCLUDED_HELP_SKILLS:
            continue
        metadata = raw if isinstance(raw, Mapping) else {}
        rows.append(
            {
                "name": str(metadata.get("name", name)).strip() or str(name).strip(),
                "description": str(metadata.get("description", "")).strip(),
                "analysis_categories": [
                    str(category).strip()
                    for category in list(metadata.get("analysis_categories", []) or [])
                    if str(category).strip()
                ],
                "capabilities": [
                    str(capability).strip()
                    for capability in list(metadata.get("capabilities", []) or [])
                    if str(capability).strip()
                ],
                "tools_required": [
                    str(tool).strip()
                    for tool in list(metadata.get("tools_required", []) or [])
                    if str(tool).strip()
                ],
            }
        )
    return rows


def _group_skill_categories(
    skill_rows: Sequence[Mapping[str, Any]],
    *,
    section_limit: int,
    example_limit: int,
) -> list[dict[str, Any]]:
    """Group wrapped skills by analysis category for user-help summaries."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in skill_rows:
        skill_name = str(row.get("name", "")).strip()
        if not skill_name:
            continue
        categories = [str(category).strip() for category in list(row.get("analysis_categories", []) or []) if str(category).strip()]
        if not categories:
            categories = ["general"]
        for category in categories:
            grouped[category].append(skill_name)

    rows: list[dict[str, Any]] = []
    for category, names in grouped.items():
        unique_names = sorted(dict.fromkeys(names))
        rows.append(
            {
                "category_id": category,
                "category": _humanize_token(category),
                "skill_count": len(unique_names),
                "example_skills": unique_names[:example_limit],
            }
        )
    rows.sort(key=lambda row: (-int(row["skill_count"]), str(row["category"])))
    return rows[:section_limit]


def _group_wrapped_tool_families(
    scientific_tool_catalog: Mapping[str, Any],
    *,
    section_limit: int,
    example_limit: int,
    allowed_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Group first-class wrapped tools by family for model-friendly summaries."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for raw in list(scientific_tool_catalog.get("tools", []) or []) if isinstance(scientific_tool_catalog, Mapping) else []:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("support_tier", "")).strip().lower() != "wrapped":
            continue
        tool_name = str(raw.get("name", "")).strip()
        if not tool_name or tool_name in _EXCLUDED_HELP_SKILLS:
            continue
        if allowed_tool_names is not None and tool_name not in allowed_tool_names:
            continue
        family = str(raw.get("family", "")).strip() or "general"
        grouped[family].append(tool_name)

    rows: list[dict[str, Any]] = []
    for family, names in grouped.items():
        unique_names = sorted(dict.fromkeys(names))
        rows.append(
            {
                "family_id": family,
                "family": _humanize_token(family),
                "tool_count": len(unique_names),
                "example_tools": unique_names[:example_limit],
            }
        )
    rows.sort(key=lambda row: (-int(row["tool_count"]), str(row["family"])))
    return rows[:section_limit]


def _support_tier_counts(scientific_tool_catalog: Mapping[str, Any]) -> dict[str, int]:
    """Count merged scientific-tool entries by support tier."""
    counts = {"wrapped": 0, "helper_script": 0, "catalog_only": 0}
    for raw in list(scientific_tool_catalog.get("tools", []) or []) if isinstance(scientific_tool_catalog, Mapping) else []:
        if not isinstance(raw, Mapping):
            continue
        tier = str(raw.get("support_tier", "")).strip().lower()
        if tier in counts:
            counts[tier] += 1
    return counts


def _capability_highlights(
    capability_catalog: Mapping[str, Any],
    *,
    section_limit: int,
    allowed_capability_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Return a short enabled-capability highlight list."""
    rows: list[dict[str, str]] = []
    for raw in list(capability_catalog.get("capabilities", []) or []) if isinstance(capability_catalog, Mapping) else []:
        if not isinstance(raw, Mapping):
            continue
        if not bool(raw.get("enabled", True)):
            continue
        capability_id = str(raw.get("id", "")).strip()
        name = str(raw.get("name", "")).strip()
        if not capability_id or not name:
            continue
        if allowed_capability_ids is not None and capability_id not in allowed_capability_ids:
            continue
        rows.append(
            {
                "id": capability_id,
                "name": name,
                "description": str(raw.get("description", "")).strip(),
            }
        )
    rows.sort(key=lambda row: str(row["name"]).lower())
    return rows[:section_limit]


def looks_like_harness_help_query(text: str) -> bool:
    """Return True when the user appears to be asking about harness self-help.

    Args:
        text: Raw user message.

    Returns:
        True when the query is about Bio-Harness capabilities, extension paths,
        staging inputs, or trusted downloads.
    """
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if any(phrase in lowered for phrase in _HELP_QUERY_PHRASES):
        return True
    object_hits = sum(1 for token in _HELP_QUERY_TOKENS if token in lowered)
    action_hits = sum(1 for token in _HELP_ACTION_TOKENS if token in lowered)
    if ("bio-harness" in lowered or "harness" in lowered) and object_hits >= 1:
        return True
    return object_hits >= 2 and action_hits >= 1


def _query_prefers_global_help(query: str) -> bool:
    """Return True when the help query is asking for a global overview."""

    lowered = str(query or "").strip().lower()
    if not lowered:
        return True
    return any(phrase in lowered for phrase in _GLOBAL_HELP_PHRASES)


def _focus_skill_rows(
    skill_rows: Sequence[Mapping[str, Any]],
    *,
    retrieval_query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return a retrieval-focused skill subset for narrow help questions."""

    if _query_prefers_global_help(retrieval_query):
        return [dict(row) for row in skill_rows if isinstance(row, Mapping)]
    records = [
        build_skill_retrieval_record(
            {
                "name": str(row.get("name", "")).strip(),
                "description": str(row.get("description", "")).strip(),
                "analysis_categories": list(row.get("analysis_categories", []) or []),
                "capabilities": list(row.get("capabilities", []) or []),
                "tools_required": list(row.get("tools_required", []) or []),
                "file_path": str(row.get("name", "")).strip(),
            }
        )
        for row in skill_rows
        if isinstance(row, Mapping) and str(row.get("name", "")).strip()
    ]
    matches = search_skill_records(str(retrieval_query or ""), records, limit=max(1, int(limit)))
    focused_names = {str(match.name).strip() for match in matches if str(match.name).strip()}
    if not focused_names:
        return [dict(row) for row in skill_rows if isinstance(row, Mapping)]
    return [
        dict(row)
        for row in skill_rows
        if isinstance(row, Mapping) and str(row.get("name", "")).strip() in focused_names
    ]


def build_harness_help_payload(
    skills: Mapping[str, Mapping[str, Any]],
    *,
    capability_catalog: Mapping[str, Any] | None = None,
    scientific_tool_catalog: Mapping[str, Any] | None = None,
    project_root: Path | None = None,
    compact: bool = False,
    retrieval_query: str | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable Bio-Harness help payload.

    Args:
        skills: Loaded skill metadata keyed by skill name.
        capability_catalog: Optional injected capability catalog for tests.
        scientific_tool_catalog: Optional injected scientific tool catalog for tests.
        project_root: Optional repo root override.
        compact: Whether to reduce section and example counts.
        retrieval_query: Optional narrow help query used to focus skill-heavy
            sections for interactive help turns.

    Returns:
        A deterministic payload describing capabilities, wrapped tool families,
        extension workflows, and user convenience entry points.
    """
    root = (project_root or PROJECT_ROOT).resolve()
    section_limit = _COMPACT_SECTION_LIMIT if compact else _DEFAULT_SECTION_LIMIT
    example_limit = _COMPACT_EXAMPLE_LIMIT if compact else _DEFAULT_EXAMPLE_LIMIT
    skill_rows = _normalize_skill_rows(skills)
    focused_rows = (
        _focus_skill_rows(skill_rows, retrieval_query=str(retrieval_query or ""), limit=_FOCUSED_HELP_LIMIT)
        if str(retrieval_query or "").strip()
        else skill_rows
    )
    capability_source = capability_catalog or load_capability_catalog(root / "bio_harness" / "capabilities" / "catalog.json")
    scientific_source = scientific_tool_catalog or load_scientific_tool_catalog()
    support_tiers = _support_tier_counts(scientific_source)
    allowed_names = {
        str(row.get("name", "")).strip()
        for row in focused_rows
        if isinstance(row, Mapping) and str(row.get("name", "")).strip()
    }
    allowed_capabilities = {
        str(capability).strip()
        for row in focused_rows
        if isinstance(row, Mapping)
        for capability in list(row.get("capabilities", []) or [])
        if str(capability).strip()
    }
    return {
        "summary": {
            "wrapped_skills": len(focused_rows),
            "analysis_categories": len(
                {
                    str(category).strip()
                    for row in focused_rows
                    for category in list(row.get("analysis_categories", []) or [])
                    if str(category).strip()
                }
            ),
            "capability_ids": len(
                {
                    str(capability).strip()
                    for row in focused_rows
                    for capability in list(row.get("capabilities", []) or [])
                    if str(capability).strip()
                }
            ),
            "scientific_tool_entries": len(
                [row for row in list(scientific_source.get("tools", []) or []) if isinstance(row, Mapping)]
            ),
        },
        "focus": {
            "active": bool(str(retrieval_query or "").strip()) and len(focused_rows) < len(skill_rows),
            "query": str(retrieval_query or "").strip(),
            "skill_names": sorted(allowed_names),
        },
        "support_tiers": support_tiers,
        "capability_highlights": _capability_highlights(
            capability_source,
            section_limit=section_limit,
            allowed_capability_ids=allowed_capabilities or None,
        ),
        "capability_categories": _group_skill_categories(
            focused_rows,
            section_limit=section_limit,
            example_limit=example_limit,
        ),
        "wrapped_tool_families": _group_wrapped_tool_families(
            scientific_source,
            section_limit=section_limit,
            example_limit=example_limit,
            allowed_tool_names=allowed_names or None,
        ),
        "user_entrypoints": {
            "bootstrap_bioharness": "python3 scripts/bootstrap_bioharness.py",
            "doctor_bioharness": "python3 scripts/doctor_bioharness.py --probe-llm-backend",
            "setup_llm_backend": "python3 scripts/setup_llm_backend.py --help",
            "stage_inputs": "python3 scripts/stage_inputs.py --help",
            "trusted_download": "python3 scripts/trusted_download.py --help",
            "show_harness_help": "python3 scripts/show_harness_help.py --help",
            "upsert_scientific_tool": "python3 scripts/upsert_scientific_tool.py --help",
            "enrich_skill_definitions": "python3 scripts/enrich_skill_definitions.py",
        },
        "extension_workflows": {
            "create_skill": [
                "Add a skill definition at bio_harness/skills/definitions/<skill>.md with metadata and usage notes.",
                "Add a deterministic wrapper at bio_harness/skills/library/<skill>.py.",
                "Run python3 scripts/enrich_skill_definitions.py to refresh enriched metadata and index.json.",
                "Prefer wrapped deterministic parameters over bash_run or hidden shell overrides.",
            ],
            "add_capability": [
                "Update the skill frontmatter capabilities and analysis_categories to expose the new workflow.",
                "Add or update scientific tool catalog context with python3 scripts/upsert_scientific_tool.py --help.",
                "Regenerate enriched metadata with python3 scripts/enrich_skill_definitions.py.",
                "If the tool needs controlled onboarding, use the helpers under bio_harness/core/tool_onboarding.py.",
            ],
        },
        "user_convenience": [
            "Use bootstrap_bioharness plus setup_llm_backend to get the Python environment, local backend host, and requested model into a ready state.",
            "Run doctor_bioharness with --probe-llm-backend when you want a deterministic readiness summary instead of guessing which runtime piece is missing.",
            "Stage local inputs into workspace/inputs_readonly with the stage_inputs helper rather than ad hoc shell moves.",
            "Use trusted_download for allowlisted manuals, papers, or software archives that should land in the workspace with receipts.",
            "Prefer Librarian-backed paper/manual lookup for PubMed and trusted reference-domain searches before downloading artifacts.",
        ],
        "model_rules": [
            "Describe wrapped skills and wrapped tool families first when asked what Bio-Harness can execute directly.",
            "Mention support tier when a tool is only catalog-only or helper-script context instead of a first-class wrapper.",
            "When asked how to get the model runtime ready, recommend bootstrap_bioharness, setup_llm_backend, and doctor_bioharness before ad hoc shell debugging.",
            "For local file ingress or approved downloads, recommend stage_inputs and trusted_download instead of raw shell fetches.",
        ],
    }


def render_harness_help_text(payload: Mapping[str, Any]) -> str:
    """Render a help payload as deterministic Markdown-like text.

    Args:
        payload: Output from build_harness_help_payload().

    Returns:
        A concise multi-section help guide suitable for prompts or CLI output.
    """
    summary = payload.get("summary", {}) if isinstance(payload, Mapping) else {}
    support_tiers = payload.get("support_tiers", {}) if isinstance(payload, Mapping) else {}
    focus = payload.get("focus", {}) if isinstance(payload, Mapping) else {}
    capability_categories = payload.get("capability_categories", []) if isinstance(payload, Mapping) else []
    wrapped_tool_families = payload.get("wrapped_tool_families", []) if isinstance(payload, Mapping) else []
    capability_highlights = payload.get("capability_highlights", []) if isinstance(payload, Mapping) else []
    entrypoints = payload.get("user_entrypoints", {}) if isinstance(payload, Mapping) else {}
    extension_workflows = payload.get("extension_workflows", {}) if isinstance(payload, Mapping) else {}
    convenience = payload.get("user_convenience", []) if isinstance(payload, Mapping) else []
    model_rules = payload.get("model_rules", []) if isinstance(payload, Mapping) else []

    lines = [
        "## Bio-Harness User Help",
        "- This guide describes the local Bio-Harness repository in this workspace; do not substitute details from unrelated projects with similar names.",
        (
            "- Bio-Harness currently exposes "
            f"{int(summary.get('wrapped_skills', 0))} wrapped skills across "
            f"{int(summary.get('analysis_categories', 0))} analysis categories and "
            f"{int(summary.get('capability_ids', 0))} skill-linked capability IDs."
        ),
        (
            "- Scientific-tool support tiers matter: "
            f"{int(support_tiers.get('wrapped', 0))} wrapped, "
            f"{int(support_tiers.get('helper_script', 0))} helper_script, "
            f"{int(support_tiers.get('catalog_only', 0))} catalog_only."
        ),
        "- Wrapped skills are executable by the harness; catalog-only tools are reference context unless a named wrapper exists.",
    ]
    if bool(focus.get("active", False)):
        focus_names = ", ".join(
            str(name).strip()
            for name in list(focus.get("skill_names", []) or [])
            if str(name).strip()
        )
        if focus_names:
            lines.append(f"- Focused help slice for this question: {focus_names}")
    lines.extend(["", "## Capability Categories"])

    for row in capability_categories if isinstance(capability_categories, Sequence) else []:
        if not isinstance(row, Mapping):
            continue
        examples = ", ".join([str(name).strip() for name in list(row.get("example_skills", []) or []) if str(name).strip()])
        lines.append(
            f"- {str(row.get('category', 'General')).strip()} "
            f"({int(row.get('skill_count', 0))} wrapped skills): {examples}"
        )

    lines.extend(["", "## Capability Highlights"])
    for row in capability_highlights if isinstance(capability_highlights, Sequence) else []:
        if not isinstance(row, Mapping):
            continue
        description = str(row.get("description", "")).strip()
        suffix = f": {description}" if description else ""
        lines.append(f"- {str(row.get('name', '')).strip()} [{str(row.get('id', '')).strip()}]{suffix}")

    lines.extend(["", "## Wrapped Program Families"])
    for row in wrapped_tool_families if isinstance(wrapped_tool_families, Sequence) else []:
        if not isinstance(row, Mapping):
            continue
        examples = ", ".join([str(name).strip() for name in list(row.get("example_tools", []) or []) if str(name).strip()])
        lines.append(
            f"- {str(row.get('family', 'General')).strip()} "
            f"({int(row.get('tool_count', 0))} wrapped tools): {examples}"
        )

    lines.extend(
        [
            "",
            "## User Convenience Paths",
            f"- Bootstrap the repo environment: `{str(entrypoints.get('bootstrap_bioharness', '')).strip()}`",
            f"- Verify backend/model setup deterministically: `{str(entrypoints.get('setup_llm_backend', '')).strip()}`",
            f"- Run doctor with LLM probing: `{str(entrypoints.get('doctor_bioharness', '')).strip()}`",
            f"- Stage local inputs into the workspace: `{str(entrypoints.get('stage_inputs', '')).strip()}`",
            f"- Download allowlisted papers/manuals/software with receipts: `{str(entrypoints.get('trusted_download', '')).strip()}`",
            f"- Print this deterministic guide from the CLI: `{str(entrypoints.get('show_harness_help', '')).strip()}`",
        ]
    )

    if convenience:
        for item in convenience:
            lines.append(f"- {str(item).strip()}")

    lines.extend(["", "## Extending The Harness", "To create a new skill:"])
    for step in list(extension_workflows.get("create_skill", []) or []):
        lines.append(f"1. {str(step).strip()}")

    lines.append("To add or expand a capability/tool entry:")
    for step in list(extension_workflows.get("add_capability", []) or []):
        lines.append(f"1. {str(step).strip()}")

    lines.extend(["", "## Model-Facing Rules"])
    for rule in model_rules:
        lines.append(f"- {str(rule).strip()}")

    return "\n".join(lines).strip() + "\n"


def build_harness_help_context(
    skills: Mapping[str, Mapping[str, Any]],
    *,
    capability_catalog: Mapping[str, Any] | None = None,
    scientific_tool_catalog: Mapping[str, Any] | None = None,
    project_root: Path | None = None,
    compact: bool = False,
    retrieval_query: str | None = None,
) -> str:
    """Build rendered deterministic Bio-Harness help context.

    Args:
        skills: Loaded skill metadata keyed by skill name.
        capability_catalog: Optional injected capability catalog.
        scientific_tool_catalog: Optional injected scientific tool catalog.
        project_root: Optional repo root override.
        compact: Whether to render a shorter help guide.
        retrieval_query: Optional narrow help query used to focus skill-heavy
            sections for interactive help turns.

    Returns:
        Rendered help text suitable for CLI display or prompt context.
    """
    payload = build_harness_help_payload(
        skills,
        capability_catalog=capability_catalog,
        scientific_tool_catalog=scientific_tool_catalog,
        project_root=project_root,
        compact=compact,
        retrieval_query=retrieval_query,
    )
    return render_harness_help_text(payload)
