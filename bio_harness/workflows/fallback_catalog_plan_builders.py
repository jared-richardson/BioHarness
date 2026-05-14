from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bio_harness.workflows.fallback_catalog_utils import (
    _choose_bam as _choose_bam,
    _choose_counts_and_metadata as _choose_counts_and_metadata,
    _choose_two_bams as _choose_two_bams,
    _group_marker_step as _group_marker_step,
    _is_fresh_alignment_mode as _is_fresh_alignment_mode,
    _pick_first_pair as _pick_first_pair,
    _pick_two_group_pairs as _pick_two_group_pairs,
)
from bio_harness.workflows.templates import build_splicing_execution_plan

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SCRIPT_DIR = PROJECT_ROOT / "bio_harness" / "pipeline_scripts"
DEFAULT_TEST_READS_PER_FASTQ = 1_000_000
DEFAULT_CACHE_ROOTS = {
    "star_index_cache_root": "outputs/_cache/star_indexes",
    "aligner_index_cache_root": "outputs/_cache/aligner_indexes",
}


def _fallback_out_base(pipeline_id: str, selected_dir: str = "") -> str:
    """Compute output base path — absolute when selected_dir is known."""
    relative = f"outputs/fallback/{pipeline_id}"
    if selected_dir:
        return str(Path(selected_dir) / "outputs" / "fallback" / pipeline_id)
    return relative


def _script_command(script_name: str, *args: str) -> str:
    script_path = PIPELINE_SCRIPT_DIR / script_name
    rendered_args = " ".join(shlex.quote(str(a)) for a in args)
    base = f"bash {shlex.quote(str(script_path))}"
    return f"{base} {rendered_args}".strip()


def _guarded_featurecounts_command(
    *,
    annotation_gtf: str,
    counts_path: str,
    control_bams_path: str,
    treatment_bams_path: str,
    out_base: str,
) -> str:
    """Build a guarded featureCounts command for two-group fallback plans."""

    quoted_control = shlex.quote(control_bams_path)
    quoted_treatment = shlex.quote(treatment_bams_path)
    return (
        f"mkdir -p {shlex.quote(out_base)} && "
        f"if [ ! -s {quoted_control} ] || [ ! -s {quoted_treatment} ]; then "
        f"echo __EMPTY_INPUT_FILE__:{quoted_control}:{quoted_treatment}; exit 1; "
        "fi && "
        f"featureCounts -T 2 -p --countReadPairs -a {shlex.quote(annotation_gtf)} "
        f"-o {shlex.quote(counts_path)} $(cat {quoted_control} {quoted_treatment})"
    )


def _build_two_group_star_count_plan(
    *,
    pipeline_id: str,
    data_root: str,
    control_tag: str,
    treatment_tag: str,
    annotation_gtf: str,
    reference_fasta: str,
    subset_mode: bool,
    test_reads_per_fastq: int,
    cache_paths: dict[str, str],
    differential_tool: str,
    differential_script_path: str,
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    manifest = f"{out_base}/fastq_manifest.txt"
    ctl_r1 = f"{out_base}/control_r1.txt"
    trt_r1 = f"{out_base}/treatment_r1.txt"
    ctl_r1_active = ctl_r1
    trt_r1_active = trt_r1
    ctl_r1_test = f"{out_base}/control_r1_test.txt"
    trt_r1_test = f"{out_base}/treatment_r1_test.txt"
    subset_dir = f"{out_base}/test_subset"
    star_idx = f"{out_base}/star_index"
    star_out = f"{out_base}/star"
    ctl_bams = f"{out_base}/control_bams.txt"
    trt_bams = f"{out_base}/treatment_bams.txt"
    counts_path = f"{out_base}/featurecounts_gene_counts.txt"
    metadata_path = f"{out_base}/coldata.tsv"
    de_out = f"{out_base}/{differential_tool}_out"

    steps: list[dict[str, Any]] = [
        {
            "tool_name": "bash_run",
            "arguments": {"command": _script_command("fastq_manifest.sh", data_root, manifest)},
        },
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    f"{_script_command('select_sample_r1.sh', manifest, control_tag, ctl_r1, 'CONTROL')} ; "
                    f"{_script_command('select_sample_r1.sh', manifest, treatment_tag, trt_r1, 'TREATMENT')}"
                )
            },
        },
    ]

    if subset_mode:
        steps.append(
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": _script_command(
                        "create_test_subset_from_r1_lists.sh",
                        ctl_r1,
                        trt_r1,
                        subset_dir,
                        ctl_r1_test,
                        trt_r1_test,
                        str(max(10_000, int(test_reads_per_fastq))),
                        "control",
                        "treatment",
                    )
                },
            }
        )
        ctl_r1_active = ctl_r1_test
        trt_r1_active = trt_r1_test

    steps.extend(
        [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": _script_command("check_required_tools.sh", "star", "samtools", "featureCounts", "Rscript")
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": _script_command(
                        "build_star_index.sh",
                        star_idx,
                        reference_fasta,
                        annotation_gtf,
                        "2",
                        cache_paths.get("star_index_cache_root", DEFAULT_CACHE_ROOTS["star_index_cache_root"]),
                        "149",
                    )
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": _script_command(
                        "align_r1_list_with_star.sh",
                        ctl_r1_active,
                        star_idx,
                        star_out,
                        ctl_bams,
                        "control",
                        "2",
                    )
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": _script_command(
                        "align_r1_list_with_star.sh",
                        trt_r1_active,
                        star_idx,
                        star_out,
                        trt_bams,
                        "treatment",
                        "2",
                    )
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": _guarded_featurecounts_command(
                        annotation_gtf=annotation_gtf,
                        counts_path=counts_path,
                        control_bams_path=ctl_bams,
                        treatment_bams_path=trt_bams,
                        out_base=out_base,
                    )
                },
            },
            {
                "tool_name": differential_tool,
                "arguments": {
                    "script_path": differential_script_path,
                    "counts_matrix": counts_path,
                    "metadata_table": metadata_path,
                    "design_formula": "~ condition",
                    "contrast": "condition_treatment_vs_control",
                    "output_dir": de_out,
                },
            },
        ]
    )

    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": "Deterministic two-group short-read expression fallback workflow with STAR alignment and differential testing.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {
            "subset_mode": bool(subset_mode),
            "test_reads_per_fastq": int(test_reads_per_fastq),
            "cache_paths": dict(cache_paths),
            "control_tag": str(control_tag),
            "treatment_tag": str(treatment_tag),
        },
    }


def _build_alignment_plan(
    *,
    pipeline_id: str,
    align_tool: str,
    reads_1: str,
    reads_2: str,
    reference_fasta: str,
    annotation_gtf: str,
    cache_paths: dict[str, str],
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    output_bam = f"{out_base}/alignment.sorted.bam"
    if align_tool == "star_2pass_align":
        steps = [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": _script_command(
                        "build_star_index.sh",
                        f"{out_base}/star_index",
                        reference_fasta,
                        annotation_gtf,
                        "2",
                        cache_paths.get("star_index_cache_root", DEFAULT_CACHE_ROOTS["star_index_cache_root"]),
                        "149",
                    )
                },
            },
            {
                "tool_name": "star_2pass_align",
                "arguments": {
                    "threads": 2,
                    "genome_dir": f"{out_base}/star_index",
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_prefix": f"{out_base}/star_",
                },
            },
        ]
    elif align_tool == "hisat2_align":
        steps = [
            {
                "tool_name": "hisat2_align",
                "arguments": {
                    "threads": 2,
                    "index_base": f"{out_base}/hisat2_index/genome",
                    "reference_fasta": reference_fasta,
                    "cache_index_base": (
                        f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/hisat2_genome"
                    ),
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_sam": f"{out_base}/hisat2.sam",
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {shlex.quote(out_base)} && "
                        f"samtools sort -@ 2 -o {shlex.quote(output_bam)} {shlex.quote(f'{out_base}/hisat2.sam')} && "
                        f"samtools index {shlex.quote(output_bam)}"
                    )
                },
            },
        ]
    elif align_tool == "bwa_mem_align":
        steps = [
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "threads": 2,
                    "reference_fasta": reference_fasta,
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_bam": output_bam,
                    "cache_index_prefix": (
                        f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/bwa_genome"
                    ),
                },
            }
        ]
    else:
        steps = [
            {
                "tool_name": "bowtie2_align",
                "arguments": {
                    "threads": 2,
                    "reference_fasta": reference_fasta,
                    "index_base": f"{out_base}/bowtie2_index/genome",
                    "cache_index_base": (
                        f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/bowtie2_genome"
                    ),
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_bam": output_bam,
                },
            }
        ]

    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": f"Deterministic fallback alignment plan using {align_tool}.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {
            "cache_paths": dict(cache_paths),
        },
    }


def _build_minimap2_alignment_plan(
    *,
    pipeline_id: str,
    reads: str,
    reference_fasta: str,
    preset: str,
    cache_paths: dict[str, str],
    existing_inputs: dict[str, list[str]],
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    output_bam = f"{out_base}/alignment.sorted.bam"
    cached_bam = _choose_bam(existing_inputs, output_bam)
    reuse_cached_bam = cached_bam != output_bam and Path(cached_bam).exists()
    if reuse_cached_bam:
        copy_cmd = (
            "set -euo pipefail; "
            f"mkdir -p {shlex.quote(str(Path(output_bam).parent))}; "
            f"cp {shlex.quote(cached_bam)} {shlex.quote(output_bam)}; "
            f"if [ -f {shlex.quote(cached_bam + '.bai')} ]; then "
            f"cp {shlex.quote(cached_bam + '.bai')} {shlex.quote(output_bam + '.bai')}; "
            f"elif [ -f {shlex.quote(cached_bam + '.bam.bai')} ]; then "
            f"cp {shlex.quote(cached_bam + '.bam.bai')} {shlex.quote(output_bam + '.bai')}; "
            "elif command -v samtools >/dev/null 2>&1; then "
            f"samtools index {shlex.quote(output_bam)}; "
            "fi"
        )
        plan = {
            "thought_process": "Deterministic long-read fallback reused existing BAM artifact (safe cache reuse).",
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {"command": copy_cmd},
                    "step_id": 1,
                }
            ],
            "canonical_template": pipeline_id,
            "execution_options": {"cache_paths": dict(cache_paths), "used_cached_bam": True},
        }
    else:
        plan = {
            "thought_process": f"Deterministic fallback long-read alignment plan using minimap2 preset {preset}.",
            "plan": [
                {
                    "tool_name": "minimap2_align",
                    "arguments": {
                        "threads": 2,
                        "preset": preset,
                        "reference_fasta": reference_fasta,
                        "reads": reads,
                        "output_bam": output_bam,
                        "cache_index_path": (
                            f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/{pipeline_id}.mmi"
                        ),
                    },
                    "step_id": 1,
                }
            ],
            "canonical_template": pipeline_id,
            "execution_options": {"cache_paths": dict(cache_paths)},
        }
    return plan


def _build_germline_variant_plan(
    *,
    pipeline_id: str,
    caller_tool: str,
    reference_fasta: str,
    reads_1: str,
    reads_2: str,
    existing_inputs: dict[str, list[str]],
    cache_paths: dict[str, str],
    prefer_fresh_alignment: bool = False,
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    default_aligned_bam = f"{out_base}/alignment.sorted.bam"
    has_fastq_pair = bool(str(reads_1).strip() and str(reads_2).strip())
    aligned_bam = (
        default_aligned_bam if (prefer_fresh_alignment and has_fastq_pair) else _choose_bam(existing_inputs, default_aligned_bam)
    )
    steps: list[dict[str, Any]] = []
    reuse_existing_bam = Path(aligned_bam).exists() and aligned_bam in (existing_inputs.get("bam", []) or [])
    if prefer_fresh_alignment and has_fastq_pair:
        reuse_existing_bam = False
    if not reuse_existing_bam:
        steps.append(
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "threads": 2,
                    "reference_fasta": reference_fasta,
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_bam": default_aligned_bam,
                    "cache_index_prefix": (
                        f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/bwa_genome"
                    ),
                },
            }
        )
        aligned_bam = default_aligned_bam
    if caller_tool == "gatk_haplotypecaller":
        steps.append(
            {
                "tool_name": "gatk_haplotypecaller",
                "arguments": {
                    "reference_fasta": reference_fasta,
                    "input_bam": aligned_bam,
                    "output_vcf": f"{out_base}/germline.vcf.gz",
                },
            }
        )
    elif caller_tool == "bcftools_call":
        steps.append(
            {
                "tool_name": "bcftools_call",
                "arguments": {
                    "reference_fasta": reference_fasta,
                    "input_bam": aligned_bam,
                    "output_vcf_gz": f"{out_base}/germline.vcf.gz",
                },
            }
        )
    elif caller_tool == "varscan_call":
        steps.append(
            {
                "tool_name": "varscan_call",
                "arguments": {
                    "reference_fasta": reference_fasta,
                    "input_bam": aligned_bam,
                    "output_vcf": f"{out_base}/germline.vcf",
                },
            }
        )
    else:
        steps.append(
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "reference_fasta": reference_fasta,
                    "input_bam": aligned_bam,
                    "output_vcf": f"{out_base}/germline.vcf",
                },
            }
        )
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": f"Deterministic fallback germline variant-calling workflow using {caller_tool}.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {"cache_paths": dict(cache_paths)},
    }


def _build_somatic_mutect2_plan(
    *,
    pipeline_id: str,
    reference_fasta: str,
    control_pair: tuple[str, str],
    treatment_pair: tuple[str, str],
    existing_inputs: dict[str, list[str]],
    cache_paths: dict[str, str],
    prefer_fresh_alignment: bool = False,
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    default_control_bam = f"{out_base}/control.sorted.bam"
    default_tumor_bam = f"{out_base}/treatment.sorted.bam"
    has_two_group_fastq = bool(
        str(control_pair[0]).strip()
        and str(control_pair[1]).strip()
        and str(treatment_pair[0]).strip()
        and str(treatment_pair[1]).strip()
    )
    if prefer_fresh_alignment and has_two_group_fastq:
        control_bam, tumor_bam = default_control_bam, default_tumor_bam
    else:
        control_bam, tumor_bam = _choose_two_bams(existing_inputs, (default_control_bam, default_tumor_bam))
    steps: list[dict[str, Any]] = [_group_marker_step(control_pair[0], treatment_pair[0])]

    reuse_existing_pair = Path(control_bam).exists() and Path(tumor_bam).exists()
    if prefer_fresh_alignment and has_two_group_fastq:
        reuse_existing_pair = False
    if not reuse_existing_pair:
        steps.extend(
            [
                {
                    "tool_name": "bwa_mem_align",
                    "arguments": {
                        "threads": 2,
                        "reference_fasta": reference_fasta,
                        "reads_1": control_pair[0],
                        "reads_2": control_pair[1],
                        "output_bam": default_control_bam,
                        "cache_index_prefix": (
                            f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/bwa_genome"
                        ),
                    },
                },
                {
                    "tool_name": "bwa_mem_align",
                    "arguments": {
                        "threads": 2,
                        "reference_fasta": reference_fasta,
                        "reads_1": treatment_pair[0],
                        "reads_2": treatment_pair[1],
                        "output_bam": default_tumor_bam,
                        "cache_index_prefix": (
                            f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/bwa_genome"
                        ),
                    },
                },
            ]
        )
        control_bam = default_control_bam
        tumor_bam = default_tumor_bam

    steps.append(
        {
            "tool_name": "gatk_mutect2_call",
            "arguments": {
                "reference_fasta": reference_fasta,
                "tumor_bam": tumor_bam,
                "tumor_sample": "treatment",
                "normal_bam": control_bam,
                "normal_sample": "control",
                "output_vcf": f"{out_base}/somatic.vcf.gz",
            },
        }
    )
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": "Deterministic somatic tumor-normal fallback workflow using Mutect2.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {"cache_paths": dict(cache_paths)},
    }


def _build_somatic_bcftools_degrade_plan(
    *,
    pipeline_id: str,
    reference_fasta: str,
    control_pair: tuple[str, str],
    treatment_pair: tuple[str, str],
    existing_inputs: dict[str, list[str]],
    cache_paths: dict[str, str],
    prefer_fresh_alignment: bool = False,
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    control_bam_default = f"{out_base}/control.sorted.bam"
    tumor_bam_default = f"{out_base}/treatment.sorted.bam"
    has_two_group_fastq = bool(
        str(control_pair[0]).strip()
        and str(control_pair[1]).strip()
        and str(treatment_pair[0]).strip()
        and str(treatment_pair[1]).strip()
    )
    if prefer_fresh_alignment and has_two_group_fastq:
        control_bam, tumor_bam = control_bam_default, tumor_bam_default
    else:
        control_bam, tumor_bam = _choose_two_bams(existing_inputs, (control_bam_default, tumor_bam_default))
    steps: list[dict[str, Any]] = [_group_marker_step(control_pair[0], treatment_pair[0])]

    reuse_existing_pair = Path(control_bam).exists() and Path(tumor_bam).exists()
    if prefer_fresh_alignment and has_two_group_fastq:
        reuse_existing_pair = False
    if not reuse_existing_pair:
        steps.extend(
            [
                {
                    "tool_name": "bwa_mem_align",
                    "arguments": {
                        "threads": 2,
                        "reference_fasta": reference_fasta,
                        "reads_1": control_pair[0],
                        "reads_2": control_pair[1],
                        "output_bam": control_bam_default,
                        "cache_index_prefix": (
                            f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/bwa_genome"
                        ),
                    },
                },
                {
                    "tool_name": "bwa_mem_align",
                    "arguments": {
                        "threads": 2,
                        "reference_fasta": reference_fasta,
                        "reads_1": treatment_pair[0],
                        "reads_2": treatment_pair[1],
                        "output_bam": tumor_bam_default,
                        "cache_index_prefix": (
                            f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/bwa_genome"
                        ),
                    },
                },
            ]
        )
        control_bam = control_bam_default
        tumor_bam = tumor_bam_default

    control_vcf = f"{out_base}/control.vcf.gz"
    tumor_vcf = f"{out_base}/tumor.vcf.gz"
    somatic_like_vcf = f"{out_base}/somatic_like.vcf.gz"
    steps.extend(
        [
            {
                "tool_name": "bcftools_call",
                "arguments": {
                    "reference_fasta": reference_fasta,
                    "input_bam": control_bam,
                    "output_vcf_gz": control_vcf,
                },
            },
            {
                "tool_name": "bcftools_call",
                "arguments": {
                    "reference_fasta": reference_fasta,
                    "input_bam": tumor_bam,
                    "output_vcf_gz": tumor_vcf,
                },
            },
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "set -euo pipefail; "
                        f"mkdir -p {shlex.quote(out_base)}; "
                        f"bcftools index -f {shlex.quote(control_vcf)}; "
                        f"bcftools index -f {shlex.quote(tumor_vcf)}; "
                        f"bcftools isec -C -w1 {shlex.quote(tumor_vcf)} {shlex.quote(control_vcf)} "
                        f"-Oz -o {shlex.quote(somatic_like_vcf)}; "
                        f"bcftools index -f {shlex.quote(somatic_like_vcf)}"
                    )
                },
            },
        ]
    )
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": "Deterministic somatic degrade fallback using bcftools tumor-vs-normal subtraction when GATK is unavailable.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {"cache_paths": dict(cache_paths), "degrade_mode": "bcftools_tn_subtraction"},
    }


def _build_dexseq_plan(
    *,
    pipeline_id: str,
    data_root: str,
    control_tag: str,
    treatment_tag: str,
    annotation_gtf: str,
    reference_fasta: str,
    subset_mode: bool,
    test_reads_per_fastq: int,
    cache_paths: dict[str, str],
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    counts_path = f"{out_base}/featurecounts_exon_counts.txt"
    metadata_path = f"{out_base}/coldata.tsv"
    de_out = f"{out_base}/dexseq_out"
    base = _build_two_group_star_count_plan(
        pipeline_id=pipeline_id,
        data_root=data_root,
        control_tag=control_tag,
        treatment_tag=treatment_tag,
        annotation_gtf=annotation_gtf,
        reference_fasta=reference_fasta,
        subset_mode=subset_mode,
        test_reads_per_fastq=test_reads_per_fastq,
        cache_paths=cache_paths,
        differential_tool="deseq2_run",
        differential_script_path=str(PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "deseq2_wrapper.R"),
    )
    steps = list(base.get("plan", []))
    if len(steps) >= 1:
        steps = steps[:-1]
    steps.append(
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    f"mkdir -p {shlex.quote(out_base)} && "
                    f"if [ ! -s {shlex.quote(f'{out_base}/control_bams.txt')} ] || "
                    f"[ ! -s {shlex.quote(f'{out_base}/treatment_bams.txt')} ]; then "
                    f"echo '__EMPTY_INPUT_FILE__:{out_base}/control_bams.txt:{out_base}/treatment_bams.txt'; exit 1; fi && "
                    f"featureCounts -T 2 -f -p --countReadPairs -a {shlex.quote(annotation_gtf)} "
                    f"-o {shlex.quote(counts_path)} "
                    f"$(cat {shlex.quote(f'{out_base}/control_bams.txt')} {shlex.quote(f'{out_base}/treatment_bams.txt')})"
                )
            },
        }
    )
    steps.append(
        {
            "tool_name": "dexseq_run",
            "arguments": {
                "script_path": str(PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "deseq2_wrapper.R"),
                "counts_matrix": counts_path,
                "metadata_table": metadata_path,
                "design_formula": "~ condition",
                "contrast": "condition_treatment_vs_control",
                "output_dir": de_out,
            },
        }
    )
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": "Deterministic fallback exon-level differential usage workflow (DEXSeq-compatible interface).",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {
            "subset_mode": bool(subset_mode),
            "test_reads_per_fastq": int(test_reads_per_fastq),
            "cache_paths": dict(cache_paths),
        },
    }


def _build_majiq_plan(
    *,
    pipeline_id: str,
    existing_inputs: dict[str, list[str]],
    control_pair: tuple[str, str],
    treatment_pair: tuple[str, str],
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    control_bam_default = f"{out_base}/control.sorted.bam"
    treatment_bam_default = f"{out_base}/treatment.sorted.bam"
    control_bam, treatment_bam = _choose_two_bams(existing_inputs, (control_bam_default, treatment_bam_default))
    steps: list[dict[str, Any]] = [
        _group_marker_step(control_pair[0], treatment_pair[0]),
        {
            "tool_name": "bash_run",
            "arguments": {
                "command": (
                    f"mkdir -p {shlex.quote(out_base)} && "
                    f"majiq build -j 2 -c {shlex.quote(f'{out_base}/majiq_config.txt')} -o {shlex.quote(out_base)} && "
                    f"majiq deltapsi -j 2 -grp1 {shlex.quote(control_bam)} -grp2 {shlex.quote(treatment_bam)} "
                    f"-n control_vs_treatment -o {shlex.quote(out_base)}"
                )
            },
        },
    ]
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": "Deterministic fallback MAJIQ splice-variation plan with explicit control/treatment evidence markers.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {},
    }


def _build_protein_plan(
    *,
    pipeline_id: str,
    tool_name: str,
    protein_fasta: str,
    reference_db: str,
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    steps: list[dict[str, Any]] = []
    protein_path = str(protein_fasta or "").strip()
    if not protein_path:
        protein_path = f"{out_base}/fallback_query.faa"
    if not Path(protein_path).exists():
        seed_cmd = (
            f"mkdir -p {shlex.quote(str(Path(protein_path).parent))} ; "
            f"if [ ! -s {shlex.quote(protein_path)} ]; then "
            f"printf '>fallback_protein\\nMSTNPKPQRK\\n' > {shlex.quote(protein_path)} ; "
            "fi"
        )
        steps.append({"tool_name": "bash_run", "arguments": {"command": seed_cmd}})

    if tool_name == "blastp_search":
        args = {
            "query_fasta": protein_path,
            "database": reference_db or "swissprot",
            "output_tsv": f"{out_base}/blastp.tsv",
            "threads": 2,
            "evalue": "1e-5",
            "outfmt": "6 qseqid sseqid pident length evalue bitscore",
        }
    elif tool_name == "hmmscan_search":
        args = {
            "query_fasta": protein_path,
            "hmm_db": reference_db or "Pfam-A.hmm",
            "output_tbl": f"{out_base}/hmmscan.tbl",
            "output_txt": f"{out_base}/hmmscan.txt",
            "threads": 2,
        }
    else:
        args = {
            "input_fasta": protein_path,
            "sample_prefix": "fallback",
            "output_dir": f"{out_base}/prokka",
        }
    steps.append({"tool_name": tool_name, "arguments": args})
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": f"Deterministic fallback protein analysis workflow using {tool_name}.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {},
    }


def _build_uncommon_pair_plan(
    *,
    pipeline_id: str,
    tool_name: str,
    first_pair: tuple[str, str],
    reference_fasta: str,
    selected_dir: str,
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    reads_1, reads_2 = first_pair
    steps: list[dict[str, Any]] = []

    if tool_name == "methylation_bismark_style":
        genome_folder = str(Path(reference_fasta).parent) if reference_fasta else str(Path(selected_dir) / "inputs_readonly")
        steps.append(
            {
                "tool_name": tool_name,
                "arguments": {
                    "genome_folder": genome_folder,
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_dir": out_base,
                    "output_report": f"{out_base}/methylation_summary.tsv",
                    "threads": 2,
                    "sample_name": "fallback_sample",
                },
            }
        )
    elif tool_name == "metagenomics_kraken2_bracken_style":
        steps.append(
            {
                "tool_name": tool_name,
                "arguments": {
                    "database": str(Path(selected_dir) / "inputs_readonly" / "kraken_db"),
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_dir": out_base,
                    "output_report": f"{out_base}/bracken_abundance.tsv",
                    "threads": 2,
                    "read_len": 150,
                },
            }
        )
    elif tool_name == "fusion_star_fusion_style":
        steps.append(
            {
                "tool_name": tool_name,
                "arguments": {
                    "genome_lib_dir": str(Path(selected_dir) / "inputs_readonly" / "ctat_genome_lib_build_dir"),
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_dir": out_base,
                    "output_report": f"{out_base}/fusions.tsv",
                    "threads": 2,
                },
            }
        )
    elif tool_name == "immune_repertoire_mixcr_style":
        steps.append(
            {
                "tool_name": tool_name,
                "arguments": {
                    "reads_1": reads_1,
                    "reads_2": reads_2,
                    "output_dir": out_base,
                    "output_report": f"{out_base}/clones.tsv",
                    "threads": 2,
                },
            }
        )

    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": f"Deterministic uncommon fallback workflow using {tool_name}.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {},
    }


def _build_cnv_plan(
    *,
    pipeline_id: str,
    first_pair: tuple[str, str],
    reference_fasta: str,
    existing_inputs: dict[str, list[str]],
    cache_paths: dict[str, str],
    prefer_fresh_alignment: bool = False,
    selected_dir: str = "",
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    default_input_bam = f"{out_base}/alignment.sorted.bam"
    has_fastq_pair = bool(str(first_pair[0]).strip() and str(first_pair[1]).strip())
    input_bam = default_input_bam if (prefer_fresh_alignment and has_fastq_pair) else _choose_bam(existing_inputs, default_input_bam)
    steps: list[dict[str, Any]] = []
    reuse_existing_bam = Path(input_bam).exists() and input_bam in (existing_inputs.get("bam", []) or [])
    if prefer_fresh_alignment and has_fastq_pair:
        reuse_existing_bam = False
    if not reuse_existing_bam:
        steps.append(
            {
                "tool_name": "bwa_mem_align",
                "arguments": {
                    "threads": 2,
                    "reference_fasta": reference_fasta,
                    "reads_1": first_pair[0],
                    "reads_2": first_pair[1],
                    "output_bam": default_input_bam,
                    "cache_index_prefix": (
                        f"{cache_paths.get('aligner_index_cache_root', DEFAULT_CACHE_ROOTS['aligner_index_cache_root'])}/bwa_genome"
                    ),
                },
            }
        )
        input_bam = default_input_bam
    steps.append(
        {
            "tool_name": "cnv_cnvkit_style",
            "arguments": {
                "input_bam": input_bam,
                "reference_fasta": reference_fasta,
                "output_dir": out_base,
                "output_report": f"{out_base}/cnv_summary.tsv",
                "threads": 2,
            },
        }
    )
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": "Deterministic uncommon CNV fallback workflow using CNVkit-style wrapper.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {"cache_paths": dict(cache_paths)},
    }


def _build_phylogenetics_plan(
    *,
    pipeline_id: str,
    selected_dir: str,
    existing_inputs: dict[str, list[str]],
) -> dict[str, Any]:
    out_base = _fallback_out_base(pipeline_id, selected_dir)
    proteins = existing_inputs.get("protein_fasta", []) if isinstance(existing_inputs, dict) else []
    alignment_fasta = proteins[0] if proteins else str(Path(selected_dir) / "workspace_proteins.faa")

    steps: list[dict[str, Any]] = []
    if not Path(alignment_fasta).exists():
        steps.append(
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        f"mkdir -p {shlex.quote(str(Path(alignment_fasta).parent))} ; "
                        f"if [ ! -s {shlex.quote(alignment_fasta)} ]; then "
                        f"printf '>taxonA\\nACGTACGT\\n>taxonB\\nACGTTCGT\\n' > {shlex.quote(alignment_fasta)} ; "
                        "fi"
                    )
                },
            }
        )
    steps.append(
        {
            "tool_name": "phylogenetics_iqtree_style",
            "arguments": {
                "alignment_fasta": alignment_fasta,
                "output_dir": out_base,
                "output_prefix": f"{out_base}/iqtree",
                "output_tree": f"{out_base}/tree.nwk",
                "model": "MFP",
                "threads": 2,
                "seed": 42,
            },
        }
    )
    for idx, step in enumerate(steps, start=1):
        step["step_id"] = idx
    return {
        "thought_process": "Deterministic uncommon phylogenetics fallback workflow using IQ-TREE-style wrapper.",
        "plan": steps,
        "canonical_template": pipeline_id,
        "execution_options": {},
    }


def _build_fallback_template_plan(
    template: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any] | None:
    control_tag = str(ctx.get("control_tag", "") or "").strip()
    treatment_tag = str(ctx.get("treatment_tag", "") or "").strip()
    subset_mode = bool(ctx.get("subset_mode", True))
    test_reads = int(ctx.get("test_reads_per_fastq", DEFAULT_TEST_READS_PER_FASTQ))
    data_root = str(ctx.get("data_root", ""))
    selected_dir = str(ctx.get("selected_dir", ""))
    cache_paths = dict(DEFAULT_CACHE_ROOTS)
    cache_paths.update(dict(ctx.get("cache_paths", {})))
    provenance_mode = str(ctx.get("provenance_mode", "standard") or "standard").strip().lower()
    prefer_fresh_alignment = _is_fresh_alignment_mode(provenance_mode)
    refs = dict(ctx.get("references", {}))
    annotation_gtf = str(refs.get("annotation_gtf", "")).strip()
    reference_fasta = str(refs.get("reference_fasta", "")).strip()
    existing_inputs = dict(ctx.get("existing_inputs", {}))
    pair_map = dict(ctx.get("pair_map", {}))
    control_pair, treatment_pair = _pick_two_group_pairs(pair_map, control_tag, treatment_tag)
    first_pair = _pick_first_pair(pair_map)
    long_fastqs = list(ctx.get("long_fastqs", []))
    first_long = long_fastqs[0] if long_fastqs else (first_pair[0] or "")
    protein_fasta = ""
    proteins = existing_inputs.get("protein_fasta", [])
    if proteins:
        protein_fasta = proteins[0]
    if not protein_fasta:
        protein_fasta = str(Path(selected_dir) / "workspace_proteins.faa")

    template_id = str(template.get("pipeline_id", ""))
    if template_id == "sr_rna_splicing_rmats_star":
        return build_splicing_execution_plan(
            data_root=data_root,
            gtf_path=annotation_gtf,
            fasta_path=reference_fasta,
            control_tag=control_tag,
            treatment_tag=treatment_tag,
            use_test_subset=subset_mode,
            test_reads_per_fastq=test_reads,
        )
    if template_id in {"sr_rna_align_star_2pass", "sr_rna_align_hisat2", "sr_dna_align_bwa_mem", "sr_dna_align_bowtie2"}:
        align_tool = {
            "sr_rna_align_star_2pass": "star_2pass_align",
            "sr_rna_align_hisat2": "hisat2_align",
            "sr_dna_align_bwa_mem": "bwa_mem_align",
            "sr_dna_align_bowtie2": "bowtie2_align",
        }[template_id]
        return _build_alignment_plan(
            pipeline_id=template_id,
            align_tool=align_tool,
            reads_1=first_pair[0],
            reads_2=first_pair[1],
            reference_fasta=reference_fasta,
            annotation_gtf=annotation_gtf,
            cache_paths=cache_paths,
            selected_dir=selected_dir,
        )
    if template_id in {"lr_rna_align_minimap2_splice", "lr_dna_align_minimap2"}:
        preset = "splice" if template_id == "lr_rna_align_minimap2_splice" else "map-ont"
        return _build_minimap2_alignment_plan(
            pipeline_id=template_id,
            reads=first_long,
            reference_fasta=reference_fasta,
            preset=preset,
            cache_paths=cache_paths,
            existing_inputs=existing_inputs,
            selected_dir=selected_dir,
        )
    if template_id in {
        "germline_variant_gatk_haplotypecaller",
        "germline_variant_bcftools",
        "germline_variant_varscan",
        "germline_variant_freebayes",
    }:
        caller = {
            "germline_variant_gatk_haplotypecaller": "gatk_haplotypecaller",
            "germline_variant_bcftools": "bcftools_call",
            "germline_variant_varscan": "varscan_call",
            "germline_variant_freebayes": "freebayes_call",
        }[template_id]
        return _build_germline_variant_plan(
            pipeline_id=template_id,
            caller_tool=caller,
            reference_fasta=reference_fasta,
            reads_1=first_pair[0],
            reads_2=first_pair[1],
            existing_inputs=existing_inputs,
            cache_paths=cache_paths,
            prefer_fresh_alignment=prefer_fresh_alignment,
            selected_dir=selected_dir,
        )
    if template_id == "somatic_variant_mutect2_tn":
        if not (control_pair[0] and treatment_pair[0]):
            return None
        return _build_somatic_mutect2_plan(
            pipeline_id=template_id,
            reference_fasta=reference_fasta,
            control_pair=control_pair,
            treatment_pair=treatment_pair,
            existing_inputs=existing_inputs,
            cache_paths=cache_paths,
            prefer_fresh_alignment=prefer_fresh_alignment,
            selected_dir=selected_dir,
        )
    if template_id == "somatic_variant_bcftools_tn_degrade":
        if not (control_pair[0] and treatment_pair[0]):
            return None
        return _build_somatic_bcftools_degrade_plan(
            pipeline_id=template_id,
            reference_fasta=reference_fasta,
            control_pair=control_pair,
            treatment_pair=treatment_pair,
            existing_inputs=existing_inputs,
            cache_paths=cache_paths,
            prefer_fresh_alignment=prefer_fresh_alignment,
            selected_dir=selected_dir,
        )
    if template_id in {
        "differential_expression_deseq2",
        "differential_expression_edger",
        "differential_expression_limma_voom",
    }:
        if not (control_pair[0] and treatment_pair[0]):
            return None
        tool = {
            "differential_expression_deseq2": "deseq2_run",
            "differential_expression_edger": "edger_run",
            "differential_expression_limma_voom": "limma_voom_run",
        }[template_id]
        script_path = str(PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "deseq2_wrapper.R")
        return _build_two_group_star_count_plan(
            pipeline_id=template_id,
            data_root=data_root,
            control_tag=control_tag,
            treatment_tag=treatment_tag,
            annotation_gtf=annotation_gtf,
            reference_fasta=reference_fasta,
            subset_mode=subset_mode,
            test_reads_per_fastq=test_reads,
            cache_paths=cache_paths,
            differential_tool=tool,
            differential_script_path=script_path,
            selected_dir=selected_dir,
        )
    if template_id == "differential_expression_deseq2_from_counts":
        out_base = _fallback_out_base(template_id, selected_dir)
        counts, metadata = _choose_counts_and_metadata(existing_inputs, out_base)
        if not counts or not metadata:
            return None
        return {
            "thought_process": "Deterministic DESeq2 fallback from existing count matrix + metadata inputs.",
            "plan": [
                {
                    "tool_name": "deseq2_run",
                    "arguments": {
                        "script_path": str(PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "deseq2_wrapper.R"),
                        "counts_matrix": counts,
                        "metadata_table": metadata,
                        "design_formula": "~ condition",
                        "contrast": "condition_treatment_vs_control",
                        "output_dir": f"{out_base}/deseq2_out",
                    },
                    "step_id": 1,
                }
            ],
            "canonical_template": template_id,
            "execution_options": {},
        }
    if template_id == "alt_splicing_dexseq":
        return _build_dexseq_plan(
            pipeline_id=template_id,
            data_root=data_root,
            control_tag=control_tag,
            treatment_tag=treatment_tag,
            annotation_gtf=annotation_gtf,
            reference_fasta=reference_fasta,
            subset_mode=subset_mode,
            test_reads_per_fastq=test_reads,
            cache_paths=cache_paths,
            selected_dir=selected_dir,
        )
    if template_id == "alt_splicing_majiq":
        return _build_majiq_plan(
            pipeline_id=template_id,
            existing_inputs=existing_inputs,
            control_pair=control_pair,
            treatment_pair=treatment_pair,
            selected_dir=selected_dir,
        )
    if template_id == "protein_blastp_homology":
        return _build_protein_plan(
            pipeline_id=template_id,
            tool_name="blastp_search",
            protein_fasta=protein_fasta,
            reference_db=str(ctx.get("protein_db", "swissprot")),
            selected_dir=selected_dir,
        )
    if template_id == "protein_hmmscan_domains":
        return _build_protein_plan(
            pipeline_id=template_id,
            tool_name="hmmscan_search",
            protein_fasta=protein_fasta,
            reference_db=str(ctx.get("hmm_db", "Pfam-A.hmm")),
            selected_dir=selected_dir,
        )
    if template_id == "protein_prokka_annotation":
        return _build_protein_plan(
            pipeline_id=template_id,
            tool_name="prokka_annotate",
            protein_fasta=protein_fasta,
            reference_db="",
            selected_dir=selected_dir,
        )
    if template_id in {
        "methylation_bismark_style",
        "metagenomics_kraken2_bracken_style",
        "fusion_star_fusion_style",
        "immune_repertoire_mixcr_style",
    }:
        return _build_uncommon_pair_plan(
            pipeline_id=template_id,
            tool_name=template_id,
            first_pair=first_pair,
            reference_fasta=reference_fasta,
            selected_dir=selected_dir,
        )
    if template_id == "cnv_cnvkit_style":
        return _build_cnv_plan(
            pipeline_id=template_id,
            first_pair=first_pair,
            reference_fasta=reference_fasta,
            existing_inputs=existing_inputs,
            cache_paths=cache_paths,
            prefer_fresh_alignment=prefer_fresh_alignment,
            selected_dir=selected_dir,
        )
    if template_id == "phylogenetics_iqtree_style":
        return _build_phylogenetics_plan(
            pipeline_id=template_id,
            selected_dir=selected_dir,
            existing_inputs=existing_inputs,
        )
    if template_id == "variant_annotation_vep":
        out_base = _fallback_out_base(template_id, selected_dir)
        vcf = (existing_inputs.get("vcf", [""]) or [""])[0]
        if not vcf:
            vcf = f"{out_base}/input.vcf"
        return {
            "thought_process": "Deterministic fallback variant-effect annotation workflow using VEP.",
            "plan": [
                {
                    "tool_name": "vep_annotate",
                    "arguments": {
                        "assembly": "GRCh38",
                        "input_vcf": vcf,
                        "output_vcf": f"{out_base}/annotated.vcf",
                    },
                    "step_id": 1,
                }
            ],
            "canonical_template": template_id,
            "execution_options": {},
        }
    if template_id == "variant_annotation_snpeff":
        out_base = _fallback_out_base(template_id, selected_dir)
        vcf = (existing_inputs.get("vcf", [""]) or [""])[0]
        if not vcf:
            vcf = f"{out_base}/input.vcf"
        return {
            "thought_process": "Deterministic fallback variant-effect annotation workflow using snpEff.",
            "plan": [
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {
                        "genome_db": "GRCh38.99",
                        "input_vcf": vcf,
                        "output_vcf": f"{out_base}/annotated.vcf",
                    },
                    "step_id": 1,
                }
            ],
            "canonical_template": template_id,
            "execution_options": {},
        }

    return {"thought_process": "Fallback template unresolved.", "plan": []}


__all__ = [
    "DEFAULT_CACHE_ROOTS",
    "DEFAULT_TEST_READS_PER_FASTQ",
    "_build_fallback_template_plan",
]
