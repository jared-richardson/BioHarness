"""FASTQ and whitelist helpers for the single-cell counting skill."""

from __future__ import annotations

import gzip
import re
import sys
from collections import Counter
from pathlib import Path


def _open_fastq(path: str):
    """Open plain or gzipped FASTQ."""

    text = str(path)
    if text.endswith(".gz"):
        return gzip.open(text, "rt")
    return open(text, "r")


def read_fastq_pairs(r1_path: str, r2_path: str):
    """Yield paired ``(name, r1_seq, r2_seq)`` records from two FASTQs."""

    with _open_fastq(r1_path) as f1, _open_fastq(r2_path) as f2:
        while True:
            name1 = f1.readline().strip()
            seq1 = f1.readline().strip()
            f1.readline()
            f1.readline()
            name2 = f2.readline().strip()
            seq2 = f2.readline().strip()
            f2.readline()
            f2.readline()
            if not name1 or not name2:
                break
            yield name1, seq1, seq2


def _read_fastq_sequences(path: str):
    """Yield sequence strings from one FASTQ, plain or gzipped."""

    with _open_fastq(path) as handle:
        while True:
            name = handle.readline()
            if not name:
                break
            seq = handle.readline().strip()
            handle.readline()
            handle.readline()
            yield seq


def infer_barcode_whitelist(
    r1_path: str,
    *,
    barcode_len: int = 16,
    min_observations: int = 2,
) -> list[str]:
    """Infer a deterministic barcode whitelist from observed R1 prefixes."""

    counts: Counter[str] = Counter()
    for sequence in _read_fastq_sequences(r1_path):
        barcode = sequence[:barcode_len]
        if barcode:
            counts[barcode] += 1
    if not counts:
        raise ValueError(f"No barcode sequences could be inferred from {r1_path}")

    retained = [
        barcode
        for barcode, count in counts.most_common()
        if count >= max(1, int(min_observations))
    ]
    if retained:
        return retained
    return [barcode for barcode, _count in counts.most_common()]


def _normalize_whitelist_barcode(raw_barcode: str, *, barcode_len: int) -> str:
    """Normalize one whitelist barcode entry to the raw R1 barcode form."""

    text = str(raw_barcode or "").strip().upper()
    if not text:
        return ""
    match = re.match(r"^([ACGTN]+)(?:-\d+)?$", text)
    if not match:
        return ""
    barcode = match.group(1)
    if len(barcode) != barcode_len:
        return ""
    return barcode


def load_whitelist_barcodes(
    whitelist_path: str,
    *,
    barcode_len: int = 16,
) -> set[str]:
    """Load normalized barcode entries from a whitelist file."""

    barcodes: set[str] = set()
    with open(whitelist_path) as handle:
        for line in handle:
            barcode = _normalize_whitelist_barcode(line, barcode_len=barcode_len)
            if barcode:
                barcodes.add(barcode)
    return barcodes


def resolve_whitelist_path(
    whitelist_path: str,
    *,
    r1_path: str,
    output_dir: str,
    barcode_len: int = 16,
) -> str:
    """Return an existing or deterministically inferred barcode whitelist path."""

    candidate = str(whitelist_path or "").strip()
    if candidate and Path(candidate).expanduser().exists():
        normalized = load_whitelist_barcodes(candidate, barcode_len=barcode_len)
        if normalized:
            return str(Path(candidate).expanduser().resolve(strict=False))
        print(
            f"  WARNING: Whitelist {candidate} had no usable {barcode_len}bp barcode entries; inferring from R1 instead.",
            file=sys.stderr,
        )
    elif candidate:
        print(
            f"  WARNING: Whitelist {candidate} was not found; inferring barcodes from R1 instead.",
            file=sys.stderr,
        )
    else:
        print(
            "  No whitelist provided; inferring barcodes from observed R1 prefixes.",
            file=sys.stderr,
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    inferred_path = out_dir / "inferred_barcodes_whitelist.txt"
    barcodes = infer_barcode_whitelist(r1_path, barcode_len=barcode_len)
    inferred_path.write_text("".join(f"{barcode}\n" for barcode in barcodes), encoding="utf-8")
    print(f"  Inferred whitelist: {len(barcodes)} barcodes -> {inferred_path}", file=sys.stderr)
    return str(inferred_path.resolve(strict=False))
