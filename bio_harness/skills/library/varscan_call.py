from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi


def varscan_call(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    input_bam = str(kwargs.get("input_bam", "")).strip()
    output_vcf = str(kwargs.get("output_vcf", "")).strip()
    if not reference_fasta or not input_bam or not output_vcf:
        raise ValueError("Missing required parameter(s) for template: input_bam, output_vcf, reference_fasta")

    out_dir = str(Path(output_vcf).expanduser().parent)
    samtools_bin = which_with_pixi("samtools") or "samtools"
    varscan_bin = which_with_pixi("varscan") or which_with_pixi("VarScan") or "varscan"
    path_prefix = shell_path_prefix("samtools", "varscan", "java")
    min_var_freq = str(kwargs.get("min_var_freq", "")).strip()
    p_value = str(kwargs.get("p_value", "")).strip()
    extra_flags: list[str] = []
    if min_var_freq:
        extra_flags.append(f"--min-var-freq {shlex.quote(min_var_freq)}")
    if p_value:
        extra_flags.append(f"--p-value {shlex.quote(p_value)}")
    extra_text = (" " + " ".join(extra_flags)) if extra_flags else ""
    command = (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"if [ ! -f {shlex.quote(reference_fasta)}.fai ]; then {shlex.quote(samtools_bin)} faidx {shlex.quote(reference_fasta)}; fi && "
        f"if [ ! -f {shlex.quote(input_bam)}.bai ]; then {shlex.quote(samtools_bin)} index {shlex.quote(input_bam)}; fi && "
        f"{shlex.quote(samtools_bin)} mpileup -f {shlex.quote(reference_fasta)} {shlex.quote(input_bam)} "
        f"| {shlex.quote(varscan_bin)} mpileup2cns --output-vcf 1{extra_text} > {shlex.quote(output_vcf)}"
    )
    return command
