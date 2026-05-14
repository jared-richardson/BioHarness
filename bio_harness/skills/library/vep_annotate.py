from __future__ import annotations

import shlex
import shutil
import string
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi
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


def _vep_command() -> str:
    launcher = tool_launcher_command("vep")
    if launcher:
        return launcher
    return which_with_pixi("vep") or "vep"


def _annotation_mode(kwargs: dict) -> str:
    if str(kwargs.get("annotation_gff", "")).strip() or str(kwargs.get("annotation_gtf", "")).strip():
        return "custom"
    if str(kwargs.get("use_database", "")).strip().lower() in {"1", "true", "yes"}:
        return "database"
    return "cache"


def _require_helper_binary(tool_name: str, *, context: str) -> str:
    """Resolve a helper binary required by a conditional execution path."""

    resolved = which_with_pixi(tool_name) or shutil.which(tool_name)
    if resolved:
        return shlex.quote(str(resolved))
    raise ValueError(f"{context} requires helper tool '{tool_name}' to be available.")


def vep_annotate(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    params = dict(kwargs)
    input_vcf = str(params.get("input_vcf", "")).strip()
    output_vcf = str(params.get("output_vcf", "")).strip()
    if not input_vcf or not output_vcf:
        raise ValueError("Missing required parameter(s) for template: input_vcf, output_vcf")

    vep_cmd = _vep_command()
    mode = _annotation_mode(params)
    parts = [vep_cmd, "--format", "vcf", "-i", "{input_vcf}", "-o", "{output_vcf}", "--vcf", "--no_stats", "--force_overwrite"]

    if mode == "custom":
        annotation_path = str(params.get("annotation_gff", "")).strip() or str(params.get("annotation_gtf", "")).strip()
        reference_fasta = str(params.get("reference_fasta", "")).strip()
        if not annotation_path or not reference_fasta:
            raise ValueError("Missing required parameter(s) for template: reference_fasta, annotation_gff|annotation_gtf")
        output_dir = Path(output_vcf).expanduser().resolve().parent
        stage_root = output_dir / "_vep"
        source_path = Path(annotation_path).expanduser().resolve()
        staged_name = source_path.name if source_path.name.endswith(".gz") else f"{source_path.name}.gz"
        staged_annotation = stage_root / staged_name
        bgzip = _require_helper_binary("bgzip", context="vep_annotate custom-reference mode")
        tabix = _require_helper_binary("tabix", context="vep_annotate custom-reference mode")
        preset = "gff"
        params["annotation_path"] = str(source_path)
        params["reference_fasta"] = reference_fasta
        params["stage_root"] = str(stage_root)
        params["staged_annotation"] = str(staged_annotation)
        prep = (
            "mkdir -p {stage_root}; "
            "if [ ! -s {staged_annotation} ] || [ {annotation_path} -nt {staged_annotation} ]; then "
            f"{bgzip} -c {{annotation_path}} > {{staged_annotation}}; "
            "fi; "
            "if [ ! -s {staged_annotation}.tbi ] || [ {staged_annotation} -nt {staged_annotation}.tbi ]; then "
            f"{tabix} -f -p {preset} {{staged_annotation}}; "
            "fi; "
        )
        parts.extend(["--gff", "{staged_annotation}", "--fasta", "{reference_fasta}", "--species", "{species}"])
        params.setdefault("species", "custom")
        return _render_template(prep + " ".join(parts), params)

    if mode == "database":
        parts.extend(["--database", "--assembly", "{assembly}", "--species", "{species}"])
        params.setdefault("species", "homo_sapiens")
        return _render_template(" ".join(parts), params)

    parts.extend(["--cache", "--offline", "--assembly", "{assembly}", "--species", "{species}"])
    cache_dir = str(params.get("cache_dir", "")).strip()
    if cache_dir:
        parts.extend(["--dir_cache", "{cache_dir}"])
    params.setdefault("species", "homo_sapiens")
    return _render_template(" ".join(parts), params)
