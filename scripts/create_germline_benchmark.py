#!/usr/bin/env python3
"""Create synthetic benchmark data for germline variant calling.

Generates:
  - A small (~50kb) reference genome FASTA
  - A mutant genome with known SNPs and indels
  - Paired-end FASTQ reads simulated from the mutant (diploid, ~30x)
  - A truth VCF listing all introduced variants
"""

import random
import subprocess
import sys
from pathlib import Path

SEED = 42
REF_LEN = 50_000
CHROM = "chr_synthetic"
NUM_SNPS = 25
NUM_INDELS = 5  # small insertions/deletions (1-3 bp)
READ_LEN = 150
COVERAGE = 30
FRAGMENT_SIZE = 350
BASE_ERROR_RATE = 0.001
MUTATION_RATE = 0.0  # wgsim's random mutations disabled; we inject our own


def generate_reference(length: int, rng: random.Random) -> str:
    """Generate a random DNA sequence."""
    return "".join(rng.choice("ACGT") for _ in range(length))


def introduce_variants(ref_seq: str, rng: random.Random):
    """Introduce known SNPs and indels into the reference, return mutant + variant list."""
    seq = list(ref_seq)
    variants = []  # (pos_1based, ref_allele, alt_allele, variant_type)

    # Pick non-overlapping positions for variants
    # Avoid first/last 500bp to ensure reads can span them
    available = list(range(500, len(seq) - 500))
    rng.shuffle(available)

    # Ensure minimum spacing of 50bp between variants
    chosen = []
    used = set()
    for pos in available:
        if any(abs(pos - u) < 50 for u in used):
            continue
        chosen.append(pos)
        used.add(pos)
        if len(chosen) >= NUM_SNPS + NUM_INDELS:
            break

    chosen.sort()
    snp_positions = chosen[:NUM_SNPS]
    indel_positions = chosen[NUM_SNPS : NUM_SNPS + NUM_INDELS]

    # Process indels first (from end to start to preserve positions)
    indel_variants = []
    for pos in sorted(indel_positions, reverse=True):
        if rng.random() < 0.5:
            # Deletion (1-3 bp)
            del_len = rng.randint(1, 3)
            if pos + del_len + 1 >= len(seq):
                continue
            ref_allele = "".join(seq[pos : pos + del_len + 1])
            alt_allele = seq[pos]
            for i in range(del_len):
                seq.pop(pos + 1)
            indel_variants.append((pos + 1, ref_allele, alt_allele, "DEL"))
        else:
            # Insertion (1-3 bp)
            ins_len = rng.randint(1, 3)
            ins_bases = "".join(rng.choice("ACGT") for _ in range(ins_len))
            ref_allele = seq[pos]
            alt_allele = seq[pos] + ins_bases
            for i, b in enumerate(ins_bases):
                seq.insert(pos + 1 + i, b)
            indel_variants.append((pos + 1, ref_allele, alt_allele, "INS"))

    # SNPs (positions still valid since we track by original pos)
    # Re-index after indels — use the original positions mapped to current
    for pos in snp_positions:
        if pos >= len(seq):
            continue
        old_base = seq[pos]
        alternatives = [b for b in "ACGT" if b != old_base]
        new_base = rng.choice(alternatives)
        seq[pos] = new_base
        variants.append((pos + 1, old_base, new_base, "SNP"))

    variants.extend(indel_variants)
    variants.sort(key=lambda v: v[0])

    return "".join(seq), variants


def write_fasta(path: Path, chrom: str, seq: str):
    """Write a FASTA file."""
    with open(path, "w") as f:
        f.write(f">{chrom}\n")
        for i in range(0, len(seq), 80):
            f.write(seq[i : i + 80] + "\n")


def write_vcf(path: Path, chrom: str, variants: list, ref_path: str):
    """Write a truth VCF file."""
    with open(path, "w") as f:
        f.write("##fileformat=VCFv4.2\n")
        f.write(f'##reference={ref_path}\n')
        f.write(f'##contig=<ID={chrom},length={REF_LEN}>\n')
        f.write('##INFO=<ID=TYPE,Number=1,Type=String,Description="Variant type">\n')
        f.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n")
        for pos, ref, alt, vtype in variants:
            # Simulate heterozygous (0/1) and homozygous (1/1) calls
            gt = "0/1" if random.random() < 0.6 else "1/1"
            f.write(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t100\tPASS\tTYPE={vtype}\tGT\t{gt}\n")


def simulate_reads(mutant_fasta: Path, out_r1: Path, out_r2: Path, coverage: int):
    """Use wgsim to simulate paired-end reads."""
    # Calculate number of read pairs for desired coverage
    num_pairs = int((REF_LEN * coverage) / (2 * READ_LEN))

    cmd = [
        "wgsim",
        "-1", str(READ_LEN),
        "-2", str(READ_LEN),
        "-d", str(FRAGMENT_SIZE),
        "-N", str(num_pairs),
        "-e", str(BASE_ERROR_RATE),
        "-r", "0",  # No random mutations (we already introduced ours)
        "-R", "0",  # No indel fraction
        "-S", str(SEED),
        str(mutant_fasta),
        str(out_r1),
        str(out_r2),
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"wgsim stderr: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"wgsim output: {result.stderr.strip()}")


def main():
    rng = random.Random(SEED)

    base_dir = Path("workspace/benchmarks/bioagent-bench/tasks/germline-vc")
    data_dir = base_dir / "data"
    results_dir = base_dir / "results"
    data_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    # Generate reference
    print("Generating reference genome...")
    ref_seq = generate_reference(REF_LEN, rng)
    ref_fasta = data_dir / "ref_genome.fa"
    write_fasta(ref_fasta, CHROM, ref_seq)

    # Introduce variants
    print(f"Introducing {NUM_SNPS} SNPs and {NUM_INDELS} indels...")
    mutant_seq, variants = introduce_variants(ref_seq, rng)
    mutant_fasta = data_dir / "mutant_genome.fa"
    write_fasta(mutant_fasta, CHROM, mutant_seq)

    # Write truth VCF
    truth_vcf = results_dir / "truth_variants.vcf"
    write_vcf(truth_vcf, CHROM, variants, str(ref_fasta.resolve()))
    print(f"Wrote {len(variants)} truth variants to {truth_vcf}")

    # Also put a copy in data dir so compiler can find it
    truth_vcf_data = data_dir / "truth_variants.vcf"
    write_vcf(truth_vcf_data, CHROM, variants, str(ref_fasta.resolve()))

    # Simulate reads
    print(f"Simulating ~{COVERAGE}x coverage paired-end reads...")
    r1 = data_dir / "sample_1.fastq"
    r2 = data_dir / "sample_2.fastq"
    simulate_reads(mutant_fasta, r1, r2, COVERAGE)

    # Index reference
    print("Indexing reference with samtools...")
    subprocess.run(["samtools", "faidx", str(ref_fasta)], check=True)

    # Create sequence dictionary for GATK
    dict_path = ref_fasta.with_suffix(".dict")
    if not dict_path.exists():
        print("Creating sequence dictionary for GATK...")
        subprocess.run(
            ["samtools", "dict", str(ref_fasta), "-o", str(dict_path)],
            check=True,
        )

    # Summary
    r1_lines = sum(1 for _ in open(r1))
    print(f"\nBenchmark data created in {data_dir}:")
    print(f"  Reference: {ref_fasta} ({REF_LEN} bp)")
    print(f"  Reads: {r1} + {r2} ({r1_lines // 4} pairs)")
    print(f"  Truth VCF: {truth_vcf} ({len(variants)} variants: "
          f"{sum(1 for v in variants if v[3] == 'SNP')} SNPs, "
          f"{sum(1 for v in variants if v[3] in ('INS', 'DEL'))} indels)")

    # Print variants for reference
    print("\nTruth variants:")
    for pos, ref, alt, vtype in variants:
        print(f"  {CHROM}:{pos} {ref}>{alt} ({vtype})")


if __name__ == "__main__":
    main()
