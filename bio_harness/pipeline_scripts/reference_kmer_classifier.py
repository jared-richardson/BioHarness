"""Shared exact-kmer reference classification helpers for small benchmark panels."""

from __future__ import annotations

import gzip
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Iterator, Sequence

from Bio import SeqIO


@dataclass(frozen=True)
class ReferenceRecord:
    """Normalized reference metadata used by k-mer classifiers."""

    accession: str
    description: str
    sequence: str


def _open_text(path: Path):
    """Open a plain-text or gzip-compressed file for reading."""

    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def reverse_complement(sequence: str) -> str:
    """Return the reverse complement of a nucleotide sequence."""

    table = str.maketrans("ACGTNacgtn", "TGCANtgcan")
    return sequence.translate(table)[::-1]


def canonical_kmer(sequence: str) -> str:
    """Return the canonical strand-invariant representation of a k-mer."""

    forward = sequence.upper()
    reverse = reverse_complement(forward)
    return min(forward, reverse)


def load_reference_records(reference_paths: Sequence[Path]) -> list[ReferenceRecord]:
    """Load FASTA references into normalized records."""

    records: list[ReferenceRecord] = []
    for reference_path in reference_paths:
        for record in SeqIO.parse(str(reference_path), "fasta"):
            records.append(
                ReferenceRecord(
                    accession=str(record.id),
                    description=str(record.description),
                    sequence=str(record.seq).upper(),
                )
            )
    return records


def build_reference_index(
    records: Sequence[ReferenceRecord],
    *,
    kmer_size: int,
) -> dict[str, dict[str, list[int]]]:
    """Build a canonical-kmer index for small reference panels."""

    index: dict[str, dict[str, list[int]]] = {}
    for record in records:
        limit = max(len(record.sequence) - kmer_size + 1, 0)
        for offset in range(limit):
            kmer = record.sequence[offset : offset + kmer_size]
            if "N" in kmer:
                continue
            bucket = index.setdefault(canonical_kmer(kmer), {})
            bucket.setdefault(record.accession, []).append(offset)
    return index


def iter_fastq_pairs(reads_1: Path, reads_2: Path) -> Iterator[tuple[str, str]]:
    """Yield paired-end FASTQ sequences from two FASTQ files."""

    with _open_text(reads_1) as handle_1, _open_text(reads_2) as handle_2:
        while True:
            header_1 = handle_1.readline()
            header_2 = handle_2.readline()
            if not header_1 and not header_2:
                return
            if not header_1 or not header_2:
                raise ValueError("FASTQ pair files ended at different offsets.")
            seq_1 = handle_1.readline().strip().upper()
            seq_2 = handle_2.readline().strip().upper()
            plus_1 = handle_1.readline()
            plus_2 = handle_2.readline()
            qual_1 = handle_1.readline()
            qual_2 = handle_2.readline()
            if not (seq_1 and seq_2 and plus_1 and plus_2 and qual_1 and qual_2):
                raise ValueError("Encountered truncated FASTQ record.")
            yield seq_1, seq_2


def score_read_pair(
    read_pair: tuple[str, str],
    *,
    index: dict[str, dict[str, list[int]]],
    kmer_size: int,
) -> tuple[Counter[str], dict[str, set[int]]]:
    """Score a read pair against a reference index."""

    votes: Counter[str] = Counter()
    covered_positions: DefaultDict[str, set[int]] = defaultdict(set)
    for sequence in read_pair:
        upper = sequence.upper()
        limit = max(len(upper) - kmer_size + 1, 0)
        for offset in range(limit):
            kmer = upper[offset : offset + kmer_size]
            if "N" in kmer:
                continue
            hits = index.get(canonical_kmer(kmer), {})
            for accession, positions in hits.items():
                votes[accession] += len(positions)
                for position in positions:
                    covered_positions[accession].update(range(position, position + kmer_size))
    return votes, {accession: set(positions) for accession, positions in covered_positions.items()}


def choose_best_accession(votes: Counter[str]) -> str:
    """Choose the highest-scoring accession or return an empty string on ties."""

    if not votes:
        return ""
    most_common = votes.most_common()
    if len(most_common) >= 2 and most_common[0][1] == most_common[1][1]:
        return ""
    return str(most_common[0][0])


def discover_reference_fastas(reference_dir: Path) -> list[Path]:
    """Discover FASTA references under a directory."""

    return sorted(
        path
        for path in reference_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".fa", ".fasta", ".fna"}
    )
