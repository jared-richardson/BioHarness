from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.core.tool_env import rscript_for_requirement

BUNDLED_DEXSEQ_WRAPPER = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "dexseq_wrapper.R"


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


def dexseq_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    kwargs = dict(kwargs)
    script_path = str(kwargs.get("script_path", "")).strip()
    if (not script_path) or (not Path(script_path).expanduser().exists()):
        kwargs["script_path"] = str(BUNDLED_DEXSEQ_WRAPPER)
    kwargs["rscript_bin"] = str(rscript_for_requirement("dexseq") or "Rscript")
    template = (
        "{rscript_bin} {script_path} --counts {counts_matrix} --metadata {metadata_table} "
        "--design {design_formula} --contrast {contrast} --outdir {output_dir}"
    )
    return _render_template(template, kwargs)
