"""Strict-mode command-builder helpers for benchmark artifact binding."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Dict

from bio_harness.core.strict_artifact_binding_paths import (
    GermlineVariantArtifactPaths,
    RnaSeqDeArtifactPaths,
)

PYDESEQ2_WRAPPER = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "pydeseq2_wrapper.py"


def _copy_step_with_arguments(step_spec: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Copy a step specification and normalize its arguments mapping."""

    constrained = dict(step_spec if isinstance(step_spec, dict) else {})
    args = constrained.get("arguments", {})
    if not isinstance(args, dict):
        args = {}
    return constrained, dict(args)


def _build_germline_verify_command(paths: GermlineVariantArtifactPaths) -> str:
    """Build a lightweight output verification command for blind germline runs."""

    return "\n".join(
        [
            "python3 - <<'EOF'",
            "import os",
            f"final_vcf = {paths.final_vcf!r}",
            "if not os.path.exists(final_vcf):",
            "    raise SystemExit(f'Missing germline VCF: {final_vcf}')",
            "print(f'Validated germline VCF at {final_vcf}')",
            "EOF",
        ]
    )


def _build_rna_seq_de_alignment_command(paths: RnaSeqDeArtifactPaths) -> str:
    """Build the canonical multi-sample Subjunc alignment command."""

    sample_cmds: list[str] = []
    for bam_path in paths.bam_paths:
        bam_name = Path(bam_path).name
        sample = Path(bam_name).stem
        reads_1 = Path(paths.metadata_tsv).parent / f"{sample}_1.fastq"
        reads_2 = Path(paths.metadata_tsv).parent / f"{sample}_2.fastq"
        unsorted_bam = str(Path(bam_path).with_suffix(".unsorted.bam"))
        sample_cmds.append(
            " && ".join(
                [
                    (
                        f"subjunc -T 8 -i {shlex.quote(paths.index_base)} "
                        f"-r {shlex.quote(str(reads_1.resolve(strict=False)))} "
                        f"-R {shlex.quote(str(reads_2.resolve(strict=False)))} "
                        f"-o {shlex.quote(unsorted_bam)}"
                    ),
                    f"samtools sort -@ 8 -o {shlex.quote(bam_path)} {shlex.quote(unsorted_bam)}",
                    f"samtools index {shlex.quote(bam_path)}",
                    f"rm -f {shlex.quote(unsorted_bam)}",
                ]
            )
        )

    return " && ".join(
        [
            "set -euo pipefail",
            f"mkdir -p {shlex.quote(str(Path(paths.index_base).parent))}",
            f"mkdir -p {shlex.quote(paths.alignments_dir)}",
            (
                f"if [ ! -s {shlex.quote(paths.index_base + '.00.b.array')} ]; then "
                f"subread-buildindex -o {shlex.quote(paths.index_base)} {shlex.quote(paths.reference_fasta)}; fi"
            ),
            *sample_cmds,
        ]
    )


def _build_rna_seq_de_export_command(paths: RnaSeqDeArtifactPaths) -> str:
    """Build the final DESeq2 run plus CSV export command."""

    result_tsv = str((Path(paths.deseq_output_dir) / "deseq2_results.tsv").resolve(strict=False))
    return "\n".join(
        [
            "set -euo pipefail",
            f"mkdir -p {shlex.quote(paths.deseq_output_dir)} {shlex.quote(str(Path(paths.final_csv).parent))}",
            (
                f"python3 {shlex.quote(str(PYDESEQ2_WRAPPER.resolve(strict=False)))} "
                f"--counts {shlex.quote(paths.counts_path)} "
                f"--metadata {shlex.quote(paths.metadata_tsv)} "
                "--design '~ condition' "
                f"--contrast {shlex.quote(paths.contrast)} "
                f"--outdir {shlex.quote(paths.deseq_output_dir)}"
            ),
            "python3 - <<'EOF'",
            "import pandas as pd",
            f"result_tsv = {result_tsv!r}",
            f"final_csv = {paths.final_csv!r}",
            "df = pd.read_csv(result_tsv, sep='\\t')",
            "required = ['gene_id', 'log2FoldChange', 'pvalue', 'padj']",
            "missing = [col for col in required if col not in df.columns]",
            "if missing:",
            "    raise SystemExit(f'Missing DESeq2 result columns: {missing}')",
            "df[required].to_csv(final_csv, index=False)",
            "EOF",
        ]
    )
