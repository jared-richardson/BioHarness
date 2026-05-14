from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi


def _normalize_minimap2_preset(raw_preset: str) -> str:
    """Return a canonical minimap2 preset from common planner/model aliases."""

    preset = str(raw_preset or "").strip().lower()
    if not preset:
        return "map-ont"
    alias_map = {
        "hifi": "map-hifi",
        "map-hifi": "map-hifi",
        "pacbio-hifi": "map-hifi",
        "pb-hifi": "map-hifi",
        "pacbio": "map-pb",
        "pb": "map-pb",
        "map-pb": "map-pb",
        "pacbio-raw": "map-pb",
        "pb-raw": "map-pb",
        "ont": "map-ont",
        "nanopore": "map-ont",
        "map-ont": "map-ont",
        "nano": "map-ont",
        "nano-raw": "map-ont",
    }
    return alias_map.get(preset, preset)


def minimap2_align(**kwargs) -> str:
    """Render a minimap2 alignment command for FASTQ or FASTA query inputs."""

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    reads = str(kwargs.get("reads", "")).strip()
    reads_1 = str(kwargs.get("reads_1", "")).strip()
    reads_2 = str(kwargs.get("reads_2", "")).strip()
    output_bam = str(kwargs.get("output_bam", "")).strip()
    if not reference_fasta or not output_bam:
        raise ValueError("Missing required parameter(s) for template: output_bam, reference_fasta")
    query_inputs: list[str] = []
    if reads:
        query_inputs.append(reads)
    if reads_1:
        query_inputs.append(reads_1)
    if reads_2:
        query_inputs.append(reads_2)
    if not query_inputs:
        raise ValueError("Missing required parameter(s) for template: reads")

    execution_cwd = str(kwargs.get("execution_cwd", "")).strip()
    cwd = Path(execution_cwd).expanduser().resolve(strict=False) if execution_cwd else Path.cwd().resolve()

    def _path_within_cwd(path_text: str) -> bool:
        raw = str(path_text or "").strip()
        if not raw:
            return False
        try:
            path = Path(raw).expanduser().resolve(strict=False)
            path.relative_to(cwd)
            return True
        except Exception:
            return False

    def _first_existing(candidates: list[Path]) -> str:
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return ""

    # Prefer artifacts produced earlier in the same run directory over stale
    # external paths from prior runs or generic workspace references.
    if not _path_within_cwd(reference_fasta):
        local_ref = _first_existing(
            [
                cwd / "output" / "viral_references_combined.fasta",
                cwd / "viral_references_combined.fasta",
            ]
        )
        if local_ref:
            reference_fasta = local_ref

    if not all(_path_within_cwd(path_text) for path_text in query_inputs if path_text):
        local_trimmed_r1 = _first_existing(
            [
                cwd / "output" / "trimmed_R1.fastq",
                cwd / "output" / "trimmed_R1.fastq.gz",
                cwd / "trimmed_R1.fastq",
                cwd / "trimmed_R1.fastq.gz",
            ]
        )
        local_trimmed_r2 = _first_existing(
            [
                cwd / "output" / "trimmed_R2.fastq",
                cwd / "output" / "trimmed_R2.fastq.gz",
                cwd / "trimmed_R2.fastq",
                cwd / "trimmed_R2.fastq.gz",
            ]
        )
        if local_trimmed_r1 and local_trimmed_r2 and len(query_inputs) >= 2:
            query_inputs = [local_trimmed_r1, local_trimmed_r2]

    def _resolve_query_path(path_text: str) -> str:
        raw = str(path_text or "").strip()
        if not raw:
            return raw
        raw_path = Path(raw).expanduser()
        if raw_path.exists():
            return str(raw_path)
        if not raw_path.is_absolute():
            fallback = (Path("inputs_readonly") / raw).expanduser()
            if fallback.exists():
                return str(fallback)
            roots = [Path("inputs_readonly").expanduser()]
            suffixes = (".fastq", ".fastq.gz", ".fq", ".fq.gz")
            candidates: list[Path] = []
            for root in roots:
                if not root.exists():
                    continue
                for p in root.rglob("*"):
                    try:
                        if not p.is_file():
                            continue
                    except OSError:
                        continue
                    if p.name.lower().endswith(suffixes):
                        candidates.append(p)
            if candidates:
                candidates = sorted(candidates, key=lambda p: (len(p.name), str(p)))
                preferred = [
                    p
                    for p in candidates
                    if any(tag in p.name.lower() for tag in ("nanopore", "ont", "pacbio", "long", "pb"))
                ]
                if preferred:
                    return str(preferred[0])
                return str(candidates[0])
        return raw

    query_inputs = [_resolve_query_path(p) for p in query_inputs]

    preset = _normalize_minimap2_preset(str(kwargs.get("preset", "map-ont")))
    threads = int(kwargs.get("threads", 2) or 2)
    cache_index_path = str(kwargs.get("cache_index_path", "")).strip()
    extra_args = str(kwargs.get("extra_args", "")).strip()
    out_dir = str(Path(output_bam).expanduser().parent)
    minimap2_bin = shlex.quote(which_with_pixi("minimap2") or "minimap2")
    samtools_bin = shlex.quote(which_with_pixi("samtools") or "samtools")

    ref_arg = reference_fasta
    pre = ""
    if cache_index_path:
        ref_arg = cache_index_path
        pre = (
            f"mkdir -p {shlex.quote(str(Path(cache_index_path).expanduser().parent))}; "
            f"if [ ! -s {shlex.quote(cache_index_path)} ]; then "
            f"{minimap2_bin} -d {shlex.quote(cache_index_path)} {shlex.quote(reference_fasta)}; fi; "
        )

    query_text = " ".join(shlex.quote(x) for x in query_inputs)
    output_path = Path(output_bam).expanduser()
    output_is_sam = output_path.suffix.lower() == ".sam"
    render_alignment = (
        f"{minimap2_bin} -ax {shlex.quote(preset)} -t {int(threads)} {extra_args} "
        f"{shlex.quote(ref_arg)} {query_text}"
    )
    if output_is_sam:
        pipeline = f"{render_alignment} > {shlex.quote(output_bam)}"
    else:
        pipeline = (
            f"{render_alignment} | "
            f"{samtools_bin} sort -@ {int(threads)} -o {shlex.quote(output_bam)} -; "
            f"{samtools_bin} index {shlex.quote(output_bam)}"
        )
    command = (
        "set -euo pipefail; "
        f"mkdir -p {shlex.quote(out_dir)}; "
        + pre
        + pipeline
    )
    return f"bash -c {shlex.quote(command)}"
