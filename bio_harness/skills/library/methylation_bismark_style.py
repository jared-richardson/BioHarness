from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi
from bio_harness.core.uncommon_skill_framework import _assert_safe_command

BUNDLED_BISMARK_SUMMARY = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "bismark_summary.py"


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


def methylation_bismark_style(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        manual = str(kwargs["command"]).strip()
        _assert_safe_command(manual)
        return manual

    params = dict(kwargs)
    reads_1 = str(params.get("reads_1", "")).strip()
    reads_2 = str(params.get("reads_2", "")).strip()
    genome_folder = str(params.get("genome_folder", "")).strip()
    output_dir = str(params.get("output_dir", "")).strip()
    output_report = str(params.get("output_report", "")).strip()
    missing = [
        name
        for name, value in (
            ("reads_1", reads_1),
            ("reads_2", reads_2),
            ("genome_folder", genome_folder),
            ("output_dir", output_dir),
            ("output_report", output_report),
        )
        if not value
    ]
    if missing:
        missing_args = ", ".join(sorted(set(missing)))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")

    sample_name = str(params.get("sample_name", "")).strip() or Path(reads_1).name.split(".", 1)[0]
    output_dir_path = Path(output_dir)
    genome_folder_path = Path(genome_folder)
    output_report_path = Path(output_report)
    reference_fasta = str(params.get("reference_fasta", "")).strip()

    bismark_bin = which_with_pixi("bismark") or "bismark"
    prep_bin = which_with_pixi("bismark_genome_preparation") or "bismark_genome_preparation"
    python_bin = which_with_pixi("python3") or "python3"
    bismark_dir = str(Path(bismark_bin).expanduser().resolve().parent)
    bowtie2_dir = str((Path(which_with_pixi("bowtie2-build") or "bowtie2-build")).expanduser().resolve().parent)
    samtools_dir = str((Path(which_with_pixi("samtools") or "samtools")).expanduser().resolve().parent)

    params["threads"] = int(params.get("threads", 2) or 2)
    params["sample_name"] = sample_name
    params["reference_fasta"] = reference_fasta
    params["output_report_dir"] = str(output_report_path.parent)
    params["path_prefix"] = ":".join(dict.fromkeys([bismark_dir, bowtie2_dir, samtools_dir]))
    params["summary_script"] = str(BUNDLED_BISMARK_SUMMARY)
    params["report_path"] = str(output_dir_path / f"{sample_name}_PE_report.txt")
    params["bam_path"] = str(output_dir_path / f"{sample_name}_pe.bam")
    params["genome_fasta_link"] = str(genome_folder_path / (Path(reference_fasta).name if reference_fasta else ""))
    params["bisulfite_index_dir"] = str(genome_folder_path / "Bisulfite_Genome")

    prep_steps: list[str] = [
        "if [ ! -d {bisulfite_index_dir} ]; then ",
        "mkdir -p {genome_folder}; ",
    ]
    if reference_fasta:
        prep_steps.append("cp -f {reference_fasta} {genome_fasta_link}; ")
    prep_steps.extend(
        [
            f"if ! command -v {shlex.quote(Path(prep_bin).name)} >/dev/null 2>&1; then "
            "echo 'Missing helper binary: bismark_genome_preparation' >&2; exit 2; fi; ",
            f"if ! command -v {shlex.quote(Path(which_with_pixi('bowtie2-build') or 'bowtie2-build').name)} >/dev/null 2>&1; then "
            "echo 'Missing helper binary: bowtie2-build' >&2; exit 2; fi; ",
            f"if ! command -v {shlex.quote(Path(which_with_pixi('samtools') or 'samtools').name)} >/dev/null 2>&1; then "
            "echo 'Missing helper binary: samtools' >&2; exit 2; fi; ",
            f"if ! command -v {shlex.quote(Path(python_bin).name)} >/dev/null 2>&1; then "
            "echo 'Missing helper binary: python3' >&2; exit 2; fi; ",
            "if [ -z \"$(find {genome_folder} -maxdepth 1 -type f \\( -name '*.fa' -o -name '*.fasta' -o -name '*.fna' \\) -print -quit)\" ]; then ",
            "echo 'Missing reference FASTA for Bismark genome preparation.' >&2; ",
            "exit 2; ",
            "fi; ",
            f"{prep_bin} --bowtie2 {{genome_folder}}; ",
            "fi; ",
        ]
    )
    prep_block = "".join(prep_steps)

    template = (
        "set -euo pipefail; "
        "export PATH={path_prefix}:$PATH; "
        "mkdir -p {genome_folder}; "
        "mkdir -p {output_dir}; "
        "mkdir -p {output_report_dir}; "
        "if command -v bismark >/dev/null 2>&1; then "
        + prep_block
        + f"{bismark_bin} --genome_folder {{genome_folder}} -1 {{reads_1}} -2 {{reads_2}} "
        "--parallel {threads} --basename {sample_name} -o {output_dir}; "
        f"{python_bin} {{summary_script}} --report {{report_path}} --bam {{bam_path}} "
        "--sample-name {sample_name} --output {output_report}; "
        "else "
        "printf 'metric\\tvalue\\nworkflow_status\\tdegraded\\nreason\\tmissing_bismark\\n' > {output_report}; "
        "fi"
    )
    return _render_template(template, params)
