from __future__ import annotations

import json
import re
import shlex
import string
from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.tool_launchers import apply_tool_launcher, tool_launcher_guard_expr


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UNCOMMON_SPEC_PATH = PROJECT_ROOT / "bio_harness" / "skills" / "uncommon" / "specs.json"
DEFAULT_UNCOMMON_SCHEMA_PATH = PROJECT_ROOT / "bio_harness" / "skills" / "uncommon" / "spec_schema.json"
_ALLOWED_RISK_LEVELS = {"low", "medium", "high"}
_DESTRUCTIVE_PATTERNS = (
    re.compile(r"(^|\s)rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/(\s|$)", flags=re.IGNORECASE),
    re.compile(r"(^|\s)sudo\s+rm\b", flags=re.IGNORECASE),
    re.compile(r"(^|\s)mkfs(\.|\s)", flags=re.IGNORECASE),
    re.compile(r"(^|\s)dd\s+if=", flags=re.IGNORECASE),
    re.compile(r"(^|\s)shutdown\b", flags=re.IGNORECASE),
    re.compile(r"(^|\s)reboot\b", flags=re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*;\s*\}", flags=re.IGNORECASE),
)


class UncommonSkillSpecError(ValueError):
    pass


def load_uncommon_skill_catalog(spec_path: Path | None = None) -> dict[str, Any]:
    path = spec_path or DEFAULT_UNCOMMON_SPEC_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise UncommonSkillSpecError("Uncommon skill catalog must be a JSON object.")
    return payload


def load_uncommon_skill_schema(schema_path: Path | None = None) -> dict[str, Any]:
    path = schema_path or DEFAULT_UNCOMMON_SCHEMA_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise UncommonSkillSpecError("Uncommon skill schema must be a JSON object.")
    return payload


def uncommon_skill_specs(spec_path: Path | None = None) -> list[dict[str, Any]]:
    catalog = load_uncommon_skill_catalog(spec_path)
    rows = catalog.get("skills", []) if isinstance(catalog.get("skills", []), list) else []
    specs = [dict(row) for row in rows if isinstance(row, dict)]
    specs.sort(key=lambda row: str(row.get("name", "")))
    return specs


def uncommon_skill_index(spec_path: Path | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in uncommon_skill_specs(spec_path):
        name = str(row.get("name", "")).strip()
        if name:
            out[name] = row
    return out


def validate_uncommon_skill_spec(spec: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    name = str(spec.get("name", "")).strip()
    if not name:
        errors.append("missing required field 'name'")

    risk_level = str(spec.get("risk_level", "")).strip().lower()
    if risk_level not in _ALLOWED_RISK_LEVELS:
        errors.append("risk_level must be one of: low, medium, high")

    capabilities = [str(x).strip() for x in spec.get("capabilities", []) if str(x).strip()]
    if not capabilities:
        errors.append("capabilities must be non-empty")

    tools_required = [str(x).strip() for x in spec.get("tools_required", []) if str(x).strip()]
    if not tools_required:
        errors.append("tools_required must be non-empty")

    parameters = spec.get("parameters", {}) if isinstance(spec.get("parameters", {}), Mapping) else {}
    if not parameters:
        errors.append("parameters must be a non-empty object")

    required_args = [str(x).strip() for x in spec.get("required_args", []) if str(x).strip()]
    if not required_args:
        errors.append("required_args must be non-empty")
    for arg in required_args:
        if arg not in parameters:
            errors.append(f"required_args contains unknown parameter '{arg}'")

    template = str(spec.get("command_template", "")).strip()
    if not template:
        errors.append("command_template is required")
    else:
        formatter = string.Formatter()
        fields = [f for _, f, _, _ in formatter.parse(template) if f]
        for field in fields:
            if field == "optional_flags":
                continue
            if field not in parameters:
                errors.append(f"command_template field '{field}' missing from parameters")

    tool_groups = spec.get("tool_groups", []) if isinstance(spec.get("tool_groups", []), list) else []
    if not tool_groups:
        errors.append("tool_groups must be non-empty")

    fallback_outputs = spec.get("fallback_outputs", []) if isinstance(spec.get("fallback_outputs", []), list) else []
    if not fallback_outputs:
        errors.append("fallback_outputs must be non-empty")
    else:
        for row in fallback_outputs:
            if not isinstance(row, Mapping):
                errors.append("fallback_outputs entries must be objects")
                continue
            path_arg = str(row.get("path_arg", "")).strip()
            if not path_arg:
                errors.append("fallback_outputs.path_arg is required")
            elif path_arg not in parameters:
                errors.append(f"fallback_outputs path_arg '{path_arg}' missing from parameters")

    docs_anchor = str(spec.get("docs_anchor", "")).strip()
    if not docs_anchor:
        errors.append("docs_anchor is required")

    test_files = [str(x).strip() for x in spec.get("test_files", []) if str(x).strip()]
    if not test_files:
        errors.append("test_files must be non-empty")

    fixtures = [str(x).strip() for x in spec.get("fixtures", []) if str(x).strip()]
    if not fixtures:
        errors.append("fixtures must be non-empty")

    return errors


def validate_uncommon_skill_catalog(catalog: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    version = int(catalog.get("version", 0) or 0)
    if version < 1:
        errors.append("catalog version must be >= 1")

    rows = catalog.get("skills", []) if isinstance(catalog.get("skills", []), list) else []
    if not rows:
        errors.append("catalog.skills must be non-empty")
        return errors

    seen: set[str] = set()
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            errors.append(f"skills[{idx}] must be an object")
            continue
        name = str(row.get("name", "")).strip()
        if name in seen:
            errors.append(f"duplicate skill name '{name}'")
        seen.add(name)
        row_errors = validate_uncommon_skill_spec(row)
        errors.extend([f"skills[{idx}] {msg}" for msg in row_errors])

    return errors


def _normalized_values(spec: Mapping[str, Any], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    params = spec.get("parameters", {}) if isinstance(spec.get("parameters", {}), Mapping) else {}
    values: dict[str, Any] = {}
    for key, meta in params.items():
        raw = kwargs.get(key)
        if raw is None and isinstance(meta, Mapping) and "default" in meta:
            raw = meta.get("default")
        values[str(key)] = raw
    return values


def _ensure_required(values: Mapping[str, Any], required_args: list[str]) -> None:
    missing: list[str] = []
    for key in required_args:
        value = values.get(key)
        if value is None:
            missing.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(key)
    if missing:
        raise ValueError(f"Missing required parameter(s) for uncommon wrapper: {', '.join(sorted(set(missing)))}")


def _render_optional_flags(values: Mapping[str, Any], optional_flags: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    for key in sorted(optional_flags.keys()):
        flag = str(optional_flags.get(key, "")).strip()
        if not flag:
            continue
        value = values.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                chunks.append(flag)
            continue
        text = str(value).strip()
        if not text:
            continue
        chunks.append(f"{flag} {shlex.quote(text)}")
    return " ".join(chunks)


def _render_passthrough_flags(spec: Mapping[str, Any], kwargs: Mapping[str, Any]) -> str:
    params = set((spec.get("parameters", {}) or {}).keys())
    reserved = {"command"}
    chunks: list[str] = []
    for key in sorted(kwargs.keys()):
        if key in params or key in reserved:
            continue
        value = kwargs.get(key)
        if value is None:
            continue
        flag = "--" + str(key).strip().replace("_", "-")
        if isinstance(value, bool):
            if value:
                chunks.append(flag)
            continue
        text = str(value).strip()
        if not text:
            continue
        chunks.append(f"{flag} {shlex.quote(text)}")
    return " ".join(chunks)


def _build_tool_guard(spec: Mapping[str, Any]) -> str:
    tool_groups = spec.get("tool_groups", []) if isinstance(spec.get("tool_groups", []), list) else []
    group_exprs: list[str] = []
    for group in tool_groups:
        tools = [str(x).strip() for x in group if str(x).strip()] if isinstance(group, list) else []
        if not tools:
            continue
        checks = [tool_launcher_guard_expr(tool) or f"command -v {shlex.quote(tool)} >/dev/null 2>&1" for tool in tools]
        group_exprs.append("(" + " || ".join(checks) + ")")
    if not group_exprs:
        return "true"
    return " && ".join(group_exprs)


def _build_fallback_command(spec: Mapping[str, Any], values: Mapping[str, Any]) -> str:
    parts: list[str] = ["set -euo pipefail"]

    tools_required = [str(x).strip() for x in spec.get("tools_required", []) if str(x).strip()]
    for tool in tools_required:
        safe_tool = re.sub(r"[^A-Za-z0-9._+-]+", "_", tool)
        parts.append(f"echo __MISSING_TOOL__:{safe_tool}")

    fallback_outputs = spec.get("fallback_outputs", []) if isinstance(spec.get("fallback_outputs", []), list) else []
    for row in fallback_outputs:
        if not isinstance(row, Mapping):
            continue
        path_arg = str(row.get("path_arg", "")).strip()
        raw_path = values.get(path_arg)
        out_path = str(raw_path).strip() if raw_path is not None else ""
        if not out_path:
            continue
        q_path = shlex.quote(out_path)
        q_dir = shlex.quote(str(Path(out_path).expanduser().parent))
        content = str(row.get("content", ""))
        q_content = shlex.quote(content)
        parts.append(f"mkdir -p {q_dir}")
        parts.append(f"printf %s {q_content} > {q_path}")

    note = str(spec.get("fallback_note", "")).strip()
    if note:
        parts.append(f"echo {shlex.quote(note)}")

    return " ; ".join(parts)


def _assert_safe_command(command: str) -> None:
    normalized = f" {str(command or '').strip()} "
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(normalized):
            raise ValueError("Rejected destructive command pattern in uncommon wrapper command.")


def build_uncommon_wrapper_command(
    skill_name: str,
    kwargs: Mapping[str, Any],
    *,
    spec_path: Path | None = None,
) -> str:
    specs = uncommon_skill_index(spec_path)
    spec = specs.get(str(skill_name).strip())
    if not spec:
        raise ValueError(f"Unknown uncommon skill spec: {skill_name}")

    manual = str(kwargs.get("command", "")).strip()
    if manual:
        _assert_safe_command(manual)
        return manual

    values = _normalized_values(spec, kwargs)
    required_args = [str(x).strip() for x in spec.get("required_args", []) if str(x).strip()]
    _ensure_required(values, required_args)

    optional_flags = spec.get("optional_flags", {}) if isinstance(spec.get("optional_flags", {}), Mapping) else {}
    rendered_optional = _render_optional_flags(values, optional_flags)
    passthrough = _render_passthrough_flags(spec, kwargs)

    quoted_values: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            quoted_values[key] = ""
        else:
            quoted_values[key] = shlex.quote(str(value))
    merged_optional = " ".join(x for x in [rendered_optional, passthrough] if x).strip()
    quoted_values["optional_flags"] = merged_optional

    template = str(spec.get("command_template", "")).strip()
    if not template:
        raise ValueError(f"Uncommon skill '{skill_name}' has empty command_template")
    try:
        main_command = template.format(**quoted_values).strip()
    except KeyError as exc:
        raise ValueError(f"Uncommon skill '{skill_name}' missing template value: {exc}") from exc
    for tool_name in [str(x).strip() for x in spec.get("tools_required", []) if str(x).strip()]:
        main_command = apply_tool_launcher(main_command, tool_name)

    tool_guard = _build_tool_guard(spec)
    fallback_command = _build_fallback_command(spec, values)
    command = f"set -euo pipefail; if {tool_guard}; then {main_command}; else {fallback_command}; fi"
    _assert_safe_command(command)
    return command


def render_uncommon_wrapper_template(skill_name: str) -> str:
    safe_name = str(skill_name).strip()
    if not safe_name:
        raise ValueError("skill_name is required")
    return (
        "from __future__ import annotations\\n\\n"
        "from bio_harness.core.uncommon_skill_framework import build_uncommon_wrapper_command\\n\\n\\n"
        f"def {safe_name}(**kwargs) -> str:\\n"
        f"    return build_uncommon_wrapper_command(\"{safe_name}\", kwargs)\\n"
    )


def build_uncommon_onboarding_drafts(spec_path: Path | None = None) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    for spec in uncommon_skill_specs(spec_path):
        drafts.append(
            {
                "skill_name": str(spec.get("name", "")).strip(),
                "description": str(spec.get("description", "")).strip(),
                "risk_level": str(spec.get("risk_level", "medium")).strip().lower(),
                "tools_required": [str(x).strip() for x in spec.get("tools_required", []) if str(x).strip()],
                "capabilities": [str(x).strip() for x in spec.get("capabilities", []) if str(x).strip()],
                "parameters": dict(spec.get("parameters", {})),
                "command_template": str(spec.get("command_template", "")).strip(),
                "usage_guide": (
                    "Generated from uncommon skill schema catalog. "
                    "Wrapper behavior includes deterministic argument rendering and safe degrade when tools are missing."
                ),
            }
        )
    return drafts


def install_uncommon_onboarding_drafts(
    *,
    source_meta: Mapping[str, Any],
    skills_definitions_dir: Path,
    skills_library_dir: Path,
    capability_catalog_path: Path,
    install_workflow: str = "uncommon_schema_onboarding",
    spec_path: Path | None = None,
) -> dict[str, Any]:
    from bio_harness.core.tool_onboarding import install_tool_onboarding_batch

    entries = [{"draft": row, "source_meta": dict(source_meta)} for row in build_uncommon_onboarding_drafts(spec_path)]
    return install_tool_onboarding_batch(
        entries,
        skills_definitions_dir=skills_definitions_dir,
        skills_library_dir=skills_library_dir,
        capability_catalog_path=capability_catalog_path,
        install_workflow=install_workflow,
        record_custom_tool=False,
    )
