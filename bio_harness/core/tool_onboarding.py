from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from bio_harness.core.capability_catalog import (
    load_capability_catalog,
    normalize_capability_id,
    save_capability_catalog,
    update_capability_tool_hints,
)
from bio_harness.core.onboarding_capability_enrichment import enrich_onboarding_metadata
from bio_harness.skills.registry import SkillRegistry

ALLOWED_RISK_LEVELS = {"low", "medium", "high"}
ALLOWED_PARAM_TYPES = {"string", "path", "integer", "boolean"}


def slugify_skill_name(raw: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    if not token:
        token = "custom_tool"
    if token[0].isdigit():
        token = f"tool_{token}"
    return token


def build_generic_skill_library_stub(skill_name: str, default_tool: str) -> str:
    tool_name = default_tool or skill_name
    tool_literal = json.dumps(str(tool_name).strip())
    return (
        "from __future__ import annotations\n\n"
        "import shlex\n\n\n"
        f"def {skill_name}(**kwargs) -> str:\n"
        "    # If caller provides a full command, trust and return it.\n"
        "    if \"command\" in kwargs and str(kwargs.get(\"command\", \"\")).strip():\n"
        "        return str(kwargs[\"command\"]).strip()\n"
        f"    tool = {tool_literal}\n"
        "    parts: list[str] = [tool]\n"
        "    for key, value in kwargs.items():\n"
        "        if key == \"command\":\n"
        "            continue\n"
        "        flag = \"--\" + str(key).strip().replace(\"_\", \"-\")\n"
        "        if isinstance(value, bool):\n"
        "            if value:\n"
        "                parts.append(flag)\n"
        "            continue\n"
        "        if value is None:\n"
        "            continue\n"
        "        parts.extend([flag, str(value)])\n"
        "    return \" \".join(shlex.quote(x) for x in parts)\n"
    )


def build_template_skill_library_stub(skill_name: str, command_template: str) -> str:
    template_literal = json.dumps(command_template)
    return (
        "from __future__ import annotations\n\n"
        "import shlex\n"
        "import string\n\n\n"
        "def _render_template(template: str, kwargs: dict) -> str:\n"
        "    rendered: dict[str, str] = {}\n"
        "    for key, value in kwargs.items():\n"
        "        if value is None:\n"
        "            continue\n"
        "        rendered[key] = shlex.quote(str(value))\n"
        "    formatter = string.Formatter()\n"
        "    field_names = [field_name for _, field_name, _, _ in formatter.parse(template) if field_name]\n"
        "    missing = [field for field in field_names if field not in rendered]\n"
        "    if missing:\n"
        "        missing_args = \", \".join(sorted(set(missing)))\n"
        "        raise ValueError(f\"Missing required parameter(s) for template: {missing_args}\")\n"
        "    return template.format(**rendered).strip()\n\n\n"
        f"def {skill_name}(**kwargs) -> str:\n"
        "    if \"command\" in kwargs and str(kwargs.get(\"command\", \"\")).strip():\n"
        "        return str(kwargs[\"command\"]).strip()\n"
        f"    template = {template_literal}\n"
        "    return _render_template(template, kwargs)\n"
    )


def _normalize_parameters(parameters: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_spec in parameters.items():
        name = str(raw_name).strip()
        if not name:
            continue
        spec = raw_spec if isinstance(raw_spec, Mapping) else {}
        param_type = str(spec.get("type", "string")).strip().lower()
        if param_type not in ALLOWED_PARAM_TYPES:
            param_type = "string"
        desc = str(spec.get("description", "")).strip() or f"Parameter `{name}`."
        normalized_spec: dict[str, Any] = {
            "type": param_type,
            "description": desc,
            "required": bool(spec.get("required", False)),
        }
        if "default" in spec:
            normalized_spec["default"] = spec.get("default")
        normalized[name] = normalized_spec
    return normalized


def _normalize_tools_required(raw_tools: Iterable[Any], fallback: str) -> list[str]:
    tools: list[str] = []
    for raw in raw_tools:
        tool = str(raw).strip().lower()
        if tool:
            tools.append(tool)
    if not tools:
        tools = [fallback]
    return list(dict.fromkeys(tools))


def _normalize_capabilities(raw_capabilities: Iterable[Any]) -> list[str]:
    caps: list[str] = []
    for raw in raw_capabilities:
        cap = normalize_capability_id(str(raw))
        if cap:
            caps.append(cap)
    return list(dict.fromkeys(caps))


def normalize_onboarding_draft(draft: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    skill_name = slugify_skill_name(str(draft.get("skill_name", "") or draft.get("name", "")))
    description = str(draft.get("description", "")).strip()
    risk_level = str(draft.get("risk_level", "medium")).strip().lower()
    if risk_level not in ALLOWED_RISK_LEVELS:
        errors.append("Risk level must be one of: low, medium, high.")
    if not description:
        errors.append("Skill description is required.")

    raw_params = draft.get("parameters", {})
    params = _normalize_parameters(raw_params if isinstance(raw_params, Mapping) else {})
    if not params:
        errors.append("Skill parameters cannot be empty.")

    capabilities = _normalize_capabilities(
        draft.get("capabilities", []) if isinstance(draft.get("capabilities", []), list) else []
    )
    tools_required = _normalize_tools_required(
        draft.get("tools_required", []) if isinstance(draft.get("tools_required", []), list) else [],
        skill_name,
    )
    usage_guide = str(draft.get("usage_guide", "")).strip()
    command_template = str(draft.get("command_template", "")).strip()
    system_requirements = draft.get("system_requirements", {})
    if not isinstance(system_requirements, Mapping):
        system_requirements = {}
    else:
        system_requirements = dict(system_requirements)

    normalized = {
        "skill_name": skill_name,
        "description": description,
        "risk_level": risk_level,
        "tools_required": tools_required,
        "capabilities": capabilities,
        "parameters": params,
        "command_template": command_template,
        "usage_guide": usage_guide,
        "system_requirements": system_requirements,
    }
    return normalized, errors


def install_tool_onboarding_draft(
    draft: Mapping[str, Any],
    source_meta: Mapping[str, Any],
    *,
    manual_summary: Mapping[str, Any] | None = None,
    tool_card: Any | None = None,
    skills_definitions_dir: Path,
    skills_library_dir: Path,
    capability_catalog_path: Path,
    tool_cards_dir: Path | None = None,
    install_workflow: str = "controlled_source_onboarding",
    record_custom_tool: bool = True,
    installed_at: str | None = None,
) -> tuple[bool, str]:
    normalized, errors = normalize_onboarding_draft(draft)
    if errors:
        return False, "; ".join(errors)

    capability_catalog = load_capability_catalog(capability_catalog_path)
    enriched = enrich_onboarding_metadata(
        {**draft, **normalized},
        manual_summary=manual_summary,
        capability_catalog=capability_catalog,
    )
    normalized["capabilities"] = [
        normalize_capability_id(value)
        for value in enriched.get("capabilities", []) or []
        if normalize_capability_id(value)
    ] or normalized["capabilities"]

    skill_name = normalized["skill_name"]
    metadata = {
        "name": skill_name,
        "description": normalized["description"],
        "risk_level": normalized["risk_level"],
        "tools_required": normalized["tools_required"],
        "capabilities": normalized["capabilities"],
        "parameters": normalized["parameters"],
    }
    for field in ("when_to_use", "when_not_to_use", "input_types", "output_types", "analysis_categories"):
        value = enriched.get(field)
        if isinstance(value, list):
            value = [str(item).strip().lower() for item in value if str(item).strip()]
        elif value is not None:
            value = str(value).strip()
        if value:
            metadata[field] = value
    if normalized["system_requirements"]:
        metadata["system_requirements"] = normalized["system_requirements"]
    if normalized["command_template"]:
        metadata["command_template"] = normalized["command_template"]

    skills_definitions_dir.mkdir(parents=True, exist_ok=True)
    skills_library_dir.mkdir(parents=True, exist_ok=True)
    capability_catalog_path.parent.mkdir(parents=True, exist_ok=True)

    registry = SkillRegistry(skills_definitions_dir)
    valid, validation_errors = registry.validate_skill_metadata(metadata)
    if not valid:
        return False, "Invalid skill metadata: " + "; ".join(validation_errors)

    timestamp = installed_at or datetime.now().isoformat()
    usage_block = normalized["usage_guide"] or "Generated capability draft."
    usage_block += (
        "\n\n## Onboarding Metadata\n"
        f"- Source: {str(source_meta.get('source', '')).strip()}\n"
        f"- Source Mode: {str(source_meta.get('mode', '')).strip()}\n"
        f"- Installed At: {timestamp}\n"
        f"- Install Workflow: {install_workflow}\n"
    )

    skill_frontmatter = "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n" + usage_block + "\n"
    skill_path = skills_definitions_dir / f"{skill_name}.md"
    skill_path.write_text(skill_frontmatter, encoding="utf-8")

    library_path = skills_library_dir / f"{skill_name}.py"
    if not library_path.exists():
        if normalized["command_template"]:
            lib_text = build_template_skill_library_stub(skill_name, normalized["command_template"])
        else:
            lib_text = build_generic_skill_library_stub(
                skill_name,
                normalized["tools_required"][0] if normalized["tools_required"] else skill_name,
            )
        library_path.write_text(lib_text, encoding="utf-8")

    tool_card_path = ""
    if tool_cards_dir is not None:
        from bio_harness.core.tool_cards import tool_card_from_draft, write_tool_card

        card = tool_card
        if card is None:
            card = tool_card_from_draft(
                draft,
                source_meta=source_meta,
                manual_summary=manual_summary,
                support_tier="catalog_only",
                validated=True,
            )
        tool_card_path = str(write_tool_card(card, tool_cards_dir=tool_cards_dir))

    capability_catalog = update_capability_tool_hints(
        capability_catalog,
        capability_ids=normalized["capabilities"],
        tool_hints=normalized["tools_required"] or [skill_name],
        plan_signals=[skill_name],
    )
    if record_custom_tool:
        custom_tools = (
            list(capability_catalog.get("custom_tools", []))
            if isinstance(capability_catalog.get("custom_tools", []), list)
            else []
        )
        custom_tools.append(
            {
                "skill_name": skill_name,
                "capabilities": normalized["capabilities"],
                "source": str(source_meta.get("source", "")).strip(),
                "source_mode": str(source_meta.get("mode", "")).strip(),
                "skill_path": str(skill_path),
                "library_path": str(library_path),
                "tool_card_path": tool_card_path,
                "installed_at": timestamp,
                "install_workflow": install_workflow,
            }
        )
        capability_catalog["custom_tools"] = custom_tools[-200:]
    save_capability_catalog(capability_catalog_path, capability_catalog)

    registry.load_skills()
    registry.generate_index()
    return True, f"Installed skill `{skill_name}` with capability tags: {normalized['capabilities']}"


def install_tool_onboarding_batch(
    batch_entries: Iterable[Mapping[str, Any]],
    *,
    skills_definitions_dir: Path,
    skills_library_dir: Path,
    capability_catalog_path: Path,
    tool_cards_dir: Path | None = None,
    install_workflow: str,
    record_custom_tool: bool = True,
    stop_on_error: bool = False,
    installed_at: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "installed": [],
        "failed": [],
        "attempted": 0,
    }
    for entry in batch_entries:
        if not isinstance(entry, Mapping):
            continue
        draft = entry.get("draft", entry)
        source_meta = entry.get("source_meta", {})
        manual_summary = entry.get("manual_summary")
        if not isinstance(draft, Mapping):
            continue
        if not isinstance(source_meta, Mapping):
            source_meta = {}
        if manual_summary is not None and not isinstance(manual_summary, Mapping):
            manual_summary = None

        report["attempted"] += 1
        ok, message = install_tool_onboarding_draft(
            draft,
            source_meta,
            manual_summary=manual_summary,
            skills_definitions_dir=skills_definitions_dir,
            skills_library_dir=skills_library_dir,
            capability_catalog_path=capability_catalog_path,
            tool_cards_dir=tool_cards_dir,
            install_workflow=install_workflow,
            record_custom_tool=record_custom_tool,
            installed_at=installed_at,
        )
        tool_name = slugify_skill_name(str(draft.get("skill_name", "")))
        if ok:
            report["installed"].append({"skill_name": tool_name, "message": message})
        else:
            report["failed"].append({"skill_name": tool_name, "message": message})
            if stop_on_error:
                break

    report["passed"] = len(report["failed"]) == 0
    return report
