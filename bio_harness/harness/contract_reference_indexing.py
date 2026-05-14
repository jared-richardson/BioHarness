"""Index-path helpers for reference-aware contract repair."""

from __future__ import annotations

from pathlib import Path
import re


def _stable_index_base_for_tool(tool_name: str, selected_dir: Path, reference_path: str) -> str:
    """Return a stable selected-dir cache path for one genome index base."""

    tool_l = str(tool_name or "").strip().lower()
    cache_dir_map = {
        "subread_align": "subread_indexes",
        "hisat2_align": "hisat2_indexes",
        "bowtie2_align": "bowtie2_indexes",
    }
    cache_dir = cache_dir_map.get(tool_l, "")
    if not cache_dir:
        return ""
    ref_name = Path(str(reference_path or "")).name or "reference"
    ref_token = re.sub(r"[^A-Za-z0-9._-]+", "_", ref_name).strip("._-") or "reference"
    return str((selected_dir / "outputs" / "_cache" / cache_dir / ref_token / "genome").resolve(strict=False))


def _stable_quant_index_path_for_tool(tool_name: str, selected_dir: Path, reference_path: str) -> str:
    """Return a stable selected-dir cache path for one transcript index."""

    tool_l = str(tool_name or "").strip().lower()
    if not str(reference_path or "").strip():
        return ""
    ref_name = Path(str(reference_path or "")).name or "reference"
    ref_token = re.sub(r"[^A-Za-z0-9._-]+", "_", ref_name).strip("._-") or "reference"
    if tool_l == "kallisto_quant":
        return str((selected_dir / "outputs" / "_cache" / "kallisto_indexes" / f"{ref_token}.idx").resolve(strict=False))
    if tool_l == "salmon_quant":
        return str((selected_dir / "outputs" / "_cache" / "salmon_indexes" / ref_token).resolve(strict=False))
    return ""


def _find_prebuilt_quant_index(
    tool_name: str,
    transcriptome_fasta: str,
    search_roots: list[Path],
) -> str:
    """Return a prebuilt quantification index from the allowed workspace roots."""

    tool_l = str(tool_name or "").strip().lower()
    transcriptome_path = str(transcriptome_fasta or "").strip()
    if tool_l != "kallisto_quant" or not transcriptome_path:
        return ""

    ref_name = Path(transcriptome_path).name or "reference"
    ref_token = re.sub(r"[^A-Za-z0-9._-]+", "_", ref_name).strip("._-") or "reference"
    canonical_names = (
        "transcripts.idx",
        f"{ref_token}.idx",
        Path(ref_name).with_suffix(".idx").name,
    )

    for root in search_roots:
        if not root.exists():
            continue
        canonical_candidates = [root / "kallisto_index" / name for name in canonical_names]
        for candidate in canonical_candidates:
            if candidate.is_file():
                return str(candidate)

    ranked_matches: list[tuple[int, int, str]] = []
    for root in search_roots:
        if not root.exists():
            continue
        for candidate in root.rglob("*.idx"):
            if not candidate.is_file():
                continue
            name_l = candidate.name.lower()
            parent_l = candidate.parent.name.lower()
            if "kallisto" not in parent_l and "kallisto" not in name_l and candidate.name not in canonical_names:
                continue
            score = 0
            if candidate.name == "transcripts.idx":
                score -= 3
            if ref_token.lower() in name_l:
                score -= 2
            if parent_l == "kallisto_index":
                score -= 1
            ranked_matches.append((score, len(str(candidate)), str(candidate)))

    if not ranked_matches:
        return ""
    ranked_matches.sort()
    return ranked_matches[0][2]
