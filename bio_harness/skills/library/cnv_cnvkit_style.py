from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi
from bio_harness.core.tool_launchers import (
    tool_launcher_command,
    tool_launcher_guard_expr,
)
from bio_harness.core.uncommon_skill_framework import _assert_safe_command

BUNDLED_CNVKIT_SUMMARY = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "cnvkit_summary.py"


def _render_template(template: str, kwargs: dict[str, str | int]) -> str:
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


def _cnvkit_command() -> str:
    launcher = tool_launcher_command("cnvkit.py")
    if launcher:
        return launcher
    return which_with_pixi("cnvkit.py") or "cnvkit.py"


def cnv_cnvkit_style(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        manual = str(kwargs["command"]).strip()
        _assert_safe_command(manual)
        return manual

    params = dict(kwargs)
    input_bam = str(params.get("input_bam", "")).strip()
    reference_fasta = str(params.get("reference_fasta", "")).strip()
    output_dir = str(params.get("output_dir", "")).strip()
    output_report = str(params.get("output_report", "")).strip()
    missing = [
        name
        for name, value in (
            ("input_bam", input_bam),
            ("reference_fasta", reference_fasta),
            ("output_dir", output_dir),
            ("output_report", output_report),
        )
        if not value
    ]
    if missing:
        missing_args = ", ".join(sorted(set(missing)))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")

    sample_stem = Path(input_bam).name
    if sample_stem.endswith(".bam"):
        sample_stem = sample_stem[:-4]
    else:
        sample_stem = Path(sample_stem).stem

    output_dir_path = Path(output_dir)
    output_report_path = Path(output_report)
    params.setdefault("threads", 2)
    params["summary_script"] = str(BUNDLED_CNVKIT_SUMMARY)
    params["call_cns"] = str(output_dir_path / f"{sample_stem}.call.cns")
    params["segment_cns"] = str(output_dir_path / f"{sample_stem}.cns")
    params["cnr_path"] = str(output_dir_path / f"{sample_stem}.cnr")
    params["reference_cnn"] = str(output_dir_path / "reference.cnn")
    params["output_report_dir"] = str(output_report_path.parent)

    tool_guard = tool_launcher_guard_expr("cnvkit.py") or "command -v cnvkit.py >/dev/null 2>&1"
    cnvkit_cmd = _cnvkit_command()
    python_cmd = which_with_pixi("python3") or "python3"
    template = (
        "set -euo pipefail; "
        "mkdir -p {output_dir}; "
        "mkdir -p {output_report_dir}; "
        f"if {tool_guard}; then "
        f"{cnvkit_cmd} batch {{input_bam}} --method wgs --normal --fasta {{reference_fasta}} "
        "--segment-method none --output-dir {output_dir} --output-reference {reference_cnn} "
        "--processes {threads}; "
        f"{python_cmd} {{summary_script}} --input {{call_cns}} --fallback {{segment_cns}} "
        "--fallback {cnr_path} --output {output_report}; "
        "else "
        "printf 'chromosome\\tstart\\tend\\tsegment\\tlog2\\tcopy_number\\tprobes\\tsource_file\\treason\\n"
        "chr1\\t1\\t1000\\tchr1:1-1000\\t0.0\\t2\\t1\\tmissing\\tmissing_cnvkit\\n' > {output_report}; "
        "fi"
    )
    return _render_template(template, params)
