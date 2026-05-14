from __future__ import annotations

import shlex
import string

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi
from bio_harness.core.tool_launchers import tool_launcher_command


def _render_template(template: str, kwargs: dict) -> str:
    rendered: dict[str, str] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        rendered[key] = shlex.quote(str(value))
    formatter = string.Formatter()
    field_names = [field_name for _, field_name, _, _ in formatter.parse(template) if field_name]
    missing = [field for field in field_names if field not in rendered]
    if missing:
        missing_args = ", ".join(sorted(set(missing)))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")
    return template.format(**rendered).strip()


def trinity_assemble(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    params = dict(kwargs)
    trinity_cmd = tool_launcher_command("trinity") or tool_launcher_command("Trinity")
    if not trinity_cmd:
        trinity_cmd = which_with_pixi("Trinity") or which_with_pixi("trinity") or "Trinity"
    params["path_prefix"] = shell_path_prefix("Trinity", "trinity", "jellyfish")
    template_parts = [
        "set -euo pipefail; "
        "export PATH={path_prefix}:$PATH; "
        "mkdir -p {output_dir}; "
        f"{trinity_cmd} --seqType fq --left {{reads_1}} --right {{reads_2}} --CPU {{threads}} --max_memory {{max_memory_gb}}G --output {{output_dir}}"
    ]
    no_normalize_reads = kwargs.get("no_normalize_reads")
    if isinstance(no_normalize_reads, bool):
        if no_normalize_reads:
            template_parts.append(" --no_normalize_reads")
    elif str(no_normalize_reads or "").strip().lower() in {"1", "true", "yes", "on"}:
        template_parts.append(" --no_normalize_reads")
    return _render_template("".join(template_parts), params)
