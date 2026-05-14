from __future__ import annotations

import shlex
import string

from bio_harness.skills.library._star_support import (
    default_star_index_cache_root,
    default_star_read_cache_root,
    is_gz_fastq,
    render_star_gz_input_prereqs,
    render_star_index_prereqs,
    resolve_read_files_command,
    resolve_star_bin,
    should_stage_gz_fastq_inputs,
)


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


def _with_reuse_guard(core_command: str, output_prefix: str, quant_mode: str = "") -> str:
    prefix = str(output_prefix or "").strip()
    if not prefix:
        return core_command
    require_gene_counts = "genecounts" in str(quant_mode or "").replace(" ", "").lower()
    script = (
        "set -euo pipefail; "
        f"prefix={shlex.quote(prefix)}; "
        'bam="${prefix}Aligned.out.bam"; '
        'log="${prefix}Log.final.out"; '
        + ('counts="${prefix}ReadsPerGene.out.tab"; ' if require_gene_counts else "")
        + "if [ -s \"$bam\" ] && [ -s \"$log\" ] "
        + ('&& [ -s \"$counts\" ] ' if require_gene_counts else "")
        + "&& awk -F'|' '/Number of input reads/ {gsub(/[ \\t]/,\"\",$2); if (($2+0)>0) ok=1} END {exit ok?0:1}' \"$log\"; then "
        "echo \"__STAR_ALIGN_REUSE__:${bam}\"; "
        f"else {core_command}; fi"
    )
    return f"bash -c {shlex.quote(script)}"


def star_align(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    kwargs = dict(kwargs)
    kwargs.setdefault("star_bin", resolve_star_bin())
    read_prereqs: list[str] = []
    reads_are_gz = is_gz_fastq(str(kwargs.get("reads_1", ""))) and is_gz_fastq(str(kwargs.get("reads_2", "")))
    if reads_are_gz and should_stage_gz_fastq_inputs():
        stage_root = str(
            kwargs.get("star_read_cache_root", "") or default_star_read_cache_root(str(kwargs.get("output_prefix", "")))
        ).strip()
        stage_prereqs, staged_reads = render_star_gz_input_prereqs(
            reads=[str(kwargs.get("reads_1", "")), str(kwargs.get("reads_2", ""))],
            staging_root=stage_root,
        )
        read_prereqs.extend(stage_prereqs)
        kwargs["reads_1"], kwargs["reads_2"] = staged_reads
    command_parts = [
        "{star_bin}",
        "--runThreadN {threads}",
        "--genomeDir {genome_dir}",
        "--readFilesIn {reads_1} {reads_2}",
    ]
    if reads_are_gz and not should_stage_gz_fastq_inputs():
        command_parts.append(f"--readFilesCommand {resolve_read_files_command()}")
    annotation_gtf = str(kwargs.get("annotation_gtf", "")).strip()
    if annotation_gtf:
        command_parts.append("--sjdbGTFfile {annotation_gtf}")
    out_sam_type = str(kwargs.get("outSAMtype", "")).strip() or "BAM Unsorted"
    command_parts.append(f"--outSAMtype {out_sam_type}")
    quant_mode = str(kwargs.get("quant_mode", "")).strip()
    if quant_mode:
        command_parts.append(f"--quantMode {quant_mode}")
    command_parts.append("--outFileNamePrefix {output_prefix}")
    template = " ".join(command_parts)
    core = _render_template(template, kwargs)

    genome_dir = str(kwargs.get("genome_dir", "")).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    threads = int(kwargs.get("threads", 2) or 2)
    cache_root = str(
        kwargs.get("star_index_cache_root", "")
        or kwargs.get("cache_root", "")
        or default_star_index_cache_root(genome_dir)
    ).strip()
    sjdb_overhang = int(kwargs.get("sjdb_overhang", 149) or 149)
    index_prereqs = render_star_index_prereqs(
        genome_dir=genome_dir,
        reference_fasta=reference_fasta,
        annotation_gtf=annotation_gtf,
        threads=threads,
        cache_root=cache_root,
        sjdb_overhang=sjdb_overhang,
    )
    prereqs = index_prereqs + read_prereqs
    final_core = " && ".join(prereqs + [core]) if prereqs else core
    return _with_reuse_guard(final_core, str(kwargs.get("output_prefix", "")), quant_mode=quant_mode)
