#!/usr/bin/env python3
"""Create benchmark data for comparative genomics (minimap2 ANI computation).

Downloads 3 Enterobacteriaceae genomes from NCBI for a meaningful ANI gradient:
  1. E. coli K-12 MG1655 (NC_000913.3) — copied from metagenomics benchmark if available
  2. E. coli O157:H7 EDL933 — downloaded from NCBI (~98% ANI to K-12)
  3. Salmonella enterica Typhimurium LT2 — downloaded from NCBI (~82% ANI to E. coli)
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
import urllib.request
from pathlib import Path


GENOMES = [
    {
        "name": "E. coli K-12 MG1655",
        "file": "ecoli_k12.fna",
        "local_source": "benchmark_data/metagenomics/references/NC_000913.3.fna",
        "url": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/005/845/GCF_000005845.2_ASM584v2/GCF_000005845.2_ASM584v2_genomic.fna.gz",
    },
    {
        "name": "E. coli O157:H7 EDL933",
        "file": "ecoli_o157.fna",
        "local_source": None,
        "url": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/006/665/GCF_000006665.1_ASM666v1/GCF_000006665.1_ASM666v1_genomic.fna.gz",
    },
    {
        "name": "Salmonella enterica Typhimurium LT2",
        "file": "salmonella_lt2.fna",
        "local_source": None,
        "url": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/006/945/GCF_000006945.2_ASM694v2/GCF_000006945.2_ASM694v2_genomic.fna.gz",
    },
]

TRUTH = {
    "genomes": [
        {"name": g["name"], "file": g["file"]}
        for g in GENOMES
    ],
    "expected_pairs": [
        {"genome_a": "ecoli_k12", "genome_b": "ecoli_o157", "ani_min": 0.95, "ani_max": 1.0},
        {"genome_a": "ecoli_k12", "genome_b": "salmonella_lt2", "ani_min": 0.75, "ani_max": 0.90},
        {"genome_a": "ecoli_o157", "genome_b": "salmonella_lt2", "ani_min": 0.75, "ani_max": 0.90},
    ],
    "closest_pair": ["ecoli_k12", "ecoli_o157"],
    "min_pairs_with_alignment": 3,
}


def download_genome(url: str, dest: Path) -> None:
    """Download gzipped genome FASTA from NCBI FTP and decompress."""
    gz_path = Path(str(dest) + ".gz")
    print(f"  [download] {url}")
    urllib.request.urlretrieve(url, str(gz_path))
    with gzip.open(str(gz_path), "rt") as fin, open(dest, "w") as fout:
        fout.write(fin.read())
    gz_path.unlink(missing_ok=True)


def count_bases(path: Path) -> int:
    total = 0
    with open(path) as fh:
        for line in fh:
            if not line.startswith(">"):
                total += len(line.strip())
    return total


def main(output_dir: str | None = None):
    out = Path(output_dir or "benchmark_data/comparative_genomics")
    out.mkdir(parents=True, exist_ok=True)

    print("Setting up comparative genomics benchmark data...")
    for g in GENOMES:
        dest = out / g["file"]
        if dest.exists():
            bp = count_bases(dest)
            print(f"  [cache] {g['name']}: {bp:,} bp")
            continue

        # Try local copy first
        if g["local_source"]:
            src = Path(g["local_source"])
            if src.exists():
                shutil.copy2(src, dest)
                bp = count_bases(dest)
                print(f"  [copy] {g['name']}: {bp:,} bp (from {src})")
                continue

        # Download from NCBI
        download_genome(g["url"], dest)
        bp = count_bases(dest)
        print(f"  {g['name']}: {bp:,} bp")

    # Write truth.json
    truth_path = out / "truth.json"
    truth_path.write_text(json.dumps(TRUTH, indent=2) + "\n")
    print(f"\nTruth file: {truth_path}")
    print(f"Genomes: {[g['name'] for g in GENOMES]}")
    print("\nExpected ANI gradient:")
    for pair in TRUTH["expected_pairs"]:
        print(f"  {pair['genome_a']} <-> {pair['genome_b']}: {pair['ani_min']:.0%}-{pair['ani_max']:.0%}")
    print(f"Closest pair: {TRUTH['closest_pair']}")
    print("\n✓ Benchmark data created successfully.")
    return 0


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(output))
