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


def salmon_quant(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    normalized = dict(kwargs)
    if not str(normalized.get("library_type", "")).strip():
        normalized["library_type"] = "A"
    index_dir = str(normalized.get("index_dir", "")).strip()
    transcriptome_fasta = str(normalized.get("transcriptome_fasta", "")).strip()
    quant_cmd = _render_template(
        "salmon quant -i {index_dir} -l {library_type} -1 {reads_1} -2 {reads_2} --validateMappings -p {threads} -o {output_dir}",
        normalized,
    )
    if not transcriptome_fasta:
        return quant_cmd
    index_parent = Path(index_dir).parent
    sentinel = Path(index_dir) / "versionInfo.json"
    build_cmd = (
        f"mkdir -p {shlex.quote(str(index_parent))} "
        f"&& if [ ! -f {shlex.quote(str(sentinel))} ]; then "
        f"salmon index -t {shlex.quote(transcriptome_fasta)} -i {shlex.quote(index_dir)}; "
        f"fi"
    )
    return f"{build_cmd} && {quant_cmd}"
