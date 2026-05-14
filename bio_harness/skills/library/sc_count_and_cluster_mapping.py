"""Genome indexing and counting helpers for the single-cell counting skill."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict

from bio_harness.skills.library.sc_count_and_cluster_fastq import (
    load_whitelist_barcodes,
    read_fastq_pairs,
)


def parse_gtf_genes(gtf_path: str) -> list[tuple[str, str, int, int]]:
    """Parse exon coordinates from a GTF file."""

    genes: list[tuple[str, str, int, int]] = []
    with open(gtf_path) as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            fields = line.strip().split("\t")
            if len(fields) < 9 or fields[2] != "exon":
                continue
            chrom = fields[0]
            start = int(fields[3]) - 1
            end = int(fields[4])
            gene_name = "unknown"
            marker = 'gene_id "'
            if marker in fields[8]:
                gene_name = fields[8].split(marker, 1)[1].split('"', 1)[0]
            genes.append((gene_name, chrom, start, end))
    return genes


def load_genome(fasta_path: str) -> dict[str, str]:
    """Load a FASTA file into a chromosome-to-sequence mapping."""

    sequences: dict[str, str] = {}
    current = ""
    parts: list[str] = []
    with open(fasta_path) as handle:
        for line in handle:
            line = line.strip()
            if line.startswith(">"):
                if current:
                    sequences[current] = "".join(parts)
                current = line[1:].split()[0]
                parts = []
            else:
                parts.append(line)
    if current:
        sequences[current] = "".join(parts)
    return sequences


def build_gene_kmer_index(
    genes: list[tuple[str, str, int, int]],
    genome: dict[str, str],
    k: int = 30,
) -> dict[str, str]:
    """Build a simple k-mer to gene index for approximate read mapping."""

    index: dict[str, str] = {}
    for gene_name, chrom, start, end in genes:
        seq = genome.get(chrom, "")[start:end]
        for offset in range(0, len(seq) - k + 1, 5):
            kmer = seq[offset : offset + k]
            if kmer not in index:
                index[kmer] = gene_name
            elif index[kmer] != gene_name:
                index[kmer] = "__ambig__"
    return {kmer: gene for kmer, gene in index.items() if gene != "__ambig__"}


def map_read_to_gene(seq: str, kmer_index: dict[str, str], k: int = 30) -> str | None:
    """Map one cDNA read to a gene using simple k-mer voting."""

    votes: dict[str, int] = defaultdict(int)
    for offset in range(0, len(seq) - k + 1, 5):
        gene = kmer_index.get(seq[offset : offset + k])
        if gene:
            votes[gene] += 1
    if not votes:
        return None
    best_gene = max(votes, key=lambda gene: votes[gene])
    if votes[best_gene] >= 2:
        return best_gene
    return None


def count_matrix(
    r1_path: str,
    r2_path: str,
    whitelist_path: str,
    reference_fasta: str,
    gtf_path: str,
    barcode_len: int = 16,
    umi_len: int = 12,
    kmer_size: int = 30,
) -> tuple[dict[str, dict[str, int]], set[str]]:
    """Build a cell-by-gene count matrix from 10x-style FASTQs."""

    whitelist = load_whitelist_barcodes(whitelist_path, barcode_len=barcode_len)
    print(f"  Whitelist: {len(whitelist)} barcodes", file=sys.stderr)

    genome = load_genome(reference_fasta)
    genes = parse_gtf_genes(gtf_path)
    print(f"  Genes: {len(genes)}", file=sys.stderr)
    kmer_index = build_gene_kmer_index(genes, genome, k=kmer_size)
    print(f"  Kmer index: {len(kmer_index)} {kmer_size}-mers", file=sys.stderr)

    gene_names = sorted({gene[0] for gene in genes})
    counts: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    total = 0
    matched_bc = 0
    mapped = 0

    for _name, r1_seq, r2_seq in read_fastq_pairs(r1_path, r2_path):
        total += 1
        barcode = r1_seq[:barcode_len]
        umi = r1_seq[barcode_len : barcode_len + umi_len]
        if barcode not in whitelist:
            continue
        matched_bc += 1

        gene = map_read_to_gene(r2_seq, kmer_index, k=kmer_size)
        if gene:
            counts[barcode][gene].add(umi)
            mapped += 1

    print(f"  Total reads: {total}", file=sys.stderr)
    print(f"  Barcode matched: {matched_bc} ({100*matched_bc/max(total,1):.1f}%)", file=sys.stderr)
    print(f"  Gene mapped: {mapped} ({100*mapped/max(total,1):.1f}%)", file=sys.stderr)

    count_output: dict[str, dict[str, int]] = {}
    for barcode in counts:
        count_output[barcode] = {}
        for gene in counts[barcode]:
            count_output[barcode][gene] = len(counts[barcode][gene])

    print(f"  Cells with reads: {len(count_output)}", file=sys.stderr)
    if count_output:
        return count_output, set(gene_names)

    print(
        "  No reads mapped to annotated genes; falling back to alignment-free k-mer features.",
        file=sys.stderr,
    )
    return count_matrix_from_kmers(
        r1_path=r1_path,
        r2_path=r2_path,
        whitelist=whitelist,
        barcode_len=barcode_len,
        kmer_size=min(max(11, kmer_size), 15),
    )


def count_matrix_from_kmers(
    *,
    r1_path: str,
    r2_path: str,
    whitelist: set[str],
    barcode_len: int = 16,
    kmer_size: int = 15,
    stride: int = 10,
    min_feature_occurrences: int = 5,
    max_features: int = 400,
) -> tuple[dict[str, dict[str, int]], set[str]]:
    """Build a barcode-by-k-mer count matrix when gene mapping has no signal."""

    feature_counts: Counter[str] = Counter()
    rows: list[tuple[str, list[str]]] = []
    for _name, r1_seq, r2_seq in read_fastq_pairs(r1_path, r2_path):
        barcode = r1_seq[:barcode_len]
        if barcode not in whitelist:
            continue
        kmers = [
            r2_seq[offset : offset + kmer_size]
            for offset in range(0, max(0, len(r2_seq) - kmer_size + 1), stride)
            if len(r2_seq[offset : offset + kmer_size]) == kmer_size
        ]
        if not kmers:
            continue
        rows.append((barcode, kmers))
        feature_counts.update(kmers)

    selected_features = [
        kmer
        for kmer, count in feature_counts.most_common(max_features)
        if count >= min_feature_occurrences
    ]
    if not selected_features:
        selected_features = [kmer for kmer, _count in feature_counts.most_common(max_features)]
    selected = set(selected_features)

    count_output: dict[str, dict[str, int]] = defaultdict(dict)
    for barcode, kmers in rows:
        barcode_counts = count_output.setdefault(barcode, {})
        for kmer in kmers:
            if kmer not in selected:
                continue
            feature_name = f"kmer_{kmer}"
            barcode_counts[feature_name] = int(barcode_counts.get(feature_name, 0)) + 1

    feature_names = {f"kmer_{kmer}" for kmer in selected}
    print(
        f"  Alignment-free fallback: {len(count_output)} cells × {len(feature_names)} k-mer features",
        file=sys.stderr,
    )
    return count_output, feature_names
