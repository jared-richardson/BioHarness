#!/usr/bin/env python3
"""Create metagenomics benchmark data with synthetic paired-end reads from real bacterial genomes.

Downloads 3 reference genomes from NCBI (cached locally), generates 3000 paired-end read pairs
at known species proportions, and writes gzipped FASTQ files + truth.json.

Species mix:
  - Escherichia coli K-12 MG1655 (taxid 562)   — 60%
  - Bacillus subtilis 168 (taxid 1423)          — 30%
  - Staphylococcus aureus NCTC 8325 (taxid 1280)— 10%
"""

from __future__ import annotations

import gzip
import json
import random
import sys
import urllib.request
from pathlib import Path


# ── Configuration ──────────────────────────────────────────────────────────

SPECIES = [
    {
        "name": "Escherichia coli",
        "taxid": 562,
        "proportion": 0.60,
        "accession": "NC_000913.3",
        "url": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/005/845/GCF_000005845.2_ASM584v2/GCF_000005845.2_ASM584v2_genomic.fna.gz",
    },
    {
        "name": "Bacillus subtilis",
        "taxid": 1423,
        "proportion": 0.30,
        "accession": "NC_000964.3",
        "url": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/009/045/GCF_000009045.1_ASM904v1/GCF_000009045.1_ASM904v1_genomic.fna.gz",
    },
    {
        "name": "Staphylococcus aureus",
        "taxid": 1280,
        "proportion": 0.10,
        "accession": "NC_007795.1",
        "url": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/013/425/GCF_000013425.1_ASM1342v1/GCF_000013425.1_ASM1342v1_genomic.fna.gz",
    },
]

TOTAL_READ_PAIRS = 3000
READ_LENGTH = 150
FRAGMENT_SIZE = 350
ERROR_RATE = 0.005
SEED = 42
PHRED_BASE = 33


# ── Helpers ────────────────────────────────────────────────────────────────

COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")


def reverse_complement(seq: str) -> str:
    return seq.translate(COMPLEMENT)[::-1]


def download_genome(url: str, dest: Path) -> str:
    """Download and decompress a genome FASTA, returning the concatenated sequence."""
    if dest.exists():
        print(f"  [cache] {dest.name}")
    else:
        print(f"  [download] {url} → {dest.name}")
        urllib.request.urlretrieve(url, str(dest) + ".gz")
        with gzip.open(str(dest) + ".gz", "rt") as fin, open(dest, "w") as fout:
            fout.write(fin.read())
        Path(str(dest) + ".gz").unlink(missing_ok=True)

    # Parse FASTA — concatenate all contigs (usually 1 chromosome)
    seq_parts: list[str] = []
    with open(dest) as fh:
        for line in fh:
            if not line.startswith(">"):
                seq_parts.append(line.strip().upper())
    return "".join(seq_parts)


def add_errors(seq: str, rng: random.Random, rate: float) -> str:
    bases = list(seq)
    for i in range(len(bases)):
        if rng.random() < rate:
            bases[i] = rng.choice([b for b in "ACGT" if b != bases[i]])
    return "".join(bases)


def make_qual_string(length: int, rng: random.Random) -> str:
    """Generate realistic Illumina-like quality scores (Phred33, variable Q20-Q40).

    Quality degrades slightly toward the 3' end, as in real Illumina data.
    """
    quals = []
    for i in range(length):
        # Base quality: high at start (Q35-Q40), slightly lower at end (Q20-Q35)
        fraction = i / length
        mean_q = 37 - 12 * fraction  # 37 at start, 25 at end
        q = int(rng.gauss(mean_q, 3))
        q = max(2, min(q, 40))  # Clamp to valid Phred range
        quals.append(chr(PHRED_BASE + q))
    return "".join(quals)


def generate_read_pair(
    genome: str, rng: random.Random, read_idx: int
) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    """Generate a paired-end read pair (R1, R2) from a random genome position.

    Returns ((header1, seq1, qual1), (header2, seq2, qual2)).
    """
    max_start = len(genome) - FRAGMENT_SIZE
    if max_start < 1:
        max_start = 1
    pos = rng.randint(0, max_start)
    fragment = genome[pos : pos + FRAGMENT_SIZE]

    r1_seq = add_errors(fragment[:READ_LENGTH], rng, ERROR_RATE)
    r2_seq = add_errors(reverse_complement(fragment[-READ_LENGTH:]), rng, ERROR_RATE)

    header = f"@read_{read_idx} pos={pos}"
    qual1 = make_qual_string(READ_LENGTH, rng)
    qual2 = make_qual_string(READ_LENGTH, rng)
    return (header + "/1", r1_seq, qual1), (header + "/2", r2_seq, qual2)


# ── Main ───────────────────────────────────────────────────────────────────

def main(output_dir: str | None = None):
    out = Path(output_dir or "benchmark_data/metagenomics")
    ref_dir = out / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)

    # 1. Download reference genomes
    print("Downloading reference genomes...")
    genomes: dict[str, str] = {}
    for sp in SPECIES:
        dest = ref_dir / f"{sp['accession']}.fna"
        genomes[sp["accession"]] = download_genome(sp["url"], dest)
        print(f"    {sp['name']}: {len(genomes[sp['accession']]):,} bp")

    # 2. Generate read pairs
    print(f"\nGenerating {TOTAL_READ_PAIRS} paired-end read pairs...")
    all_reads: list[tuple[tuple[str, str, str], tuple[str, str, str], str]] = []
    species_counts: dict[str, int] = {}

    for sp in SPECIES:
        n_pairs = int(sp["proportion"] * TOTAL_READ_PAIRS)
        species_counts[sp["name"]] = n_pairs
        genome = genomes[sp["accession"]]
        for i in range(n_pairs):
            r1, r2 = generate_read_pair(genome, rng, len(all_reads))
            all_reads.append((r1, r2, sp["name"]))
        print(f"  {sp['name']}: {n_pairs} pairs ({sp['proportion']*100:.0f}%)")

    # Shuffle deterministically
    rng.shuffle(all_reads)

    # 3. Write FASTQ files
    r1_path = out / "sample_R1.fastq.gz"
    r2_path = out / "sample_R2.fastq.gz"
    print(f"\nWriting {r1_path} and {r2_path}...")

    with gzip.open(r1_path, "wt") as f1, gzip.open(r2_path, "wt") as f2:
        for r1, r2, _species in all_reads:
            f1.write(f"{r1[0]}\n{r1[1]}\n+\n{r1[2]}\n")
            f2.write(f"{r2[0]}\n{r2[1]}\n+\n{r2[2]}\n")

    # 4. Write truth.json
    truth = {
        "species": [
            {
                "name": sp["name"],
                "taxid": sp["taxid"],
                "proportion": sp["proportion"],
                "read_pairs": species_counts[sp["name"]],
            }
            for sp in SPECIES
        ],
        "total_read_pairs": TOTAL_READ_PAIRS,
        "min_classification_rate": 0.80,
        "expected_top_genus": ["Escherichia", "Bacillus", "Staphylococcus"],
    }
    truth_path = out / "truth.json"
    truth_path.write_text(json.dumps(truth, indent=2) + "\n")
    print(f"\nTruth file: {truth_path}")
    print(f"  Total pairs: {TOTAL_READ_PAIRS}")
    print(f"  Species: {', '.join(sp['name'] for sp in SPECIES)}")

    print("\n✓ Benchmark data created successfully.")
    return 0


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(output))
