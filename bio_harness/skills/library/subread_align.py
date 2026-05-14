from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi


def subread_align(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    index_base = str(kwargs.get("index_base", "")).strip()
    reads_1 = str(kwargs.get("reads_1", "")).strip()
    reads_2 = str(kwargs.get("reads_2", "")).strip()
    output_bam = str(kwargs.get("output_bam", "")).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    if not index_base or not reads_1 or not reads_2 or not output_bam or not reference_fasta:
        raise ValueError(
            "Missing required parameter(s) for template: index_base, output_bam, reads_1, reads_2, reference_fasta"
        )

    threads = int(kwargs.get("threads", 2) or 2)
    out_dir = Path(output_bam).expanduser().parent
    cache_index_base = str(kwargs.get("cache_index_base", "")).strip()
    source_index = cache_index_base or index_base
    builder = which_with_pixi("subread-buildindex") or "subread-buildindex"
    aligner = which_with_pixi("subjunc") or which_with_pixi("subread-align") or "subjunc"
    samtools_bin = which_with_pixi("samtools") or "samtools"
    temp_bam = str(Path(output_bam).with_suffix(".unsorted.bam"))
    align_args = (
        f"{shlex.quote(aligner)} -T {threads} -i {shlex.quote(index_base)} "
        f"-r {shlex.quote(reads_1)} -R {shlex.quote(reads_2)} -o {shlex.quote(temp_bam)}"
    )
    prep = (
        f"mkdir -p {shlex.quote(str(Path(source_index).expanduser().parent))}; "
        f"if [ ! -s {shlex.quote(source_index + '.00.b.array')} ]; then "
        f"{shlex.quote(builder)} -o {shlex.quote(source_index)} {shlex.quote(reference_fasta)}; fi; "
    )
    if source_index != index_base:
        prep += (
            f"mkdir -p {shlex.quote(str(Path(index_base).expanduser().parent))}; "
            f"cp {shlex.quote(source_index)}.* {shlex.quote(str(Path(index_base).expanduser().parent))}/ || true; "
        )
    command = (
        "set -euo pipefail; "
        f"mkdir -p {shlex.quote(str(out_dir))}; "
        + prep
        + align_args
        + (
            f"; {shlex.quote(samtools_bin)} sort -@ {threads} -o {shlex.quote(output_bam)} {shlex.quote(temp_bam)}; "
            f"{shlex.quote(samtools_bin)} index {shlex.quote(output_bam)}; "
            f"rm -f {shlex.quote(temp_bam)}"
        )
    )
    return f"bash -c {shlex.quote(command)}"
