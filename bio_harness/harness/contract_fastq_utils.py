"""FASTQ sample tag, mate, and pair discovery helpers for contract utilities."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.harness.path_utils import _discover_fastq_files


def _extract_fastq_sample_tag(text: str) -> str:
    raw = Path(str(text or "").strip()).name
    if not raw:
        return ""
    match = re.search(r"(?:^|[_-])(S\d+)(?:[_-]|$)", raw, flags=re.IGNORECASE)
    if match:
        return str(match.group(1)).upper()
    match = re.search(r"(?:^|[_-])([A-Za-z]+[0-9]+)(?:[_-]|$)", raw, flags=re.IGNORECASE)
    if match:
        return str(match.group(1)).upper()
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", raw.lower()) if tok]
    ignore = {
        "r1",
        "r2",
        "fastq",
        "fq",
        "fasta",
        "fa",
        "fna",
        "gz",
        "bam",
        "sam",
        "vcf",
        "gff",
        "gff3",
        "faa",
        "ffn",
        "sorted",
        "dedup",
        "aligned",
        "annotated",
        "annotation",
        "alignments",
        "assembly",
        "spades",
        "contigs",
        "scaffolds",
        "variants",
        "reads",
        "read",
        "output",
    }
    for token in reversed(tokens):
        if token in ignore or len(token) < 3:
            continue
        return token.upper()
    return ""


def _sample_tag_kind(sample_tag: str) -> str:
    tag = str(sample_tag or "").strip().lower()
    if not tag:
        return ""
    if any(term in tag for term in ("anc", "ancestor")):
        return "ancestor"
    if any(term in tag for term in ("evol", "evolved", "isolate", "mutant")):
        return "evolved"
    return ""


def _infer_evolution_step_sample_tag(args: dict[str, Any]) -> str:
    probe_keys = (
        "reads_1",
        "reads_2",
        "input_fasta",
        "reference_fasta",
        "output_bam",
        "output_sam",
        "input_bam",
        "input_vcf",
        "output_vcf_gz",
        "output_vcf",
        "output_dir",
        "output_gff",
        "output_faa",
        "annotation_gff",
    )
    for probe_key in probe_keys:
        sample_tag = _extract_fastq_sample_tag(str(args.get(probe_key, "")).strip())
        if sample_tag:
            return sample_tag
    return ""


def _resolve_sample_pair(sample_pairs: dict[str, dict[str, str]], sample_tag: str) -> tuple[str, dict[str, str]]:
    tag = str(sample_tag or "").strip().upper()
    if not tag:
        return "", {}
    if tag in sample_pairs:
        return tag, sample_pairs[tag]
    kind = _sample_tag_kind(tag)
    if kind == "ancestor":
        for key, pair in sample_pairs.items():
            if _sample_tag_kind(key) == "ancestor":
                return key, pair
    digits_match = re.search(r"(\d+)$", tag)
    if digits_match:
        suffix = digits_match.group(1)
        matches = [(key, pair) for key, pair in sample_pairs.items() if re.search(rf"{re.escape(suffix)}$", str(key))]
        if len(matches) == 1:
            return matches[0]
    return "", {}


def _extract_fastq_mate(text: str) -> str:
    raw = Path(str(text or "").strip()).name.lower()
    if not raw:
        return ""
    for token in reversed([tok for tok in re.split(r"[^a-z0-9]+", raw) if tok]):
        if token == "r1" or token == "1":
            return "r1"
        if token == "r2" or token == "2":
            return "r2"
    return ""


def _discover_fastq_pair_map(data_root: Path) -> dict[str, dict[str, str]]:
    discovered = _discover_fastq_files(str(data_root), True, "", 5000)
    pair_map: dict[str, dict[str, str]] = {}
    for raw_path in discovered:
        sample_tag = _extract_fastq_sample_tag(raw_path)
        mate = _extract_fastq_mate(raw_path)
        if not sample_tag or not mate:
            continue
        entry = pair_map.setdefault(sample_tag, {})
        entry[mate] = raw_path
    return pair_map
