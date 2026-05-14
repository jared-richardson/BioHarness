from __future__ import annotations

import shlex
import string

from bio_harness.core.tool_launchers import tool_launcher_command


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


def prokka_annotate(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    output_dir = str(kwargs.get("output_dir", "")).strip()
    sample_prefix = str(kwargs.get("sample_prefix", "")).strip()
    input_fasta = str(kwargs.get("input_fasta", "")).strip()
    prokka_cmd = tool_launcher_command("prokka") or "prokka"
    if not output_dir or not sample_prefix or not input_fasta:
        return _render_template(f"{prokka_cmd} --outdir {{output_dir}} --prefix {{sample_prefix}} {{input_fasta}}", kwargs)

    parts = [prokka_cmd, "--outdir", "{output_dir}", "--prefix", "{sample_prefix}"]
    cpus = str(kwargs.get("cpus", "")).strip()
    if cpus:
        parts.extend(["--cpus", "{cpus}"])
    kingdom = str(kwargs.get("kingdom", "")).strip()
    if kingdom:
        parts.extend(["--kingdom", "{kingdom}"])
    genus = str(kwargs.get("genus", "")).strip()
    if genus:
        parts.extend(["--genus", "{genus}"])
    species = str(kwargs.get("species", "")).strip()
    if species:
        parts.extend(["--species", "{species}"])
    strain = str(kwargs.get("strain", "")).strip()
    if strain:
        parts.extend(["--strain", "{strain}"])
    locustag = str(kwargs.get("locustag", "")).strip()
    if locustag:
        parts.extend(["--locustag", "{locustag}"])
    parts.append("{input_fasta}")
    return _render_template(" ".join(parts), kwargs)
