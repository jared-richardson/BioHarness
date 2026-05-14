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


def majiq_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    kwargs = dict(kwargs)
    kwargs.setdefault("threads", 2)
    kwargs.setdefault("analysis_name", "control_vs_treatment")
    template = (
        "majiq build -j {threads} -c {config_file} -o {output_dir} && "
        "majiq deltapsi -j {threads} -grp1 {group1_bams} -grp2 {group2_bams} "
        "-n {analysis_name} -o {output_dir}"
    )
    return _render_template(template, kwargs)

