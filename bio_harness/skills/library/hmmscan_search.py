from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi


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


def hmmscan_search(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    kwargs = dict(kwargs)
    kwargs.setdefault("threads", 2)
    output_tbl = str(kwargs.get("output_tbl", "")).strip()
    if not output_tbl:
        raise ValueError("Missing required parameter(s) for template: output_tbl")
    output_txt = str(kwargs.get("output_txt", "")).strip()
    if not output_txt:
        output_txt = f"{output_tbl}.txt"
        kwargs["output_txt"] = output_txt
    out_dir = str(Path(output_tbl).expanduser().parent)
    kwargs["hmmscan_bin"] = which_with_pixi("hmmscan") or "hmmscan"
    kwargs["path_prefix"] = shell_path_prefix("hmmscan")
    core = _render_template(
        "set -euo pipefail; export PATH={path_prefix}:$PATH; {hmmscan_bin} --cpu {threads} --tblout {output_tbl} {hmm_db} {query_fasta} > {output_txt}",
        kwargs,
    )
    return f"mkdir -p {shlex.quote(out_dir)} && {core}"
