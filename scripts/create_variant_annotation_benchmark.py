#!/usr/bin/env python3
"""Create synthetic benchmark data for variant annotation with SnpEff.

Generates:
  - A small synthetic genome (chr1, 10 kb)
  - GFF3 gene annotation with two multi-exon genes
  - VCF with 10 variants of known impact (HIGH / MODERATE / LOW / MODIFIER)
  - truth.json with expected impact for each variant

The benchmark validates that SnpEff + SnpSift correctly annotates and filters
variants by functional impact using a custom-built database.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Genome design
# ---------------------------------------------------------------------------
CHROM = "chr1"
GENOME_LEN = 10_000
SEED = 42

# Gene A:  positions 500..2500 (+ strand)
#   5' UTR:     500..599
#   Exon 1 CDS: 600..899   (300 bp = 100 codons)
#   Intron 1:   900..1099
#   Exon 2 CDS: 1100..1499 (400 bp)
#   3' UTR:     1500..1599
# Total CDS = 700 bp → 233 codons (699 bp used, last bp is stop overhang)

# Gene B:  positions 4000..6500 (+ strand)
#   5' UTR:     4000..4099
#   Exon 1 CDS: 4100..4499 (400 bp)
#   Intron 1:   4500..4699
#   Exon 2 CDS: 4700..4999 (300 bp)
#   3' UTR:     5000..5099

# ---------------------------------------------------------------------------
# Variant design – 10 variants with known impacts
# ---------------------------------------------------------------------------
# For SnpEff, we need the actual codon at each position to predict effect.
# Strategy: build the genome sequence first, then read codons to design
# variants that produce known effects.


def _random_seq(length: int, rng: random.Random) -> str:
    return "".join(rng.choice("ACGT") for _ in range(length))


def _codon_at(seq: str, cds_start: int, codon_index: int) -> str:
    """Return the codon at given index (0-based) within CDS starting at cds_start."""
    pos = cds_start + codon_index * 3
    return seq[pos : pos + 3]


def _complement(base: str) -> str:
    return {"A": "T", "T": "A", "C": "G", "G": "C"}[base]


# Standard codon table
CODON_TABLE = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
    "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
    "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
    "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
    "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


def _mutate_to_stop(codon: str) -> tuple[str, int] | None:
    """Find a single-base change that turns codon into a stop codon.
    Returns (alt_base, position_in_codon) or None."""
    stops = {"TAA", "TAG", "TGA"}
    for pos in range(3):
        for alt in "ACGT":
            if alt == codon[pos]:
                continue
            new_codon = codon[:pos] + alt + codon[pos + 1 :]
            if new_codon in stops:
                return alt, pos
    return None


def _mutate_to_missense(codon: str) -> tuple[str, int] | None:
    """Find a single-base change that changes the amino acid (not to stop)."""
    orig_aa = CODON_TABLE.get(codon, "?")
    for pos in range(3):
        for alt in "ACGT":
            if alt == codon[pos]:
                continue
            new_codon = codon[:pos] + alt + codon[pos + 1 :]
            new_aa = CODON_TABLE.get(new_codon, "?")
            if new_aa != orig_aa and new_aa != "*":
                return alt, pos
    return None


def _mutate_to_synonymous(codon: str) -> tuple[str, int] | None:
    """Find a single-base change that preserves the amino acid."""
    orig_aa = CODON_TABLE.get(codon, "?")
    for pos in range(3):
        for alt in "ACGT":
            if alt == codon[pos]:
                continue
            new_codon = codon[:pos] + alt + codon[pos + 1 :]
            new_aa = CODON_TABLE.get(new_codon, "?")
            if new_aa == orig_aa:
                return alt, pos
    return None


def build_benchmark(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)
    genome = list(_random_seq(GENOME_LEN, rng))

    # --- Place start/stop codons for genes ---
    # Gene A: CDS at 600..899 (exon1) + 1100..1499 (exon2)
    # Combined CDS = 700 bp. We need ATG at start and stop at end.
    # CDS is: genome[600:900] + genome[1100:1500]
    # Place ATG at position 600
    genome[600] = "A"
    genome[601] = "T"
    genome[602] = "G"
    # Combined CDS is 700 bp = 233 codons + 1 bp leftover
    # Actually 700 / 3 = 233.33... Let's make it clean: 699 bp = 233 codons
    # Exon 1: 300 bp (100 codons), Exon 2: 399 bp (133 codons) → total 233 codons
    # Adjust: Exon 2 CDS: 1100..1498 (399 bp)
    # Place stop codon at last 3 bases of combined CDS
    # Combined CDS last 3 bases = genome[1496:1499]
    genome[1496] = "T"
    genome[1497] = "A"
    genome[1498] = "A"  # TAA stop

    # Gene B: CDS at 4100..4499 (exon1) + 4700..4999 (exon2)
    # Combined CDS = 400 + 300 = 700 bp → same as above: 699 bp = 233 codons
    # Exon 2: 4700..4998 (299 bp)
    genome[4100] = "A"
    genome[4101] = "T"
    genome[4102] = "G"
    # Stop at end of combined CDS: exon1(400) + first 299-of-exon2
    # 400 + 299 = 699. Combined CDS position 696..698 maps to genome[4997:5000]
    # Actually: first 400 from exon1 (genome[4100:4500]), then 299 from exon2 (genome[4700:4999])
    genome[4996] = "T"
    genome[4997] = "A"
    genome[4998] = "G"  # TAG stop

    genome_str = "".join(genome)

    # ---------------------------------------------------------------
    # Design 10 variants
    # ---------------------------------------------------------------
    variants = []
    truth = []

    # Gene A CDS: positions 600..899 (exon 1) and 1100..1498 (exon 2)
    # For codon operations, we work with the combined CDS sequence
    gene_a_cds_ranges = [(600, 900), (1100, 1499)]  # [start, end)
    gene_b_cds_ranges = [(4100, 4500), (4700, 4999)]

    def _get_cds_seq(ranges):
        return "".join(genome_str[s:e] for s, e in ranges)

    def _cds_pos_to_genome(ranges, cds_pos):
        """Map CDS-relative position to genome position."""
        offset = 0
        for s, e in ranges:
            span = e - s
            if cds_pos < offset + span:
                return s + (cds_pos - offset)
            offset += span
        raise ValueError(f"CDS position {cds_pos} out of range")

    cds_a = _get_cds_seq(gene_a_cds_ranges)
    cds_b = _get_cds_seq(gene_b_cds_ranges)

    def _find_codon_for(cds: str, mutator, start_idx: int = 5, skip: set | None = None):
        """Search for a codon that can be mutated by mutator, starting at start_idx."""
        skip = skip or set()
        n_codons = len(cds) // 3
        for ci in range(start_idx, n_codons - 1):  # avoid last codon (stop)
            if ci in skip:
                continue
            codon = cds[ci * 3 : ci * 3 + 3]
            result = mutator(codon)
            if result:
                return ci, result
        raise ValueError(f"No suitable codon found starting from index {start_idx}")

    used_codons_a: set[int] = set()
    used_codons_b: set[int] = set()

    # 1. GENE_A: nonsense (stop_gained) → HIGH
    codon_idx, (alt_base, pos_in_codon) = _find_codon_for(cds_a, _mutate_to_stop, 5, used_codons_a)
    used_codons_a.add(codon_idx)
    cds_pos = codon_idx * 3 + pos_in_codon
    gpos = _cds_pos_to_genome(gene_a_cds_ranges, cds_pos)
    ref_base = genome_str[gpos]
    variants.append((CHROM, gpos + 1, "stop_gained_A", ref_base, alt_base))
    truth.append({"id": "stop_gained_A", "pos": gpos + 1, "expected_impact": "HIGH",
                  "expected_effect": "stop_gained", "gene": "GENE_A"})

    # 2. GENE_A: frameshift deletion → HIGH
    gpos_fs = _cds_pos_to_genome(gene_a_cds_ranges, 50)  # middle of exon 1
    ref_2 = genome_str[gpos_fs : gpos_fs + 2]
    variants.append((CHROM, gpos_fs + 1, "frameshift_A", ref_2, ref_2[0]))
    truth.append({"id": "frameshift_A", "pos": gpos_fs + 1, "expected_impact": "HIGH",
                  "expected_effect": "frameshift_variant", "gene": "GENE_A"})

    # 3. GENE_A: missense → MODERATE
    codon_idx, (alt_base, pos_in_codon) = _find_codon_for(cds_a, _mutate_to_missense, 25, used_codons_a)
    used_codons_a.add(codon_idx)
    cds_pos = codon_idx * 3 + pos_in_codon
    gpos = _cds_pos_to_genome(gene_a_cds_ranges, cds_pos)
    ref_base = genome_str[gpos]
    variants.append((CHROM, gpos + 1, "missense_A", ref_base, alt_base))
    truth.append({"id": "missense_A", "pos": gpos + 1, "expected_impact": "MODERATE",
                  "expected_effect": "missense_variant", "gene": "GENE_A"})

    # 4. GENE_A: synonymous → LOW
    codon_idx, (alt_base, pos_in_codon) = _find_codon_for(cds_a, _mutate_to_synonymous, 45, used_codons_a)
    used_codons_a.add(codon_idx)
    cds_pos = codon_idx * 3 + pos_in_codon
    gpos = _cds_pos_to_genome(gene_a_cds_ranges, cds_pos)
    ref_base = genome_str[gpos]
    variants.append((CHROM, gpos + 1, "synonymous_A", ref_base, alt_base))
    truth.append({"id": "synonymous_A", "pos": gpos + 1, "expected_impact": "LOW",
                  "expected_effect": "synonymous_variant", "gene": "GENE_A"})

    # 5. GENE_A: intronic variant → MODIFIER
    gpos_intron = 950  # middle of intron 1 (900..1099)
    ref_base = genome_str[gpos_intron]
    alt_base = {"A": "G", "G": "A", "C": "T", "T": "C"}[ref_base]
    variants.append((CHROM, gpos_intron + 1, "intron_A", ref_base, alt_base))
    truth.append({"id": "intron_A", "pos": gpos_intron + 1, "expected_impact": "MODIFIER",
                  "expected_effect": "intron_variant", "gene": "GENE_A"})

    # 6. GENE_B: nonsense → HIGH
    codon_idx, (alt_base, pos_in_codon) = _find_codon_for(cds_b, _mutate_to_stop, 5, used_codons_b)
    used_codons_b.add(codon_idx)
    cds_pos = codon_idx * 3 + pos_in_codon
    gpos = _cds_pos_to_genome(gene_b_cds_ranges, cds_pos)
    ref_base = genome_str[gpos]
    variants.append((CHROM, gpos + 1, "stop_gained_B", ref_base, alt_base))
    truth.append({"id": "stop_gained_B", "pos": gpos + 1, "expected_impact": "HIGH",
                  "expected_effect": "stop_gained", "gene": "GENE_B"})

    # 7. GENE_B: missense → MODERATE
    codon_idx, (alt_base, pos_in_codon) = _find_codon_for(cds_b, _mutate_to_missense, 25, used_codons_b)
    used_codons_b.add(codon_idx)
    cds_pos = codon_idx * 3 + pos_in_codon
    gpos = _cds_pos_to_genome(gene_b_cds_ranges, cds_pos)
    ref_base = genome_str[gpos]
    variants.append((CHROM, gpos + 1, "missense_B", ref_base, alt_base))
    truth.append({"id": "missense_B", "pos": gpos + 1, "expected_impact": "MODERATE",
                  "expected_effect": "missense_variant", "gene": "GENE_B"})

    # 8. GENE_B: synonymous → LOW
    codon_idx, (alt_base, pos_in_codon) = _find_codon_for(cds_b, _mutate_to_synonymous, 45, used_codons_b)
    used_codons_b.add(codon_idx)
    cds_pos = codon_idx * 3 + pos_in_codon
    gpos = _cds_pos_to_genome(gene_b_cds_ranges, cds_pos)
    ref_base = genome_str[gpos]
    variants.append((CHROM, gpos + 1, "synonymous_B", ref_base, alt_base))
    truth.append({"id": "synonymous_B", "pos": gpos + 1, "expected_impact": "LOW",
                  "expected_effect": "synonymous_variant", "gene": "GENE_B"})

    # 9. GENE_B: intronic → MODIFIER
    gpos_intron = 4600  # middle of intron 1 (4500..4699)
    ref_base = genome_str[gpos_intron]
    alt_base = {"A": "G", "G": "A", "C": "T", "T": "C"}[ref_base]
    variants.append((CHROM, gpos_intron + 1, "intron_B", ref_base, alt_base))
    truth.append({"id": "intron_B", "pos": gpos_intron + 1, "expected_impact": "MODIFIER",
                  "expected_effect": "intron_variant", "gene": "GENE_B"})

    # 10. Intergenic variant → MODIFIER
    gpos_ig = 3000  # between gene A (ends ~1600) and gene B (starts ~4000)
    ref_base = genome_str[gpos_ig]
    alt_base = {"A": "G", "G": "A", "C": "T", "T": "C"}[ref_base]
    variants.append((CHROM, gpos_ig + 1, "intergenic", ref_base, alt_base))
    truth.append({"id": "intergenic", "pos": gpos_ig + 1, "expected_impact": "MODIFIER",
                  "expected_effect": "intergenic_region", "gene": "none"})

    # Sort variants by position
    variants.sort(key=lambda v: v[1])
    truth.sort(key=lambda t: t["pos"])

    # ---------------------------------------------------------------
    # Write reference genome FASTA
    # ---------------------------------------------------------------
    ref_path = output_dir / "reference.fa"
    with open(ref_path, "w") as f:
        f.write(f">{CHROM}\n")
        for i in range(0, len(genome_str), 80):
            f.write(genome_str[i : i + 80] + "\n")

    # ---------------------------------------------------------------
    # Write GFF3 annotation
    # ---------------------------------------------------------------
    gff_path = output_dir / "genes.gff"
    with open(gff_path, "w") as f:
        f.write("##gff-version 3\n")
        f.write(f"##sequence-region {CHROM} 1 {GENOME_LEN}\n")

        # Gene A
        f.write(f"{CHROM}\t.\tgene\t501\t1600\t.\t+\t.\tID=gene_A;Name=GENE_A\n")
        f.write(f"{CHROM}\t.\tmRNA\t501\t1600\t.\t+\t.\tID=mRNA_A;Parent=gene_A;Name=GENE_A\n")
        # 5' UTR
        f.write(f"{CHROM}\t.\tfive_prime_UTR\t501\t600\t.\t+\t.\tID=utr5_A;Parent=mRNA_A\n")
        # Exon 1 (UTR + CDS)
        f.write(f"{CHROM}\t.\texon\t501\t900\t.\t+\t.\tID=exon_A1;Parent=mRNA_A\n")
        f.write(f"{CHROM}\t.\tCDS\t601\t900\t.\t+\t0\tID=cds_A1;Parent=mRNA_A\n")
        # Exon 2 (CDS + UTR)
        f.write(f"{CHROM}\t.\texon\t1101\t1600\t.\t+\t.\tID=exon_A2;Parent=mRNA_A\n")
        f.write(f"{CHROM}\t.\tCDS\t1101\t1499\t.\t+\t0\tID=cds_A2;Parent=mRNA_A\n")
        # 3' UTR
        f.write(f"{CHROM}\t.\tthree_prime_UTR\t1500\t1600\t.\t+\t.\tID=utr3_A;Parent=mRNA_A\n")

        # Gene B
        f.write(f"{CHROM}\t.\tgene\t4001\t5100\t.\t+\t.\tID=gene_B;Name=GENE_B\n")
        f.write(f"{CHROM}\t.\tmRNA\t4001\t5100\t.\t+\t.\tID=mRNA_B;Parent=gene_B;Name=GENE_B\n")
        # 5' UTR
        f.write(f"{CHROM}\t.\tfive_prime_UTR\t4001\t4100\t.\t+\t.\tID=utr5_B;Parent=mRNA_B\n")
        # Exon 1
        f.write(f"{CHROM}\t.\texon\t4001\t4500\t.\t+\t.\tID=exon_B1;Parent=mRNA_B\n")
        f.write(f"{CHROM}\t.\tCDS\t4101\t4500\t.\t+\t0\tID=cds_B1;Parent=mRNA_B\n")
        # Exon 2
        f.write(f"{CHROM}\t.\texon\t4701\t5100\t.\t+\t.\tID=exon_B2;Parent=mRNA_B\n")
        f.write(f"{CHROM}\t.\tCDS\t4701\t4999\t.\t+\t0\tID=cds_B2;Parent=mRNA_B\n")
        # 3' UTR
        f.write(f"{CHROM}\t.\tthree_prime_UTR\t5000\t5100\t.\t+\t.\tID=utr3_B;Parent=mRNA_B\n")

    # ---------------------------------------------------------------
    # Write VCF
    # ---------------------------------------------------------------
    vcf_path = output_dir / "input_variants.vcf"
    with open(vcf_path, "w") as f:
        f.write("##fileformat=VCFv4.2\n")
        f.write(f"##contig=<ID={CHROM},length={GENOME_LEN}>\n")
        f.write('##INFO=<ID=BENCH,Number=1,Type=String,Description="Benchmark variant ID">\n')
        f.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        for chrom, pos, vid, ref, alt in variants:
            f.write(f"{chrom}\t{pos}\t{vid}\t{ref}\t{alt}\t100\tPASS\tBENCH={vid}\n")

    # ---------------------------------------------------------------
    # Write truth JSON
    # ---------------------------------------------------------------
    # Summary counts
    impact_counts = {}
    for t in truth:
        imp = t["expected_impact"]
        impact_counts[imp] = impact_counts.get(imp, 0) + 1

    high_moderate_ids = [t["id"] for t in truth if t["expected_impact"] in ("HIGH", "MODERATE")]

    truth_data = {
        "variants": truth,
        "total_variants": len(truth),
        "impact_counts": impact_counts,
        "high_moderate_ids": high_moderate_ids,
        "high_moderate_count": len(high_moderate_ids),
    }
    truth_path = output_dir / "truth.json"
    with open(truth_path, "w") as f:
        json.dump(truth_data, f, indent=2)

    print(f"Benchmark data created in {output_dir}")
    print(f"  Reference: {ref_path}")
    print(f"  Annotation: {gff_path}")
    print(f"  Input VCF: {vcf_path}")
    print(f"  Truth: {truth_path}")
    print(f"  Total variants: {len(truth)}")
    print(f"  Impact counts: {impact_counts}")
    print(f"  HIGH+MODERATE variants: {len(high_moderate_ids)}")
    for t in truth:
        print(f"    {t['id']:20s} pos={t['pos']:5d}  impact={t['expected_impact']:10s}  effect={t['expected_effect']}")

    return truth_data


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("benchmark_data/variant_annotation")
    build_benchmark(out)
