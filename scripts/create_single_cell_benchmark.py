#!/usr/bin/env python3
"""Create synthetic 10x Chromium scRNA-seq benchmark data.

Generates:
  - A small reference genome with embedded "genes"
  - GTF annotation for those genes
  - 10x-format FASTQs: Read 1 (16bp barcode + 12bp UMI), Read 2 (cDNA 90bp)
  - Barcode whitelist
  - Truth data: cell-type assignments, expected marker genes, count matrix

Architecture:
  - 3 cell types (TypeA, TypeB, TypeC), 30 cells each = 90 cells
  - 200 genes total: 15 marker genes per type + 155 shared/housekeeping
  - ~100 reads per cell = ~9000 total reads
  - Read 2 sequences sampled from gene bodies with simulated errors
"""

import gzip
import json
import random
from pathlib import Path

SEED = 42
N_CELL_TYPES = 3
CELLS_PER_TYPE = 30
N_GENES = 200
N_MARKERS_PER_TYPE = 15  # genes highly expressed in one type only
READS_PER_CELL = 100
GENE_LEN = 300  # bp per gene
INTERGENIC = 100  # bp between genes
READ2_LEN = 90
BARCODE_LEN = 16
UMI_LEN = 12
ERROR_RATE = 0.01  # 1% sequencing error rate

BASES = "ACGT"
CELL_TYPES = ["TypeA", "TypeB", "TypeC"]


def random_seq(length: int, rng: random.Random) -> str:
    return "".join(rng.choice(BASES) for _ in range(length))


def random_qual(length: int, rng: random.Random) -> str:
    """Generate random quality scores (Phred+33), mostly high quality."""
    return "".join(chr(rng.randint(30, 40) + 33) for _ in range(length))


def add_errors(seq: str, rate: float, rng: random.Random) -> str:
    result = list(seq)
    for i in range(len(result)):
        if rng.random() < rate:
            result[i] = rng.choice([b for b in BASES if b != result[i]])
    return "".join(result)


def generate_barcodes(n: int, rng: random.Random) -> list[str]:
    """Generate unique 16bp cell barcodes."""
    barcodes = set()
    while len(barcodes) < n:
        bc = random_seq(BARCODE_LEN, rng)
        barcodes.add(bc)
    return sorted(barcodes)


def build_expression_profiles(
    n_genes: int, n_markers: int, rng: random.Random
) -> dict[str, list[float]]:
    """Build per-cell-type expression profiles.

    Returns dict mapping cell_type -> list of relative expression weights.
    Marker genes have 20x higher expression in their type.
    """
    # Assign marker genes: first n_markers for TypeA, next for TypeB, etc.
    profiles: dict[str, list[float]] = {}
    for ct_idx, ct in enumerate(CELL_TYPES):
        weights = []
        for g in range(n_genes):
            base_expr = rng.uniform(0.5, 2.0)  # baseline expression
            marker_start = ct_idx * n_markers
            marker_end = marker_start + n_markers
            if marker_start <= g < marker_end:
                # This is a marker gene for this cell type
                base_expr *= 20.0
            weights.append(base_expr)
        profiles[ct] = weights
    return profiles


def main():
    rng = random.Random(SEED)

    base_dir = Path("workspace/benchmarks/bioagent-bench/tasks/single-cell")
    data_dir = base_dir / "data"
    results_dir = base_dir / "results"
    data_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    print("Generating synthetic single-cell RNA-seq benchmark...")

    # 1. Generate reference genome with genes
    gene_seqs: list[str] = []
    genome_seq = ""
    gene_names: list[str] = []
    gtf_lines: list[str] = []
    chrom = "chr_sc"

    for i in range(N_GENES):
        intergenic = random_seq(INTERGENIC, rng)
        gene_body = random_seq(GENE_LEN, rng)
        gene_seqs.append(gene_body)
        gene_name = f"Gene{i:04d}"
        gene_names.append(gene_name)

        gene_start = len(genome_seq) + len(intergenic) + 1  # 1-based
        gene_end = gene_start + GENE_LEN - 1
        genome_seq += intergenic + gene_body

        # GTF: gene and transcript entries
        attrs_gene = f'gene_id "{gene_name}"; gene_name "{gene_name}";'
        attrs_tx = f'gene_id "{gene_name}"; transcript_id "{gene_name}.1"; gene_name "{gene_name}";'
        gtf_lines.append(f"{chrom}\tsynthetic\tgene\t{gene_start}\t{gene_end}\t.\t+\t.\t{attrs_gene}")
        gtf_lines.append(f"{chrom}\tsynthetic\ttranscript\t{gene_start}\t{gene_end}\t.\t+\t.\t{attrs_tx}")
        gtf_lines.append(f"{chrom}\tsynthetic\texon\t{gene_start}\t{gene_end}\t.\t+\t.\t{attrs_tx}")

    # Add trailing sequence
    genome_seq += random_seq(INTERGENIC, rng)

    # Write genome FASTA
    genome_path = data_dir / "reference.fa"
    with open(genome_path, "w") as f:
        f.write(f">{chrom}\n")
        for i in range(0, len(genome_seq), 80):
            f.write(genome_seq[i : i + 80] + "\n")
    print(f"  Reference genome: {len(genome_seq)} bp, {N_GENES} genes -> {genome_path}")

    # Write GTF
    gtf_path = data_dir / "annotation.gtf"
    with open(gtf_path, "w") as f:
        for line in gtf_lines:
            f.write(line + "\n")
    print(f"  GTF annotation: {len(gtf_lines)} entries -> {gtf_path}")

    # 2. Generate cell barcodes and whitelist
    total_cells = N_CELL_TYPES * CELLS_PER_TYPE
    # Generate more barcodes than needed (whitelist typically larger)
    all_barcodes = generate_barcodes(total_cells + 200, rng)
    cell_barcodes = all_barcodes[:total_cells]

    whitelist_path = data_dir / "barcodes_whitelist.txt"
    with open(whitelist_path, "w") as f:
        for bc in all_barcodes:
            f.write(bc + "\n")
    print(f"  Barcode whitelist: {len(all_barcodes)} barcodes -> {whitelist_path}")

    # 3. Build expression profiles
    profiles = build_expression_profiles(N_GENES, N_MARKERS_PER_TYPE, rng)

    # 4. Generate 10x FASTQs and truth data
    truth_assignments: dict[str, str] = {}  # barcode -> cell_type
    truth_counts: dict[str, dict[str, int]] = {}  # barcode -> {gene: count}

    r1_path = data_dir / "sample_R1.fastq.gz"
    r2_path = data_dir / "sample_R2.fastq.gz"

    r1_handle = gzip.open(r1_path, "wt")
    r2_handle = gzip.open(r2_path, "wt")

    read_idx = 0
    for ct_idx, ct in enumerate(CELL_TYPES):
        weights = profiles[ct]
        total_w = sum(weights)
        probs = [w / total_w for w in weights]

        for cell_i in range(CELLS_PER_TYPE):
            bc_idx = ct_idx * CELLS_PER_TYPE + cell_i
            barcode = cell_barcodes[bc_idx]
            truth_assignments[barcode] = ct
            truth_counts[barcode] = {}

            # Vary reads per cell slightly
            n_reads = int(READS_PER_CELL * rng.uniform(0.7, 1.3))

            for _ in range(n_reads):
                # Pick a gene based on expression profile
                gene_idx = rng.choices(range(N_GENES), weights=probs, k=1)[0]
                gene_name = gene_names[gene_idx]
                truth_counts[barcode][gene_name] = truth_counts[barcode].get(gene_name, 0) + 1

                # Generate UMI
                umi = random_seq(UMI_LEN, rng)

                # Read 1: barcode + UMI (28 bp)
                r1_seq = barcode + umi
                r1_qual = random_qual(len(r1_seq), rng)

                # Read 2: fragment from gene body (with errors)
                gene_body = gene_seqs[gene_idx]
                start = rng.randint(0, max(0, len(gene_body) - READ2_LEN))
                r2_seq = gene_body[start : start + READ2_LEN]
                if len(r2_seq) < READ2_LEN:
                    r2_seq += random_seq(READ2_LEN - len(r2_seq), rng)
                r2_seq = add_errors(r2_seq, ERROR_RATE, rng)
                r2_qual = random_qual(len(r2_seq), rng)

                read_name = f"@SIM:{read_idx}:{barcode}:{umi}"
                r1_handle.write(f"{read_name}\n{r1_seq}\n+\n{r1_qual}\n")
                r2_handle.write(f"{read_name}\n{r2_seq}\n+\n{r2_qual}\n")
                read_idx += 1

    r1_handle.close()
    r2_handle.close()
    print(f"  Generated {read_idx} reads across {total_cells} cells")
    print(f"  Read 1 (barcode+UMI): {r1_path}")
    print(f"  Read 2 (cDNA): {r2_path}")

    # 5. Write truth data
    # Cell type assignments
    assignments_path = results_dir / "truth_cell_types.json"
    with open(assignments_path, "w") as f:
        json.dump(truth_assignments, f, indent=2)
    print(f"  Truth cell types: {assignments_path}")

    # Marker genes
    markers: dict[str, list[str]] = {}
    for ct_idx, ct in enumerate(CELL_TYPES):
        start = ct_idx * N_MARKERS_PER_TYPE
        end = start + N_MARKERS_PER_TYPE
        markers[ct] = [gene_names[g] for g in range(start, end)]
    markers_path = results_dir / "truth_markers.json"
    with open(markers_path, "w") as f:
        json.dump(markers, f, indent=2)
    print(f"  Truth markers: {markers_path}")

    # Count matrix (for validation)
    counts_path = results_dir / "truth_counts.json"
    with open(counts_path, "w") as f:
        json.dump(truth_counts, f, indent=2)
    print(f"  Truth counts: {counts_path}")

    # Summary
    print(f"\n  Cell types: {CELL_TYPES}")
    for ct_idx, ct in enumerate(CELL_TYPES):
        start = ct_idx * N_MARKERS_PER_TYPE
        end = start + N_MARKERS_PER_TYPE
        print(f"    {ct}: {CELLS_PER_TYPE} cells, markers Gene{start:04d}-Gene{end-1:04d}")


if __name__ == "__main__":
    main()
