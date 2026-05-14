"""Benchmark/task helper discovery and command builders for strict artifact binding."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.protocol_grounding._shared import _discover_fastq_pairs

METAGENOMICS_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "classify_metagenomics_kmer.py"
COMPARE_PATHWAYS_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "compare_pathways.py"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHYLOGENY_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "infer_phylogeny_biopython.py"
VIRAL_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "classify_viral_reads_kmer.py"

_BENCHMARK_TASK_ALIASES = {
    "giab": "germline-vc",
}
_ABS_PATH_RE = re.compile(r"/[A-Za-z0-9._~/-]+")


def _discover_multi_model_pathway_inputs(data_root: Path | None) -> dict[str, str]:
    """Discover the canonical Alzheimer multi-model pathway inputs from task data."""

    if data_root is None:
        return {}

    discovered: dict[str, str] = {}
    for candidate in sorted(data_root.rglob("*")):
        if not candidate.is_file():
            continue
        name_l = candidate.name.lower()
        resolved = str(candidate.resolve(strict=False))
        if "ps3o1s" in name_l and name_l.endswith(".csv"):
            discovered.setdefault("ps3o1s_csv", resolved)
        elif "161904" in name_l and candidate.suffix.lower() in {".txt", ".tsv", ".csv"}:
            discovered.setdefault("tg_counts", resolved)
        elif "168137" in name_l and candidate.suffix.lower() in {".txt", ".tsv", ".csv"}:
            discovered.setdefault("fad_counts", resolved)
    return discovered


def _build_multi_model_compare_command(
    *,
    selected_dir: Path,
    data_root: Path | None,
) -> str | None:
    """Build the canonical compare-pathways helper command for strict mode."""

    discovered = _discover_multi_model_pathway_inputs(data_root)
    ps3o1s_csv = str(discovered.get("ps3o1s_csv", "")).strip()
    tg_counts = str(discovered.get("tg_counts", "")).strip()
    fad_counts = str(discovered.get("fad_counts", "")).strip()
    if not (ps3o1s_csv and tg_counts and fad_counts):
        return None

    output_dir = str((selected_dir / "outputs" / "alzheimer_mouse").resolve(strict=False))
    output_csv = str((selected_dir / "final" / "pathway_comparison.csv").resolve(strict=False))
    python_bin = str(preferred_helper_python_executable())
    project_root = str(COMPARE_PATHWAYS_SCRIPT.resolve(strict=False).parents[2])
    return (
        f"env {shlex.quote(f'PYTHONPATH={project_root}')} {shlex.quote(python_bin)} "
        f"{shlex.quote(str(COMPARE_PATHWAYS_SCRIPT))} "
        f"--precomputed-de-table PS3O1S={shlex.quote(ps3o1s_csv)} "
        f"--count-table 3xTG_AD={shlex.quote(tg_counts)} "
        f"--count-table 5xFAD={shlex.quote(fad_counts)} "
        f"--output_dir {shlex.quote(output_dir)} "
        f"--output-csv {shlex.quote(output_csv)} "
        "--run-differential-analysis"
    )


def _build_multi_model_verify_command(*, selected_dir: Path) -> str:
    """Build a lightweight final-deliverable verification command."""

    output_csv = str((selected_dir / "final" / "pathway_comparison.csv").resolve(strict=False))
    expected_columns = ["Pathway", "5xFAD_pvalue", "3xTG_AD_pvalue", "PS3O1S_pvalue"]
    return "\n".join(
        [
            "python3 - <<'EOF'",
            "import csv",
            "import os",
            "",
            f"output_csv = {output_csv!r}",
            f"expected_columns = {expected_columns!r}",
            "if not os.path.exists(output_csv):",
            "    raise SystemExit(f'Missing pathway comparison output: {output_csv}')",
            "with open(output_csv, 'r', encoding='utf-8', newline='') as handle:",
            "    reader = csv.DictReader(handle)",
            "    if list(reader.fieldnames or []) != expected_columns:",
            "        raise SystemExit(",
            "            f'Unexpected pathway comparison columns: {reader.fieldnames}; expected {expected_columns}'",
            "        )",
            "    row_count = sum(1 for _ in reader)",
            "print(f'Validated pathway comparison CSV with {row_count} rows at {output_csv}')",
            "EOF",
        ]
    )


def _discover_phylogenetics_input_fasta(data_root: Path | None) -> str:
    """Discover the canonical phylogenetics input FASTA from task data."""

    if data_root is None:
        return ""
    for candidate in sorted(data_root.rglob("*")):
        if candidate.is_file() and candidate.suffix.lower() in {".fa", ".fasta", ".faa", ".fna"}:
            return str(candidate.resolve(strict=False))
    return ""


def _build_phylogenetics_command(*, selected_dir: Path, data_root: Path | None) -> str | None:
    """Build the strict helper command for phylogenetics tree inference."""

    input_fasta = _discover_phylogenetics_input_fasta(data_root)
    if not input_fasta:
        return None
    output_tree = str((selected_dir / "final" / "phylogeny.treefile").resolve(strict=False))
    return (
        f"python3 {shlex.quote(str(PHYLOGENY_HELPER_SCRIPT))} "
        f"--input-fasta {shlex.quote(input_fasta)} "
        f"--output-tree {shlex.quote(output_tree)}"
    )


def _build_metagenomics_command(*, selected_dir: Path, data_root: Path | None) -> str | None:
    """Build the strict helper command for metagenomics classification."""

    if data_root is None:
        return None
    pairs = _discover_fastq_pairs(data_root)
    if not pairs:
        return None
    label, pair = next(iter(sorted(pairs.items())))
    reference_dir = PROJECT_ROOT / "benchmark_data" / "metagenomics" / "references"
    taxonomy_tsv = PROJECT_ROOT / "benchmark_data" / "metagenomics" / "kraken2_db" / "ktaxonomy.tsv"
    output_report = selected_dir / "output" / f"{label}_kraken2_report.txt"
    return (
        f"PYTHONPATH={shlex.quote(str(PROJECT_ROOT.resolve(strict=False)))} "
        f"python3 {shlex.quote(str(METAGENOMICS_HELPER_SCRIPT))} "
        f"--reads-1 {shlex.quote(str(pair['reads_1']))} "
        f"--reads-2 {shlex.quote(str(pair['reads_2']))} "
        f"--reference-dir {shlex.quote(str(reference_dir.resolve(strict=False)))} "
        f"--taxonomy-tsv {shlex.quote(str(taxonomy_tsv.resolve(strict=False)))} "
        f"--output-report {shlex.quote(str(output_report.resolve(strict=False)))} "
        "--kmer-size 31"
    )


def _discover_primary_fastq_pair(data_root: Path | None) -> tuple[str, str] | None:
    """Return the first paired FASTQ paths discovered under task data."""

    if data_root is None:
        return None
    pairs = _discover_fastq_pairs(data_root)
    if not pairs:
        return None
    _, pair = next(iter(sorted(pairs.items())))
    reads_1 = str(pair.get("reads_1", "")).strip()
    reads_2 = str(pair.get("reads_2", "")).strip()
    if not (reads_1 and reads_2):
        return None
    return reads_1, reads_2


def _build_viral_metagenomics_command(
    *,
    selected_dir: Path,
    data_root: Path | None,
    reference_dir: Path | None = None,
) -> str | None:
    """Build the strict helper command for viral metagenomics classification."""

    if data_root is None:
        return None
    pairs = _discover_fastq_pairs(data_root)
    if not pairs:
        return None
    _, pair = next(iter(sorted(pairs.items())))
    if reference_dir is None:
        reference_dir = _benchmark_task_reference_dir(selected_dir)
    if reference_dir is None:
        return None
    output_dir = selected_dir / "output"
    return (
        f"PYTHONPATH={shlex.quote(str(PROJECT_ROOT.resolve(strict=False)))} "
        f"python3 {shlex.quote(str(VIRAL_HELPER_SCRIPT))} "
        f"--reads-1 {shlex.quote(str(pair['reads_1']))} "
        f"--reads-2 {shlex.quote(str(pair['reads_2']))} "
        f"--reference-dir {shlex.quote(str(reference_dir.resolve(strict=False)))} "
        f"--output-report {shlex.quote(str((output_dir / 'classification_report.tsv').resolve(strict=False)))} "
        f"--output-detected {shlex.quote(str((output_dir / 'detected_viruses.txt').resolve(strict=False)))} "
        "--coverage-threshold 50 --kmer-size 21"
    )


def _infer_selected_dir_from_payload(*payloads: dict[str, Any]) -> Path | None:
    """Infer the selected benchmark run directory from serialized payloads."""

    text_parts: list[str] = []
    for payload in payloads:
        if isinstance(payload, dict):
            try:
                text_parts.append(json.dumps(payload, ensure_ascii=True))
            except Exception:
                text_parts.append(str(payload))
    for match in _ABS_PATH_RE.findall("\n".join(text_parts)):
        candidate = Path(match.rstrip(".,;:"))
        parts = candidate.parts
        try:
            idx = parts.index("official_runs")
        except ValueError:
            continue
        if idx + 2 >= len(parts):
            continue
        return Path(*parts[: idx + 3])
    return None


def _selected_dir_from_analysis_spec(analysis_spec: dict[str, Any] | None) -> Path | None:
    """Read the selected run directory from the analysis spec when present."""

    if not isinstance(analysis_spec, dict):
        return None
    raw = str(analysis_spec.get("selected_dir", "") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve(strict=False)


def _benchmark_task_data_dir(selected_dir: Path | None) -> Path | None:
    """Map an official-run directory back to its benchmark task data directory."""

    if selected_dir is None:
        return None
    parts = list(selected_dir.resolve(strict=False).parts)
    try:
        idx = parts.index("official_runs")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    task_name = _BENCHMARK_TASK_ALIASES.get(parts[idx + 1], parts[idx + 1])
    return Path(*parts[:idx], "tasks", task_name, "data")


def _benchmark_task_reference_dir(selected_dir: Path | None) -> Path | None:
    """Map an official-run directory back to its benchmark task reference directory."""

    data_dir = _benchmark_task_data_dir(selected_dir)
    if data_dir is None:
        return None
    return data_dir.parent / "references"
