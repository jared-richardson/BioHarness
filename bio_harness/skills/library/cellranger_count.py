from __future__ import annotations

import shlex
import string


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


def cellranger_count(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    template = "cellranger count --id={run_id} --transcriptome={transcriptome_ref} --fastqs={fastq_dir} --sample={sample_name}"
    return _render_template(template, kwargs)
