#!/usr/bin/env python3
"""Create synthetic long-read benchmark data for 3 base cases + 6 stress variants.

Cases:
  - dna_sv: ONT DNA reads with an embedded 500bp deletion
  - assembly: ONT reads from a 15kb circular genome
  - rna_isoform: Spliced reads with 2 isoforms per gene

Stress variants derived from base cases:
  - dna_sv_pacbio: Same data, prompt says PacBio HiFi
  - dna_sv_noisy_prompt: Vague prompt
  - assembly_meta: Prompt says metagenome
  - assembly_malformed: Truncated FASTQ
  - rna_isoform_no_annot: Same reads, no GTF provided
  - dna_sv_nested_output: Prompt requests nested output path
"""
import gzip
import json
import random
import shutil
from pathlib import Path

BASE = Path("workspace/benchmark_data/long_read")
BASES = "ACGT"


def random_seq(length, rng):
    return "".join(rng.choice(BASES) for _ in range(length))


def add_errors(seq, rate, rng):
    result = list(seq)
    for i in range(len(result)):
        if rng.random() < rate:
            result[i] = rng.choice([b for b in BASES if b != result[i]])
    return "".join(result)


def random_qual(length, rng, lo=20, hi=35):
    return "".join(chr(rng.randint(lo, hi) + 33) for _ in range(length))


# ──────────── Case 1: DNA alignment + SV calling ────────────
def create_dna_sv():
    rng = random.Random(42)
    out = BASE / "dna_sv" / "data"
    out.mkdir(parents=True, exist_ok=True)

    # Reference with a 500bp region that is deleted in the sample
    ref_a = random_seq(5000, rng)
    sv_del = random_seq(500, rng)
    ref_b = random_seq(5000, rng)
    ref_full = ref_a + sv_del + ref_b
    ref_sample = ref_a + ref_b  # sample has the deletion

    with open(out / "ref.fasta", "w") as f:
        f.write(">chr1\n")
        for i in range(0, len(ref_full), 80):
            f.write(ref_full[i : i + 80] + "\n")

    # ONT-like reads from the sample (with deletion)
    with open(out / "reads.fastq", "w") as f:
        for i in range(300):
            start = rng.randint(0, len(ref_sample) - 1000)
            length = rng.randint(500, 2000)
            seq = ref_sample[start : start + length]
            seq = add_errors(seq, 0.05, rng)
            qual = random_qual(len(seq), rng)
            f.write(f"@read_{i}\n{seq}\n+\n{qual}\n")

    with open(out / "truth.json", "w") as f:
        json.dump(
            {"sv_type": "DEL", "chrom": "chr1", "pos": 5000, "length": 500}, f, indent=2
        )
    print(f"  dna_sv: {out}")


# ──────────── Case 2: De novo assembly ────────────
def create_assembly():
    rng = random.Random(43)
    out = BASE / "assembly" / "data"
    out.mkdir(parents=True, exist_ok=True)

    genome = random_seq(15000, rng)

    with open(out / "reads.fastq", "w") as f:
        for i in range(500):
            start = rng.randint(0, 14000)
            length = rng.randint(1000, 5000)
            end = start + length
            if end <= 15000:
                seq = genome[start:end]
            else:
                seq = genome[start:] + genome[: end - 15000]
            seq = add_errors(seq, 0.05, rng)
            qual = random_qual(len(seq), rng)
            f.write(f"@read_{i}\n{seq}\n+\n{qual}\n")

    with open(out / "truth.json", "w") as f:
        json.dump({"genome_size": 15000, "circular": True}, f, indent=2)
    print(f"  assembly: {out}")


# ──────────── Case 3: RNA isoform ────────────
def create_rna_isoform():
    rng = random.Random(44)
    out = BASE / "rna_isoform" / "data"
    out.mkdir(parents=True, exist_ok=True)

    genome = random_seq(5000, rng)

    with open(out / "ref.fasta", "w") as f:
        f.write(">chr1\n")
        for i in range(0, len(genome), 80):
            f.write(genome[i : i + 80] + "\n")

    # GTF with 2 isoforms for gene1 (exon skipping)
    with open(out / "annot.gtf", "w") as f:
        # Isoform A: 3 exons
        f.write(
            'chr1\ttest\ttranscript\t1\t1001\t.\t+\t.\t'
            'gene_id "gene1"; transcript_id "gene1_A";\n'
        )
        for s, e in [(1, 201), (401, 601), (801, 1001)]:
            f.write(
                f'chr1\ttest\texon\t{s}\t{e}\t.\t+\t.\t'
                f'gene_id "gene1"; transcript_id "gene1_A";\n'
            )
        # Isoform B: skip middle exon
        f.write(
            'chr1\ttest\ttranscript\t1\t1001\t.\t+\t.\t'
            'gene_id "gene1"; transcript_id "gene1_B";\n'
        )
        for s, e in [(1, 201), (801, 1001)]:
            f.write(
                f'chr1\ttest\texon\t{s}\t{e}\t.\t+\t.\t'
                f'gene_id "gene1"; transcript_id "gene1_B";\n'
            )

    exons_A = [(0, 200), (400, 600), (800, 1000)]
    exons_B = [(0, 200), (800, 1000)]

    with open(out / "reads.fastq", "w") as f:
        for i in range(200):
            exons = exons_A if rng.random() < 0.7 else exons_B
            seq = "".join(genome[s:e] for s, e in exons)
            seq = add_errors(seq, 0.03, rng)
            qual = random_qual(len(seq), rng, 25, 40)
            f.write(f"@read_{i}\n{seq}\n+\n{qual}\n")

    with open(out / "truth.json", "w") as f:
        json.dump(
            {"gene1_A_fraction": 0.7, "gene1_B_fraction": 0.3, "n_isoforms": 2},
            f,
            indent=2,
        )
    print(f"  rna_isoform: {out}")


# ──────────── Prompts ────────────
def write_prompts():
    prompts = {
        "dna_sv": (
            "Align these Oxford Nanopore DNA reads to the reference genome and call "
            "structural variants. Report any deletions, insertions, or inversions found."
        ),
        "assembly": (
            "Assemble these Oxford Nanopore reads into a de novo genome assembly. "
            "The organism is a small bacterium. Report the assembly in FASTA format."
        ),
        "rna_isoform": (
            "These are Oxford Nanopore direct-RNA reads. Align them to the reference "
            "genome using the provided annotation and quantify transcript isoforms. "
            "Report per-isoform abundance estimates."
        ),
        # Stress variants
        "dna_sv_pacbio": (
            "Align these PacBio HiFi DNA reads to the reference genome and call "
            "structural variants. Report any deletions, insertions, or inversions."
        ),
        "dna_sv_noisy_prompt": (
            "I have some long sequencing reads and a reference genome. Can you figure "
            "out if there are any big structural changes in my sample compared to the "
            "reference?"
        ),
        "assembly_meta": (
            "Assemble these Oxford Nanopore reads into a metagenome assembly. "
            "There may be multiple organisms present. Report contigs in FASTA format."
        ),
        "assembly_malformed": (
            "Assemble these Oxford Nanopore reads into a de novo genome assembly. "
            "The organism is a small bacterium. Report the assembly in FASTA format."
        ),
        "rna_isoform_no_annot": (
            "These are Oxford Nanopore direct-RNA reads. Align them to the reference "
            "genome and quantify transcript isoforms. No annotation file is provided."
        ),
        "dna_sv_nested_output": (
            "Align these Oxford Nanopore DNA reads to the reference genome and call "
            "structural variants. Save all results to results/variants/ directory."
        ),
    }
    for name, text in prompts.items():
        p = BASE / name / "prompt.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")
    print("  prompts written")


# ──────────── Stress variant data ────────────
def create_stress_variants():
    # dna_sv_pacbio / dna_sv_noisy_prompt / dna_sv_nested_output: reuse dna_sv data
    src = BASE / "dna_sv" / "data"
    for variant in ["dna_sv_pacbio", "dna_sv_noisy_prompt", "dna_sv_nested_output"]:
        dst = BASE / variant / "data"
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            shutil.copy2(f, dst / f.name)

    # assembly_meta: reuse assembly data
    src = BASE / "assembly" / "data"
    dst = BASE / "assembly_meta" / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        shutil.copy2(f, dst / f.name)

    # assembly_malformed: truncated FASTQ
    dst = BASE / "assembly_malformed" / "data"
    dst.mkdir(parents=True, exist_ok=True)
    lines = (BASE / "assembly" / "data" / "reads.fastq").read_text().splitlines()
    # chop off last 20 lines (corrupt end of file mid-read)
    truncated = "\n".join(lines[: -20]) + "\nTRUNCAT"
    (dst / "reads.fastq").write_text(truncated)
    # copy truth
    shutil.copy2(BASE / "assembly" / "data" / "truth.json", dst / "truth.json")

    # rna_isoform_no_annot: reads + ref but no GTF
    dst = BASE / "rna_isoform_no_annot" / "data"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE / "rna_isoform" / "data" / "ref.fasta", dst / "ref.fasta")
    shutil.copy2(BASE / "rna_isoform" / "data" / "reads.fastq", dst / "reads.fastq")
    shutil.copy2(BASE / "rna_isoform" / "data" / "truth.json", dst / "truth.json")

    print("  stress variants created")


def main():
    print("Creating long-read benchmark data...")
    create_dna_sv()
    create_assembly()
    create_rna_isoform()
    write_prompts()
    create_stress_variants()
    print("Done.")


if __name__ == "__main__":
    main()
