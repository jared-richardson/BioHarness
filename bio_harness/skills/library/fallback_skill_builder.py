from __future__ import annotations

import json
import shlex
import string


def _render_template(template: str, kwargs: dict[str, object]) -> str:
    rendered: dict[str, str] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        rendered[key] = shlex.quote(str(value))
    formatter = string.Formatter()
    required_fields = [field_name for _, field_name, _, _ in formatter.parse(template) if field_name]
    missing = [field for field in required_fields if field not in rendered]
    if missing:
        missing_args = ", ".join(sorted(set(missing)))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")
    return template.format(**rendered).strip()


def _normalize_cli_value(key: str, value: object) -> object:
    token = str(key or "").strip()
    if value is None:
        return None
    if token == "strictness_mode":
        mode = str(value).strip().lower()
        if mode in {"strict", "default", "safe"}:
            return "conservative"
        if mode not in {"conservative", "aggressive"}:
            return "conservative"
        return mode
    if token == "target_capability_set":
        aliases = {
            "coverage_report": "run_reporting",
            "coverage_report_generation": "run_reporting",
        }
        if isinstance(value, (list, tuple, set)):
            parts = [
                aliases.get(str(item).strip(), str(item).strip())
                for item in value
                if str(item).strip()
            ]
            return ",".join(dict.fromkeys(parts))
        text = str(value).strip()
        if not text:
            return text
        parts = [aliases.get(part.strip(), part.strip()) for part in text.split(",") if part.strip()]
        return ",".join(dict.fromkeys(parts))
    if token in {"target_capability_set", "allowed_tools"} and isinstance(value, (list, tuple, set)):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    if token == "data_reference_constraints" and isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if token == "data_reference_constraints":
        text = str(value).strip()
        if not text:
            return "{}"
        try:
            parsed = json.loads(text)
        except Exception:
            return "{}"
        if not isinstance(parsed, (dict, list)):
            return "{}"
        return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return value


def fallback_skill_builder(**kwargs: object) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    template = (
        "set -euo pipefail; "
        "repo_root=\"$PWD\"; "
        "while [ ! -f \"$repo_root/pixi.toml\" ] || [ ! -d \"$repo_root/bio_harness\" ] || [ ! -d \"$repo_root/scripts\" ]; do "
        "parent=$(dirname \"$repo_root\"); "
        "if [ \"$parent\" = \"$repo_root\" ]; then echo 'Could not locate Bio-Harness repo root from current working directory.' >&2; exit 1; fi; "
        "repo_root=\"$parent\"; "
        "done; "
        "cd \"$repo_root\"; "
        "if python3 -m scripts.fallback_skill_builder "
        "--target-capabilities {target_capability_set} "
        "--allowed-tools {allowed_tools} "
        "--data-constraints-json {data_reference_constraints} "
        "--strictness-mode {strictness_mode} "
        "--request-text {request_text} "
        "--out-json {out_json}; "
        "then :; "
        "else status=$?; "
        "if [ \"$status\" -eq 2 ] && [ -s {out_json} ]; then :; else exit \"$status\"; fi; "
        "fi"
    )

    defaults = {
        "request_text": "",
        "out_json": "workspace/outputs/fallback/fallback_skill_builder_report.json",
    }
    merged = dict(defaults)
    merged.update({k: v for k, v in kwargs.items() if v is not None})
    normalized = {
        key: _normalize_cli_value(key, value)
        for key, value in merged.items()
    }
    return _render_template(template, normalized)
