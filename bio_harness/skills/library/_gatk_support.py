from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi


def resolve_gatk_bin() -> str:
    return shlex.quote(which_with_pixi("gatk") or "gatk")


def resolve_samtools_bin() -> str:
    return shlex.quote(which_with_pixi("samtools") or "samtools")


def canonical_dict_path(reference_fasta: str) -> str:
    return str(Path(reference_fasta).expanduser().with_suffix(".dict"))


def render_reference_and_bam_prereqs(reference_fasta: str, input_bams: list[str]) -> list[str]:
    prereqs: list[str] = []
    if reference_fasta:
        ref_q = shlex.quote(reference_fasta)
        fai_q = shlex.quote(f"{reference_fasta}.fai")
        dict_q = shlex.quote(canonical_dict_path(reference_fasta))
        samtools_bin = resolve_samtools_bin()
        gatk_bin = resolve_gatk_bin()
        prereqs.append(f"if [ ! -f {fai_q} ]; then {samtools_bin} faidx {ref_q}; fi")
        prereqs.append(f"if [ ! -f {dict_q} ]; then {gatk_bin} CreateSequenceDictionary -R {ref_q} -O {dict_q}; fi")

    samtools_bin = resolve_samtools_bin()
    for bam_path in input_bams:
        bam = str(bam_path or "").strip()
        if not bam:
            continue
        bam_q = shlex.quote(bam)
        bam_bai_q = shlex.quote(f"{bam}.bai")
        alt_bai_q = shlex.quote(str(Path(bam).expanduser().with_suffix(".bai")))
        prereqs.append(
            f"if [ ! -f {bam_bai_q} ] && [ ! -f {alt_bai_q} ]; then {samtools_bin} index {bam_q}; fi"
        )
    return prereqs
