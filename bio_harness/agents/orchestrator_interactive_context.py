from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def subagent_dataset_scout(
    data_root: str | None,
    include_subdirs: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    if not data_root:
        return {"file_count": 0, "files": [], "pairs": []}
    root = Path(data_root).expanduser()
    if not root.exists():
        return {"file_count": 0, "files": [], "pairs": [], "warning": f"path not found: {root}"}

    suffixes = (".fastq", ".fq", ".fastq.gz", ".fq.gz")
    iterator = root.rglob("*") if include_subdirs else root.glob("*")
    files: list[str] = []
    for path in iterator:
        if not path.is_file():
            continue
        path_str = str(path)
        if path_str.lower().endswith(suffixes):
            files.append(path_str)
        if len(files) >= limit:
            break
    files = sorted(files)

    pair_map: dict[str, dict[str, str]] = {}
    for file_path in files:
        name = Path(file_path).name
        match = re.match(r"(.+)_R([12])_001", name)
        if not match:
            continue
        key = match.group(1)
        read = match.group(2)
        pair_map.setdefault(key, {})[read] = file_path
    pairs = [{"sample": key, "R1": value.get("1", ""), "R2": value.get("2", "")} for key, value in pair_map.items()]
    return {"file_count": len(files), "files": files[:50], "pairs": pairs[:25]}


def subagent_requirements(user_text: str) -> list[str]:
    reqs: list[str] = []
    text = user_text.lower()
    if "alternative splicing" in text or "splicing" in text:
        reqs.extend(
            [
                "Need sample group mapping (case/control) and replicate labels.",
                "Need reference genome FASTA and annotation GTF path.",
                "Need decision on splicing tool (rMATS, DEXSeq, MAJIQ).",
            ]
        )
    if "differential expression" in text or "deseq2" in text:
        reqs.extend(
            [
                "Need sample group mapping and replicate metadata.",
                "Need count strategy (featureCounts or Salmon import).",
            ]
        )
    return reqs


def infer_autonomy_mode(user_text: str) -> bool:
    text = user_text.lower()
    autonomy_markers = (
        "figure out",
        "you have everything you need",
        "proceed",
        "just do it",
        "don't ask",
        "infer the rest",
        "autonomous",
    )
    return any(marker in text for marker in autonomy_markers)


def detect_context_completeness(user_text: str, context: dict[str, Any]) -> dict[str, Any]:
    text = user_text.lower()
    data_ctx = context.get("data_context", {}) or {}
    pairs = data_ctx.get("pairs", []) if isinstance(data_ctx, dict) else []

    has_pair_discovery = bool(pairs)
    has_reference_paths = bool(
        re.search(r"/\S+\.(fa|fasta|fna)(\.gz)?\b", user_text, flags=re.IGNORECASE)
        and re.search(r"/\S+\.gtf(\.gz)?\b", user_text, flags=re.IGNORECASE)
    )
    has_splicing_tool = any(token in text for token in ("rmats", "majiq", "suppa2", "leafcutter", "dexseq"))
    has_aligner = any(token in text for token in ("star", "hisat2"))

    missing: list[str] = []
    if "splicing" in text:
        if not has_pair_discovery:
            missing.append("input sample pairing not confirmed")
        if not has_reference_paths:
            missing.append("reference FASTA + GTF paths not confirmed in this turn")
        if not has_splicing_tool:
            missing.append("splicing tool not explicitly selected")
        if not has_aligner:
            missing.append("aligner not explicitly selected")

    return {
        "is_likely_complete": len(missing) == 0,
        "missing": missing,
        "has_pair_discovery": has_pair_discovery,
        "has_reference_paths": has_reference_paths,
        "has_splicing_tool": has_splicing_tool,
        "has_aligner": has_aligner,
    }
