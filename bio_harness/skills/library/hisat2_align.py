from __future__ import annotations

import shlex
import string
from pathlib import Path


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


def hisat2_align(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    index_base = str(kwargs.get("index_base", "")).strip()
    reads_1 = str(kwargs.get("reads_1", "")).strip()
    reads_2 = str(kwargs.get("reads_2", "")).strip()
    output_sam = str(kwargs.get("output_sam", "")).strip()
    if not index_base or not reads_1 or not reads_2 or not output_sam:
        raise ValueError("Missing required parameter(s) for template: index_base, output_sam, reads_1, reads_2")
    threads = int(kwargs.get("threads", 2) or 2)
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    cache_index_base = str(kwargs.get("cache_index_base", "")).strip()
    prep = ""
    source_index = cache_index_base if cache_index_base else index_base
    if reference_fasta:
        prep = (
            f"mkdir -p {shlex.quote(str(Path(source_index).expanduser().parent))}; "
            f"if [ ! -s {shlex.quote(source_index + '.1.ht2')} ]; then "
            f"hisat2-build {shlex.quote(reference_fasta)} {shlex.quote(source_index)}; fi; "
        )
        if source_index != index_base:
            prep += (
                f"mkdir -p {shlex.quote(str(Path(index_base).expanduser().parent))}; "
                f"cp {shlex.quote(source_index)}*.ht2 {shlex.quote(str(Path(index_base).expanduser().parent))}/ || true; "
            )
    output_dir = str(Path(output_sam).expanduser().parent)
    command = (
        "set -euo pipefail; "
        + f"mkdir -p {shlex.quote(output_dir)}; "
        + prep
        + _render_template(
            "hisat2 -x {index_base} -1 {reads_1} -2 {reads_2} -p {threads} -S {output_sam}",
            {
                "index_base": index_base if not prep else index_base,
                "reads_1": reads_1,
                "reads_2": reads_2,
                "threads": threads,
                "output_sam": output_sam,
            },
        )
    )
    return f"bash -c {shlex.quote(command)}"
