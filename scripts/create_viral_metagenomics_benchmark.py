#!/usr/bin/env python3
"""Create viral metagenomics benchmark data with synthetic paired-end reads from viral RefSeq genomes.

Downloads 3 well-known viral reference genomes from NCBI, generates 3000 paired-end
read pairs at known proportions, and writes gzipped FASTQ files + truth.json.

Virus mix:
  - Bacteriophage Lambda (NC_001416.1)   — 50% (1500 pairs)
  - SARS-CoV-2 Wuhan-Hu-1 (NC_045512.2) — 30% (900 pairs)
  - HIV-1 HXB2 (NC_001802.1)             — 20% (600 pairs)
"""

from __future__ import annotations

import gzip
import json
import random
import sys
import urllib.request
from pathlib import Path


# ── Configuration ──────────────────────────────────────────────────────────

VIRUSES = [
    {
        "name": "Bacteriophage_Lambda",
        "accession": "NC_001416.1",
        "proportion": 0.50,
        "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nucleotide&id=NC_001416.1&rettype=fasta&retmode=text",
    },
    {
        "name": "SARS-CoV-2",
        "accession": "NC_045512.2",
        "proportion": 0.30,
        "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nucleotide&id=NC_045512.2&rettype=fasta&retmode=text",
    },
    {
        "name": "HIV-1_HXB2",
        "accession": "NC_001802.1",
        "proportion": 0.20,
        "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nucleotide&id=NC_001802.1&rettype=fasta&retmode=text",
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


def download_genome(url: str, dest: Path, accession: str) -> str:
    """Download a viral genome FASTA via NCBI efetch, standardize header, return sequence."""
    if dest.exists():
        print(f"  [cache] {dest.name}")
    else:
        print(f"  [download] {accession} -> {dest.name}")
        req = urllib.request.Request(url, headers={"User-Agent": "bio_harness/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")

        # Rewrite FASTA header to just accession (e.g., >NC_001416.1)
        lines = raw.strip().split("\n")
        with open(dest, "w") as fout:
            for line in lines:
                if line.startswith(">"):
                    fout.write(f">{accession}\n")
                else:
                    fout.write(line.strip() + "\n")

    # Parse FASTA — concatenate sequence lines
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
    """Generate realistic Illumina-like quality scores (Phred33, variable Q20-Q40)."""
    quals = []
    for i in range(length):
        fraction = i / length
        mean_q = 37 - 12 * fraction  # 37 at start, 25 at end
        q = int(rng.gauss(mean_q, 3))
        q = max(2, min(q, 40))
        quals.append(chr(PHRED_BASE + q))
    return "".join(quals)


def generate_read_pair(
    genome: str, rng: random.Random, read_idx: int
) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    """Generate a paired-end read pair (R1, R2) from a random genome position."""
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
    out = Path(output_dir or "benchmark_data/viral_metagenomics")
    ref_dir = out / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)

    # 1. Download reference genomes
    print("Downloading viral reference genomes...")
    genomes: dict[str, str] = {}
    for v in VIRUSES:
        dest = ref_dir / f"{v['accession']}.fna"
        genomes[v["accession"]] = download_genome(v["url"], dest, v["accession"])
        print(f"    {v['name']} ({v['accession']}): {len(genomes[v['accession']]):,} bp")

    # 2. Generate read pairs
    print(f"\nGenerating {TOTAL_READ_PAIRS} paired-end read pairs...")
    all_reads: list[tuple[tuple[str, str, str], tuple[str, str, str], str]] = []
    virus_counts: dict[str, int] = {}

    for v in VIRUSES:
        n_pairs = int(v["proportion"] * TOTAL_READ_PAIRS)
        virus_counts[v["accession"]] = n_pairs
        genome = genomes[v["accession"]]
        for i in range(n_pairs):
            r1, r2 = generate_read_pair(genome, rng, len(all_reads))
            all_reads.append((r1, r2, v["accession"]))
        print(f"  {v['name']}: {n_pairs} pairs ({v['proportion']*100:.0f}%)")

    # Shuffle deterministically
    rng.shuffle(all_reads)

    # 3. Write FASTQ files
    r1_path = out / "sample_R1.fastq.gz"
    r2_path = out / "sample_R2.fastq.gz"
    print(f"\nWriting {r1_path} and {r2_path}...")

    with gzip.open(r1_path, "wt") as f1, gzip.open(r2_path, "wt") as f2:
        for r1, r2, _virus in all_reads:
            f1.write(f"{r1[0]}\n{r1[1]}\n+\n{r1[2]}\n")
            f2.write(f"{r2[0]}\n{r2[1]}\n+\n{r2[2]}\n")

    # 4. Write truth.json
    truth = {
        "viruses": [
            {
                "name": v["name"],
                "accession": v["accession"],
                "proportion": v["proportion"],
                "read_pairs": virus_counts[v["accession"]],
            }
            for v in VIRUSES
        ],
        "total_read_pairs": TOTAL_READ_PAIRS,
        "expected_viruses": [v["accession"] for v in VIRUSES],
        "expected_abundances": {
            "NC_001416.1": {"min": 0.35, "max": 0.65},
            "NC_045512.2": {"min": 0.15, "max": 0.45},
            "NC_001802.1": {"min": 0.05, "max": 0.35},
        },
        "min_coverage_pct": 50.0,
        "min_detected_viruses": 3,
    }
    truth_path = out / "truth.json"
    truth_path.write_text(json.dumps(truth, indent=2) + "\n")
    print(f"\nTruth file: {truth_path}")
    print(f"  Total pairs: {TOTAL_READ_PAIRS}")
    print(f"  Viruses: {', '.join(v['name'] for v in VIRUSES)}")

    print("\nDone. Benchmark data created successfully.")
    return 0


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(output))
