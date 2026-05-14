"""Strict benchmark artifact-path dataclasses and deterministic path builders."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from bio_harness.core.strict_artifact_binding_benchmark_helpers import _discover_primary_fastq_pair


@dataclass(frozen=True)
class CysticFibrosisArtifactPaths:
    """Canonical artifact paths for strict cystic-fibrosis runs."""

    input_vcf: str
    family_description: str
    clinvar_vcf: str
    selected_dir: str
    intermediate_dir: str
    filtered_csv: str
    clinvar_csv: str
    final_csv: str


@dataclass(frozen=True)
class RnaSeqDeArtifactPaths:
    """Canonical artifact paths for strict RNA-seq differential-expression runs."""

    reference_fasta: str
    annotation_gff: str
    metadata_tsv: str
    index_base: str
    alignments_dir: str
    bam_paths: tuple[str, ...]
    counts_path: str
    deseq_output_dir: str
    final_csv: str
    contrast: str


@dataclass(frozen=True)
class GermlineVariantArtifactPaths:
    """Canonical artifact paths for strict germline-variant runs."""

    reference_fasta: str
    reads_1: str
    reads_2: str
    aligned_bam: str
    final_vcf: str


@dataclass(frozen=True)
class SingleCellArtifactPaths:
    """Canonical artifact paths for strict single-cell runs."""

    r1_fastq: str
    r2_fastq: str
    whitelist: str
    reference_fasta: str
    annotation_gtf: str
    output_dir: str


def _build_cystic_fibrosis_paths(
    *,
    selected_dir: Path | None,
    data_root: Path | None,
) -> CysticFibrosisArtifactPaths:
    """Build the canonical strict scaffold for cystic-fibrosis artifacts."""

    selected_root = selected_dir.resolve(strict=False) if selected_dir is not None else Path(".")
    intermediate_root = selected_root / "intermediate"
    if data_root is not None:
        input_vcf = str((data_root / "ex1.eff.vcf").resolve(strict=False))
        family_description = str((data_root / "family_description.txt").resolve(strict=False))
        clinvar_vcf = str((data_root.parent / "references" / "clinvar_20250521.vcf.gz").resolve(strict=False))
    else:
        input_vcf = ""
        family_description = ""
        clinvar_vcf = ""
    return CysticFibrosisArtifactPaths(
        input_vcf=input_vcf,
        family_description=family_description,
        clinvar_vcf=clinvar_vcf,
        selected_dir=str(selected_root),
        intermediate_dir=str(intermediate_root),
        filtered_csv=str((intermediate_root / "filtered_variants.csv").resolve(strict=False)),
        clinvar_csv=str((intermediate_root / "clinvar_annotated_variants.csv").resolve(strict=False)),
        final_csv=str((selected_root / "final" / "cf_variants.csv").resolve(strict=False)),
    )


def _read_rna_seq_sample_rows(metadata_tsv: Path) -> list[tuple[str, str]]:
    """Read sample-condition rows from the benchmark metadata table."""

    rows: list[tuple[str, str]] = []
    if not metadata_tsv.exists():
        return rows
    with metadata_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            sample = _normalize_rna_seq_sample_id(
                str((row or {}).get("sample", "") or "").strip()
            )
            condition = str((row or {}).get("condition", "") or "").strip()
            if sample and condition:
                rows.append((sample, condition))
    return rows


def _normalize_rna_seq_sample_id(sample: str) -> str:
    """Normalize path-like RNA-seq metadata sample fields to sample ids."""

    normalized = str(sample or "").strip()
    if not normalized:
        return ""
    name = Path(normalized).name
    if name.endswith(".bam"):
        return Path(name).stem
    return name or normalized


def _build_rna_seq_de_paths(
    *,
    selected_dir: Path | None,
    data_root: Path | None,
) -> RnaSeqDeArtifactPaths | None:
    """Build canonical strict RNA-seq DE artifact paths from benchmark inputs."""

    if selected_dir is None or data_root is None:
        return None

    references_dir = data_root.parent / "references"
    reference_fasta = references_dir / "C_parapsilosis_CDC317_current_chromosomes.fasta"
    annotation_gff = references_dir / "C_parapsilosis_CDC317_current_features.gff"
    metadata_tsv = data_root / "sample_metadata.tsv"
    sample_rows = _read_rna_seq_sample_rows(metadata_tsv)
    if not reference_fasta.exists() or not annotation_gff.exists() or not sample_rows:
        return None

    alignments_dir = selected_dir / "alignments"
    bam_paths = tuple(str((alignments_dir / f"{sample}.bam").resolve(strict=False)) for sample, _ in sample_rows)
    conditions = [condition for _, condition in sample_rows]
    unique_conditions: list[str] = []
    for condition in conditions:
        if condition not in unique_conditions:
            unique_conditions.append(condition)
    treatment = unique_conditions[-1] if unique_conditions else "Biofilm"
    control = unique_conditions[0] if unique_conditions else "Plankton"

    return RnaSeqDeArtifactPaths(
        reference_fasta=str(reference_fasta.resolve(strict=False)),
        annotation_gff=str(annotation_gff.resolve(strict=False)),
        metadata_tsv=str(metadata_tsv.resolve(strict=False)),
        index_base=str((selected_dir / "subread_index" / "genome").resolve(strict=False)),
        alignments_dir=str(alignments_dir.resolve(strict=False)),
        bam_paths=bam_paths,
        counts_path=str((selected_dir / "counts" / "gene_counts.txt").resolve(strict=False)),
        deseq_output_dir=str((selected_dir / "deseq2_results").resolve(strict=False)),
        final_csv=str((selected_dir / "final" / "deseq_results.csv").resolve(strict=False)),
        contrast=f"condition_{treatment}_vs_{control}",
    )


def _discover_first_existing_path(candidates: list[Path]) -> str:
    """Return the first existing path from a candidate list."""

    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve(strict=False))
    return ""


def _build_germline_variant_paths(
    *,
    selected_dir: Path | None,
    data_root: Path | None,
) -> GermlineVariantArtifactPaths | None:
    """Build canonical strict germline-variant artifact paths."""

    if selected_dir is None or data_root is None:
        return None

    reads = _discover_primary_fastq_pair(data_root)
    if reads is None:
        return None

    reference_fasta = _discover_first_existing_path(
        [
            data_root / "ref_genome.fa",
            data_root / "ref_genome.fasta",
            data_root / "reference.fa",
            data_root / "reference.fasta",
        ]
    )
    if not reference_fasta:
        return None

    return GermlineVariantArtifactPaths(
        reference_fasta=reference_fasta,
        reads_1=reads[0],
        reads_2=reads[1],
        aligned_bam=str((selected_dir / "intermediate" / "aligned_sorted_markdup.bam").resolve(strict=False)),
        final_vcf=str((selected_dir / "final" / "variants.vcf").resolve(strict=False)),
    )


def _build_single_cell_paths(
    *,
    selected_dir: Path | None,
    data_root: Path | None,
) -> SingleCellArtifactPaths | None:
    """Build canonical strict single-cell artifact paths."""

    if selected_dir is None or data_root is None:
        return None

    r1_fastq = _discover_first_existing_path(
        [
            data_root / "sample_R1.fastq",
            data_root / "sample_R1.fastq.gz",
        ]
    )
    r2_fastq = _discover_first_existing_path(
        [
            data_root / "sample_R2.fastq",
            data_root / "sample_R2.fastq.gz",
        ]
    )
    whitelist = _discover_first_existing_path(
        [
            data_root / "barcodes_whitelist.txt",
            data_root / "whitelist.txt",
        ]
    )
    reference_fasta = _discover_first_existing_path(
        [
            data_root / "genome.fa",
            data_root / "genome.fasta",
            data_root / "reference.fa",
            data_root / "reference.fasta",
        ]
    )
    annotation_gtf = _discover_first_existing_path(
        [
            data_root / "annotation.gtf",
            data_root / "genes.gtf",
        ]
    )
    if not (r1_fastq and r2_fastq and reference_fasta and annotation_gtf):
        return None

    return SingleCellArtifactPaths(
        r1_fastq=r1_fastq,
        r2_fastq=r2_fastq,
        whitelist=whitelist or "",
        reference_fasta=reference_fasta,
        annotation_gtf=annotation_gtf,
        output_dir=str(selected_dir.resolve(strict=False)),
    )
