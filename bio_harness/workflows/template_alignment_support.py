"""Alignment-output repair helpers for workflow template canonicalization."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.workflows.template_fastq_support import extract_sample_hint
from bio_harness.workflows.template_io_base import (
    dedupe_preserve_order,
    parse_path_tokens,
    render_path_tokens,
)

STRUCTURED_ALIGNMENT_BAM_TOOLS = {
    "bwa_mem_align",
    "bowtie2_align",
    "hisat2_align",
    "minimap2_align",
    "star_align",
    "star_2pass_align",
    "subread_align",
}
STAR_BAM_SUFFIX_RE = re.compile(r"Aligned\.out\.bam$", flags=re.IGNORECASE)
STAR_BAM_DOTTED_SUFFIX_RE = re.compile(r"\.Aligned\.out\.bam$", flags=re.IGNORECASE)
_GENERIC_BAM_NAME_TOKENS = frozenset(
    {"aligned", "alignment", "alignments", "bam", "markdup", "output", "outputs", "sorted"}
)


def alignment_bam_hints_for_step(tool_name: str, args: dict[str, Any]) -> list[str]:
    """Return likely BAM outputs from one structured alignment step."""

    tool_l = str(tool_name or "").strip().lower()
    hints: list[str] = []
    output_bam = str(args.get("output_bam", "")).strip()
    if tool_l in STRUCTURED_ALIGNMENT_BAM_TOOLS and output_bam:
        hints.append(output_bam)
    if tool_l in {"star_align", "star_2pass_align"}:
        prefix = str(args.get("output_prefix", "")).strip()
        if prefix:
            hints.append(f"{prefix}Aligned.out.bam")
    return dedupe_preserve_order(hints)


def candidate_star_bam_variants(path_value: str) -> list[str]:
    """Return alternate STAR BAM suffix variants for one path."""

    raw = str(path_value or "").strip()
    if not raw:
        return []
    variants: list[str] = []
    if STAR_BAM_DOTTED_SUFFIX_RE.search(raw):
        variants.append(STAR_BAM_DOTTED_SUFFIX_RE.sub("Aligned.out.bam", raw))
    elif STAR_BAM_SUFFIX_RE.search(raw):
        variants.append(STAR_BAM_SUFFIX_RE.sub(".Aligned.out.bam", raw))
    return dedupe_preserve_order([variant for variant in variants if variant and variant != raw])


def repair_featurecounts_input_bams(
    raw_input_bams: Any,
    *,
    alignment_bam_hints: list[str],
) -> tuple[Any, bool]:
    """Repair missing featureCounts BAM inputs using alignment hints."""

    tokens = parse_path_tokens(raw_input_bams)
    if not tokens:
        return raw_input_bams, False

    hints = dedupe_preserve_order([str(item).strip() for item in alignment_bam_hints if str(item).strip()])
    hints_by_sample: dict[str, list[str]] = {}
    for hint in hints:
        sample_hint = extract_sample_hint(hint)
        if sample_hint:
            hints_by_sample.setdefault(sample_hint, []).append(hint)

    changed = False
    rewritten: list[str] = []
    for idx, token in enumerate(tokens):
        current = str(token).strip()
        if not current:
            continue
        if Path(current).expanduser().exists():
            rewritten.append(current)
            continue

        replacement = ""
        for candidate in candidate_star_bam_variants(current):
            if candidate in hints or Path(candidate).expanduser().exists():
                replacement = candidate
                break

        if not replacement:
            sample_hint = extract_sample_hint(current)
            sample_matches = hints_by_sample.get(sample_hint, []) if sample_hint else []
            if len(sample_matches) == 1:
                replacement = sample_matches[0]
            elif len(tokens) == len(hints) and 0 <= idx < len(hints):
                replacement = hints[idx]

        if replacement and replacement != current:
            rewritten.append(replacement)
            changed = True
        else:
            rewritten.append(current)

    if not changed:
        return raw_input_bams, False
    return render_path_tokens(raw_input_bams, rewritten), True


def _bam_path_tokens(path_text: str, *, extra_tokens: tuple[str, ...] = ()) -> list[str]:
    """Return stable matching tokens for one BAM path or sample name."""

    raw = str(path_text or "").strip()
    candidates: list[str] = []
    if raw:
        path = Path(raw).expanduser()
        candidates.extend([path.parent.name, path.stem, path.name])
    candidates.extend(str(token or "").strip() for token in extra_tokens if str(token or "").strip())

    seen: set[str] = set()
    tokens: list[str] = []
    for candidate in candidates:
        for token in re.split(r"[^a-z0-9]+", str(candidate).lower()):
            cleaned = token.strip()
            if len(cleaned) < 3 or cleaned in _GENERIC_BAM_NAME_TOKENS or cleaned in seen:
                continue
            seen.add(cleaned)
            tokens.append(cleaned)
    return tokens


def repair_alignment_dependent_bam_input(
    raw_input_bam: str,
    *,
    alignment_bam_hints: list[str],
    sample_tokens: tuple[str, ...] = (),
) -> tuple[str, bool]:
    """Repair one BAM-consuming argument from upstream alignment output hints."""

    current = str(raw_input_bam or "").strip()
    if not current:
        return current, False
    if Path(current).expanduser().exists():
        return current, False

    hints = dedupe_preserve_order([str(item).strip() for item in alignment_bam_hints if str(item).strip()])
    if not hints:
        return current, False

    tokens = _bam_path_tokens(current, extra_tokens=sample_tokens)
    matched: list[str] = []
    if tokens:
        for hint in hints:
            hint_l = hint.lower()
            if any(
                f"/{token}/" in hint_l
                or f"_{token}" in Path(hint).name.lower()
                or token in Path(hint).stem.lower()
                for token in tokens
            ):
                matched.append(hint)
    replacement = ""
    if len(matched) == 1:
        replacement = matched[0]
    elif len(hints) == 1:
        replacement = hints[0]
    if not replacement or replacement == current:
        return current, False
    return replacement, True
