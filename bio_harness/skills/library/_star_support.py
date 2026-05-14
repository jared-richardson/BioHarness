from __future__ import annotations

import hashlib
import os
import platform
import shlex
import shutil
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi


def is_gz_fastq(path: str) -> bool:
    token = str(path or "").strip().lower()
    return token.endswith(".fastq.gz") or token.endswith(".fq.gz")


def resolve_star_bin() -> str:
    override = str(os.getenv("BIO_HARNESS_STAR_BIN", "")).strip()
    shared = which_with_pixi("STAR") or which_with_pixi("star")
    if shared:
        return shared
    project_root = Path(__file__).resolve().parents[3]
    pixi_star = project_root / ".pixi" / "envs" / "default" / "bin" / "STAR"
    pixi_star_lower = project_root / ".pixi" / "envs" / "default" / "bin" / "star"
    candidates = [
        override,
        str(pixi_star),
        str(pixi_star_lower),
        shutil.which("STAR") or "",
        shutil.which("star") or "",
        "/usr/local/bin/STAR",
        "/usr/local/bin/star",
    ]
    for candidate in candidates:
        rendered = str(candidate or "").strip()
        if rendered and os.path.isfile(rendered) and os.access(rendered, os.X_OK):
            return rendered
    return "STAR"


def resolve_read_files_command() -> str:
    candidates = [
        shutil.which("gunzip") or "",
        shutil.which("gzcat") or "",
        shutil.which("zcat") or "",
    ]
    for candidate in candidates:
        rendered = str(candidate or "").strip()
        if rendered and os.path.isfile(rendered) and os.access(rendered, os.X_OK):
            return rendered if Path(rendered).name != "gunzip" else f"{rendered} -c"
    project_root = Path(__file__).resolve().parents[3]
    wrapper = project_root / "bio_harness" / "pipeline_scripts" / "star_readfiles_command.sh"
    if wrapper.is_file():
        return str(wrapper)
    return "zcat"


def resolve_gzip_reader_command() -> str:
    candidates = [
        (shutil.which("gunzip") or "", " -c"),
        (shutil.which("gzip") or "", " -cd"),
        (shutil.which("gzcat") or "", ""),
        (shutil.which("zcat") or "", ""),
    ]
    for candidate, suffix in candidates:
        rendered = str(candidate or "").strip()
        if rendered and os.path.isfile(rendered) and os.access(rendered, os.X_OK):
            return f"{rendered}{suffix}"
    return "/usr/bin/gunzip -c"


def should_stage_gz_fastq_inputs() -> bool:
    override = str(os.getenv("BIO_HARNESS_STAR_STAGE_GZ", "")).strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return platform.system() == "Darwin"


def default_star_read_cache_root(output_prefix: str) -> str:
    prefix_path = Path(str(output_prefix or "").strip()).expanduser()
    if not str(prefix_path):
        return "outputs/_cache/star_reads"
    return str(prefix_path.parent / "_cache" / "star_reads")


def _decompressed_read_name(path: str) -> str:
    source = str(path or "").strip()
    path_obj = Path(source)
    name = path_obj.name
    if name.endswith(".gz"):
        name = name[:-3]
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
    return f"{name}.{digest}"


def render_star_gz_input_prereqs(*, reads: list[str], staging_root: str) -> tuple[list[str], list[str]]:
    stage_root_token = str(staging_root or "").strip()
    if not stage_root_token or not reads:
        return ([], list(reads))
    decoder = resolve_gzip_reader_command()
    commands = [f"mkdir -p {shlex.quote(stage_root_token)}"]
    staged_reads: list[str] = []
    for read in reads:
        source_token = str(read or "").strip()
        if not source_token:
            staged_reads.append(source_token)
            continue
        staged_path = str(Path(stage_root_token).expanduser() / _decompressed_read_name(source_token))
        source_q = shlex.quote(source_token)
        staged_q = shlex.quote(staged_path)
        commands.append(
            f"if [ ! -s {staged_q} ] || [ {source_q} -nt {staged_q} ]; then {decoder} {source_q} > {staged_q}; fi"
        )
        staged_reads.append(staged_path)
    return commands, staged_reads


def render_star_index_prereqs(
    *,
    genome_dir: str,
    reference_fasta: str,
    annotation_gtf: str,
    threads: int,
    cache_root: str,
    sjdb_overhang: int,
) -> list[str]:
    genome_dir_token = str(genome_dir or "").strip()
    reference_token = str(reference_fasta or "").strip()
    gtf_token = str(annotation_gtf or "").strip()
    if not genome_dir_token or not reference_token or not gtf_token:
        return []
    project_root = Path(__file__).resolve().parents[3]
    build_script = project_root / "bio_harness" / "pipeline_scripts" / "build_star_index.sh"
    if not build_script.is_file():
        return []
    genome_dir_q = shlex.quote(genome_dir_token)
    reference_q = shlex.quote(reference_token)
    gtf_q = shlex.quote(gtf_token)
    cache_root_q = shlex.quote(cache_root)
    script_q = shlex.quote(str(build_script))
    genome_parameters_q = shlex.quote(str(Path(genome_dir_token).expanduser() / "genomeParameters.txt"))
    return [
        f"mkdir -p {genome_dir_q}",
        (
            f"if [ ! -s {genome_parameters_q} ]; then "
            f"bash {script_q} {genome_dir_q} {reference_q} {gtf_q} {int(threads)} {cache_root_q} {int(sjdb_overhang)}; "
            "fi"
        ),
    ]


def default_star_index_cache_root(genome_dir: str) -> str:
    genome_dir_path = Path(str(genome_dir or "").strip()).expanduser()
    if not str(genome_dir_path):
        return "outputs/_cache/star_indexes"
    return str(genome_dir_path.parent / "_cache" / "star_indexes")


def render_star_solo_reuse_guard(core_command: str, output_prefix: str) -> str:
    prefix = str(output_prefix or "").strip()
    if not prefix:
        return core_command
    prefix_parent = str(Path(prefix).expanduser().parent)
    script = (
        "set -euo pipefail; "
        f"mkdir -p {shlex.quote(prefix_parent)}; "
        f"prefix={shlex.quote(prefix)}; "
        'bam="${prefix}Aligned.out.bam"; '
        'matrix="${prefix}Solo.out/Gene/raw/matrix.mtx"; '
        'barcodes="${prefix}Solo.out/Gene/raw/barcodes.tsv"; '
        'features="${prefix}Solo.out/Gene/raw/features.tsv"; '
        "if [ -s \"$bam\" ] && [ -s \"$matrix\" ] && [ -s \"$barcodes\" ] && [ -s \"$features\" ]; then "
        "echo \"__STAR_SOLO_REUSE__:${bam}\"; "
        f"else {core_command}; fi"
    )
    return f"bash -c {shlex.quote(script)}"
