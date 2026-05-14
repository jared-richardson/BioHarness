from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi


def bowtie2_align(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    reads_1 = str(kwargs.get("reads_1", "")).strip()
    reads_2 = str(kwargs.get("reads_2", "")).strip()
    output_bam = str(kwargs.get("output_bam", "")).strip()
    index_base = str(kwargs.get("index_base", "")).strip()
    cache_index_base = str(kwargs.get("cache_index_base", "")).strip()
    if not reference_fasta or not reads_1 or not reads_2 or not output_bam:
        raise ValueError("Missing required parameter(s) for template: output_bam, reads_1, reads_2, reference_fasta")
    if not index_base:
        index_base = str(Path(output_bam).expanduser().parent / "bowtie2_index" / "genome")
    threads = int(kwargs.get("threads", 2) or 2)
    out_dir = str(Path(output_bam).expanduser().parent)
    builder = shlex.quote(which_with_pixi("bowtie2-build") or "bowtie2-build")
    aligner = shlex.quote(which_with_pixi("bowtie2") or "bowtie2")
    samtools_bin = shlex.quote(which_with_pixi("samtools") or "samtools")

    primary_index = cache_index_base if cache_index_base else index_base
    sync_local = ""
    if cache_index_base and cache_index_base != index_base:
        sync_local = (
            f"mkdir -p {shlex.quote(str(Path(index_base).expanduser().parent))}; "
            f"cp {shlex.quote(cache_index_base)}*.bt2* {shlex.quote(str(Path(index_base).expanduser().parent))}/ || true; "
        )

    script = (
        "set -euo pipefail; "
        f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(str(Path(primary_index).expanduser().parent))}; "
        f"if [ ! -s {shlex.quote(primary_index + '.1.bt2')} ] && [ ! -s {shlex.quote(primary_index + '.1.bt2l')} ]; then "
        f"{builder} {shlex.quote(reference_fasta)} {shlex.quote(primary_index)}; fi; "
        + sync_local
        + (
            f"{aligner} -x {shlex.quote(index_base if sync_local else primary_index)} "
            f"-1 {shlex.quote(reads_1)} -2 {shlex.quote(reads_2)} -p {int(threads)} "
            f"| {samtools_bin} sort -@ {int(threads)} -o {shlex.quote(output_bam)} -; "
            f"{samtools_bin} index {shlex.quote(output_bam)}"
        )
    )
    return f"bash -c {shlex.quote(script)}"
