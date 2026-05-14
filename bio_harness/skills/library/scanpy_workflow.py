from __future__ import annotations

import os
import shlex
import string
import sys
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi


BUNDLED_SCANPY_WORKFLOW = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "scanpy_workflow.py"


def _resolve_python_bin() -> str:
    configured = str(os.getenv("BIO_HARNESS_PYTHON", "")).strip()
    if configured:
        return configured
    return which_with_pixi("python3") or which_with_pixi("python") or sys.executable or "python3"


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


def scanpy_workflow(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    params = dict(kwargs)
    input_path = str(params.get("input_path", "")).strip() or str(params.get("input_h5ad", "")).strip()
    if not input_path:
        raise ValueError("Missing required parameter(s) for template: input_path")
    params["input_path"] = input_path

    script_path = str(params.get("script_path", "")).strip()
    if (not script_path) or (not Path(script_path).expanduser().exists()):
        params["script_path"] = str(BUNDLED_SCANPY_WORKFLOW)

    params["python_bin"] = _resolve_python_bin()
    template = (
        "{python_bin} {script_path} --input-path {input_path} --output-dir {output_dir}"
        " --min-genes {min_genes} --min-cells {min_cells} --max-mito-pct {max_mito_pct}"
        " --n-hvgs {n_hvgs} --leiden-resolution {leiden_resolution}"
    )
    params.setdefault("min_genes", 300)
    params.setdefault("min_cells", 20)
    params.setdefault("max_mito_pct", 15)
    params.setdefault("n_hvgs", 2000)
    params.setdefault("leiden_resolution", 0.3)
    return _render_template(template, params)
