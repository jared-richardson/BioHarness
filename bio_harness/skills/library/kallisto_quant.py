from __future__ import annotations

from pathlib import Path
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


def kallisto_quant(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    index_path = str(kwargs.get("index_path", "")).strip()
    transcriptome_fasta = str(kwargs.get("transcriptome_fasta", "")).strip()
    quant_cmd = _render_template(
        "kallisto quant -i {index_path} -o {output_dir} -t {threads} {reads_1} {reads_2}",
        kwargs,
    )
    if not transcriptome_fasta:
        return quant_cmd
    index_parent = Path(index_path).parent
    build_cmd = (
        f"mkdir -p {shlex.quote(str(index_parent))} "
        f"&& if [ ! -f {shlex.quote(index_path)} ]; then "
        f"kallisto index -i {shlex.quote(index_path)} {shlex.quote(transcriptome_fasta)}; "
        f"fi"
    )
    return f"{build_cmd} && {quant_cmd}"
