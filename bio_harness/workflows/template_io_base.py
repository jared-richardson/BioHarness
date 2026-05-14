"""Generic path and output helpers for workflow template canonicalization."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.shell_output_hints import extract_shell_output_hints

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SCRIPT_DIR = PROJECT_ROOT / "bio_harness" / "pipeline_scripts"
DEFAULT_STAR_INDEX_CACHE_ROOT = "outputs/_cache/star_indexes"
STRUCTURED_READ_PAIR_TOOLS = {
    "star_align",
    "star_2pass_align",
    "hisat2_align",
    "subread_align",
    "bwa_mem_align",
    "bowtie2_align",
    "salmon_quant",
    "kallisto_quant",
    "spades_assemble",
    "trinity_assemble",
}
STRUCTURED_STAR_GENOME_DIR_TOOLS = {"star_align", "star_2pass_align", "star_solo_count"}
STRUCTURED_ARGUMENT_ALIASES = {
    "reads_1": ("read1", "read_1", "reads1", "r1", "left_fq", "left_reads", "fastq_r1"),
    "reads_2": ("read2", "read_2", "reads2", "r2", "right_fq", "right_reads", "fastq_r2"),
}
STRUCTURED_ALIAS_TOOLS = {
    "bwa_mem_align",
    "bowtie2_align",
    "cutadapt_run",
    "fastp_run",
    "hisat2_align",
    "kallisto_quant",
    "metagenomics_kraken2_bracken_style",
    "minimap2_align",
    "salmon_quant",
    "spades_assemble",
    "star_align",
    "star_2pass_align",
    "star_solo_count",
    "subread_align",
    "trinity_assemble",
}
_ANNOTATION_GFF_SUFFIXES = (".gff", ".gff.gz", ".gff3", ".gff3.gz")
_ANNOTATION_GTF_SUFFIXES = (".gtf", ".gtf.gz")


def script_command(script_name: str, *args: str) -> str:
    """Render one deterministic pipeline-script command."""

    script_path = PIPELINE_SCRIPT_DIR / script_name
    rendered_args = " ".join(shlex.quote(str(arg)) for arg in args)
    base = f"bash {shlex.quote(str(script_path))}"
    return f"{base} {rendered_args}".strip()


def normalize_structured_argument_aliases(
    tool_name: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Normalize structured argument aliases for known tools."""

    normalized = dict(args or {})
    changed = False
    if str(tool_name or "").strip().lower() not in STRUCTURED_ALIAS_TOOLS:
        return normalized, changed
    for canonical_key, aliases in STRUCTURED_ARGUMENT_ALIASES.items():
        if str(normalized.get(canonical_key, "")).strip():
            continue
        for alias in aliases:
            value = str(normalized.get(alias, "")).strip()
            if not value:
                continue
            normalized[canonical_key] = value
            changed = True
            break
    return normalized, changed


def is_structured_output_key(key: str) -> bool:
    """Return whether an argument name denotes an output location."""

    key_l = str(key or "").strip().lower()
    if not key_l:
        return False
    if key_l.startswith(("input_", "reads_", "read_")):
        return False
    if key_l in {
        "annotation_gtf",
        "ballgown_dir",
        "config_dir",
        "counts_matrix",
        "fasta",
        "gene_abundance_tsv",
        "genome_dir",
        "genome_fasta",
        "genome_fasta_file",
        "gtf",
        "gtf_path",
        "input_bam",
        "input_bams",
        "input_file",
        "input_path",
        "metadata_table",
        "r1",
        "r2",
        "reference",
        "reference_fasta",
        "run_input",
        "script_path",
        "whitelist",
    }:
        return key_l in {"ballgown_dir", "config_dir", "gene_abundance_tsv"}
    if key_l.endswith("_dir") or key_l in {"output_dir", "config_dir", "ballgown_dir"}:
        return True
    if key_l.startswith("output_"):
        return True
    return key_l.endswith(("_vcf", "_vcf_gz", "_gff", "_faa", "_fasta", "_fa", "_counts", "_txt"))


def structured_output_hints_for_step(tool_name: str, args: dict[str, Any]) -> tuple[list[Path], list[Path]]:
    """Infer structured output paths and roots for one non-bash step."""

    output_paths: list[Path] = []
    output_roots: list[Path] = []
    for key, value in (args or {}).items():
        text = str(value or "").strip()
        if not text:
            continue
        try:
            path = Path(text).expanduser()
        except (RuntimeError, ValueError):
            continue
        key_l = str(key).strip().lower()
        if key_l.endswith("_dir") or key_l in {"output_dir", "config_dir", "ballgown_dir"}:
            output_roots.append(path)
            continue
        if is_structured_output_key(key_l):
            output_paths.append(path)
    if str(tool_name or "").strip().lower() == "spades_assemble":
        out_dir = str((args or {}).get("output_dir", "")).strip()
        if out_dir:
            try:
                root = Path(out_dir).expanduser()
            except (RuntimeError, ValueError):
                root = Path(out_dir)
            output_roots.append(root)
            output_paths.extend([root / "contigs.fasta", root / "scaffolds.fasta"])
    return output_paths, output_roots


def path_matches_planned_outputs(candidate: str, output_paths: list[Path], output_roots: list[Path]) -> bool:
    """Return whether one candidate path matches planned outputs or roots."""

    text = str(candidate or "").strip()
    if not text:
        return False
    path = Path(text).expanduser()
    if any(path == planned for planned in output_paths):
        return True
    return any(path == root or root in path.parents for root in output_roots)


def annotation_reference_kind(path_text: str) -> str:
    """Infer whether an annotation path should resolve as GFF or GTF."""

    lowered = str(path_text or "").strip().lower()
    if lowered.endswith(_ANNOTATION_GFF_SUFFIXES):
        return "gff"
    if lowered.endswith(_ANNOTATION_GTF_SUFFIXES):
        return "gtf"
    return "gtf"


def bash_output_hints_for_command(command: str) -> tuple[list[Path], list[Path]]:
    """Infer likely output paths from deterministic helper-backed bash commands."""

    hints = extract_shell_output_hints(command)
    return (
        [Path(path).expanduser() for path in hints.output_paths],
        [Path(path).expanduser() for path in hints.output_roots],
    )


def new_canonicalization_state() -> dict[str, Any]:
    """Return a fresh mutable state dict for template canonicalization."""

    return {
        "fastq_pair_map": None,
        "alignment_bam_hints": [],
        "planned_output_paths": [],
        "planned_output_roots": [],
        "output_path_rewrites": [],
    }


def extend_output_hints(state: dict[str, Any], output_paths: list[Path], output_roots: list[Path]) -> None:
    """Append output hints into canonicalization state."""

    state["planned_output_paths"].extend(output_paths)
    state["planned_output_roots"].extend(output_roots)


def path_within_root(candidate: Path, root: Path) -> bool:
    """Return whether one path is equal to or nested under a root."""

    try:
        candidate_resolved = candidate.expanduser().resolve(strict=False)
        root_resolved = root.expanduser().resolve(strict=False)
    except Exception:
        return False
    return candidate_resolved == root_resolved or root_resolved in candidate_resolved.parents


def rewrite_output_dependency_path(path_text: str, state: dict[str, Any]) -> tuple[str, bool]:
    """Rewrite a path to follow previously relocated upstream outputs."""

    raw = str(path_text or "").strip()
    if not raw:
        return raw, False
    try:
        candidate = Path(raw).expanduser()
    except (RuntimeError, ValueError):
        return raw, False
    if not candidate.is_absolute():
        return raw, False
    for old_root, new_root in state.get("output_path_rewrites", []):
        try:
            old_path = Path(old_root).expanduser().resolve(strict=False)
            new_path = Path(new_root).expanduser().resolve(strict=False)
            candidate_resolved = candidate.resolve(strict=False)
        except Exception:
            continue
        if candidate_resolved == old_path:
            return str(new_path), True
        if old_path in candidate_resolved.parents:
            relative = candidate_resolved.relative_to(old_path)
            return str((new_path / relative).resolve(strict=False)), True
    return raw, False


def register_output_path_rewrite(state: dict[str, Any], old_path: Path, new_path: Path) -> None:
    """Persist one upstream output relocation for dependent-step rewrites."""

    rewrites = state.setdefault("output_path_rewrites", [])
    old_resolved = old_path.expanduser().resolve(strict=False)
    new_resolved = new_path.expanduser().resolve(strict=False)
    pair = (old_resolved, new_resolved)
    if pair not in rewrites:
        rewrites.append(pair)


def normalize_structured_output_path(
    key: str,
    value: str,
    *,
    selected_dir: str,
    state: dict[str, Any],
) -> tuple[str, bool]:
    """Rewrite one structured output argument into the active selected dir."""

    raw = str(value or "").strip()
    selected = str(selected_dir or "").strip()
    if not raw or not selected or not is_structured_output_key(key):
        return raw, False
    selected_path = Path(selected).expanduser().resolve(strict=False)
    try:
        candidate = Path(raw).expanduser()
    except (RuntimeError, ValueError):
        return raw, False

    if not candidate.is_absolute():
        target = selected_path if raw in {".", "./"} else (selected_path / candidate).resolve(strict=False)
        return str(target), str(target) != raw

    candidate_resolved = candidate.resolve(strict=False)
    if path_within_root(candidate_resolved, selected_path):
        return str(candidate_resolved), False

    target = (selected_path / candidate_resolved.name).resolve(strict=False)
    register_output_path_rewrite(state, candidate_resolved, target)
    return str(target), str(target) != str(candidate_resolved)


def parse_path_tokens(value: Any) -> list[str]:
    """Parse one path-like scalar or list into ordered path tokens."""

    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    try:
        return [str(item).strip() for item in shlex.split(raw, posix=True) if str(item).strip()]
    except Exception:
        return [part for part in raw.split() if part]


def render_path_tokens(original: Any, tokens: list[str]) -> Any:
    """Render parsed path tokens back into the original container style."""

    if isinstance(original, (list, tuple, set)):
        return list(tokens)
    return " ".join(shlex.quote(token) for token in tokens)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    """Deduplicate string values while preserving original order."""

    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out
