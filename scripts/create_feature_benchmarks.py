#!/usr/bin/env python3
"""Generate all synthetic benchmark data for the 7-feature benchmark suite.

Usage:
    python3 scripts/create_feature_benchmarks.py --all
    python3 scripts/create_feature_benchmarks.py --feature output-quality-gate
    python3 scripts/create_feature_benchmarks.py --verify-only
    python3 scripts/create_feature_benchmarks.py --dry-run

All data is generated deterministically with SEED=42.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

SEED = 42
BASES = "ACGT"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seq(rng: random.Random, length: int) -> str:
    """Generate a random DNA sequence."""
    return "".join(rng.choice(BASES) for _ in range(length))


def _qual(length: int, char: str = "I") -> str:
    """Generate a quality string of uniform character."""
    return char * length


def _fastq_record(name: str, seq: str, qual: str) -> str:
    return f"@{name}\n{seq}\n+\n{qual}"


def _sam_header(contigs: list[tuple[str, int]], sorted_order: str = "coordinate") -> str:
    lines = [f"@HD\tVN:1.6\tSO:{sorted_order}"]
    for cname, clen in contigs:
        lines.append(f"@SQ\tSN:{cname}\tLN:{clen}")
    return "\n".join(lines)


def _sam_read(
    name: str,
    flag: int,
    contig: str,
    pos: int,
    mapq: int,
    cigar: str,
    seq: str,
    qual: str,
) -> str:
    return f"{name}\t{flag}\t{contig}\t{pos}\t{mapq}\t{cigar}\t*\t0\t0\t{seq}\t{qual}"


def _vcf_header(
    contigs: list[tuple[str, int]],
    sample: str | None = None,
    extra_info: list[str] | None = None,
    extra_format: list[str] | None = None,
) -> str:
    lines = ["##fileformat=VCFv4.2"]
    for cname, clen in contigs:
        lines.append(f"##contig=<ID={cname},length={clen}>")
    lines.append('##INFO=<ID=DP,Number=1,Type=Integer,Description="Read depth">')
    if extra_info:
        lines.extend(extra_info)
    lines.append('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">')
    lines.append('##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">')
    if extra_format:
        lines.extend(extra_format)
    cols = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"
    if sample:
        cols += f"\tFORMAT\t{sample}"
    lines.append(cols)
    return "\n".join(lines)


def _vcf_variant(
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    qual: int,
    filt: str,
    dp: int,
    gq: int | None = None,
    sample: bool = False,
) -> str:
    info = f"DP={dp}"
    base = f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t{qual}\t{filt}\t{info}"
    if sample:
        fmt = "GT:GQ"
        sdata = f"0/1:{gq if gq is not None else qual}"
        base += f"\t{fmt}\t{sdata}"
    return base


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


# ===================================================================
# Feature 1: Output Quality Gate
# ===================================================================


def create_output_quality_scenarios(task_dir: Path) -> list[dict]:
    """Generate 23 files with known quality levels for the output quality gate."""
    rng = random.Random(SEED)
    data_dir = task_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    ref_len = 500
    ref_seq = _seq(rng, ref_len)
    _write(data_dir / "reference.fa", f">chr1\n{ref_seq}\n")

    contigs = [("chr1", ref_len)]
    read_len = 100
    scenarios: list[dict] = []

    # ---- BAM scenarios (SAM text files) ----

    # 1. good_bam: 20 mapped, 1 unmapped
    lines = [_sam_header(contigs)]
    for i in range(20):
        pos = rng.randint(1, ref_len - read_len)
        s = _seq(rng, read_len)
        lines.append(_sam_read(f"read_{i}", 0, "chr1", pos, 60, f"{read_len}M", s, _qual(read_len)))
    lines.append(_sam_read("unmapped_0", 4, "*", 0, 0, "*", _seq(rng, read_len), _qual(read_len)))
    _write(data_dir / "good_bam.sam", "\n".join(lines) + "\n")
    scenarios.append({
        "scenario_id": "good_bam",
        "file": "data/good_bam.sam",
        "file_type": "bam",
        "expected_level": "PASS",
        "expected_metrics": {
            "mapping_rate": {"min": 0.90, "max": 1.0},
            "duplicate_rate": {"min": 0.0, "max": 0.05},
        },
        "expected_flags": [],
    })

    # 2. low_mapping_bam: 1 mapped, 29 unmapped
    lines = [_sam_header(contigs)]
    s = _seq(rng, read_len)
    lines.append(_sam_read("mapped_0", 0, "chr1", 10, 60, f"{read_len}M", s, _qual(read_len)))
    for i in range(29):
        lines.append(_sam_read(f"unmapped_{i}", 4, "*", 0, 0, "*", _seq(rng, read_len), _qual(read_len)))
    _write(data_dir / "low_mapping_bam.sam", "\n".join(lines) + "\n")
    scenarios.append({
        "scenario_id": "low_mapping_bam",
        "file": "data/low_mapping_bam.sam",
        "file_type": "bam",
        "expected_level": "FAIL",
        "expected_metrics": {
            "mapping_rate": {"min": 0.0, "max": 0.10},
        },
        "expected_flags": ["low_mapping_rate"],
    })

    # 3. high_dup_bam: 12 mapped, 8 duplicates (flag=1024), 0 unmapped
    lines = [_sam_header(contigs)]
    for i in range(12):
        pos = rng.randint(1, ref_len - read_len)
        s = _seq(rng, read_len)
        lines.append(_sam_read(f"mapped_{i}", 0, "chr1", pos, 60, f"{read_len}M", s, _qual(read_len)))
    for i in range(8):
        pos = rng.randint(1, ref_len - read_len)
        s = _seq(rng, read_len)
        lines.append(_sam_read(f"dup_{i}", 1024, "chr1", pos, 60, f"{read_len}M", s, _qual(read_len)))
    _write(data_dir / "high_dup_bam.sam", "\n".join(lines) + "\n")
    scenarios.append({
        "scenario_id": "high_dup_bam",
        "file": "data/high_dup_bam.sam",
        "file_type": "bam",
        "expected_level": "WARN",
        "expected_metrics": {
            "mapping_rate": {"min": 0.90, "max": 1.0},
            "duplicate_rate": {"min": 0.30, "max": 0.50},
        },
        "expected_flags": ["high_duplicate_rate"],
    })

    # 4. empty_bam: Header only
    _write(data_dir / "empty_bam.sam", _sam_header(contigs) + "\n")
    scenarios.append({
        "scenario_id": "empty_bam",
        "file": "data/empty_bam.sam",
        "file_type": "bam",
        "expected_level": "FAIL",
        "expected_metrics": {},
        "expected_flags": ["empty_file"],
    })

    # 5. low_coverage_bam: 2 mapped reads on 500bp reference
    lines = [_sam_header(contigs)]
    for i in range(2):
        pos = rng.randint(1, ref_len - read_len)
        s = _seq(rng, read_len)
        lines.append(_sam_read(f"read_{i}", 0, "chr1", pos, 60, f"{read_len}M", s, _qual(read_len)))
    _write(data_dir / "low_coverage_bam.sam", "\n".join(lines) + "\n")
    scenarios.append({
        "scenario_id": "low_coverage_bam",
        "file": "data/low_coverage_bam.sam",
        "file_type": "bam",
        "expected_level": "WARN",
        "expected_metrics": {
            "mapping_rate": {"min": 0.90, "max": 1.0},
        },
        "expected_flags": ["low_coverage"],
    })

    # 6. mixed_issues_bam: 3 mapped, 12 unmapped, 5 duplicates
    lines = [_sam_header(contigs)]
    for i in range(3):
        pos = rng.randint(1, ref_len - read_len)
        s = _seq(rng, read_len)
        lines.append(_sam_read(f"mapped_{i}", 0, "chr1", pos, 60, f"{read_len}M", s, _qual(read_len)))
    for i in range(12):
        lines.append(_sam_read(f"unmapped_{i}", 4, "*", 0, 0, "*", _seq(rng, read_len), _qual(read_len)))
    for i in range(5):
        pos = rng.randint(1, ref_len - read_len)
        s = _seq(rng, read_len)
        lines.append(_sam_read(f"dup_{i}", 1024, "chr1", pos, 60, f"{read_len}M", s, _qual(read_len)))
    _write(data_dir / "mixed_issues_bam.sam", "\n".join(lines) + "\n")
    scenarios.append({
        "scenario_id": "mixed_issues_bam",
        "file": "data/mixed_issues_bam.sam",
        "file_type": "bam",
        "expected_level": "WARN",
        "expected_metrics": {
            "mapping_rate": {"min": 0.10, "max": 0.50},
            "duplicate_rate": {"min": 0.15, "max": 0.35},
        },
        "expected_flags": ["low_mapping_rate", "high_duplicate_rate"],
    })

    # ---- VCF scenarios ----

    vcf_contigs = [("chr1", 10000), ("chr2", 10000)]

    # 7. good_vcf: 150 PASS variants with GQ>30, 10 filtered
    hdr = _vcf_header(vcf_contigs, sample="sample1")
    var_lines = [hdr]
    transitions = {"A": "G", "G": "A", "C": "T", "T": "C"}
    for i in range(150):
        chrom = "chr1" if i < 80 else "chr2"
        pos = 100 + i * 50
        ref_base = rng.choice(BASES)
        alt_base = rng.choice([b for b in BASES if b != ref_base])
        gq = rng.randint(31, 60)
        dp = rng.randint(15, 50)
        var_lines.append(_vcf_variant(chrom, pos, ref_base, alt_base, gq, "PASS", dp, gq=gq, sample=True))
    for i in range(10):
        chrom = "chr1"
        pos = 8000 + i * 10
        ref_base = rng.choice(BASES)
        alt_base = rng.choice([b for b in BASES if b != ref_base])
        var_lines.append(_vcf_variant(chrom, pos, ref_base, alt_base, 5, "LowQual", 3, gq=5, sample=True))
    _write(data_dir / "good_vcf.vcf", "\n".join(var_lines) + "\n")
    scenarios.append({
        "scenario_id": "good_vcf",
        "file": "data/good_vcf.vcf",
        "file_type": "vcf",
        "expected_level": "PASS",
        "expected_metrics": {
            "pass_variant_count": {"min": 140, "max": 160},
            "pass_rate": {"min": 0.85, "max": 1.0},
        },
        "expected_flags": [],
    })

    # 8. all_filtered_vcf: 50 variants all FILTER=LowQual
    hdr = _vcf_header(vcf_contigs, sample="sample1")
    var_lines = [hdr]
    for i in range(50):
        chrom = "chr1" if i < 30 else "chr2"
        pos = 200 + i * 100
        ref_base = rng.choice(BASES)
        alt_base = rng.choice([b for b in BASES if b != ref_base])
        var_lines.append(_vcf_variant(chrom, pos, ref_base, alt_base, 4, "LowQual", 2, gq=4, sample=True))
    _write(data_dir / "all_filtered_vcf.vcf", "\n".join(var_lines) + "\n")
    scenarios.append({
        "scenario_id": "all_filtered_vcf",
        "file": "data/all_filtered_vcf.vcf",
        "file_type": "vcf",
        "expected_level": "FAIL",
        "expected_metrics": {
            "pass_variant_count": {"min": 0, "max": 0},
            "pass_rate": {"min": 0.0, "max": 0.0},
        },
        "expected_flags": ["no_pass_variants"],
    })

    # 9. empty_vcf: Header only
    _write(data_dir / "empty_vcf.vcf", _vcf_header(vcf_contigs, sample="sample1") + "\n")
    scenarios.append({
        "scenario_id": "empty_vcf",
        "file": "data/empty_vcf.vcf",
        "file_type": "vcf",
        "expected_level": "FAIL",
        "expected_metrics": {},
        "expected_flags": ["empty_file"],
    })

    # 10. low_qual_vcf: 100 variants with GQ<10
    hdr = _vcf_header(vcf_contigs, sample="sample1")
    var_lines = [hdr]
    for i in range(100):
        chrom = "chr1" if i < 55 else "chr2"
        pos = 100 + i * 80
        ref_base = rng.choice(BASES)
        alt_base = rng.choice([b for b in BASES if b != ref_base])
        gq = rng.randint(1, 9)
        var_lines.append(_vcf_variant(chrom, pos, ref_base, alt_base, gq, "PASS", 5, gq=gq, sample=True))
    _write(data_dir / "low_qual_vcf.vcf", "\n".join(var_lines) + "\n")
    scenarios.append({
        "scenario_id": "low_qual_vcf",
        "file": "data/low_qual_vcf.vcf",
        "file_type": "vcf",
        "expected_level": "WARN",
        "expected_metrics": {
            "mean_gq": {"min": 0, "max": 10},
        },
        "expected_flags": ["low_genotype_quality"],
    })

    # 11. clustered_vcf: 50 variants all within 100bp window on chr1
    hdr = _vcf_header(vcf_contigs, sample="sample1")
    var_lines = [hdr]
    for i in range(50):
        pos = 5000 + i * 2  # all within 100bp
        ref_base = rng.choice(BASES)
        alt_base = rng.choice([b for b in BASES if b != ref_base])
        gq = rng.randint(25, 45)
        var_lines.append(_vcf_variant("chr1", pos, ref_base, alt_base, gq, "PASS", 20, gq=gq, sample=True))
    _write(data_dir / "clustered_vcf.vcf", "\n".join(var_lines) + "\n")
    scenarios.append({
        "scenario_id": "clustered_vcf",
        "file": "data/clustered_vcf.vcf",
        "file_type": "vcf",
        "expected_level": "WARN",
        "expected_metrics": {
            "pass_variant_count": {"min": 45, "max": 55},
        },
        "expected_flags": ["variant_clustering"],
    })

    # 12. normal_titv_vcf: ~70 transitions, ~35 transversions (ti/tv ~2.0)
    hdr = _vcf_header(vcf_contigs, sample="sample1")
    var_lines = [hdr]
    ti_pairs = [("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")]
    tv_pairs = [("A", "C"), ("A", "T"), ("G", "C"), ("G", "T"),
                ("C", "A"), ("C", "G"), ("T", "A"), ("T", "G")]
    pos_counter = 100
    for _ in range(70):
        ref_b, alt_b = rng.choice(ti_pairs)
        chrom = rng.choice(["chr1", "chr2"])
        gq = rng.randint(30, 50)
        var_lines.append(_vcf_variant(chrom, pos_counter, ref_b, alt_b, gq, "PASS", 25, gq=gq, sample=True))
        pos_counter += rng.randint(50, 200)
    for _ in range(35):
        ref_b, alt_b = rng.choice(tv_pairs)
        chrom = rng.choice(["chr1", "chr2"])
        gq = rng.randint(30, 50)
        var_lines.append(_vcf_variant(chrom, pos_counter, ref_b, alt_b, gq, "PASS", 25, gq=gq, sample=True))
        pos_counter += rng.randint(50, 200)
    _write(data_dir / "normal_titv_vcf.vcf", "\n".join(var_lines) + "\n")
    scenarios.append({
        "scenario_id": "normal_titv_vcf",
        "file": "data/normal_titv_vcf.vcf",
        "file_type": "vcf",
        "expected_level": "PASS",
        "expected_metrics": {
            "ti_tv_ratio": {"min": 1.8, "max": 2.2},
            "pass_variant_count": {"min": 100, "max": 110},
        },
        "expected_flags": [],
    })

    # ---- FASTQ scenarios ----

    # 13. good_fastq: 1000 reads, 150bp, quality chars mostly 'I' (Q40)
    fq_lines = []
    for i in range(1000):
        s = _seq(rng, 150)
        q = _qual(150, "I")
        fq_lines.append(_fastq_record(f"read_{i}", s, q))
    _write(data_dir / "good_fastq.fq", "\n".join(fq_lines) + "\n")
    scenarios.append({
        "scenario_id": "good_fastq",
        "file": "data/good_fastq.fq",
        "file_type": "fastq",
        "expected_level": "PASS",
        "expected_metrics": {
            "read_count": {"min": 1000, "max": 1000},
            "mean_read_length": {"min": 149, "max": 151},
        },
        "expected_flags": [],
    })

    # 14. short_reads_fastq: 1000 reads, 15bp
    fq_lines = []
    for i in range(1000):
        s = _seq(rng, 15)
        q = _qual(15, "I")
        fq_lines.append(_fastq_record(f"read_{i}", s, q))
    _write(data_dir / "short_reads_fastq.fq", "\n".join(fq_lines) + "\n")
    scenarios.append({
        "scenario_id": "short_reads_fastq",
        "file": "data/short_reads_fastq.fq",
        "file_type": "fastq",
        "expected_level": "WARN",
        "expected_metrics": {
            "read_count": {"min": 1000, "max": 1000},
            "mean_read_length": {"min": 14, "max": 16},
        },
        "expected_flags": ["short_reads"],
    })

    # 15. low_qual_fastq: 1000 reads, 150bp, quality chars mostly '#' (Q2)
    fq_lines = []
    for i in range(1000):
        s = _seq(rng, 150)
        q = _qual(150, "#")
        fq_lines.append(_fastq_record(f"read_{i}", s, q))
    _write(data_dir / "low_qual_fastq.fq", "\n".join(fq_lines) + "\n")
    scenarios.append({
        "scenario_id": "low_qual_fastq",
        "file": "data/low_qual_fastq.fq",
        "file_type": "fastq",
        "expected_level": "FAIL",
        "expected_metrics": {
            "read_count": {"min": 1000, "max": 1000},
            "mean_quality": {"min": 0, "max": 5},
        },
        "expected_flags": ["low_base_quality"],
    })

    # 16. empty_fastq: 0 bytes
    _write(data_dir / "empty_fastq.fq", "")
    scenarios.append({
        "scenario_id": "empty_fastq",
        "file": "data/empty_fastq.fq",
        "file_type": "fastq",
        "expected_level": "FAIL",
        "expected_metrics": {},
        "expected_flags": ["empty_file"],
    })

    # 17. truncated_fastq: 3 complete reads then truncated mid-record
    fq_lines = []
    for i in range(3):
        s = _seq(rng, 150)
        q = _qual(150, "I")
        fq_lines.append(_fastq_record(f"read_{i}", s, q))
    # Add truncated 4th record: header + sequence only, no + or quality
    fq_lines.append(f"@read_3\n{_seq(rng, 150)}")
    _write(data_dir / "truncated_fastq.fq", "\n".join(fq_lines) + "\n")
    scenarios.append({
        "scenario_id": "truncated_fastq",
        "file": "data/truncated_fastq.fq",
        "file_type": "fastq",
        "expected_level": "FAIL",
        "expected_metrics": {},
        "expected_flags": ["truncated_file"],
    })

    # ---- Tabular/DE scenarios ----

    gene_names = [f"GENE_{i:04d}" for i in range(500)]

    # 18. good_de: 500 rows, 50 with padj<0.05
    header = "gene,baseMean,log2FoldChange,pvalue,padj"
    rows = [header]
    for i, g in enumerate(gene_names):
        bm = round(rng.uniform(10, 5000), 2)
        lfc = round(rng.uniform(-4, 4), 3)
        if i < 50:
            pv = round(rng.uniform(1e-10, 0.01), 8)
            padj = round(rng.uniform(1e-8, 0.049), 8)
        else:
            pv = round(rng.uniform(0.05, 1.0), 6)
            padj = round(rng.uniform(0.1, 1.0), 6)
        rows.append(f"{g},{bm},{lfc},{pv},{padj}")
    _write(data_dir / "good_de.csv", "\n".join(rows) + "\n")
    scenarios.append({
        "scenario_id": "good_de",
        "file": "data/good_de.csv",
        "file_type": "csv",
        "expected_level": "PASS",
        "expected_metrics": {
            "total_genes": {"min": 500, "max": 500},
            "significant_genes": {"min": 45, "max": 55},
        },
        "expected_flags": [],
    })

    # 19. no_sig_de: 500 rows, all padj>0.5
    rows = [header]
    for g in gene_names:
        bm = round(rng.uniform(10, 5000), 2)
        lfc = round(rng.uniform(-0.5, 0.5), 3)
        pv = round(rng.uniform(0.5, 1.0), 6)
        padj = round(rng.uniform(0.5, 1.0), 6)
        rows.append(f"{g},{bm},{lfc},{pv},{padj}")
    _write(data_dir / "no_sig_de.csv", "\n".join(rows) + "\n")
    scenarios.append({
        "scenario_id": "no_sig_de",
        "file": "data/no_sig_de.csv",
        "file_type": "csv",
        "expected_level": "WARN",
        "expected_metrics": {
            "total_genes": {"min": 500, "max": 500},
            "significant_genes": {"min": 0, "max": 0},
        },
        "expected_flags": ["no_significant_genes"],
    })

    # 20. all_sig_de: 500 rows, all padj<0.001
    rows = [header]
    for g in gene_names:
        bm = round(rng.uniform(10, 5000), 2)
        lfc = round(rng.uniform(-6, 6), 3)
        pv = round(rng.uniform(1e-12, 1e-4), 10)
        padj = round(rng.uniform(1e-10, 0.001), 10)
        rows.append(f"{g},{bm},{lfc},{pv},{padj}")
    _write(data_dir / "all_sig_de.csv", "\n".join(rows) + "\n")
    scenarios.append({
        "scenario_id": "all_sig_de",
        "file": "data/all_sig_de.csv",
        "file_type": "csv",
        "expected_level": "WARN",
        "expected_metrics": {
            "total_genes": {"min": 500, "max": 500},
            "significant_genes": {"min": 498, "max": 500},
        },
        "expected_flags": ["suspiciously_all_significant"],
    })

    # 21. empty_de: Header only
    _write(data_dir / "empty_de.csv", header + "\n")
    scenarios.append({
        "scenario_id": "empty_de",
        "file": "data/empty_de.csv",
        "file_type": "csv",
        "expected_level": "FAIL",
        "expected_metrics": {
            "total_genes": {"min": 0, "max": 0},
        },
        "expected_flags": ["empty_file"],
    })

    # 22. missing_col_de: Only gene,baseMean,log2FoldChange (no padj)
    rows = ["gene,baseMean,log2FoldChange"]
    for g in gene_names:
        bm = round(rng.uniform(10, 5000), 2)
        lfc = round(rng.uniform(-4, 4), 3)
        rows.append(f"{g},{bm},{lfc}")
    _write(data_dir / "missing_col_de.csv", "\n".join(rows) + "\n")
    scenarios.append({
        "scenario_id": "missing_col_de",
        "file": "data/missing_col_de.csv",
        "file_type": "csv",
        "expected_level": "FAIL",
        "expected_metrics": {},
        "expected_flags": ["missing_required_column"],
    })

    # 23. nan_heavy_de: 500 rows, 400 with padj=NA
    rows = [header]
    for i, g in enumerate(gene_names):
        bm = round(rng.uniform(10, 5000), 2)
        lfc = round(rng.uniform(-4, 4), 3)
        pv = round(rng.uniform(0.001, 0.5), 6)
        if i < 100:
            padj = round(rng.uniform(0.01, 0.9), 6)
        else:
            padj = "NA"
        rows.append(f"{g},{bm},{lfc},{pv},{padj}")
    _write(data_dir / "nan_heavy_de.csv", "\n".join(rows) + "\n")
    scenarios.append({
        "scenario_id": "nan_heavy_de",
        "file": "data/nan_heavy_de.csv",
        "file_type": "csv",
        "expected_level": "WARN",
        "expected_metrics": {
            "total_genes": {"min": 500, "max": 500},
            "na_fraction": {"min": 0.75, "max": 0.85},
        },
        "expected_flags": ["high_na_fraction"],
    })

    # Write scenario manifest and truth
    _write_json(task_dir / "scenarios.json", {"scenarios": scenarios})
    _write_json(task_dir / "results" / "truth.json", {"scenarios": scenarios})
    return scenarios


# ===================================================================
# Feature 2: Preflight Scanner
# ===================================================================


def create_preflight_scenarios(task_dir: Path) -> list[dict]:
    """Generate 20 input files/directories with planted defects."""
    rng = random.Random(SEED + 1)
    data_dir = task_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    scenarios: list[dict] = []
    read_len = 100

    # 1. Valid paired FASTQ (positive control)
    d = data_dir / "valid_paired_fastq"
    d.mkdir(parents=True, exist_ok=True)
    r1_lines, r2_lines = [], []
    for i in range(100):
        s1, s2 = _seq(rng, read_len), _seq(rng, read_len)
        q = _qual(read_len)
        r1_lines.append(_fastq_record(f"pair_{i}/1", s1, q))
        r2_lines.append(_fastq_record(f"pair_{i}/2", s2, q))
    _write(d / "reads_R1.fastq", "\n".join(r1_lines) + "\n")
    _write(d / "reads_R2.fastq", "\n".join(r2_lines) + "\n")
    scenarios.append({
        "scenario_id": "valid_paired_fastq",
        "directory": "data/valid_paired_fastq",
        "expected_detections": [],
        "is_positive_control": True,
    })

    # 2. Mismatched pair: R1=100 reads, R2=80 reads
    d = data_dir / "mismatched_pair_count"
    d.mkdir(parents=True, exist_ok=True)
    r1_lines, r2_lines = [], []
    for i in range(100):
        r1_lines.append(_fastq_record(f"pair_{i}/1", _seq(rng, read_len), _qual(read_len)))
    for i in range(80):
        r2_lines.append(_fastq_record(f"pair_{i}/2", _seq(rng, read_len), _qual(read_len)))
    _write(d / "reads_R1.fastq", "\n".join(r1_lines) + "\n")
    _write(d / "reads_R2.fastq", "\n".join(r2_lines) + "\n")
    scenarios.append({
        "scenario_id": "mismatched_pair_count",
        "directory": "data/mismatched_pair_count",
        "expected_detections": ["read_count_mismatch"],
    })

    # 3. Corrupt FASTQ: quality line shorter than sequence on read 5
    d = data_dir / "corrupt_fastq_qual"
    d.mkdir(parents=True, exist_ok=True)
    fq_lines = []
    for i in range(10):
        s = _seq(rng, read_len)
        if i == 4:
            q = _qual(read_len - 20)  # too short
        else:
            q = _qual(read_len)
        fq_lines.append(_fastq_record(f"read_{i}", s, q))
    _write(d / "reads.fastq", "\n".join(fq_lines) + "\n")
    scenarios.append({
        "scenario_id": "corrupt_fastq_qual",
        "directory": "data/corrupt_fastq_qual",
        "expected_detections": ["fastq_format_error"],
    })

    # 4. Empty reference FASTA: 0 bytes
    d = data_dir / "empty_reference"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "reference.fa", "")
    scenarios.append({
        "scenario_id": "empty_reference",
        "directory": "data/empty_reference",
        "expected_detections": ["empty_file"],
    })

    # 5. Multi-line reference: properly wrapped at 80bp (positive control)
    d = data_dir / "multi_line_reference"
    d.mkdir(parents=True, exist_ok=True)
    long_seq = _seq(rng, 500)
    wrapped = "\n".join(long_seq[i:i + 80] for i in range(0, len(long_seq), 80))
    _write(d / "reference.fa", f">chr1\n{wrapped}\n")
    scenarios.append({
        "scenario_id": "multi_line_reference",
        "directory": "data/multi_line_reference",
        "expected_detections": [],
        "is_positive_control": True,
    })

    # 6. Missing .fai: write .fa but no .fai
    d = data_dir / "missing_fai_index"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "reference.fa", f">chr1\n{_seq(rng, 300)}\n")
    scenarios.append({
        "scenario_id": "missing_fai_index",
        "directory": "data/missing_fai_index",
        "expected_detections": ["missing_index"],
    })

    # 7. Metadata .csv with tab delimiter (the DESeq bug)
    d = data_dir / "metadata_delimiter_mismatch"
    d.mkdir(parents=True, exist_ok=True)
    meta_lines = ["sample\tcondition\n"]
    for i in range(6):
        cond = "treatment" if i < 3 else "control"
        meta_lines.append(f"sample_{i}\t{cond}\n")
    _write(d / "metadata.csv", "".join(meta_lines))
    scenarios.append({
        "scenario_id": "metadata_delimiter_mismatch",
        "directory": "data/metadata_delimiter_mismatch",
        "expected_detections": ["delimiter_mismatch"],
    })

    # 8. Metadata missing condition column
    d = data_dir / "metadata_missing_condition"
    d.mkdir(parents=True, exist_ok=True)
    meta_lines = ["sample,batch\n"]
    for i in range(6):
        meta_lines.append(f"sample_{i},batch_{i % 2}\n")
    _write(d / "metadata.csv", "".join(meta_lines))
    scenarios.append({
        "scenario_id": "metadata_missing_condition",
        "directory": "data/metadata_missing_condition",
        "expected_detections": ["missing_required_column"],
    })

    # 9. Metadata with 1 sample only
    d = data_dir / "metadata_single_sample"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "metadata.csv", "sample,condition\nsample_0,treatment\n")
    scenarios.append({
        "scenario_id": "metadata_single_sample",
        "directory": "data/metadata_single_sample",
        "expected_detections": ["insufficient_samples"],
    })

    # 10. Metadata with duplicate sample names
    d = data_dir / "metadata_duplicate_samples"
    d.mkdir(parents=True, exist_ok=True)
    meta_text = "sample,condition\nsample_0,treatment\nsample_0,control\nsample_1,treatment\nsample_1,control\n"
    _write(d / "metadata.csv", meta_text)
    scenarios.append({
        "scenario_id": "metadata_duplicate_samples",
        "directory": "data/metadata_duplicate_samples",
        "expected_detections": ["duplicate_sample_ids"],
    })

    # 11. GFF-extension file that's actually BED format
    d = data_dir / "gff_wrong_format"
    d.mkdir(parents=True, exist_ok=True)
    bed_text = "chr1\t100\t200\tgene1\t0\t+\nchr1\t300\t400\tgene2\t0\t-\n"
    _write(d / "annotation.gff", bed_text)
    scenarios.append({
        "scenario_id": "gff_wrong_format",
        "directory": "data/gff_wrong_format",
        "expected_detections": ["format_mismatch"],
    })

    # 12. VCF missing ##fileformat line
    d = data_dir / "vcf_malformed_header"
    d.mkdir(parents=True, exist_ok=True)
    vcf_text = (
        "##contig=<ID=chr1,length=1000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tG\t30\tPASS\tDP=10\n"
    )
    _write(d / "variants.vcf", vcf_text)
    scenarios.append({
        "scenario_id": "vcf_malformed_header",
        "directory": "data/vcf_malformed_header",
        "expected_detections": ["malformed_header"],
    })

    # 13. Reference with 50% N bases
    d = data_dir / "reference_with_ns"
    d.mkdir(parents=True, exist_ok=True)
    half = 200
    n_seq = _seq(rng, half) + "N" * half
    chars = list(n_seq)
    rng.shuffle(chars)
    _write(d / "reference.fa", f">chr1\n{''.join(chars)}\n")
    scenarios.append({
        "scenario_id": "reference_with_ns",
        "directory": "data/reference_with_ns",
        "expected_detections": ["high_n_fraction"],
    })

    # 14. FASTQ where 90% reads start with adapter sequence
    d = data_dir / "fastq_adapter_contaminated"
    d.mkdir(parents=True, exist_ok=True)
    adapter = "AGATCGGAAGAG"
    fq_lines = []
    for i in range(100):
        if i < 90:
            s = adapter + _seq(rng, read_len - len(adapter))
        else:
            s = _seq(rng, read_len)
        fq_lines.append(_fastq_record(f"read_{i}", s, _qual(read_len)))
    _write(d / "reads.fastq", "\n".join(fq_lines) + "\n")
    scenarios.append({
        "scenario_id": "fastq_adapter_contaminated",
        "directory": "data/fastq_adapter_contaminated",
        "expected_detections": ["adapter_contamination"],
    })

    # 15. Valid full dataset (positive control)
    d = data_dir / "valid_full_dataset"
    d.mkdir(parents=True, exist_ok=True)
    ref_seq_full = _seq(rng, 500)
    wrapped_ref = "\n".join(ref_seq_full[i:i + 80] for i in range(0, len(ref_seq_full), 80))
    _write(d / "reference.fa", f">chr1\n{wrapped_ref}\n")
    # Write matching .fai
    _write(d / "reference.fa.fai", f"chr1\t500\t6\t80\t81\n")
    # Write paired FASTQ
    r1_lines, r2_lines = [], []
    for i in range(50):
        r1_lines.append(_fastq_record(f"pair_{i}/1", _seq(rng, read_len), _qual(read_len)))
        r2_lines.append(_fastq_record(f"pair_{i}/2", _seq(rng, read_len), _qual(read_len)))
    _write(d / "reads_R1.fastq", "\n".join(r1_lines) + "\n")
    _write(d / "reads_R2.fastq", "\n".join(r2_lines) + "\n")
    # Metadata
    meta_text = "sample,condition\n"
    for i in range(6):
        cond = "treatment" if i < 3 else "control"
        meta_text += f"sample_{i},{cond}\n"
    _write(d / "metadata.csv", meta_text)
    scenarios.append({
        "scenario_id": "valid_full_dataset",
        "directory": "data/valid_full_dataset",
        "expected_detections": [],
        "is_positive_control": True,
    })

    # 16. Wrong quality encoding (Phred+64)
    d = data_dir / "wrong_quality_encoding"
    d.mkdir(parents=True, exist_ok=True)
    fq_lines = []
    for i in range(50):
        s = _seq(rng, read_len)
        # Phred+64: quality chars in range h-j (Q40-42 in Phred+64 = ASCII 104-106)
        q = "".join(chr(rng.randint(104, 106)) for _ in range(read_len))
        fq_lines.append(_fastq_record(f"read_{i}", s, q))
    _write(d / "reads.fastq", "\n".join(fq_lines) + "\n")
    scenarios.append({
        "scenario_id": "wrong_quality_encoding",
        "directory": "data/wrong_quality_encoding",
        "expected_detections": ["unusual_quality_encoding"],
    })

    # 17. BAM with contigs not matching reference
    d = data_dir / "bam_ref_mismatch"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "reference.fa", f">chrX\n{_seq(rng, 300)}\n")
    sam_hdr = _sam_header([("chr1", 500)], "coordinate")
    sam_read_line = _sam_read("read_0", 0, "chr1", 10, 60, "50M", _seq(rng, 50), _qual(50))
    _write(d / "aligned.sam", sam_hdr + "\n" + sam_read_line + "\n")
    scenarios.append({
        "scenario_id": "bam_ref_mismatch",
        "directory": "data/bam_ref_mismatch",
        "expected_detections": ["reference_mismatch"],
    })

    # 18. Truncated BAM (incomplete file)
    d = data_dir / "truncated_bam"
    d.mkdir(parents=True, exist_ok=True)
    sam_hdr = _sam_header([("chr1", 500)])
    full_sam = sam_hdr + "\n"
    for i in range(5):
        full_sam += _sam_read(f"read_{i}", 0, "chr1", 10 + i * 20, 60, "50M", _seq(rng, 50), _qual(50)) + "\n"
    # Write truncated -- cut off midway through the text
    _write(d / "aligned.sam", full_sam[:len(full_sam) // 2])
    scenarios.append({
        "scenario_id": "truncated_bam",
        "directory": "data/truncated_bam",
        "expected_detections": ["truncated_file"],
    })

    # 19. Unsorted BAM
    d = data_dir / "unsorted_bam"
    d.mkdir(parents=True, exist_ok=True)
    sam_hdr = _sam_header([("chr1", 1000)], "unsorted")
    lines = [sam_hdr]
    positions = [500, 100, 800, 50, 300]
    for i, pos in enumerate(positions):
        lines.append(_sam_read(f"read_{i}", 0, "chr1", pos, 60, "50M", _seq(rng, 50), _qual(50)))
    _write(d / "aligned.sam", "\n".join(lines) + "\n")
    scenarios.append({
        "scenario_id": "unsorted_bam",
        "directory": "data/unsorted_bam",
        "expected_detections": ["unsorted_bam"],
    })

    # 20. FASTA with only whitespace/newlines (not truly empty, but no sequences)
    d = data_dir / "whitespace_only_fasta"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "reference.fa", "\n\n  \n")
    scenarios.append({
        "scenario_id": "whitespace_only_fasta",
        "directory": "data/whitespace_only_fasta",
        "expected_detections": ["empty_file"],
    })

    _write_json(task_dir / "scenarios.json", {"scenarios": scenarios})
    return scenarios


# ===================================================================
# Feature 3: Output Catalog
# ===================================================================


def _write_step_completion(path: Path, step: int, tool: str, status: str = "completed") -> None:
    _write_json(path, {
        "step": step,
        "tool": tool,
        "status": status,
        "started_at": "2026-04-01T10:00:00Z",
        "finished_at": "2026-04-01T10:05:00Z",
    })


def create_output_catalog_scenarios(task_dir: Path) -> list[dict]:
    """Create 12 mock pipeline output directories."""
    rng = random.Random(SEED + 2)
    data_dir = task_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    scenarios: list[dict] = []

    # 1. simple_alignment: BWA + sort -> 5 files
    d = data_dir / "simple_alignment"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "aligned.bam", _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "aligned.bam.bai", "BAI_INDEX_PLACEHOLDER\n")
    _write(d / "sorted.bam", _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "pipeline.log", "Step 1: bwa_mem_align ... completed\nStep 2: samtools_sort ... completed\n")
    _write_step_completion(d / ".step_1_completion.json", 1, "bwa_mem_align")
    _write_step_completion(d / ".step_2_completion.json", 2, "samtools_sort")
    scenarios.append({
        "scenario_id": "simple_alignment",
        "directory": "data/simple_alignment",
        "expected_files": [
            {"path": "aligned.bam", "role": "intermediate", "format": "bam"},
            {"path": "aligned.bam.bai", "role": "intermediate", "format": "bai"},
            {"path": "sorted.bam", "role": "deliverable", "format": "bam"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 2. variant_calling_6step
    d = data_dir / "variant_calling_6step"
    d.mkdir(parents=True, exist_ok=True)
    for fname in ["aligned.bam", "aligned.bam.bai", "sorted.bam", "sorted.bam.bai"]:
        _write(d / fname, _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "variants.vcf", _vcf_header([("chr1", 1000)]) + "\nchr1\t100\t.\tA\tG\t30\tPASS\tDP=10\n")
    _write(d / "variants_filtered.vcf", _vcf_header([("chr1", 1000)]) + "\nchr1\t100\t.\tA\tG\t30\tPASS\tDP=10\n")
    _write(d / "pipeline.log", "6-step variant calling pipeline log\n")
    for i in range(1, 7):
        tools = ["bwa_mem_align", "samtools_sort", "samtools_index", "gatk_haplotype_caller", "bcftools_filter", "bcftools_stats"]
        _write_step_completion(d / f".step_{i}_completion.json", i, tools[i - 1])
    scenarios.append({
        "scenario_id": "variant_calling_6step",
        "directory": "data/variant_calling_6step",
        "expected_files": [
            {"path": "aligned.bam", "role": "intermediate", "format": "bam"},
            {"path": "aligned.bam.bai", "role": "intermediate", "format": "bai"},
            {"path": "sorted.bam", "role": "intermediate", "format": "bam"},
            {"path": "sorted.bam.bai", "role": "intermediate", "format": "bai"},
            {"path": "variants.vcf", "role": "deliverable", "format": "vcf"},
            {"path": "variants_filtered.vcf", "role": "deliverable", "format": "vcf"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_3_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_4_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_5_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_6_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 3. de_analysis: STAR -> counts -> DESeq2
    d = data_dir / "de_analysis"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "Aligned.out.bam", _sam_header([("chr1", 5000)]) + "\n")
    _write(d / "Aligned.out.bam.bai", "BAI\n")
    _write(d / "ReadsPerGene.out.tab", "gene\tunstranded\tsense\tantisense\nGENE1\t100\t50\t50\n")
    _write(d / "counts_matrix.tsv", "gene\tsample1\tsample2\tsample3\nGENE1\t100\t200\t150\n")
    _write(d / "metadata.tsv", "sample\tcondition\nsample1\ttreatment\nsample2\ttreatment\nsample3\tcontrol\n")
    _write(d / "deseq2_results.csv", "gene,baseMean,log2FoldChange,pvalue,padj\nGENE1,150.0,1.2,0.001,0.01\n")
    _write(d / "normalized_counts.csv", "gene,sample1,sample2,sample3\nGENE1,110,190,140\n")
    _write(d / "ma_plot.pdf", "%PDF-1.4 mock MA plot\n")
    _write(d / "volcano_plot.pdf", "%PDF-1.4 mock volcano plot\n")
    _write(d / "pipeline.log", "DE analysis pipeline log\n")
    for i in range(1, 6):
        tools = ["star_align", "featurecounts_count", "deseq2_run", "plot_ma", "plot_volcano"]
        _write_step_completion(d / f".step_{i}_completion.json", i, tools[i - 1])
    scenarios.append({
        "scenario_id": "de_analysis",
        "directory": "data/de_analysis",
        "expected_files": [
            {"path": "Aligned.out.bam", "role": "intermediate", "format": "bam"},
            {"path": "Aligned.out.bam.bai", "role": "intermediate", "format": "bai"},
            {"path": "ReadsPerGene.out.tab", "role": "intermediate", "format": "tsv"},
            {"path": "counts_matrix.tsv", "role": "intermediate", "format": "tsv"},
            {"path": "metadata.tsv", "role": "metadata", "format": "tsv"},
            {"path": "deseq2_results.csv", "role": "deliverable", "format": "csv"},
            {"path": "normalized_counts.csv", "role": "deliverable", "format": "csv"},
            {"path": "ma_plot.pdf", "role": "deliverable", "format": "pdf"},
            {"path": "volcano_plot.pdf", "role": "deliverable", "format": "pdf"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_3_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_4_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_5_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 4. metagenomics_pipeline: fastp -> SPAdes -> Kraken2
    d = data_dir / "metagenomics_pipeline"
    d.mkdir(parents=True, exist_ok=True)
    (d / "fastp_output").mkdir(parents=True, exist_ok=True)
    _write(d / "fastp_output" / "trimmed_R1.fastq", _fastq_record("r1", _seq(rng, 100), _qual(100)) + "\n")
    _write(d / "fastp_output" / "trimmed_R2.fastq", _fastq_record("r2", _seq(rng, 100), _qual(100)) + "\n")
    _write(d / "fastp_output" / "fastp.json", '{"summary": {"before_filtering": {"total_reads": 1000}}}\n')
    _write(d / "fastp_output" / "fastp.html", "<html>fastp report</html>\n")
    (d / "spades_output").mkdir(parents=True, exist_ok=True)
    _write(d / "spades_output" / "contigs.fasta", f">contig_1\n{_seq(rng, 300)}\n>contig_2\n{_seq(rng, 200)}\n")
    _write(d / "spades_output" / "scaffolds.fasta", f">scaffold_1\n{_seq(rng, 500)}\n")
    _write(d / "spades_output" / "assembly_graph.fastg", ">EDGE_1\nACGTACGT\n")
    _write(d / "spades_output" / "spades.log", "SPAdes assembly log\n")
    _write(d / "kraken2_report.txt", " 95.00\t950\t950\tU\t0\tunclassified\n  5.00\t50\t30\tG\t561\tEscherichia\n")
    _write(d / "kraken2_output.txt", "C\tread_1\t561\t100\t561:100\n")
    _write(d / "pipeline.log", "Metagenomics pipeline log\n")
    for i in range(1, 4):
        tools = ["fastp_trim", "spades_assemble", "kraken2_classify"]
        _write_step_completion(d / f".step_{i}_completion.json", i, tools[i - 1])
    scenarios.append({
        "scenario_id": "metagenomics_pipeline",
        "directory": "data/metagenomics_pipeline",
        "expected_files": [
            {"path": "fastp_output/trimmed_R1.fastq", "role": "intermediate", "format": "fastq"},
            {"path": "fastp_output/trimmed_R2.fastq", "role": "intermediate", "format": "fastq"},
            {"path": "fastp_output/fastp.json", "role": "intermediate", "format": "json"},
            {"path": "fastp_output/fastp.html", "role": "intermediate", "format": "html"},
            {"path": "spades_output/contigs.fasta", "role": "deliverable", "format": "fasta"},
            {"path": "spades_output/scaffolds.fasta", "role": "deliverable", "format": "fasta"},
            {"path": "spades_output/assembly_graph.fastg", "role": "intermediate", "format": "fastg"},
            {"path": "spades_output/spades.log", "role": "log", "format": "text"},
            {"path": "kraken2_report.txt", "role": "deliverable", "format": "text"},
            {"path": "kraken2_output.txt", "role": "intermediate", "format": "text"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_3_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 5. single_cell: Count -> cluster -> markers
    d = data_dir / "single_cell"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "count_matrix.h5ad", "H5AD_PLACEHOLDER_BINARY\n")
    _write(d / "clusters.csv", "cell,cluster\ncell_0,0\ncell_1,1\ncell_2,0\n")
    _write(d / "markers.csv", "gene,cluster,pval,log2fc\nGENE1,0,0.001,2.5\nGENE2,1,0.01,1.8\n")
    _write(d / "umap.csv", "cell,UMAP1,UMAP2\ncell_0,1.2,3.4\ncell_1,-0.5,2.1\n")
    _write(d / "umap_plot.png", "PNG_PLACEHOLDER\n")
    _write(d / "pipeline.log", "Single-cell pipeline log\n")
    for i in range(1, 4):
        tools = ["sc_count_and_cluster", "sc_find_markers", "sc_plot_umap"]
        _write_step_completion(d / f".step_{i}_completion.json", i, tools[i - 1])
    scenarios.append({
        "scenario_id": "single_cell",
        "directory": "data/single_cell",
        "expected_files": [
            {"path": "count_matrix.h5ad", "role": "intermediate", "format": "h5ad"},
            {"path": "clusters.csv", "role": "deliverable", "format": "csv"},
            {"path": "markers.csv", "role": "deliverable", "format": "csv"},
            {"path": "umap.csv", "role": "deliverable", "format": "csv"},
            {"path": "umap_plot.png", "role": "deliverable", "format": "png"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_3_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 6. multi_sample_joint: 3 samples -> joint call
    d = data_dir / "multi_sample_joint"
    d.mkdir(parents=True, exist_ok=True)
    for s in ["sample_A", "sample_B", "sample_C"]:
        _write(d / f"{s}_aligned.bam", _sam_header([("chr1", 2000)]) + "\n")
        _write(d / f"{s}_aligned.bam.bai", "BAI\n")
        _write(d / f"{s}_sorted.bam", _sam_header([("chr1", 2000)]) + "\n")
        _write(d / f"{s}_sorted.bam.bai", "BAI\n")
        _write(d / f"{s}.g.vcf", _vcf_header([("chr1", 2000)], sample=s) + "\n")
    _write(d / "joint_genotyped.vcf", _vcf_header([("chr1", 2000)]) + "\nchr1\t100\t.\tA\tG\t50\tPASS\tDP=30\n")
    _write(d / "joint_filtered.vcf", _vcf_header([("chr1", 2000)]) + "\nchr1\t100\t.\tA\tG\t50\tPASS\tDP=30\n")
    _write(d / "pipeline.log", "Multi-sample joint calling log\n")
    for i in range(1, 7):
        _write_step_completion(d / f".step_{i}_completion.json", i, f"step_{i}_tool")
    expected_files = []
    for s in ["sample_A", "sample_B", "sample_C"]:
        expected_files.extend([
            {"path": f"{s}_aligned.bam", "role": "intermediate", "format": "bam"},
            {"path": f"{s}_aligned.bam.bai", "role": "intermediate", "format": "bai"},
            {"path": f"{s}_sorted.bam", "role": "intermediate", "format": "bam"},
            {"path": f"{s}_sorted.bam.bai", "role": "intermediate", "format": "bai"},
            {"path": f"{s}.g.vcf", "role": "intermediate", "format": "vcf"},
        ])
    expected_files.extend([
        {"path": "joint_genotyped.vcf", "role": "intermediate", "format": "vcf"},
        {"path": "joint_filtered.vcf", "role": "deliverable", "format": "vcf"},
        {"path": "pipeline.log", "role": "log", "format": "text"},
    ])
    for i in range(1, 7):
        expected_files.append({"path": f".step_{i}_completion.json", "role": "metadata", "format": "json"})
    scenarios.append({
        "scenario_id": "multi_sample_joint",
        "directory": "data/multi_sample_joint",
        "expected_files": expected_files,
    })

    # 7. failed_partial_run: 4 of 6 steps completed
    d = data_dir / "failed_partial_run"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "aligned.bam", _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "sorted.bam", _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "sorted.bam.bai", "BAI\n")
    _write(d / "pipeline.log", "Step 4 failed with exit code 1\n")
    _write_json(d / "state.json", {"status": "failed", "completed_steps": 4, "total_steps": 6})
    for i in range(1, 5):
        tools = ["bwa_mem_align", "samtools_sort", "samtools_index", "gatk_haplotype_caller"]
        status = "completed" if i < 4 else "failed"
        _write_step_completion(d / f".step_{i}_completion.json", i, tools[i - 1], status)
    scenarios.append({
        "scenario_id": "failed_partial_run",
        "directory": "data/failed_partial_run",
        "expected_files": [
            {"path": "aligned.bam", "role": "intermediate", "format": "bam"},
            {"path": "sorted.bam", "role": "intermediate", "format": "bam"},
            {"path": "sorted.bam.bai", "role": "intermediate", "format": "bai"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": "state.json", "role": "metadata", "format": "json"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_3_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_4_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 8. empty_run: 0 steps completed
    d = data_dir / "empty_run"
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / "state.json", {"status": "failed", "completed_steps": 0, "total_steps": 6})
    scenarios.append({
        "scenario_id": "empty_run",
        "directory": "data/empty_run",
        "expected_files": [
            {"path": "state.json", "role": "metadata", "format": "json"},
        ],
    })

    # 9. resumed_run: checkpoint resume with duplicate intermediates
    d = data_dir / "resumed_run"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "aligned.bam", _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "aligned_v2.bam", _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "sorted.bam", _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "variants.vcf", _vcf_header([("chr1", 1000)]) + "\n")
    _write(d / "variants_filtered.vcf", _vcf_header([("chr1", 1000)]) + "\nchr1\t200\t.\tC\tT\t40\tPASS\tDP=20\n")
    _write(d / "pipeline.log", "Run 1 failed at step 3. Resumed. Run 2 completed.\n")
    _write_json(d / "state.json", {"status": "completed", "completed_steps": 6, "total_steps": 6, "resumed": True})
    for i in range(1, 7):
        _write_step_completion(d / f".step_{i}_completion.json", i, f"tool_{i}")
    scenarios.append({
        "scenario_id": "resumed_run",
        "directory": "data/resumed_run",
        "expected_files": [
            {"path": "aligned.bam", "role": "intermediate", "format": "bam"},
            {"path": "aligned_v2.bam", "role": "intermediate", "format": "bam"},
            {"path": "sorted.bam", "role": "intermediate", "format": "bam"},
            {"path": "variants.vcf", "role": "intermediate", "format": "vcf"},
            {"path": "variants_filtered.vcf", "role": "deliverable", "format": "vcf"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": "state.json", "role": "metadata", "format": "json"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_3_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_4_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_5_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_6_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 10. phylogenetics: MSA -> tree -> viz
    d = data_dir / "phylogenetics"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "aligned.fasta", ">seq1\nACGT\n>seq2\nACGG\n>seq3\nACGA\n")
    _write(d / "tree.nwk", "((seq1:0.1,seq2:0.2):0.3,seq3:0.4);\n")
    _write(d / "tree.svg", "<svg>tree visualization</svg>\n")
    _write(d / "model_info.txt", "Best model: GTR+G4\nBIC: 1234.56\n")
    _write(d / "pipeline.log", "Phylogenetics pipeline log\n")
    for i in range(1, 4):
        tools = ["mafft_align", "iqtree_infer", "plot_tree"]
        _write_step_completion(d / f".step_{i}_completion.json", i, tools[i - 1])
    scenarios.append({
        "scenario_id": "phylogenetics",
        "directory": "data/phylogenetics",
        "expected_files": [
            {"path": "aligned.fasta", "role": "intermediate", "format": "fasta"},
            {"path": "tree.nwk", "role": "deliverable", "format": "newick"},
            {"path": "tree.svg", "role": "deliverable", "format": "svg"},
            {"path": "model_info.txt", "role": "deliverable", "format": "text"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_3_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 11. annotation_pipeline: Predict -> annotate -> summarize
    d = data_dir / "annotation_pipeline"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "genes.gff", "##gff-version 3\nchr1\tprokka\tgene\t1\t500\t.\t+\t.\tID=gene1\n")
    _write(d / "annotation.gbk", "LOCUS       seq1 1000 bp DNA\nFEATURES\n  gene 1..500\n//\n")
    _write(d / "summary.tsv", "gene_id\tproduct\tlength\ngene1\tHypothetical protein\t500\n")
    _write(d / "stats.txt", "Total genes: 1\nCoding density: 50%\n")
    _write(d / "pipeline.log", "Annotation pipeline log\n")
    for i in range(1, 4):
        tools = ["prodigal_predict", "prokka_annotate", "summarize_annotation"]
        _write_step_completion(d / f".step_{i}_completion.json", i, tools[i - 1])
    scenarios.append({
        "scenario_id": "annotation_pipeline",
        "directory": "data/annotation_pipeline",
        "expected_files": [
            {"path": "genes.gff", "role": "deliverable", "format": "gff"},
            {"path": "annotation.gbk", "role": "deliverable", "format": "genbank"},
            {"path": "summary.tsv", "role": "deliverable", "format": "tsv"},
            {"path": "stats.txt", "role": "deliverable", "format": "text"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_2_completion.json", "role": "metadata", "format": "json"},
            {"path": ".step_3_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    # 12. mixed_deliverables: One step produces 4 files
    d = data_dir / "mixed_deliverables"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "sorted.bam", _sam_header([("chr1", 1000)]) + "\n")
    _write(d / "variants.vcf", _vcf_header([("chr1", 1000)]) + "\n")
    _write(d / "filtered.vcf", _vcf_header([("chr1", 1000)]) + "\nchr1\t100\t.\tA\tG\t40\tPASS\tDP=20\n")
    _write(d / "annotations.tsv", "variant\tgene\timpact\nchr1:100:A>G\tGENE1\tMISSENSE\n")
    _write(d / "clinical_report.txt", "Variant of interest: chr1:100 A>G (GENE1, missense)\n")
    _write(d / "pipeline.log", "Mixed deliverables pipeline log\n")
    _write_step_completion(d / ".step_1_completion.json", 1, "variant_annotation_pipeline")
    scenarios.append({
        "scenario_id": "mixed_deliverables",
        "directory": "data/mixed_deliverables",
        "expected_files": [
            {"path": "sorted.bam", "role": "intermediate", "format": "bam"},
            {"path": "variants.vcf", "role": "intermediate", "format": "vcf"},
            {"path": "filtered.vcf", "role": "deliverable", "format": "vcf"},
            {"path": "annotations.tsv", "role": "deliverable", "format": "tsv"},
            {"path": "clinical_report.txt", "role": "deliverable", "format": "text"},
            {"path": "pipeline.log", "role": "log", "format": "text"},
            {"path": ".step_1_completion.json", "role": "metadata", "format": "json"},
        ],
    })

    _write_json(task_dir / "scenarios.json", {"scenarios": scenarios})
    return scenarios


# ===================================================================
# Feature 4: Result Interpreter
# ===================================================================


def create_interpretation_scenarios(task_dir: Path) -> list[dict]:
    """Create 10 result files with ground-truth facts."""
    rng = random.Random(SEED + 3)
    data_dir = task_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    scenarios: list[dict] = []

    # 1. de_clear_signal: DE CSV with known top genes
    header = "gene,baseMean,log2FoldChange,pvalue,padj"
    rows = [header]
    top_genes_up = ["BRCA1", "TP53", "MYC", "KRAS", "EGFR"]
    top_genes_down = ["RB1", "APC", "PTEN", "VHL", "WT1"]
    for i, g in enumerate(top_genes_up):
        lfc = round(4.2 - i * 0.3, 2)
        rows.append(f"{g},{rng.randint(500, 2000)},{lfc},{1e-10:.2e},{1e-8:.2e}")
    for i, g in enumerate(top_genes_down):
        lfc = round(-3.8 + i * 0.2, 2)
        rows.append(f"{g},{rng.randint(500, 2000)},{lfc},{1e-9:.2e},{1e-7:.2e}")
    # Add 40 more significant genes
    for i in range(40):
        g = f"SIG_GENE_{i}"
        lfc = round(rng.uniform(-3, 3), 2)
        rows.append(f"{g},{rng.randint(100, 3000)},{lfc},{rng.uniform(1e-8, 0.01):.2e},{rng.uniform(1e-6, 0.04):.4f}")
    # Add 450 non-significant genes
    for i in range(450):
        g = f"NS_GENE_{i}"
        lfc = round(rng.uniform(-0.5, 0.5), 3)
        rows.append(f"{g},{rng.randint(10, 5000)},{lfc},{rng.uniform(0.1, 1.0):.4f},{rng.uniform(0.2, 1.0):.4f}")
    _write(data_dir / "de_clear_signal.csv", "\n".join(rows) + "\n")
    scenarios.append({
        "scenario_id": "de_clear_signal",
        "file": "data/de_clear_signal.csv",
        "result_type": "deseq2_csv",
        "required_facts": [
            {"pattern": r"\b50\b.*significant|significant.*\b50\b", "description": "mentions 50 significant genes"},
            {"pattern": r"BRCA1", "description": "mentions top gene BRCA1"},
            {"pattern": r"upregulated|up-regulated|overexpressed", "description": "mentions upregulation"},
            {"pattern": r"downregulated|down-regulated|underexpressed", "description": "mentions downregulation"},
            {"pattern": r"log2.*fold|fold.*change|log2FC", "description": "mentions fold change metric"},
        ],
        "required_numbers": [
            {"name": "significant_count", "value": 50, "tolerance": 0.05},
            {"name": "top_log2fc", "value": 4.2, "tolerance": 0.1},
        ],
        "forbidden_phrases": ["no significant", "no differentially expressed", "empty"],
        "min_words": 50,
        "max_words": 500,
    })

    # 2. de_no_signal: DE CSV with no significant genes
    rows = [header]
    for i in range(500):
        g = f"GENE_{i}"
        lfc = round(rng.uniform(-0.2, 0.2), 3)
        rows.append(f"{g},{rng.randint(10, 5000)},{lfc},{rng.uniform(0.3, 1.0):.4f},{rng.uniform(0.5, 1.0):.4f}")
    _write(data_dir / "de_no_signal.csv", "\n".join(rows) + "\n")
    scenarios.append({
        "scenario_id": "de_no_signal",
        "file": "data/de_no_signal.csv",
        "result_type": "deseq2_csv",
        "required_facts": [
            {"pattern": r"no significant|0 significant|no differentially expressed", "description": "states no significant genes"},
        ],
        "required_numbers": [
            {"name": "significant_count", "value": 0, "tolerance": 0.0},
        ],
        "forbidden_phrases": ["highly significant", "strongly differentially expressed"],
        "min_words": 20,
        "max_words": 300,
    })

    # 3. variant_summary: VCF with known distribution
    vcf_contigs = [("chr1", 50000)]
    hdr = _vcf_header(vcf_contigs, sample="patient1")
    var_lines = [hdr]
    snp_count = 0
    indel_count = 0
    for i in range(80):
        pos = 100 + i * 500
        ref_b = rng.choice(BASES)
        alt_b = rng.choice([b for b in BASES if b != ref_b])
        gq = rng.randint(30, 60)
        var_lines.append(_vcf_variant("chr1", pos, ref_b, alt_b, gq, "PASS", 25, gq=gq, sample=True))
        snp_count += 1
    for i in range(20):
        pos = 200 + i * 2000
        ref_b = _seq(rng, rng.randint(2, 5))
        alt_b = _seq(rng, 1)
        gq = rng.randint(25, 50)
        var_lines.append(_vcf_variant("chr1", pos, ref_b, alt_b, gq, "PASS", 20, gq=gq, sample=True))
        indel_count += 1
    _write(data_dir / "variant_summary.vcf", "\n".join(var_lines) + "\n")
    scenarios.append({
        "scenario_id": "variant_summary",
        "file": "data/variant_summary.vcf",
        "result_type": "vcf",
        "required_facts": [
            {"pattern": r"\b100\b.*variant|\bvariant.*\b100\b", "description": "mentions 100 variants"},
            {"pattern": r"SNP|SNV|single.nucleotide", "description": "mentions SNPs"},
            {"pattern": r"indel|insertion|deletion", "description": "mentions indels"},
        ],
        "required_numbers": [
            {"name": "total_variants", "value": 100, "tolerance": 0.05},
            {"name": "snp_count", "value": 80, "tolerance": 0.05},
            {"name": "indel_count", "value": 20, "tolerance": 0.05},
        ],
        "forbidden_phrases": ["no variants", "empty"],
        "min_words": 30,
        "max_words": 400,
    })

    # 4. metagenomics_report: Kraken2-format report
    kraken_report = textwrap.dedent("""\
        75.30\t7530\t7530\tU\t0\tunclassified
        24.70\t2470\t0\tR\t1\troot
        24.70\t2470\t0\tR1\t131567\t  cellular organisms
        24.70\t2470\t0\tD\t2\t    Bacteria
        15.20\t1520\t0\tP\t1224\t      Proteobacteria
        10.50\t1050\t800\tG\t561\t        Escherichia
         4.70\t470\t350\tG\t590\t        Salmonella
         5.30\t530\t0\tP\t1239\t      Firmicutes
         5.30\t530\t400\tG\t1578\t        Lactobacillus
         4.20\t420\t0\tP\t201174\t      Actinobacteria
         4.20\t420\t320\tG\t1716\t        Corynebacterium
    """)
    _write(data_dir / "metagenomics_report.txt", kraken_report)
    scenarios.append({
        "scenario_id": "metagenomics_report",
        "file": "data/metagenomics_report.txt",
        "result_type": "kraken2_report",
        "required_facts": [
            {"pattern": r"24\.7|24\.70|~25", "description": "mentions classification rate ~24.7%"},
            {"pattern": r"Escherichia", "description": "mentions top genus Escherichia"},
            {"pattern": r"Salmonella|Lactobacillus", "description": "mentions other genera"},
            {"pattern": r"unclassified|75", "description": "mentions high unclassified fraction"},
        ],
        "required_numbers": [
            {"name": "classification_rate", "value": 24.7, "tolerance": 0.05},
        ],
        "forbidden_phrases": ["fully classified", "100% classified"],
        "min_words": 30,
        "max_words": 400,
    })

    # 5. alignment_stats: samtools flagstat-format text
    flagstat_text = textwrap.dedent("""\
        50000 + 0 in total (QC-passed reads + QC-failed reads)
        2000 + 0 secondary
        0 + 0 supplementary
        1500 + 0 duplicates
        47500 + 0 mapped (95.00% : N/A)
        48000 + 0 paired in sequencing
        24000 + 0 read1
        24000 + 0 read2
        46000 + 0 properly paired (95.83% : N/A)
        47000 + 0 with itself and mate mapped
        500 + 0 singletons (1.04% : N/A)
        200 + 0 with mate mapped to a different chr
        100 + 0 with mate mapped to a different chr (mapQ>=5)
    """)
    _write(data_dir / "alignment_stats.txt", flagstat_text)
    scenarios.append({
        "scenario_id": "alignment_stats",
        "file": "data/alignment_stats.txt",
        "result_type": "flagstat",
        "required_facts": [
            {"pattern": r"95\.?0?0?\s*%.*map|map.*95\.?0?0?\s*%", "description": "mentions 95% mapping rate"},
            {"pattern": r"50.?000|50,000", "description": "mentions total reads"},
            {"pattern": r"duplicat", "description": "mentions duplicates"},
            {"pattern": r"pair", "description": "mentions paired reads"},
        ],
        "required_numbers": [
            {"name": "mapping_rate", "value": 95.0, "tolerance": 0.05},
            {"name": "duplicate_count", "value": 1500, "tolerance": 0.05},
        ],
        "forbidden_phrases": ["unmapped", "failed"],
        "min_words": 30,
        "max_words": 300,
    })

    # 6. phylo_tree: Newick file
    newick = "((((speciesA:0.1,speciesB:0.12):0.05,speciesC:0.15):0.08,speciesD:0.2):0.1,speciesE:0.25);\n"
    _write(data_dir / "phylo_tree.nwk", newick)
    scenarios.append({
        "scenario_id": "phylo_tree",
        "file": "data/phylo_tree.nwk",
        "result_type": "newick",
        "required_facts": [
            {"pattern": r"\b5\b.*taxa|taxa.*\b5\b", "description": "mentions 5 taxa"},
            {"pattern": r"speciesA|speciesB", "description": "mentions species names"},
            {"pattern": r"sister|clade|close", "description": "mentions phylogenetic relationships"},
        ],
        "required_numbers": [
            {"name": "taxa_count", "value": 5, "tolerance": 0.0},
        ],
        "forbidden_phrases": ["no tree", "empty"],
        "min_words": 20,
        "max_words": 300,
    })

    # 7. single_cell_clusters: Cluster CSV
    cluster_rows = ["cell,cluster"]
    cells_per_cluster = {0: 45, 1: 30, 2: 15}
    cell_idx = 0
    for cl, count in cells_per_cluster.items():
        for _ in range(count):
            cluster_rows.append(f"cell_{cell_idx},{cl}")
            cell_idx += 1
    _write(data_dir / "single_cell_clusters.csv", "\n".join(cluster_rows) + "\n")

    marker_rows = ["gene,cluster,pval_adj,log2fc"]
    markers = {
        0: [("CD3D", 0.001, 3.2), ("CD3E", 0.002, 2.8), ("IL7R", 0.005, 2.1)],
        1: [("CD19", 0.001, 4.1), ("MS4A1", 0.003, 3.5), ("CD79A", 0.01, 2.0)],
        2: [("CD14", 0.001, 3.8), ("LYZ", 0.002, 3.3), ("S100A8", 0.008, 2.5)],
    }
    for cl, gene_list in markers.items():
        for gene, pval, lfc in gene_list:
            marker_rows.append(f"{gene},{cl},{pval},{lfc}")
    _write(data_dir / "single_cell_markers.csv", "\n".join(marker_rows) + "\n")
    scenarios.append({
        "scenario_id": "single_cell_clusters",
        "file": "data/single_cell_clusters.csv",
        "extra_files": ["data/single_cell_markers.csv"],
        "result_type": "single_cell",
        "required_facts": [
            {"pattern": r"\b3\b.*cluster|cluster.*\b3\b", "description": "mentions 3 clusters"},
            {"pattern": r"\b90\b.*cell|cell.*\b90\b", "description": "mentions 90 cells"},
            {"pattern": r"CD3|CD19|CD14", "description": "mentions marker genes"},
        ],
        "required_numbers": [
            {"name": "cluster_count", "value": 3, "tolerance": 0.0},
            {"name": "total_cells", "value": 90, "tolerance": 0.0},
        ],
        "forbidden_phrases": ["no clusters", "failed clustering"],
        "min_words": 40,
        "max_words": 500,
    })

    # 8. pathway_enrichment: Enrichment CSV
    pathway_rows = ["pathway,pvalue,padj,genes_in_pathway,overlap_count"]
    pathways = [
        ("KEGG_CELL_CYCLE", 1e-8, 2e-7, 120, 15),
        ("KEGG_P53_SIGNALING", 2e-6, 3e-5, 70, 10),
        ("KEGG_APOPTOSIS", 5e-5, 4e-4, 85, 8),
        ("KEGG_MAPK_SIGNALING", 0.001, 0.008, 250, 12),
        ("KEGG_JAK_STAT_SIGNALING", 0.01, 0.06, 160, 7),
        ("KEGG_PPAR_SIGNALING", 0.05, 0.15, 70, 4),
        ("KEGG_GLYCOLYSIS", 0.1, 0.25, 65, 3),
        ("KEGG_TCA_CYCLE", 0.2, 0.4, 30, 2),
    ]
    for pw, pv, padj, size, overlap in pathways:
        pathway_rows.append(f"{pw},{pv:.2e},{padj:.2e},{size},{overlap}")
    _write(data_dir / "pathway_enrichment.csv", "\n".join(pathway_rows) + "\n")
    scenarios.append({
        "scenario_id": "pathway_enrichment",
        "file": "data/pathway_enrichment.csv",
        "result_type": "enrichment_csv",
        "required_facts": [
            {"pattern": r"CELL.CYCLE|cell.cycle", "description": "mentions top pathway cell cycle"},
            {"pattern": r"P53|p53", "description": "mentions p53 signaling"},
            {"pattern": r"significant.*pathw|pathw.*significant", "description": "mentions significant pathways"},
        ],
        "required_numbers": [
            {"name": "significant_pathways", "value": 4, "tolerance": 0.25},
        ],
        "forbidden_phrases": ["no enrichment", "no pathways"],
        "min_words": 30,
        "max_words": 400,
    })

    # 9. empty_result: Empty CSV
    _write(data_dir / "empty_result.csv", "gene,baseMean,log2FoldChange,pvalue,padj\n")
    scenarios.append({
        "scenario_id": "empty_result",
        "file": "data/empty_result.csv",
        "result_type": "deseq2_csv",
        "required_facts": [
            {"pattern": r"no result|empty|no data|0 gene", "description": "mentions empty results"},
        ],
        "required_numbers": [],
        "forbidden_phrases": ["significant genes found", "top gene"],
        "min_words": 10,
        "max_words": 200,
    })

    # 10. multi_output_pipeline: Multiple files to cross-reference
    # Reuse alignment_stats + a small VCF + DE summary
    multi_dir = data_dir / "multi_output"
    multi_dir.mkdir(parents=True, exist_ok=True)
    _write(multi_dir / "flagstat.txt", flagstat_text)
    small_vcf = _vcf_header([("chr1", 50000)], sample="patient1") + "\n"
    for i in range(25):
        pos = 100 + i * 1500
        ref_b = rng.choice(BASES)
        alt_b = rng.choice([b for b in BASES if b != ref_b])
        small_vcf += _vcf_variant("chr1", pos, ref_b, alt_b, 35, "PASS", 20, gq=35, sample=True) + "\n"
    _write(multi_dir / "variants.vcf", small_vcf)
    de_rows = [header]
    for i in range(100):
        g = f"GENE_{i}"
        lfc = round(rng.uniform(-3, 3), 2)
        padj = round(rng.uniform(0.001, 0.8), 4)
        de_rows.append(f"{g},{rng.randint(50, 3000)},{lfc},{rng.uniform(0.0001, 0.5):.4e},{padj}")
    _write(multi_dir / "de_results.csv", "\n".join(de_rows) + "\n")
    scenarios.append({
        "scenario_id": "multi_output_pipeline",
        "file": "data/multi_output",
        "result_type": "multi_file",
        "required_facts": [
            {"pattern": r"95.*%.*map|map.*95.*%", "description": "mentions mapping rate from flagstat"},
            {"pattern": r"25.*variant|variant.*25", "description": "mentions variant count from VCF"},
            {"pattern": r"differential|DE|fold.change", "description": "mentions DE analysis"},
        ],
        "required_numbers": [
            {"name": "mapping_rate", "value": 95.0, "tolerance": 0.05},
            {"name": "variant_count", "value": 25, "tolerance": 0.1},
        ],
        "forbidden_phrases": ["no data available"],
        "min_words": 50,
        "max_words": 600,
    })

    _write_json(task_dir / "scenarios.json", {"scenarios": scenarios})
    return scenarios


# ===================================================================
# Feature 5: Error Diagnosis
# ===================================================================


def create_error_diagnosis_scenarios(task_dir: Path) -> list[dict]:
    """Write scenarios.json with 25 error scenarios."""
    task_dir.mkdir(parents=True, exist_ok=True)

    scenarios = [
        # --- Out of memory (4) ---
        {
            "error_id": "oom_spades",
            "tool": "spades_assemble",
            "stderr": (
                "== Error == exception: std::bad_alloc\n"
                "ERR: not enough memory for the assembly\n"
                "Running SPAdes terminated with exit code 239"
            ),
            "root_cause": "out_of_memory",
            "fix": "Reduce k-mer size or use --memory flag to limit usage",
            "fix_keywords": ["memory", "k-mer", "reduce"],
        },
        {
            "error_id": "oom_star",
            "tool": "star_align",
            "stderr": (
                "EXITING because of FATAL ERROR: not enough memory for BAM sorting:\n"
                "SOLUTION: re-run STAR with at least --limitBAMsortRAM 31000000000\n"
                "Aborted (core dumped)"
            ),
            "root_cause": "out_of_memory",
            "fix": "Increase --limitBAMsortRAM or use --outBAMsortingBinsN",
            "fix_keywords": ["limitBAMsortRAM", "memory", "RAM"],
        },
        {
            "error_id": "oom_gatk",
            "tool": "gatk_haplotype_caller",
            "stderr": (
                "Exception in thread \"main\" java.lang.OutOfMemoryError: Java heap space\n"
                "    at java.base/java.util.Arrays.copyOf(Arrays.java:3512)\n"
                "    at org.broadinstitute.hellbender.engine.GATKTool.traverse(GATKTool.java:347)"
            ),
            "root_cause": "out_of_memory",
            "fix": "Increase Java heap with --java-options '-Xmx8g' or reduce interval size",
            "fix_keywords": ["Xmx", "heap", "java-options", "memory"],
        },
        {
            "error_id": "oom_r_deseq2",
            "tool": "deseq2_run",
            "stderr": (
                "Error: cannot allocate vector of size 2.4 Gb\n"
                "Execution halted\n"
                "In addition: Warning message:\n"
                "In DESeqDataSetFromMatrix(countData = cts, colData = coldata, design = ~condition) :\n"
                "  some variables in design formula are characters, converting to factors"
            ),
            "root_cause": "out_of_memory",
            "fix": "Reduce gene count by pre-filtering low-count genes or increase available RAM",
            "fix_keywords": ["filter", "memory", "pre-filter", "genes"],
        },
        # --- Missing dependency (4) ---
        {
            "error_id": "java_missing_gatk",
            "tool": "gatk_haplotype_caller",
            "stderr": "/bin/sh: java: command not found",
            "root_cause": "missing_dependency",
            "fix": "Add JVM to PATH: .pixi/envs/default/lib/jvm/bin/",
            "fix_keywords": ["java", "JVM", "PATH"],
        },
        {
            "error_id": "r_missing",
            "tool": "deseq2_run",
            "stderr": "/bin/sh: Rscript: command not found",
            "root_cause": "missing_dependency",
            "fix": "Install R via pixi or ensure Rscript is on PATH",
            "fix_keywords": ["Rscript", "install", "PATH"],
        },
        {
            "error_id": "python_module_missing",
            "tool": "sc_count_and_cluster",
            "stderr": (
                "Traceback (most recent call last):\n"
                "  File \"/path/to/sc_count_and_cluster.py\", line 5, in <module>\n"
                "    import scanpy as sc\n"
                "ModuleNotFoundError: No module named 'scanpy'"
            ),
            "root_cause": "missing_dependency",
            "fix": "Install scanpy: pip install scanpy or add to pixi environment",
            "fix_keywords": ["scanpy", "install", "pip"],
        },
        {
            "error_id": "shared_lib_missing",
            "tool": "samtools_sort",
            "stderr": (
                "samtools: error while loading shared libraries: libhts.so.3:\n"
                "cannot open shared object file: No such file or directory"
            ),
            "root_cause": "missing_dependency",
            "fix": "Set LD_LIBRARY_PATH to include htslib directory or reinstall samtools",
            "fix_keywords": ["LD_LIBRARY_PATH", "htslib", "shared library"],
        },
        # --- Missing index/reference (4) ---
        {
            "error_id": "bwa_missing_index",
            "tool": "bwa_mem_align",
            "stderr": "[E::bwa_idx_load_from_disk] fail to locate the index files",
            "root_cause": "missing_index",
            "fix": "Run bwa index on the reference FASTA first",
            "fix_keywords": ["bwa index", "reference", "index"],
        },
        {
            "error_id": "fasta_missing_fai",
            "tool": "samtools_view",
            "stderr": (
                "[E::fai_load3_core] Failed to open FAI index /data/reference.fa.fai: "
                "No such file or directory"
            ),
            "root_cause": "missing_index",
            "fix": "Run samtools faidx on the reference FASTA",
            "fix_keywords": ["samtools faidx", "index", ".fai"],
        },
        {
            "error_id": "bam_missing_bai",
            "tool": "samtools_view",
            "stderr": (
                "[E::hts_open_format] Failed to open \"sorted.bam.bai\": No such file or directory\n"
                "[E::hts_idx_load3] Could not load local index file"
            ),
            "root_cause": "missing_index",
            "fix": "Run samtools index on the BAM file",
            "fix_keywords": ["samtools index", ".bai"],
        },
        {
            "error_id": "star_missing_genome_dir",
            "tool": "star_align",
            "stderr": (
                "EXITING because of fatal INPUT FILE error: could not open genomic FASTA file:\n"
                "/data/star_index/Genome\n"
                "SOLUTION: check that the path to the genome files specified in --genomeDir exists"
            ),
            "root_cause": "missing_index",
            "fix": "Run STAR --runMode genomeGenerate first to build the genome index",
            "fix_keywords": ["genomeGenerate", "STAR", "genome index", "genomeDir"],
        },
        # --- Corrupt input (4) ---
        {
            "error_id": "truncated_bam",
            "tool": "samtools_sort",
            "stderr": (
                "[E::bgzf_read_block] Invalid BGZF header at offset 324567\n"
                "[main_samview] truncated file."
            ),
            "root_cause": "corrupt_input",
            "fix": "Input BAM is truncated - re-run the alignment step",
            "fix_keywords": ["truncated", "re-run", "alignment"],
        },
        {
            "error_id": "malformed_vcf",
            "tool": "bcftools_filter",
            "stderr": (
                "[E::vcf_parse_format] FORMAT 'GT' at chr1:1234 .. expected Integer or Float value\n"
                "Error: wrong encoding of 'GT' FORMAT field at chr1:1234\n"
                "[E::bcf_read] Parse error at chr1:1234"
            ),
            "root_cause": "corrupt_input",
            "fix": "VCF has malformed FORMAT field at chr1:1234 - regenerate VCF from BAM",
            "fix_keywords": ["FORMAT", "malformed", "regenerate"],
        },
        {
            "error_id": "broken_fastq",
            "tool": "fastp_trim",
            "stderr": (
                "ERROR: the quality string length (80) is not equal to sequence length (150)\n"
                "Failed to read the FASTQ file at read: read_42\n"
                "fastp v0.23.4 failed with exit code 1"
            ),
            "root_cause": "corrupt_input",
            "fix": "FASTQ quality string length mismatch at read_42 - input file may be truncated or corrupt",
            "fix_keywords": ["quality string", "length mismatch", "truncated"],
        },
        {
            "error_id": "empty_input_file",
            "tool": "bwa_mem_align",
            "stderr": (
                "[E::bseq_read] the input file is empty.\n"
                "[mem_process_seqs] 0 sequences have been processed.\n"
                "[main] Real time: 0.001 sec; CPU: 0.001 sec"
            ),
            "root_cause": "corrupt_input",
            "fix": "Input FASTQ file is empty - check upstream step or data transfer",
            "fix_keywords": ["empty", "input", "upstream"],
        },
        # --- Incompatible parameters (4) ---
        {
            "error_id": "spades_careful_isolate",
            "tool": "spades_assemble",
            "stderr": "== Error ==  you cannot specify --careful and --isolate simultaneously",
            "root_cause": "incompatible_parameters",
            "fix": "Remove --isolate flag; --careful is preferred",
            "fix_keywords": ["careful", "isolate", "remove"],
        },
        {
            "error_id": "snpeff_codon_table",
            "tool": "snpeff_annotate",
            "stderr": (
                "ERROR_MISSING_CODON_TABLE\t"
                "Cannot find codon table 'Bacterial_and_Plant_Plastid'"
            ),
            "root_cause": "incompatible_parameters",
            "fix": "Use empty string for codon table with SnpEff 5.3a",
            "fix_keywords": ["codon table", "empty string", "SnpEff"],
        },
        {
            "error_id": "star_incompatible_params",
            "tool": "star_align",
            "stderr": (
                "EXITING because of FATAL PARAMETER ERROR: --sjdbGTFfile and "
                "--sjdbFileChrStartEnd cannot both be specified.\n"
                "SOLUTION: use only one of the two options."
            ),
            "root_cause": "incompatible_parameters",
            "fix": "Use either --sjdbGTFfile or --sjdbFileChrStartEnd, not both",
            "fix_keywords": ["sjdbGTFfile", "sjdbFileChrStartEnd", "one of"],
        },
        {
            "error_id": "featurecounts_strand",
            "tool": "featurecounts_count",
            "stderr": (
                "WARNING: Paired-end reads are included.\n"
                "WARNING: Reads are not counted.\n"
                "Total alignments : 5000000\n"
                "Successfully assigned alignments : 0 (0.0%)\n"
                "Running Subread featureCounts v2.0.6\n"
            ),
            "root_cause": "incompatible_parameters",
            "fix": "Wrong strandedness setting - try -s 0 (unstranded), -s 1 (forward), or -s 2 (reverse)",
            "fix_keywords": ["strandedness", "-s", "unstranded"],
        },
        # --- Permission/filesystem (3) ---
        {
            "error_id": "readonly_output_dir",
            "tool": "samtools_sort",
            "stderr": (
                "[E::hts_open_format] Failed to open \"/readonly/sorted.bam\" for writing: "
                "Permission denied\n"
                "samtools sort: failed to create \"/readonly/sorted.bam\": Permission denied"
            ),
            "root_cause": "permission_filesystem",
            "fix": "Output directory is read-only - change permissions or use a different output path",
            "fix_keywords": ["permission", "read-only", "output path"],
        },
        {
            "error_id": "disk_full",
            "tool": "star_align",
            "stderr": (
                "EXITING because of OUTPUT FILE error: could not create output file\n"
                "/data/output/Aligned.out.sam\n"
                "errno: 28 (No space left on device)\n"
                "SOLUTION: check that the disk is not full"
            ),
            "root_cause": "permission_filesystem",
            "fix": "Disk is full - free space or change output directory to a volume with more space",
            "fix_keywords": ["disk", "full", "space", "free"],
        },
        {
            "error_id": "path_too_long",
            "tool": "bwa_mem_align",
            "stderr": (
                "[E::main] fail to open file '/very/deep/nested/directory/structure/"
                "that/exceeds/the/maximum/allowed/path/length/on/this/filesystem/"
                "reference.fa'. No such file or directory"
            ),
            "root_cause": "permission_filesystem",
            "fix": "File path is too long - shorten directory names or move files closer to root",
            "fix_keywords": ["path", "long", "shorten"],
        },
        # --- Novel/unknown (2) ---
        {
            "error_id": "novel_segfault",
            "tool": "minimap2_align",
            "stderr": (
                "minimap2: /home/user/minimap2-2.26/ksw2_dispatch.c:42: "
                "ksw_extd2_sse41: Assertion `googly > 0' failed.\n"
                "Aborted (core dumped)"
            ),
            "root_cause": "novel_unknown",
            "fix": "Unknown assertion failure in minimap2 - try updating to latest version or using different preset",
            "fix_keywords": ["assertion", "update", "version"],
        },
        {
            "error_id": "novel_cryptic_r_error",
            "tool": "deseq2_run",
            "stderr": (
                "Error in h(simpleError(msg, call)) :\n"
                "  error in evaluating the argument 'x' in selecting a method for "
                "function 'counts': argument \"type\" is missing, with no default\n"
                "Calls: results -> <Anonymous> -> .local -> counts\n"
                "Execution halted"
            ),
            "root_cause": "novel_unknown",
            "fix": "Cryptic R error in DESeq2 - may indicate version incompatibility or missing function argument",
            "fix_keywords": ["R", "version", "argument", "missing"],
        },
    ]

    _write_json(task_dir / "scenarios.json", {"scenarios": scenarios})
    return scenarios


# ===================================================================
# Feature 6: Quality Compare
# ===================================================================


def _mock_result_json(
    status: str = "completed",
    repair_count: int = 0,
    outputs: list[dict] | None = None,
    metrics: dict | None = None,
    elapsed_seconds: float = 120.0,
    steps_completed: int = 6,
    steps_total: int = 6,
) -> dict:
    return {
        "status": status,
        "auto_repair_history_count": repair_count,
        "outputs": outputs or [],
        "quality_metrics": metrics or {},
        "elapsed_seconds": elapsed_seconds,
        "steps_completed": steps_completed,
        "steps_total": steps_total,
    }


def create_quality_compare_scenarios(task_dir: Path) -> list[dict]:
    """Create 15 pairs of mock result.json files."""
    rng = random.Random(SEED + 5)
    data_dir = task_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    scenarios: list[dict] = []

    def _write_pair(name: str, run_a: dict, run_b: dict) -> None:
        d = data_dir / name
        (d / "run_a").mkdir(parents=True, exist_ok=True)
        (d / "run_b").mkdir(parents=True, exist_ok=True)
        _write_json(d / "run_a" / "result.json", run_a)
        _write_json(d / "run_b" / "result.json", run_b)

    # 1. clear_improvement
    _write_pair("clear_improvement",
        _mock_result_json("completed", 2, metrics={"mapping_rate": 0.60, "variant_count": 50}),
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.95, "variant_count": 150}))
    scenarios.append({
        "scenario_id": "clear_improvement",
        "directory": "data/clear_improvement",
        "expected_verdict": "IMPROVED",
        "expected_dimensions": {"mapping_rate": "improved", "variant_count": "improved", "repairs": "improved"},
    })

    # 2. clear_regression
    _write_pair("clear_regression",
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.95, "variant_count": 150}),
        _mock_result_json("completed", 3, metrics={"mapping_rate": 0.30, "variant_count": 20}))
    scenarios.append({
        "scenario_id": "clear_regression",
        "directory": "data/clear_regression",
        "expected_verdict": "REGRESSED",
        "expected_dimensions": {"mapping_rate": "regressed", "variant_count": "regressed", "repairs": "regressed"},
    })

    # 3. stable_good
    _write_pair("stable_good",
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.94, "variant_count": 145}),
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.95, "variant_count": 148}))
    scenarios.append({
        "scenario_id": "stable_good",
        "directory": "data/stable_good",
        "expected_verdict": "STABLE",
        "expected_dimensions": {"mapping_rate": "stable", "variant_count": "stable"},
    })

    # 4. stable_bad
    _write_pair("stable_bad",
        _mock_result_json("completed", 3, metrics={"mapping_rate": 0.05, "variant_count": 2}),
        _mock_result_json("completed", 4, metrics={"mapping_rate": 0.04, "variant_count": 1}))
    scenarios.append({
        "scenario_id": "stable_bad",
        "directory": "data/stable_bad",
        "expected_verdict": "STABLE",
        "expected_dimensions": {"mapping_rate": "stable", "variant_count": "stable"},
    })

    # 5. mixed_signals
    _write_pair("mixed_signals",
        _mock_result_json("completed", 1, metrics={"mapping_rate": 0.70, "variant_count": 200}),
        _mock_result_json("completed", 1, metrics={"mapping_rate": 0.95, "variant_count": 50}))
    scenarios.append({
        "scenario_id": "mixed_signals",
        "directory": "data/mixed_signals",
        "expected_verdict": "MIXED",
        "expected_dimensions": {"mapping_rate": "improved", "variant_count": "regressed"},
    })

    # 6. new_outputs_added
    outputs_a = [{"path": "aligned.bam"}, {"path": "sorted.bam"}, {"path": "variants.vcf"}]
    outputs_b = outputs_a + [{"path": "filtered.vcf"}, {"path": "annotated.vcf"}]
    _write_pair("new_outputs_added",
        _mock_result_json("completed", 0, outputs=outputs_a, metrics={"mapping_rate": 0.90}),
        _mock_result_json("completed", 0, outputs=outputs_b, metrics={"mapping_rate": 0.91}))
    scenarios.append({
        "scenario_id": "new_outputs_added",
        "directory": "data/new_outputs_added",
        "expected_verdict": "IMPROVED",
        "expected_dimensions": {"output_count": "improved"},
    })

    # 7. outputs_lost
    _write_pair("outputs_lost",
        _mock_result_json("completed", 0, outputs=outputs_b, metrics={"mapping_rate": 0.91}),
        _mock_result_json("completed", 0, outputs=outputs_a, metrics={"mapping_rate": 0.90}))
    scenarios.append({
        "scenario_id": "outputs_lost",
        "directory": "data/outputs_lost",
        "expected_verdict": "REGRESSED",
        "expected_dimensions": {"output_count": "regressed"},
    })

    # 8. different_tools
    _write_pair("different_tools",
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.92, "tools_used": ["bwa_mem_align", "gatk_haplotype_caller"]}),
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.93, "tools_used": ["minimap2_align", "deepvariant_call"]}))
    scenarios.append({
        "scenario_id": "different_tools",
        "directory": "data/different_tools",
        "expected_verdict": "DIFFERENT_PIPELINE",
        "expected_dimensions": {"tools": "different"},
    })

    # 9. failed_vs_success
    _write_pair("failed_vs_success",
        _mock_result_json("failed", 3, metrics={}, steps_completed=2, steps_total=6),
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.95}))
    scenarios.append({
        "scenario_id": "failed_vs_success",
        "directory": "data/failed_vs_success",
        "expected_verdict": "IMPROVED",
        "expected_dimensions": {"status": "improved"},
    })

    # 10. success_vs_failed
    _write_pair("success_vs_failed",
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.95}),
        _mock_result_json("failed", 2, metrics={}, steps_completed=3, steps_total=6))
    scenarios.append({
        "scenario_id": "success_vs_failed",
        "directory": "data/success_vs_failed",
        "expected_verdict": "REGRESSED",
        "expected_dimensions": {"status": "regressed"},
    })

    # 11. partial_vs_complete
    _write_pair("partial_vs_complete",
        _mock_result_json("failed", 1, metrics={"mapping_rate": 0.80}, steps_completed=3, steps_total=6),
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.92}))
    scenarios.append({
        "scenario_id": "partial_vs_complete",
        "directory": "data/partial_vs_complete",
        "expected_verdict": "IMPROVED",
        "expected_dimensions": {"status": "improved", "steps": "improved"},
    })

    # 12. more_repairs_same_result
    _write_pair("more_repairs_same_result",
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.94}),
        _mock_result_json("completed", 3, metrics={"mapping_rate": 0.93}))
    scenarios.append({
        "scenario_id": "more_repairs_same_result",
        "directory": "data/more_repairs_same_result",
        "expected_verdict": "REGRESSED",
        "expected_dimensions": {"repairs": "regressed"},
    })

    # 13. faster_same_quality
    _write_pair("faster_same_quality",
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.95}, elapsed_seconds=120.0),
        _mock_result_json("completed", 0, metrics={"mapping_rate": 0.94}, elapsed_seconds=45.0))
    scenarios.append({
        "scenario_id": "faster_same_quality",
        "directory": "data/faster_same_quality",
        "expected_verdict": "IMPROVED",
        "expected_dimensions": {"elapsed_time": "improved"},
    })

    # 14. empty_run_a
    _write_pair("empty_run_a",
        _mock_result_json("failed", 0, outputs=[], metrics={}, steps_completed=0, steps_total=6),
        _mock_result_json("completed", 0, outputs=[{"path": "out1"}, {"path": "out2"}], metrics={"mapping_rate": 0.85}))
    scenarios.append({
        "scenario_id": "empty_run_a",
        "directory": "data/empty_run_a",
        "expected_verdict": "IMPROVED",
        "expected_dimensions": {"status": "improved", "output_count": "improved"},
    })

    # 15. both_empty
    _write_pair("both_empty",
        _mock_result_json("failed", 0, outputs=[], metrics={}, steps_completed=0, steps_total=6),
        _mock_result_json("failed", 0, outputs=[], metrics={}, steps_completed=0, steps_total=6))
    scenarios.append({
        "scenario_id": "both_empty",
        "directory": "data/both_empty",
        "expected_verdict": "STABLE",
        "expected_dimensions": {},
    })

    _write_json(task_dir / "scenarios.json", {"scenarios": scenarios})
    return scenarios


# ===================================================================
# Feature 7: Literature Agent
# ===================================================================


def create_literature_scenarios(task_dir: Path) -> list[dict]:
    """Write scenarios.json with 12 offline scenarios with canned abstracts."""
    rng = random.Random(SEED + 6)
    task_dir.mkdir(parents=True, exist_ok=True)

    def _fake_pmid() -> str:
        return str(rng.randint(20000000, 39999999))

    scenarios = [
        # 1. deseq2_vs_edger_small_n
        {
            "question_id": "deseq2_vs_edger_small_n",
            "question": "For RNA-seq with only 2 replicates per condition, should I use DESeq2 or edgeR?",
            "analysis_type": "rna_seq_differential_expression",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Comparison of differential expression methods for RNA-seq with small sample sizes",
                    "abstract": (
                        "We evaluated DESeq2 and edgeR performance on RNA-seq datasets with 2-3 replicates "
                        "per condition. Our simulation study shows that edgeR maintains better control of "
                        "false discovery rate with very small sample sizes (n=2), while DESeq2 tends to be "
                        "more conservative but may lose sensitivity. Both methods showed improved performance "
                        "with 3 or more replicates. We recommend edgeR for experiments with only 2 replicates "
                        "due to its robust empirical Bayes estimation of dispersion."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "RNA-seq differential expression: best practices and recommendations",
                    "abstract": (
                        "This review covers current best practices for RNA-seq differential expression analysis. "
                        "For experiments with limited biological replication (n<3), we note that statistical power "
                        "is inherently limited. edgeR's quasi-likelihood framework provides slightly better FDR "
                        "control in low-replicate settings compared to DESeq2's Wald test. However, both tools "
                        "perform similarly with adequate replication (n>=3)."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Single-cell RNA sequencing reveals heterogeneity in tumor microenvironment",
                    "abstract": (
                        "We performed single-cell RNA sequencing on 15 tumor samples to characterize the immune "
                        "cell composition of the tumor microenvironment. Using Seurat clustering, we identified "
                        "12 distinct cell populations including T cells, B cells, and myeloid subsets."
                    ),
                    "year": 2021,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Statistical power in RNA-seq experiments with low biological replication",
                    "abstract": (
                        "We conducted a power analysis for RNA-seq experiments focusing on designs with 2-4 "
                        "biological replicates. Our results demonstrate that experiments with only 2 replicates "
                        "per condition can detect only large effect sizes (log2FC > 2). We recommend a minimum "
                        "of 3 replicates and discuss strategies for maximizing power with limited samples."
                    ),
                    "year": 2018,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Long-read sequencing improves genome assembly of novel bacterial species",
                    "abstract": (
                        "We assembled 5 novel bacterial genomes using Oxford Nanopore long reads combined with "
                        "Illumina short reads for polishing. The hybrid assemblies achieved N50 values of 2-5 Mb."
                    ),
                    "year": 2022,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "edgeR robust: improved differential expression with small sample sizes",
                    "abstract": (
                        "We present edgeR-robust, an extension of the edgeR framework that provides improved "
                        "FDR control for experiments with small sample sizes. Using a robust dispersion estimation "
                        "approach, edgeR-robust outperforms standard edgeR and DESeq2 when n=2 per group. Our "
                        "benchmarks on 100 simulated datasets show 15% improvement in true positive rate."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"edgeR|edge-R", "description": "should mention edgeR"},
                {"pattern": r"DESeq2|deseq2", "description": "should mention DESeq2"},
                {"pattern": r"replicate|sample.size|low.power", "description": "should discuss sample size"},
            ],
            "expected_tool_preference": "edgeR",
            "forbidden_claims": ["always use DESeq2", "no difference between tools"],
        },
        # 2. star_vs_hisat2_speed
        {
            "question_id": "star_vs_hisat2_speed",
            "question": "Which RNA-seq aligner is faster for human genome, STAR or HISAT2?",
            "analysis_type": "rna_seq_differential_expression",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Benchmarking RNA-seq aligners: STAR vs HISAT2 performance comparison",
                    "abstract": (
                        "We benchmarked STAR and HISAT2 on human RNA-seq datasets of varying sizes. HISAT2 "
                        "consistently used 4-8 GB RAM compared to STAR's 30+ GB requirement. In terms of speed, "
                        "HISAT2 was 1.5-2x faster for single-end reads. STAR achieved slightly higher mapping "
                        "rates (0.5-1% improvement) particularly for reads spanning multiple exons."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "STAR aligner update: improved accuracy and 2-pass alignment mode",
                    "abstract": (
                        "The latest version of STAR introduces a 2-pass alignment mode that improves splice "
                        "junction detection sensitivity by 5-10%. While this mode increases runtime by approximately "
                        "40%, it provides superior accuracy for novel transcript discovery. STAR remains the "
                        "preferred choice for applications requiring maximum sensitivity."
                    ),
                    "year": 2021,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Memory-efficient RNA-seq alignment with HISAT2",
                    "abstract": (
                        "HISAT2 uses a novel graph FM index that reduces memory consumption to under 8 GB for "
                        "the human genome, making it suitable for standard workstations. We demonstrate that "
                        "HISAT2 achieves comparable accuracy to STAR while requiring 4x less RAM and completing "
                        "alignment 30-50% faster in most scenarios."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Gut microbiome diversity in inflammatory bowel disease patients",
                    "abstract": (
                        "We analyzed the gut microbiome of 200 IBD patients using 16S rRNA sequencing and "
                        "shotgun metagenomics. Reduced bacterial diversity was observed in active disease."
                    ),
                    "year": 2022,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "HISAT2 and StringTie pipeline for transcript assembly",
                    "abstract": (
                        "We present an optimized pipeline combining HISAT2 for alignment and StringTie for "
                        "transcript assembly. Our benchmarks show this pipeline processes 100M paired-end reads "
                        "in under 45 minutes using 8 threads and 8 GB RAM."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"HISAT2|hisat2", "description": "should mention HISAT2"},
                {"pattern": r"STAR|star", "description": "should mention STAR"},
                {"pattern": r"memory|RAM|faster|speed", "description": "should discuss resource usage"},
            ],
            "expected_tool_preference": "HISAT2",
            "forbidden_claims": ["STAR is always faster"],
        },
        # 3. kmer_quant_accuracy
        {
            "question_id": "kmer_quant_accuracy",
            "question": "How accurate is Salmon kmer-based quantification compared to alignment-based counting?",
            "analysis_type": "transcript_quantification",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Salmon provides fast and bias-aware quantification of transcript expression",
                    "abstract": (
                        "We benchmarked Salmon against STAR+featureCounts on simulated and real RNA-seq data. "
                        "Salmon achieved Spearman correlation >0.95 with true transcript abundances while "
                        "completing quantification 20x faster. Salmon's GC bias correction further improved "
                        "accuracy for transcripts with extreme GC content."
                    ),
                    "year": 2017,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Transcript-level estimates improve gene-level inferences",
                    "abstract": (
                        "We show that aggregating transcript-level estimates from Salmon or Kallisto to the "
                        "gene level provides more accurate gene expression quantification than direct gene-level "
                        "counting with featureCounts. The improvement is especially notable for genes with "
                        "multiple isoforms."
                    ),
                    "year": 2015,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "CRISPR screen analysis methods for identifying essential genes",
                    "abstract": (
                        "We compared MAGeCK, BAGEL2, and JACKS for analyzing CRISPR knockout screen data. "
                        "MAGeCK showed the best overall performance for identifying essential genes."
                    ),
                    "year": 2021,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Systematic evaluation of RNA-seq quantification pipelines",
                    "abstract": (
                        "We evaluated 8 RNA-seq quantification pipelines including STAR+HTSeq, STAR+featureCounts, "
                        "Salmon, Kallisto, and RSEM. Pseudo-alignment tools (Salmon, Kallisto) showed accuracy "
                        "comparable to alignment-based methods while being 10-50x faster. Kallisto and Salmon "
                        "produced nearly identical results (r>0.99)."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Impact of multi-mapping reads on RNA-seq quantification accuracy",
                    "abstract": (
                        "Multi-mapping reads present a challenge for all quantification methods. We show that "
                        "Salmon's expectation-maximization approach handles multi-mappers more accurately than "
                        "simple unique-count methods, improving quantification of paralogous gene families."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"Salmon|salmon", "description": "should mention Salmon"},
                {"pattern": r"accurate|correlation|comparable", "description": "should discuss accuracy"},
                {"pattern": r"fast|speed|quicker", "description": "should mention speed advantage"},
            ],
            "expected_tool_preference": "Salmon",
            "forbidden_claims": ["alignment-based is always better"],
        },
        # 4. fdr_threshold_rna_seq
        {
            "question_id": "fdr_threshold_rna_seq",
            "question": "Should I use FDR threshold of 0.05 or 0.1 for RNA-seq differential expression?",
            "analysis_type": "rna_seq_differential_expression",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Choosing significance thresholds for RNA-seq experiments",
                    "abstract": (
                        "The choice of FDR threshold depends on the experimental goals. For exploratory studies "
                        "where follow-up validation is planned, FDR 0.1 is commonly accepted and recommended to "
                        "maximize discovery. For definitive studies or clinical applications, FDR 0.05 is standard. "
                        "We recommend considering both thresholds and reporting results at multiple cutoffs."
                    ),
                    "year": 2018,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Multiple testing correction in genomics: FDR and beyond",
                    "abstract": (
                        "The Benjamini-Hochberg FDR procedure is the standard for multiple testing correction in "
                        "genomics. FDR 0.05 means accepting that 5% of reported discoveries may be false positives. "
                        "In RNA-seq, FDR 0.1 is widely used as a balance between discovery and false positive control."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Proteomic analysis of heat shock response in yeast",
                    "abstract": (
                        "Using mass spectrometry-based proteomics, we identified 150 differentially abundant "
                        "proteins during heat shock in Saccharomyces cerevisiae."
                    ),
                    "year": 2020,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "DESeq2 vignette recommendations for FDR thresholds",
                    "abstract": (
                        "The DESeq2 vignette recommends an adjusted p-value cutoff of 0.1 as the default for "
                        "identifying differentially expressed genes. This threshold provides a good balance "
                        "between sensitivity and specificity for most RNA-seq experiments."
                    ),
                    "year": 2014,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Statistical considerations for RNA-seq experiments",
                    "abstract": (
                        "We provide guidelines for RNA-seq experimental design and analysis. For FDR thresholds, "
                        "we recommend 0.05 for hypothesis-driven studies and 0.1 for exploratory analyses. The "
                        "choice should be made before data analysis and reported transparently."
                    ),
                    "year": 2021,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"0\.05|0\.1", "description": "should mention specific thresholds"},
                {"pattern": r"explorator|discover", "description": "should discuss exploratory vs confirmatory"},
            ],
            "expected_tool_preference": None,
            "forbidden_claims": ["always use 0.05", "always use 0.1"],
        },
        # 5. variant_caller_germline
        {
            "question_id": "variant_caller_germline",
            "question": "For germline variant calling from whole genome sequencing, GATK HaplotypeCaller or DeepVariant?",
            "analysis_type": "germline_variant_calling",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "DeepVariant achieves state-of-the-art accuracy for germline variant calling",
                    "abstract": (
                        "DeepVariant uses deep neural networks for variant calling and achieves the highest "
                        "accuracy in the PrecisionFDA Truth Challenge for both SNPs and indels. Compared to "
                        "GATK HaplotypeCaller, DeepVariant showed F1 improvements of 0.5-2% across different "
                        "genome regions, with the largest improvements in difficult-to-map regions."
                    ),
                    "year": 2022,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "GATK best practices for germline variant discovery",
                    "abstract": (
                        "The GATK best practices pipeline for germline variant calling includes HaplotypeCaller "
                        "with joint genotyping via GenomicsDB. This approach is well-validated, widely used, and "
                        "supports multi-sample calling natively. GATK provides extensive quality metrics and "
                        "filtering recommendations through VQSR."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Comparative analysis of somatic mutation callers for tumor sequencing",
                    "abstract": (
                        "We benchmarked Mutect2, Strelka2, and VarScan2 for somatic mutation detection. "
                        "Mutect2 showed the highest sensitivity but lower specificity for low-frequency variants."
                    ),
                    "year": 2021,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Benchmarking germline variant callers on GIAB reference samples",
                    "abstract": (
                        "Using GIAB HG001-HG007 truth sets, we benchmarked GATK4, DeepVariant, and Strelka2. "
                        "DeepVariant achieved the highest overall F1 score (0.9985 for SNPs), followed by GATK "
                        "(0.9978) and Strelka2 (0.9972). For indels, DeepVariant led with F1=0.9935 compared "
                        "to GATK's 0.9890. Runtime was comparable when using GPUs for DeepVariant."
                    ),
                    "year": 2023,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Population-scale genomics using joint genotyping with GATK",
                    "abstract": (
                        "For population-scale studies with hundreds to thousands of samples, GATK's joint "
                        "genotyping via GenomicsDB remains the most practical approach. DeepVariant's single-sample "
                        "calling mode requires additional steps for joint analysis."
                    ),
                    "year": 2022,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"DeepVariant|deep.variant", "description": "should mention DeepVariant"},
                {"pattern": r"GATK|HaplotypeCaller", "description": "should mention GATK"},
                {"pattern": r"accuracy|F1|precision", "description": "should discuss accuracy metrics"},
            ],
            "expected_tool_preference": "DeepVariant",
            "forbidden_claims": ["GATK is obsolete"],
        },
        # 6. metagenomic_assembler
        {
            "question_id": "metagenomic_assembler",
            "question": "For metagenomic assembly, should I use metaSPAdes or MEGAHIT?",
            "analysis_type": "metagenomics_classification",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Comparison of metagenomic assemblers on complex microbial communities",
                    "abstract": (
                        "We benchmarked metaSPAdes, MEGAHIT, and IDBA-UD on simulated and real metagenome datasets. "
                        "metaSPAdes produced longer contigs and higher N50 values but required 10x more memory "
                        "and 3x more runtime than MEGAHIT. For datasets larger than 50 GB, MEGAHIT was the only "
                        "assembler that completed within 24 hours on standard hardware."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "MEGAHIT: memory-efficient assembly for large metagenomes",
                    "abstract": (
                        "MEGAHIT uses succinct de Bruijn graphs to achieve ultra-low memory footprint for "
                        "metagenomic assembly. We show it handles datasets up to 500 GB using under 32 GB RAM. "
                        "Assembly quality is comparable to metaSPAdes for most community structures."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Metabolomic profiling of soil microbial communities",
                    "abstract": (
                        "Using untargeted metabolomics, we characterized the metabolic activity of soil microbes "
                        "across four land use types."
                    ),
                    "year": 2021,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Assembly quality assessment for metagenomes using reference-free metrics",
                    "abstract": (
                        "We propose reference-free metrics for evaluating metagenomic assemblies including "
                        "completeness of bins, contamination rates, and N50. In our benchmarks, metaSPAdes "
                        "produced more complete bins but MEGAHIT assemblies had lower contamination rates."
                    ),
                    "year": 2021,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Practical guidelines for metagenomic assembly",
                    "abstract": (
                        "For medium-complexity communities (10-100 species), metaSPAdes is recommended if "
                        "sufficient RAM (64+ GB) is available. For high-complexity or very large datasets, "
                        "MEGAHIT provides the best balance of quality and resource requirements."
                    ),
                    "year": 2022,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"metaSPAdes|metaspades", "description": "should mention metaSPAdes"},
                {"pattern": r"MEGAHIT|megahit", "description": "should mention MEGAHIT"},
                {"pattern": r"memory|RAM|resource", "description": "should discuss resource requirements"},
            ],
            "expected_tool_preference": None,
            "forbidden_claims": ["always use metaSPAdes"],
        },
        # 7. single_cell_normalization
        {
            "question_id": "single_cell_normalization",
            "question": "For scRNA-seq, should I use SCTransform or standard log-normalization?",
            "analysis_type": "single_cell_rna_seq",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "SCTransform: variance stabilizing transformations for scRNA-seq",
                    "abstract": (
                        "SCTransform uses regularized negative binomial regression to normalize scRNA-seq data. "
                        "Compared to standard log-normalization, SCTransform better handles the mean-variance "
                        "relationship and reduces the influence of technical variation. We recommend SCTransform "
                        "for datasets with variable sequencing depth across cells."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Benchmarking single-cell normalization methods",
                    "abstract": (
                        "We compared 7 normalization methods for scRNA-seq including log-normalization, scran, "
                        "SCTransform, and sctransform v2. SCTransform and scran showed the best performance "
                        "for downstream clustering. Standard log-normalization was adequate for simple datasets "
                        "but struggled with batch effects."
                    ),
                    "year": 2021,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Spatial transcriptomics reveals tissue architecture at single-cell resolution",
                    "abstract": (
                        "We applied 10x Visium to map gene expression in mouse brain tissue sections. "
                        "Our analysis identified 15 spatially distinct domains corresponding to known brain regions."
                    ),
                    "year": 2022,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Best practices for scRNA-seq data analysis",
                    "abstract": (
                        "This tutorial covers the complete scRNA-seq analysis workflow. For normalization, we "
                        "recommend SCTransform for Seurat-based workflows and scran for Scanpy-based workflows. "
                        "Standard log-normalization remains acceptable for quick exploratory analyses."
                    ),
                    "year": 2023,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "SCTransform v2 improves accuracy of single-cell variance stabilization",
                    "abstract": (
                        "SCTransform v2 introduces improvements in the regularization procedure that reduce "
                        "artifacts from highly expressed genes. The updated method is recommended as the default "
                        "normalization in Seurat v5."
                    ),
                    "year": 2023,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"SCTransform|sctransform", "description": "should mention SCTransform"},
                {"pattern": r"log.norm|standard.*norm", "description": "should mention log-normalization"},
                {"pattern": r"variance|depth|batch", "description": "should discuss technical considerations"},
            ],
            "expected_tool_preference": "SCTransform",
            "forbidden_claims": ["log-normalization is always sufficient"],
        },
        # 8. phylogenetics_model_selection
        {
            "question_id": "phylogenetics_model_selection",
            "question": "What substitution model should I use for maximum likelihood phylogenetics?",
            "analysis_type": "phylogenetics",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "ModelFinder: fast model selection for phylogenetic analysis",
                    "abstract": (
                        "ModelFinder implements a fast algorithm for selecting the best-fit substitution model "
                        "from over 200 models. Integrated into IQ-TREE, it replaces the need for standalone "
                        "tools like jModelTest or ProtTest. ModelFinder uses BIC by default, which provides "
                        "a good balance between model fit and complexity."
                    ),
                    "year": 2017,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "IQ-TREE 2: new models and efficient methods for phylogenomic inference",
                    "abstract": (
                        "IQ-TREE 2 introduces ultrafast bootstrap approximation and expanded model selection. "
                        "The -m MFP option automatically selects the best model using ModelFinder then runs "
                        "the tree search. This is now the recommended workflow for most phylogenetic analyses."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Pan-genome analysis of Staphylococcus aureus reveals core gene conservation",
                    "abstract": (
                        "We constructed a pan-genome from 500 S. aureus genomes identifying 2,100 core genes "
                        "and 8,500 accessory genes."
                    ),
                    "year": 2021,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "GTR+G4: the workhorse model for DNA phylogenetics",
                    "abstract": (
                        "The general time reversible model with gamma-distributed rate heterogeneity (GTR+G4) "
                        "remains the most commonly selected model for DNA datasets. We show that GTR+G4 fits "
                        "well for most alignments and recommend it as a starting point when model selection "
                        "is not feasible."
                    ),
                    "year": 2018,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Choosing the right model for phylogenetic inference",
                    "abstract": (
                        "Automatic model selection using BIC is preferred over ad hoc model choice. Tools like "
                        "ModelFinder in IQ-TREE and SMS in PhyML automate this process. For protein alignments, "
                        "LG+G4 is often selected; for DNA, GTR+G4 dominates."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"ModelFinder|model.*select|MFP", "description": "should recommend automatic model selection"},
                {"pattern": r"IQ-TREE|iqtree", "description": "should mention IQ-TREE"},
                {"pattern": r"GTR|general.*time.*revers", "description": "should mention GTR as common model"},
            ],
            "expected_tool_preference": "ModelFinder",
            "forbidden_claims": ["always use GTR"],
        },
        # 9. adapter_trimming_necessity
        {
            "question_id": "adapter_trimming_necessity",
            "question": "Is adapter trimming always necessary for RNA-seq before alignment?",
            "analysis_type": "rna_seq_differential_expression",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Impact of adapter trimming on RNA-seq alignment and quantification",
                    "abstract": (
                        "We assessed the effect of adapter trimming on downstream RNA-seq analysis. For STAR "
                        "alignment, soft-clipping effectively handles adapter sequences, making pre-alignment "
                        "trimming largely unnecessary. However, for other aligners and for short reads (<50bp), "
                        "trimming improved mapping rates by 2-5%."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Quality control in RNA-seq: to trim or not to trim",
                    "abstract": (
                        "We recommend running fastp or Trim Galore for quality control reporting even if trimming "
                        "is not strictly necessary. For pseudo-alignment tools (Salmon, Kallisto), adapter trimming "
                        "is more important as these tools do not soft-clip reads."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "ChIP-seq peak calling with MACS2: best practices",
                    "abstract": (
                        "We provide updated guidelines for ChIP-seq peak calling using MACS2 including "
                        "parameter optimization for broad and narrow peaks."
                    ),
                    "year": 2021,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "fastp: an ultra-fast all-in-one FASTQ preprocessor",
                    "abstract": (
                        "fastp performs quality filtering, adapter trimming, and quality control in a single pass. "
                        "It automatically detects and removes adapter sequences without requiring adapter input. "
                        "We recommend fastp as a default preprocessing step for all NGS data."
                    ),
                    "year": 2018,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "The effect of preprocessing on RNA-seq differential expression results",
                    "abstract": (
                        "We evaluated the impact of different preprocessing strategies on DE results. Moderate "
                        "quality trimming (Q20) combined with adapter removal had minimal effect on final DE gene "
                        "lists when using STAR. However, aggressive trimming (Q30) removed too many reads and "
                        "reduced statistical power."
                    ),
                    "year": 2021,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"soft.clip|STAR", "description": "should mention STAR soft-clipping"},
                {"pattern": r"fastp|Trim.Galore", "description": "should mention trimming tools"},
                {"pattern": r"not.*always|depend|optional", "description": "should indicate context-dependence"},
            ],
            "expected_tool_preference": None,
            "forbidden_claims": ["never trim adapters", "always trim"],
        },
        # 10. batch_effect_correction
        {
            "question_id": "batch_effect_correction",
            "question": "How should I handle batch effects in RNA-seq differential expression?",
            "analysis_type": "rna_seq_differential_expression",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Batch effects in RNA-seq: detection and correction strategies",
                    "abstract": (
                        "Batch effects are a major source of unwanted variation in RNA-seq. We review three "
                        "approaches: (1) including batch as a covariate in the DE model, (2) ComBat/ComBat-seq "
                        "for explicit batch correction, and (3) SVA for detecting unknown batch effects. "
                        "Approach 1 is simplest and recommended when batch is known and confounded."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "ComBat-seq: batch effect correction for RNA-seq count data",
                    "abstract": (
                        "ComBat-seq adapts the ComBat framework specifically for RNA-seq count data using "
                        "negative binomial regression. Unlike the original ComBat which operates on transformed "
                        "data, ComBat-seq preserves the count nature of the data, making it compatible with "
                        "DESeq2 and edgeR."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Nanopore sequencing of the SARS-CoV-2 genome in wastewater samples",
                    "abstract": (
                        "We developed a nanopore-based protocol for sequencing SARS-CoV-2 from wastewater "
                        "samples to track viral variant prevalence in the community."
                    ),
                    "year": 2022,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "SVA for detecting hidden batch effects in high-throughput experiments",
                    "abstract": (
                        "Surrogate Variable Analysis (SVA) identifies hidden sources of variation that may "
                        "confound differential expression analysis. We recommend using SVA when batch information "
                        "is not available or when additional unknown confounders may be present."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "DESeq2 design formulas for complex experimental designs",
                    "abstract": (
                        "DESeq2 supports multi-factor designs through its formula interface. For batch correction, "
                        "use design = ~batch + condition to account for known batches. This approach is preferred "
                        "over pre-correction methods as it properly handles the interaction between batch and "
                        "condition effects."
                    ),
                    "year": 2021,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"ComBat|combat", "description": "should mention ComBat"},
                {"pattern": r"SVA|surrogate", "description": "should mention SVA"},
                {"pattern": r"covariate|design.*formula|~.*batch", "description": "should mention model covariate approach"},
            ],
            "expected_tool_preference": None,
            "forbidden_claims": ["ignore batch effects"],
        },
        # 11. long_read_error_correction
        {
            "question_id": "long_read_error_correction",
            "question": "What is the best approach for error correction of Oxford Nanopore long reads?",
            "analysis_type": "metagenomics_classification",
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Error correction strategies for nanopore sequencing data",
                    "abstract": (
                        "We compared self-correction (Canu, NECAT) and hybrid correction (using Illumina reads) "
                        "approaches for Oxford Nanopore data. Hybrid correction achieved the lowest error rate "
                        "(0.1-0.5%) but self-correction with Canu at high coverage (>40x) approached similar "
                        "quality. For assemblies, we recommend the Canu+Medaka+Pilon pipeline."
                    ),
                    "year": 2021,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Medaka: neural network polishing of nanopore assemblies",
                    "abstract": (
                        "Medaka uses neural networks trained on nanopore signal data to polish draft assemblies. "
                        "Applied after initial assembly, Medaka reduces consensus error rate to Q30-Q40 depending "
                        "on coverage and basecaller version. It is faster and more accurate than Nanopolish."
                    ),
                    "year": 2020,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Machine learning approaches for protein structure prediction",
                    "abstract": (
                        "Recent advances in deep learning have revolutionized protein structure prediction. "
                        "AlphaFold2 achieves atomic accuracy for most protein domains."
                    ),
                    "year": 2022,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "ONT R10.4.1 chemistry reduces systematic errors in nanopore sequencing",
                    "abstract": (
                        "The R10.4.1 pore chemistry combined with the latest Dorado basecaller achieves raw "
                        "read accuracy of Q20+ (99%), significantly reducing the need for error correction. "
                        "For recent nanopore data, assembly polishing with Medaka alone may be sufficient."
                    ),
                    "year": 2023,
                    "relevant": True,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Hybrid assembly of bacterial genomes using nanopore and Illumina data",
                    "abstract": (
                        "We demonstrate that combining nanopore long reads for scaffolding with Illumina short "
                        "reads for polishing produces complete bacterial genomes with error rates below 1 per "
                        "100,000 bases. The Unicycler pipeline automates this hybrid approach."
                    ),
                    "year": 2019,
                    "relevant": True,
                },
            ],
            "expected_recommendations": [
                {"pattern": r"Medaka|medaka", "description": "should mention Medaka"},
                {"pattern": r"Canu|canu|NECAT", "description": "should mention self-correction tools"},
                {"pattern": r"hybrid|Illumina.*polish", "description": "should discuss hybrid approach"},
            ],
            "expected_tool_preference": "Medaka",
            "forbidden_claims": ["error correction is unnecessary"],
        },
        # 12. irrelevant_query
        {
            "question_id": "irrelevant_query",
            "question": "What is the best recipe for chocolate cake?",
            "analysis_type": None,
            "canned_abstracts": [
                {
                    "pmid": _fake_pmid(),
                    "title": "Cocoa polyphenols and their health effects: a systematic review",
                    "abstract": (
                        "We systematically reviewed 50 studies on the health effects of cocoa polyphenols. "
                        "Evidence suggests moderate cocoa consumption may improve cardiovascular health markers."
                    ),
                    "year": 2021,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Food microbiome analysis using 16S rRNA sequencing",
                    "abstract": (
                        "We characterized the microbial communities in fermented foods including chocolate, "
                        "yogurt, and kimchi using 16S rRNA amplicon sequencing."
                    ),
                    "year": 2020,
                    "relevant": False,
                },
                {
                    "pmid": _fake_pmid(),
                    "title": "Metabolomics of Theobroma cacao during fermentation",
                    "abstract": (
                        "We applied untargeted metabolomics to study the biochemical changes during cacao "
                        "bean fermentation. Key flavor precursors were identified."
                    ),
                    "year": 2019,
                    "relevant": False,
                },
            ],
            "expected_recommendations": [],
            "expected_tool_preference": None,
            "expected_confidence": "low",
            "forbidden_claims": ["recipe", "baking instructions", "mix flour and sugar"],
        },
    ]

    _write_json(task_dir / "scenarios.json", {"scenarios": scenarios})
    return scenarios


# ===================================================================
# Integration scenario
# ===================================================================


def create_integration_scenario(task_dir: Path) -> dict:
    """Create one comprehensive scenario chaining all features."""
    rng = random.Random(SEED + 10)
    data_dir = task_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # --- Inputs for preflight (clean) ---
    input_dir = data_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    ref_seq = _seq(rng, 1000)
    wrapped = "\n".join(ref_seq[i:i + 80] for i in range(0, len(ref_seq), 80))
    _write(input_dir / "reference.fa", f">chr1\n{wrapped}\n")
    _write(input_dir / "reference.fa.fai", f"chr1\t1000\t6\t80\t81\n")
    r1_lines, r2_lines = [], []
    for i in range(200):
        r1_lines.append(_fastq_record(f"pair_{i}/1", _seq(rng, 150), _qual(150)))
        r2_lines.append(_fastq_record(f"pair_{i}/2", _seq(rng, 150), _qual(150)))
    _write(input_dir / "reads_R1.fastq", "\n".join(r1_lines) + "\n")
    _write(input_dir / "reads_R2.fastq", "\n".join(r2_lines) + "\n")

    # --- Mock pipeline outputs (variant calling) ---
    output_dir = data_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    contigs = [("chr1", 1000)]
    # Good BAM
    sam_lines = [_sam_header(contigs)]
    for i in range(100):
        pos = rng.randint(1, 900)
        sam_lines.append(_sam_read(f"read_{i}", 0, "chr1", pos, 60, "100M", _seq(rng, 100), _qual(100)))
    for i in range(5):
        sam_lines.append(_sam_read(f"unmapped_{i}", 4, "*", 0, 0, "*", _seq(rng, 100), _qual(100)))
    _write(output_dir / "aligned.sam", "\n".join(sam_lines) + "\n")

    # VCF with low variant count (WARN scenario)
    hdr = _vcf_header(contigs, sample="sample1")
    var_lines = [hdr]
    for i in range(8):
        pos = 100 + i * 100
        ref_b = rng.choice(BASES)
        alt_b = rng.choice([b for b in BASES if b != ref_b])
        gq = rng.randint(25, 50)
        var_lines.append(_vcf_variant("chr1", pos, ref_b, alt_b, gq, "PASS", 20, gq=gq, sample=True))
    _write(output_dir / "variants.vcf", "\n".join(var_lines) + "\n")

    _write(output_dir / "pipeline.log", "Variant calling pipeline completed with warnings\n")
    for i in range(1, 5):
        tools = ["bwa_mem_align", "samtools_sort", "gatk_haplotype_caller", "bcftools_filter"]
        _write_step_completion(output_dir / f".step_{i}_completion.json", i, tools[i - 1])

    # --- Planted error for diagnosis ---
    planted_error = {
        "tool": "gatk_haplotype_caller",
        "stderr": (
            "Exception in thread \"main\" java.lang.OutOfMemoryError: Java heap space\n"
            "    at java.base/java.util.Arrays.copyOf(Arrays.java:3512)"
        ),
        "expected_root_cause": "out_of_memory",
    }
    _write_json(data_dir / "planted_error.json", planted_error)

    # --- Previous run for comparison (run_a = previous, run_b = current) ---
    prev_dir = data_dir / "previous_run"
    prev_dir.mkdir(parents=True, exist_ok=True)
    _write_json(prev_dir / "result.json", _mock_result_json(
        "completed", 2,
        outputs=[{"path": "aligned.bam"}, {"path": "variants.vcf"}],
        metrics={"mapping_rate": 0.85, "variant_count": 5},
        elapsed_seconds=180.0,
    ))
    _write_json(output_dir / "result.json", _mock_result_json(
        "completed", 0,
        outputs=[{"path": "aligned.sam"}, {"path": "variants.vcf"}, {"path": "pipeline.log"}],
        metrics={"mapping_rate": 0.952, "variant_count": 8},
        elapsed_seconds=120.0,
    ))

    # --- Literature question ---
    literature_question = {
        "question": "Why might germline variant calling produce very few variants?",
        "expected_topics": ["coverage", "filtering", "reference", "quality"],
    }
    _write_json(data_dir / "literature_question.json", literature_question)

    scenario = {
        "scenario_id": "full_pipeline_lifecycle",
        "input_dir": "data/inputs",
        "output_dir": "data/outputs",
        "previous_run_dir": "data/previous_run",
        "planted_error": planted_error,
        "literature_question": literature_question,
        "expected_checks": {
            "preflight_clean": True,
            "bam_quality": "PASS",
            "vcf_quality": "WARN",
            "catalog_complete": True,
            "diagnosis_correct": True,
            "comparison_verdict": "IMPROVED",
        },
    }
    _write_json(task_dir / "scenarios.json", scenario)
    return scenario


# ===================================================================
# Master function
# ===================================================================


FEATURE_GENERATORS = {
    "output-quality-gate": create_output_quality_scenarios,
    "preflight-scanner": create_preflight_scenarios,
    "output-catalog": create_output_catalog_scenarios,
    "result-interpreter": create_interpretation_scenarios,
    "error-diagnosis": create_error_diagnosis_scenarios,
    "quality-compare": create_quality_compare_scenarios,
    "literature-agent": create_literature_scenarios,
}


def create_all(root: Path) -> dict:
    """Generate all benchmark data under *root*.

    Returns a summary dict with file counts per feature.
    """
    summary: dict[str, int] = {}
    for feature, gen_fn in FEATURE_GENERATORS.items():
        task_dir = root / feature
        gen_fn(task_dir)
        count = sum(1 for _ in task_dir.rglob("*") if _.is_file())
        summary[feature] = count
        print(f"  {feature}: {count} files")

    # Integration
    integration_dir = root / "integration"
    create_integration_scenario(integration_dir)
    count = sum(1 for _ in integration_dir.rglob("*") if _.is_file())
    summary["integration"] = count
    print(f"  integration: {count} files")

    # Write cache metadata
    cache_dir = root.parent / "_cache" if root.name == "tasks" else root / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    total = sum(summary.values())
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "total_files": total,
        "per_feature": summary,
    }
    _write_json(cache_dir / "generated_at.json", meta)
    print(f"\nTotal: {total} files generated.")
    return summary


def _compute_checksums(root: Path) -> dict[str, str]:
    """Compute SHA-256 for every file under root."""
    checksums: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "_cache" not in p.parts:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            checksums[str(p.relative_to(root))] = h
    return checksums


def verify_checksums(root: Path) -> bool:
    """Verify existing checksums match on-disk files.  Returns True if OK."""
    cache_dir = root.parent / "_cache" if root.name == "tasks" else root / "_cache"
    cs_path = cache_dir / "checksums.json"
    if not cs_path.exists():
        print("No checksums.json found -- run generation first.")
        return False
    saved = json.loads(cs_path.read_text())
    current = _compute_checksums(root)
    ok = True
    for rel, expected_hash in saved.items():
        actual = current.get(rel)
        if actual is None:
            print(f"  MISSING: {rel}")
            ok = False
        elif actual != expected_hash:
            print(f"  CHANGED: {rel}")
            ok = False
    for rel in current:
        if rel not in saved:
            print(f"  NEW (unexpected): {rel}")
            ok = False
    if ok:
        print("All checksums match.")
    return ok


# ===================================================================
# CLI
# ===================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic benchmark data for the 7-feature suite.",
    )
    parser.add_argument("--all", action="store_true", help="Generate everything")
    parser.add_argument("--feature", choices=list(FEATURE_GENERATORS) + ["integration"],
                        help="Generate one feature's data")
    parser.add_argument("--data-root", type=Path,
                        default=Path("workspace/benchmarks/feature-bench/tasks"),
                        help="Output root directory")
    parser.add_argument("--verify-only", action="store_true",
                        help="Check checksums without regenerating")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be generated without writing files")
    args = parser.parse_args()

    root = args.data_root.resolve()

    if args.verify_only:
        ok = verify_checksums(root)
        sys.exit(0 if ok else 1)

    if args.dry_run:
        if args.all or args.feature is None:
            features = list(FEATURE_GENERATORS) + ["integration"]
        else:
            features = [args.feature]
        for f in features:
            print(f"Would generate: {root / f}/")
        print(f"Output root: {root}")
        return

    if args.feature:
        if args.feature == "integration":
            task_dir = root / "integration"
            create_integration_scenario(task_dir)
            count = sum(1 for _ in task_dir.rglob("*") if _.is_file())
            print(f"  integration: {count} files")
        else:
            gen_fn = FEATURE_GENERATORS[args.feature]
            task_dir = root / args.feature
            gen_fn(task_dir)
            count = sum(1 for _ in task_dir.rglob("*") if _.is_file())
            print(f"  {args.feature}: {count} files")
    elif args.all:
        print("Generating all benchmark data...")
        create_all(root)
        # Write checksums after full generation
        cache_dir = root.parent / "_cache" if root.name == "tasks" else root / "_cache"
        checksums = _compute_checksums(root)
        _write_json(cache_dir / "checksums.json", checksums)
        print(f"Checksums written for {len(checksums)} files.")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
