"""Utility helpers for ranked fallback-catalog planning."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.pathing import discover_fastq_files_guarded
from bio_harness.core.tool_env import requirement_available

FASTQ_PAIR_RE = re.compile(
    r"^(?P<prefix>.+?)_R(?P<read>[12])(?:_001)?\.(?:f(?:ast)?q)(?:\.gz)?$",
    flags=re.IGNORECASE,
)
PROTEIN_FASTA_SUFFIXES = (
    ".faa",
    ".pep",
    ".aa",
    ".protein.fa",
    ".protein.fasta",
    ".faa.gz",
    ".pep.gz",
    ".aa.gz",
    ".protein.fa.gz",
    ".protein.fasta.gz",
)

_COUNTS_MATRIX_SUFFIXES = (
    ".counts.txt",
    "_counts.tsv",
    "_counts.txt",
    "featurecounts.txt",
    ".count_matrix.tsv",
    ".matrix.tsv",
    ".matrix.txt",
    ".tsv",
)
_METADATA_TABLE_SUFFIXES = (
    "metadata.tsv",
    "sample_metadata.tsv",
    "sample_sheet.tsv",
    "samples.tsv",
    "coldata.tsv",
    ".metadata.tsv",
)


def _safe_scan_files(
    roots: list[Path],
    suffixes: tuple[str, ...],
    *,
    max_hits: int = 128,
    max_scan: int = 3000,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    scanned = 0
    for root in roots:
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*")
        except Exception:
            continue
        for item in iterator:
            scanned += 1
            if scanned > max_scan:
                return out
            try:
                if not item.is_file():
                    continue
            except OSError:
                continue
            low = item.name.lower()
            if not low.endswith(suffixes):
                continue
            item_s = str(item.resolve(strict=False))
            if item_s in seen:
                continue
            seen.add(item_s)
            out.append(item_s)
            if len(out) >= max_hits:
                return out
    return out


def _read_delimited_preview(path: Path, *, max_lines: int = 2) -> tuple[list[list[str]], str]:
    """Read a short delimited preview from *path*.

    Args:
        path: Candidate file path.
        max_lines: Maximum non-empty lines to parse.

    Returns:
        A tuple of parsed rows and the inferred delimiter.
    """

    rows: list[list[str]] = []
    delimiter = "\t"
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                delimiter = "\t" if raw_line.count("\t") >= raw_line.count(",") else ","
                rows.append([part.strip() for part in raw_line.rstrip("\n").split(delimiter)])
                if len(rows) >= max_lines:
                    break
    except OSError:
        return [], delimiter
    return rows, delimiter


def _looks_like_numeric_token(value: str) -> bool:
    """Return whether *value* can be parsed as a numeric token."""

    try:
        float(str(value).strip())
    except (TypeError, ValueError):
        return False
    return True


def _looks_like_counts_matrix(path: Path) -> bool:
    """Return whether *path* looks like a gene-count matrix."""

    rows, _delimiter = _read_delimited_preview(path)
    if len(rows) < 2:
        return False
    header = [str(token).strip().lower() for token in rows[0]]
    sample = rows[1]
    if len(sample) < 2:
        return False
    if any(token in {"condition", "group", "treatment", "control"} for token in header[1:]):
        return False
    numeric_count = sum(1 for token in sample[1:] if _looks_like_numeric_token(token))
    return numeric_count >= 2


def _looks_like_metadata_table(path: Path) -> bool:
    """Return whether *path* looks like a sample-metadata table."""

    rows, _delimiter = _read_delimited_preview(path)
    if len(rows) < 2:
        return False
    header = [str(token).strip().lower() for token in rows[0]]
    has_sample = any(token in {"sample", "sample_id", "sampleid", "run", "accession"} for token in header)
    has_group = any(token in {"condition", "group", "treatment", "control", "timepoint"} for token in header)
    return has_sample and has_group


def _discover_typed_files(
    roots: list[Path],
    *,
    suffixes: tuple[str, ...],
    validator: Any | None = None,
    max_hits: int = 128,
    max_scan: int = 3000,
) -> list[str]:
    """Discover candidate files and optionally validate their content shape."""

    candidates = _safe_scan_files(roots, suffixes, max_hits=max_hits, max_scan=max_scan)
    if validator is None:
        return candidates
    validated: list[str] = []
    for candidate in candidates:
        path = Path(candidate)
        try:
            if validator(path):
                validated.append(str(path.resolve(strict=False)))
        except OSError:
            continue
    return validated


def _discover_fastq_pairs(data_root: str) -> dict[str, dict[str, str]]:
    root = Path(str(data_root or "")).expanduser()
    if not root.exists():
        return {}
    fastqs = discover_fastq_files_guarded(root, include_subdirs=True, name_filter="", max_files=5000)
    pair_map: dict[str, dict[str, str]] = {}
    for fp in fastqs:
        name = Path(fp).name
        m = FASTQ_PAIR_RE.match(name)
        if not m:
            continue
        prefix = m.group("prefix")
        read = m.group("read")
        pair_map.setdefault(prefix, {})[read] = fp
    return pair_map


def _discover_long_read_fastqs(data_root: str) -> list[str]:
    root = Path(str(data_root or "")).expanduser()
    if not root.exists():
        return []
    fastqs = discover_fastq_files_guarded(root, include_subdirs=True, name_filter="", max_files=5000)
    return sorted(set(str(Path(fp).resolve(strict=False)) for fp in fastqs))


def _find_pair_for_tag(pair_map: dict[str, dict[str, str]], tag: str) -> tuple[str, str]:
    tag_l = str(tag or "").strip().lower()
    candidates: list[tuple[int, str, str, str]] = []
    for key, pair in pair_map.items():
        r1 = str(pair.get("1", "")).strip()
        r2 = str(pair.get("2", "")).strip()
        if not r1 or not r2:
            continue
        key_l = key.lower()
        r1_name = Path(r1).name.lower()
        score = 0
        if tag_l and (f"_{tag_l}_" in key_l or f"_{tag_l}_" in r1_name):
            score += 20
        if tag_l and tag_l in key_l:
            score += 5
        candidates.append((score, key_l, r1, r2))
    if not candidates:
        return "", ""
    candidates.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
    _, _, r1, r2 = candidates[0]
    return r1, r2


def _pick_two_group_pairs(
    pair_map: dict[str, dict[str, str]],
    control_tag: str,
    treatment_tag: str,
) -> tuple[tuple[str, str], tuple[str, str]]:
    control = _find_pair_for_tag(pair_map, control_tag)
    treatment = _find_pair_for_tag(pair_map, treatment_tag)
    if control[0] and treatment[0] and control[0] != treatment[0]:
        return control, treatment

    all_pairs = sorted(
        [(key.lower(), str(v.get("1", "")).strip(), str(v.get("2", "")).strip()) for key, v in pair_map.items()],
        key=lambda x: (len(x[0]), x[0]),
    )
    usable = [(r1, r2) for _, r1, r2 in all_pairs if r1 and r2]
    if not usable:
        return ("", ""), ("", "")
    if not control[0]:
        control = usable[0]
    if not treatment[0]:
        treatment = usable[1] if len(usable) > 1 else ("", "")
    if control == treatment:
        treatment = usable[1] if len(usable) > 1 else ("", "")
    return control, treatment


def _pick_first_pair(pair_map: dict[str, dict[str, str]]) -> tuple[str, str]:
    usable = sorted(
        [(key.lower(), str(v.get("1", "")).strip(), str(v.get("2", "")).strip()) for key, v in pair_map.items()],
        key=lambda x: (len(x[0]), x[0]),
    )
    for _, r1, r2 in usable:
        if r1 and r2:
            return r1, r2
    return "", ""


def _resolve_reference_file(kind: str, requested: str, data_root: str, selected_dir: str) -> str:
    req = Path(str(requested or "")).expanduser()
    if str(requested or "").strip() and req.exists():
        return str(req.resolve(strict=False))

    workspace = Path(str(selected_dir or "")).expanduser()
    if not workspace.exists():
        workspace = Path(str(data_root or "")).expanduser()
    suffixes = (".gtf", ".gtf.gz") if kind == "gtf" else (".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz")
    aliases = ("mouse_gtf",) if kind == "gtf" else ("mouse_fasta", "mouse_fa")
    roots = [workspace / "inputs_readonly", workspace / "references", workspace, Path(str(data_root or ""))]
    files = _safe_scan_files(roots, suffixes, max_hits=80, max_scan=4000)
    alias_candidates = []
    for root in roots:
        for alias in aliases:
            p = root / alias
            if p.exists() or p.is_symlink():
                alias_candidates.append(str(p.resolve(strict=False)))
    if alias_candidates:
        return sorted(set(alias_candidates), key=len)[0]
    if files:
        return sorted(set(files), key=len)[0]
    return ""


def _resolve_optional_existing_inputs(data_root: str, selected_dir: str) -> dict[str, list[str]]:
    roots = [Path(str(selected_dir or "")).expanduser(), Path(str(data_root or "")).expanduser()]
    return {
        "bam": _safe_scan_files(roots, (".bam",), max_hits=120, max_scan=6000),
        "counts_matrix": _discover_typed_files(
            roots,
            suffixes=_COUNTS_MATRIX_SUFFIXES,
            validator=_looks_like_counts_matrix,
            max_hits=120,
            max_scan=6000,
        ),
        "metadata_table": _discover_typed_files(
            roots,
            suffixes=_METADATA_TABLE_SUFFIXES,
            validator=_looks_like_metadata_table,
            max_hits=120,
            max_scan=6000,
        ),
        "protein_fasta": _safe_scan_files(roots, PROTEIN_FASTA_SUFFIXES, max_hits=120, max_scan=6000),
        "vcf": _safe_scan_files(roots, (".vcf", ".vcf.gz"), max_hits=120, max_scan=6000),
    }


def _tool_available(tool_name: str, override: dict[str, bool] | None = None) -> bool:
    if isinstance(override, dict) and tool_name in override:
        return bool(override[tool_name])
    return requirement_available(tool_name)


def _required_exec_tools(required_tools: list[str]) -> list[str]:
    mapping = {
        "star_align": "star",
        "star_2pass_align": "star",
        "star_solo_count": "star",
        "hisat2_align": "hisat2",
        "subread_align": "subread",
        "bwa_mem_align": "bwa",
        "bowtie2_align": "bowtie2",
        "minimap2_align": "minimap2",
        "featurecounts_run": "featureCounts",
        "deseq2_run": "Rscript",
        "edger_run": "Rscript",
        "limma_voom_run": "Rscript",
        "gatk_haplotypecaller": "gatk",
        "gatk_mutect2_call": "gatk",
        "bcftools_call": "bcftools",
        "freebayes_call": "freebayes",
        "varscan_call": "varscan",
        "dexseq_run": "Rscript",
        "majiq_run": "majiq",
        "blastp_search": "blastp",
        "hmmscan_search": "hmmscan",
        "prokka_annotate": "prokka",
        "methylation_bismark_style": "bismark",
        "metagenomics_kraken2_bracken_style": "kraken2",
        "fusion_star_fusion_style": "star-fusion",
        "cnv_cnvkit_style": "cnvkit.py",
        "immune_repertoire_mixcr_style": "mixcr",
        "phylogenetics_iqtree_style": "iqtree2",
    }
    out: list[str] = []
    for raw in required_tools:
        key = str(raw).strip()
        if not key:
            continue
        out.append(mapping.get(key, key))
    return sorted(set(out))


def _is_fresh_alignment_mode(provenance_mode: str) -> bool:
    mode = str(provenance_mode or "").strip().lower()
    return mode in {"fresh_alignment", "strict"}


def _effective_required_exec_tools(
    template: dict[str, Any],
    existing_inputs: dict[str, list[str]],
    *,
    has_fastq_pair: bool = False,
    has_two_group_fastq_pair: bool = False,
    has_long_fastq: bool = False,
    provenance_mode: str = "standard",
) -> list[str]:
    req_exec_tools = _required_exec_tools([str(x).strip() for x in template.get("required_tools", []) if str(x).strip()])
    pipeline_id = str(template.get("pipeline_id", "")).strip()
    bam_count = len(existing_inputs.get("bam", [])) if isinstance(existing_inputs, dict) else 0
    fresh_alignment = _is_fresh_alignment_mode(provenance_mode)
    if pipeline_id.startswith("germline_variant_") and bam_count >= 1 and not (fresh_alignment and has_fastq_pair):
        req_exec_tools = [t for t in req_exec_tools if t != "bwa"]
    if (
        pipeline_id in {"somatic_variant_mutect2_tn", "somatic_variant_bcftools_tn_degrade"}
        and bam_count >= 2
        and not (fresh_alignment and has_two_group_fastq_pair)
    ):
        req_exec_tools = [t for t in req_exec_tools if t != "bwa"]
    if pipeline_id in {"lr_rna_align_minimap2_splice", "lr_dna_align_minimap2"} and bam_count >= 1:
        req_exec_tools = [t for t in req_exec_tools if t != "minimap2"]
    return sorted(set(req_exec_tools))


def _keyword_score(prompt: str, keywords: list[str]) -> int:
    text = f" {(prompt or '').lower()} "
    score = 0
    for kw in keywords:
        token = str(kw).strip().lower()
        if token and token in text:
            score += 1
    return score


def _contract_signal_score(prompt: str, template: dict[str, Any], requested_caps: set[str]) -> int:
    text = f" {(prompt or '').lower()} "
    raw = template.get("contract_coverage_signals", {})
    signal_map = raw if isinstance(raw, dict) else {}
    score = 0
    for cap in sorted(requested_caps):
        signals = signal_map.get(cap, []) if isinstance(signal_map.get(cap, []), list) else []
        tokens = [str(x).strip().lower() for x in signals if str(x).strip()]
        if tokens and any(token in text for token in tokens):
            score += 1
    return score


def _next_step_id(plan: list[dict[str, Any]]) -> int:
    return len(plan) + 1


def _group_marker_step(control_r1: str, treatment_r1: str) -> dict[str, Any]:
    parts: list[str] = []
    if control_r1:
        parts.append(f"echo __SELECTED_CONTROL_R1__:{shlex.quote(control_r1)}")
    if treatment_r1:
        parts.append(f"echo __SELECTED_TREATMENT_R1__:{shlex.quote(treatment_r1)}")
    if not parts:
        parts = ["echo __NO_GROUP_FASTQ_FOUND__"]
    return {"tool_name": "bash_run", "arguments": {"command": " ; ".join(parts)}}


def _choose_counts_and_metadata(existing_inputs: dict[str, list[str]], out_base: str) -> tuple[str, str]:
    metadata_candidates = [str(path).strip() for path in existing_inputs.get("metadata_table", []) if str(path).strip()]
    counts_candidates = [str(path).strip() for path in existing_inputs.get("counts_matrix", []) if str(path).strip()]
    metadata = metadata_candidates[0] if metadata_candidates else ""
    counts = ""
    for candidate in counts_candidates:
        if metadata and Path(candidate).resolve(strict=False) == Path(metadata).resolve(strict=False):
            continue
        counts = candidate
        break
    return counts, metadata


def _bam_has_index(path: str) -> bool:
    p = Path(str(path)).expanduser()
    candidates = [
        Path(str(p) + ".bai"),
        p.with_suffix(".bai"),
        Path(str(p) + ".csi"),
        p.with_suffix(".csi"),
    ]
    for cand in candidates:
        try:
            if cand.exists():
                return True
        except OSError:
            continue
    return False


def _bam_preference_key(path: str) -> tuple[int, int, str]:
    p = Path(str(path)).expanduser()
    name = p.name.lower()
    score = 0
    if "sorted" in name or ".sortedbycoord." in name:
        score += 90
    if _bam_has_index(str(p)):
        score += 40
    if name.endswith("aligned.out.bam") and "sorted" not in name:
        score -= 50
    return (-score, len(str(p)), str(p))


def _rank_bam_candidates(bams: list[str]) -> list[str]:
    cleaned = [str(x).strip() for x in bams if str(x).strip()]
    return sorted(set(cleaned), key=_bam_preference_key)


def _choose_bam(existing_inputs: dict[str, list[str]], default_path: str) -> str:
    bams = existing_inputs.get("bam", []) if isinstance(existing_inputs, dict) else []
    ranked = _rank_bam_candidates([str(x) for x in bams])
    if ranked:
        return ranked[0]
    return default_path


def _choose_two_bams(existing_inputs: dict[str, list[str]], defaults: tuple[str, str]) -> tuple[str, str]:
    bams = existing_inputs.get("bam", []) if isinstance(existing_inputs, dict) else []
    ranked = _rank_bam_candidates([str(x) for x in bams])
    if len(ranked) >= 2:
        return ranked[0], ranked[1]
    if len(ranked) == 1:
        return ranked[0], defaults[1]
    return defaults
