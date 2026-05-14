from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi


def stringtie_quant(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_bam = str(kwargs.get("input_bam", "")).strip()
    annotation_gtf = str(kwargs.get("annotation_gtf", "")).strip()
    output_gtf = str(kwargs.get("output_gtf", "")).strip()
    if not input_bam or not annotation_gtf or not output_gtf:
        raise ValueError("Missing required parameter(s) for template: annotation_gtf, input_bam, output_gtf")

    gene_abundance_tsv = str(kwargs.get("gene_abundance_tsv", "")).strip()
    if not gene_abundance_tsv:
        gene_abundance_tsv = str(Path(output_gtf).with_name("gene_abundances.tsv"))
    threads = int(kwargs.get("threads", 4) or 4)
    estimate_reference_only = bool(kwargs.get("estimate_reference_only", True))
    ballgown_dir = str(kwargs.get("ballgown_dir", "")).strip()

    stringtie_bin = which_with_pixi("stringtie") or "stringtie"
    samtools_bin = which_with_pixi("samtools") or "samtools"
    path_prefix = shell_path_prefix("stringtie", "samtools")

    output_dirs = {
        str(Path(output_gtf).expanduser().parent),
        str(Path(gene_abundance_tsv).expanduser().parent),
    }
    if ballgown_dir:
        output_dirs.add(str(Path(ballgown_dir).expanduser()))
    mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(path) for path in sorted(output_dirs))

    index_cmd = (
        f"if [ ! -f {shlex.quote(input_bam)}.bai ]; then "
        f"{shlex.quote(samtools_bin)} index {shlex.quote(input_bam)}; "
        "fi"
    )

    extra_flags: list[str] = []
    if estimate_reference_only:
        extra_flags.append("-e")
    if ballgown_dir:
        extra_flags.extend(["-b", shlex.quote(ballgown_dir)])

    extra_text = (" " + " ".join(extra_flags)) if extra_flags else ""
    return (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"{mkdir_cmd} && "
        f"{index_cmd} && "
        f"{shlex.quote(stringtie_bin)} {shlex.quote(input_bam)} "
        f"-G {shlex.quote(annotation_gtf)} "
        f"-o {shlex.quote(output_gtf)} "
        f"-A {shlex.quote(gene_abundance_tsv)} "
        f"-p {threads}{extra_text}"
    )
