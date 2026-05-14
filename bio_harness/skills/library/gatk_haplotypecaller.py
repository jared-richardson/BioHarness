from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.skills.library._gatk_support import render_reference_and_bam_prereqs, resolve_gatk_bin


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


def gatk_haplotypecaller(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    input_bam = str(kwargs.get("input_bam", "")).strip()
    output_vcf = str(kwargs.get("output_vcf", "")).strip()
    if not output_vcf:
        raise ValueError("Missing required parameter(s) for template: output_vcf")
    out_dir = str(Path(output_vcf).expanduser().parent)
    gatk_bin = resolve_gatk_bin()
    template = f"{gatk_bin} HaplotypeCaller -R {{reference_fasta}} -I {{input_bam}} -O {{output_vcf}}"
    core = _render_template(template, kwargs)
    prereqs = [f"mkdir -p {shlex.quote(out_dir)}"]
    prereqs.extend(render_reference_and_bam_prereqs(reference_fasta, [input_bam]))
    return f"{' && '.join(prereqs)} && {core}"
