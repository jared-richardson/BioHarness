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


def flye_assemble(**kwargs) -> str:
    """Render a Flye assembly command.

    Args:
        **kwargs: Flye wrapper arguments including ``reads_fastq``,
            ``output_dir``, ``genome_size``, optional ``read_mode``, and
            optional ``meta_mode``.

    Returns:
        Shell command string ready for execution.

    Raises:
        ValueError: If the requested read mode is unsupported or required
            template parameters are missing.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    params = dict(kwargs)
    read_mode = str(params.get("read_mode", "nano-raw") or "nano-raw").strip()
    if read_mode not in {"nano-raw", "nano-hq", "pacbio-raw", "pacbio-hifi", "subassemblies"}:
        raise ValueError("Unsupported Flye read_mode")
    flye_cmd = tool_launcher_command("flye") or (which_with_pixi("flye") or "flye")
    params["read_mode_flag"] = f"--{read_mode}"
    params["path_prefix"] = shell_path_prefix("flye", "minimap2")
    template = (
        "set -euo pipefail; "
        "export PATH={path_prefix}:$PATH; "
        "mkdir -p {output_dir}; "
        f"{flye_cmd} {{read_mode_flag}} {{reads_fastq}}"
    )
    if bool(params.get("meta_mode", False)):
        template += " --meta"
    template += " --threads {threads} --out-dir {output_dir} --genome-size {genome_size}"
    return _render_template(template, params)
