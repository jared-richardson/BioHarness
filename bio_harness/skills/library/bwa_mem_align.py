from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.core.wrapper_contracts import normalize_bwa_read_group
from bio_harness.core.tool_env import which_with_pixi


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


def bwa_mem_align(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    reads_1 = str(kwargs.get("reads_1", "")).strip()
    reads_2 = str(kwargs.get("reads_2", "")).strip()
    output_bam = str(kwargs.get("output_bam", "")).strip()
    if not reference_fasta or not reads_1 or not reads_2 or not output_bam:
        raise ValueError("Missing required parameter(s) for template: output_bam, reads_1, reads_2, reference_fasta")
    threads = int(kwargs.get("threads", 2) or 2)
    cache_prefix = str(kwargs.get("cache_index_prefix", "")).strip()
    postprocess_mode = str(kwargs.get("postprocess_mode", "")).strip().lower()
    output_unmapped_bam = str(kwargs.get("output_unmapped_bam", "")).strip()
    read_group = str(kwargs.get("read_group", "")).strip()
    sample_name = str(kwargs.get("sample_name", "")).strip()
    out_dir = str(Path(output_bam).expanduser().parent)
    bwa_bin = which_with_pixi("bwa") or "bwa"
    samtools_bin = shlex.quote(which_with_pixi("samtools") or "samtools")
    quoted_bwa = shlex.quote(bwa_bin)
    if Path(str(bwa_bin)).name == "bwa-mem2":
        index_cmd = f"{quoted_bwa} index"
        mem_cmd = f"{quoted_bwa} mem"
        cache_suffix = ".0123"
    else:
        index_cmd = f"{quoted_bwa} index"
        mem_cmd = f"{quoted_bwa} mem"
        cache_suffix = ".bwt"

    if cache_prefix:
        idx_prefix = cache_prefix
        index_prep = (
            f"mkdir -p {shlex.quote(str(Path(cache_prefix).expanduser().parent))}; "
            f"if [ ! -s {shlex.quote(cache_prefix + cache_suffix)} ]; then "
            f"{index_cmd} -p {shlex.quote(cache_prefix)} {shlex.quote(reference_fasta)}; fi; "
        )
    else:
        idx_prefix = reference_fasta
        index_prep = (
            f"if [ ! -s {shlex.quote(reference_fasta + cache_suffix)} ]; then "
            f"{index_cmd} {shlex.quote(reference_fasta)}; fi; "
        )

    # Ensure reference FASTA index exists (needed for bwa and downstream tools)
    fai_check = (
        f"if [ ! -f {shlex.quote(reference_fasta + '.fai')} ]; then "
        f"{samtools_bin} faidx {shlex.quote(reference_fasta)}; fi; "
    )

    # Build read group flag — required by GATK and other downstream tools.
    # If not explicitly provided, derive a default from the reads filename.
    if not sample_name:
        stem = Path(reads_1).stem
        # Strip common suffixes: _1, _R1, .fastq, .fq, .gz
        for suffix in (".fastq", ".fq", ".gz"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
        for suffix in ("_1", "_R1", "_r1", ".1", ".R1"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        sample_name = stem if stem else "sample"
    read_group = normalize_bwa_read_group(read_group, sample_name=sample_name)
    rg_flag = f"-R {shlex.quote(read_group)} "

    if postprocess_mode == "fixmate_markdup_q20":
        sort_prefix = str(Path(output_bam).expanduser())
        if sort_prefix.endswith(".bam"):
            sort_prefix = sort_prefix[:-4]
        fixmate_bam = sort_prefix + ".fixmate.bam"
        sorted_bam = sort_prefix + ".sorted.bam"
        dedup_bam = sort_prefix + ".sorted.dedup.bam"
        namesorted_bam = sort_prefix + ".namesorted.bam"
        cleanup = [
            f"rm -f {shlex.quote(fixmate_bam)} {shlex.quote(sorted_bam)} {shlex.quote(namesorted_bam)}",
            f"{samtools_bin} index {shlex.quote(output_bam)}",
        ]
        if output_unmapped_bam:
            cleanup.insert(
                1,
                f"{samtools_bin} view -@ {int(threads)} -b -f 4 {shlex.quote(dedup_bam)} > {shlex.quote(output_unmapped_bam)}",
            )
        cleanup.append(f"rm -f {shlex.quote(dedup_bam)}")
        # Pipeline: align → name-sort → fixmate → coord-sort → markdup → q20 filter
        # Name-sorting explicitly via file (not pipe) to ensure fixmate receives
        # properly name-sorted input, avoiding failures with large datasets.
        script = (
            "set -euo pipefail; "
            f"mkdir -p {shlex.quote(out_dir)}; "
            + fai_check
            + index_prep
            + (
                f"{mem_cmd} -t {int(threads)} {rg_flag}{shlex.quote(idx_prefix)} {shlex.quote(reads_1)} {shlex.quote(reads_2)} "
                f"| {samtools_bin} view -@ {int(threads)} -bS - "
                f"| {samtools_bin} sort -@ {int(threads)} -n -o {shlex.quote(namesorted_bam)} -; "
                f"{samtools_bin} fixmate -m {shlex.quote(namesorted_bam)} {shlex.quote(fixmate_bam)}; "
                f"{samtools_bin} sort -@ {int(threads)} -O bam -o {shlex.quote(sorted_bam)} {shlex.quote(fixmate_bam)}; "
                f"{samtools_bin} markdup -@ {int(threads)} -r -S {shlex.quote(sorted_bam)} {shlex.quote(dedup_bam)}; "
                f"{samtools_bin} view -@ {int(threads)} -h -b -q 20 {shlex.quote(dedup_bam)} > {shlex.quote(output_bam)}; "
                + "; ".join(cleanup)
            )
        )
    else:
        script = (
            "set -euo pipefail; "
            f"mkdir -p {shlex.quote(out_dir)}; "
            + fai_check
            + index_prep
            + (
                f"{mem_cmd} -t {int(threads)} {rg_flag}{shlex.quote(idx_prefix)} {shlex.quote(reads_1)} {shlex.quote(reads_2)} "
                f"| {samtools_bin} sort -@ {int(threads)} -o {shlex.quote(output_bam)} -; "
                f"{samtools_bin} index {shlex.quote(output_bam)}"
            )
        )
    return f"bash -c {shlex.quote(script)}"
