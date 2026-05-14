"""Artifact inspection helpers for benchmark recovery and validation."""

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO

from bio_harness.core.shell_output_hints import extract_shell_output_hints
from bio_harness.core.tool_registry import default_tool_registry, render_expected_output_path


@dataclass(frozen=True)
class VCFHeaderInspection:
    path: str
    exists: bool
    info_tags: tuple[str, ...]
    has_ann: bool


@dataclass(frozen=True)
class VCFFieldNamespaceInspection:
    path: str
    exists: bool
    info_tags: tuple[str, ...]
    format_tags: tuple[str, ...]
    has_ann: bool
    sample_names: tuple[str, ...]


@dataclass(frozen=True)
class VariantCSVInspection:
    path: str
    exists: bool
    header: tuple[str, ...]
    header_case: str
    row_count: int
    prodigal_like_gene_fraction: float


@dataclass(frozen=True)
class VCFAnnotationInspection:
    path: str
    exists: bool
    gene_samples: tuple[str, ...]
    prodigal_like_gene_fraction: float


def _open_text_auto(path: Path) -> IO[str]:
    """Open a text file, transparently handling gzip compression.

    Args:
        path: Path to the file (may be .gz compressed).

    Returns:
        File handle for reading text.
    """
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def inspect_vcf_header(path: Path) -> VCFHeaderInspection:
    """Read a VCF file header to extract INFO tags and check for ANN annotations.

    Args:
        path: Path to the VCF file (plain or gzip-compressed).

    Returns:
        VCFHeaderInspection with parsed tag information.
    """
    inspection = inspect_vcf_field_namespaces(path)
    return VCFHeaderInspection(
        path=inspection.path,
        exists=inspection.exists,
        info_tags=inspection.info_tags,
        has_ann=inspection.has_ann,
    )


def inspect_vcf_field_namespaces(path: Path) -> VCFFieldNamespaceInspection:
    """Read VCF header namespaces for INFO and FORMAT field validation.

    Args:
        path: Path to the VCF file (plain or gzip-compressed).

    Returns:
        VCFFieldNamespaceInspection containing INFO tags, FORMAT tags, and
        sample names from the header.
    """
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.exists():
        return VCFFieldNamespaceInspection(
            path=str(resolved),
            exists=False,
            info_tags=(),
            format_tags=(),
            has_ann=False,
            sample_names=(),
        )

    info_tags: list[str] = []
    format_tags: list[str] = []
    sample_names: tuple[str, ...] = ()
    has_ann = False
    try:
        with _open_text_auto(resolved) as handle:
            for line in handle:
                if not line:
                    continue
                if line.startswith("##INFO=<ID="):
                    fragment = line[len("##INFO=<ID=") :]
                    tag = fragment.split(",", 1)[0].strip()
                    if tag:
                        info_tags.append(tag)
                        if tag == "ANN":
                            has_ann = True
                    continue
                if line.startswith("##FORMAT=<ID="):
                    fragment = line[len("##FORMAT=<ID=") :]
                    tag = fragment.split(",", 1)[0].strip()
                    if tag:
                        format_tags.append(tag)
                    continue
                if line.startswith("#CHROM"):
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) > 9:
                        sample_names = tuple(str(part).strip() for part in parts[9:] if str(part).strip())
                    break
                if not line.startswith("##"):
                    break
    except Exception:
        return VCFFieldNamespaceInspection(
            path=str(resolved),
            exists=False,
            info_tags=(),
            format_tags=(),
            has_ann=False,
            sample_names=(),
        )

    return VCFFieldNamespaceInspection(
        path=str(resolved),
        exists=True,
        info_tags=tuple(sorted(set(info_tags))),
        format_tags=tuple(sorted(set(format_tags))),
        has_ann=has_ann,
        sample_names=sample_names,
    )


def inspect_variant_csv(path: Path) -> VariantCSVInspection:
    """Validate a variant CSV file's structure and detect naming conventions.

    Reads header case, row count, and scans gene names for prodigal-style
    numeric patterns (e.g. '123_456').

    Args:
        path: Path to the CSV file.

    Returns:
        VariantCSVInspection with header info, row count, and gene analysis.
    """
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.exists():
        return VariantCSVInspection(
            path=str(resolved),
            exists=False,
            header=(),
            header_case="unknown",
            row_count=0,
            prodigal_like_gene_fraction=0.0,
        )

    header: list[str] = []
    row_count = 0
    prodigal_hits = 0
    gene_seen = 0
    try:
        with resolved.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle)
            header = list(reader.fieldnames or [])
            gene_key = ""
            for candidate in ("GENE", "gene", "Gene"):
                if candidate in header:
                    gene_key = candidate
                    break
            for row in reader:
                row_count += 1
                gene = str((row or {}).get(gene_key, "")).strip()
                if not gene:
                    continue
                gene_seen += 1
                if "_" in gene:
                    left, _, right = gene.partition("_")
                    if left.isdigit() and right.isdigit():
                        prodigal_hits += 1
    except Exception:
        return VariantCSVInspection(
            path=str(resolved),
            exists=False,
            header=(),
            header_case="unknown",
            row_count=0,
            prodigal_like_gene_fraction=0.0,
        )

    header_case = "mixed"
    if header and all(col == col.upper() for col in header):
        header_case = "upper"
    elif header and all(col == col.lower() for col in header):
        header_case = "lower"
    fraction = float(prodigal_hits) / float(gene_seen) if gene_seen else 0.0
    return VariantCSVInspection(
        path=str(resolved),
        exists=True,
        header=tuple(header),
        header_case=header_case,
        row_count=row_count,
        prodigal_like_gene_fraction=fraction,
    )


def inspect_vcf_annotation_namespace(path: Path, *, sample_limit: int = 32) -> VCFAnnotationInspection:
    """Inspect VCF ANN fields to detect the gene naming convention.

    Samples gene names from ANN annotations and checks for prodigal-style
    numeric patterns to identify the annotation namespace.

    Args:
        path: Path to the VCF file.
        sample_limit: Maximum number of gene names to sample.

    Returns:
        VCFAnnotationInspection with gene samples and prodigal fraction.
    """
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.exists():
        return VCFAnnotationInspection(path=str(resolved), exists=False, gene_samples=(), prodigal_like_gene_fraction=0.0)

    genes: list[str] = []
    gene_seen = 0
    prodigal_hits = 0
    try:
        with _open_text_auto(resolved) as handle:
            for line in handle:
                if not line or line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 8:
                    continue
                info = parts[7]
                ann_value = ""
                for item in info.split(";"):
                    if item.startswith("ANN="):
                        ann_value = item[4:]
                        break
                if not ann_value:
                    continue
                first = ann_value.split(",", 1)[0]
                fields = first.split("|")
                gene = fields[3].strip() if len(fields) > 3 else ""
                if not gene:
                    continue
                gene_seen += 1
                if len(genes) < sample_limit:
                    genes.append(gene)
                if "_" in gene:
                    left, _, right = gene.partition("_")
                    if left.isdigit() and right.isdigit():
                        prodigal_hits += 1
    except Exception:
        return VCFAnnotationInspection(path=str(resolved), exists=False, gene_samples=(), prodigal_like_gene_fraction=0.0)

    fraction = float(prodigal_hits) / float(gene_seen) if gene_seen else 0.0
    return VCFAnnotationInspection(
        path=str(resolved),
        exists=True,
        gene_samples=tuple(genes),
        prodigal_like_gene_fraction=fraction,
    )


# ---------------------------------------------------------------------------
# Artifact-aware recovery helpers
# ---------------------------------------------------------------------------

# Keys in step arguments that typically hold output file paths.
_OUTPUT_ARG_KEYS = (
    "output_bam", "output_vcf", "output_vcf_gz", "output_csv", "output_dir",
    "output_gff", "output_faa", "output_counts", "output_unmapped_bam",
)

def _extract_expected_outputs(step: dict[str, Any]) -> list[str]:
    """Extract expected output file paths from a plan step's arguments."""
    paths: list[str] = []
    args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
    tool_name = str(step.get("tool_name", "")).strip().lower()
    registry = default_tool_registry()
    expected_output_files_by_key = registry.expected_output_files_by_key_for(tool_name)
    if expected_output_files_by_key:
        for key, relative_names in expected_output_files_by_key.items():
            output_root = str(args.get(key, "")).strip()
            if not output_root:
                continue
            for relative_name in relative_names:
                rendered = render_expected_output_path(
                    key=key,
                    output_root=output_root,
                    relative_name=relative_name,
                )
                if rendered:
                    paths.append(rendered)
        if paths:
            return paths

    output_roots = [
        str(args.get(key, "")).strip()
        for key in registry.output_argument_keys_for(tool_name)
        if str(args.get(key, "")).strip()
    ]
    expected_output_files = registry.expected_output_files_for(tool_name)
    if output_roots and expected_output_files:
        for output_root in output_roots:
            for relative_name in expected_output_files:
                paths.append(str(Path(output_root).expanduser() / relative_name))
        return paths
    for key in _OUTPUT_ARG_KEYS:
        val = str(args.get(key, "")).strip()
        if val:
            paths.append(val)
    if tool_name == "bash_run":
        command = str(args.get("command", "")).strip()
        hints = extract_shell_output_hints(command)
        paths.extend(hints.output_paths)
        paths.extend(hints.output_roots)
    return paths


def _inspect_expected_output_path(
    path_str: str,
    *,
    selected_dir: Path | None = None,
) -> dict[str, Any]:
    """Inspect whether an expected output path is materially present.

    A path counts as valid when it exists and is not an empty placeholder.
    Files must be non-empty. Directories must contain at least one entry.
    """

    path = Path(str(path_str or "")).expanduser()
    if not path.is_absolute() and selected_dir is not None:
        path = selected_dir / path
    resolved = path.resolve(strict=False)
    exists = resolved.exists()
    is_dir = resolved.is_dir() if exists else False
    size = 0
    valid = False
    if exists:
        if is_dir:
            try:
                valid = any(resolved.iterdir())
            except OSError:
                valid = False
        elif resolved.is_file():
            try:
                size = int(resolved.stat().st_size)
            except OSError:
                size = 0
            valid = size > 0
        else:
            valid = True
    return {
        "path": str(resolved),
        "exists": exists,
        "is_dir": is_dir,
        "size": size,
        "valid": valid,
    }


def scan_existing_step_outputs(
    selected_dir: Path,
    plan: dict[str, Any],
    step_idx: int,
) -> dict[str, dict[str, Any]]:
    """Check which expected outputs for a plan step already exist.

    Returns a mapping of path → {exists, size, valid}.
    """
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list) or step_idx >= len(steps):
        return {}

    step = steps[step_idx]
    if not isinstance(step, dict):
        return {}

    results: dict[str, dict[str, Any]] = {}
    for path_str in _extract_expected_outputs(step):
        inspected = _inspect_expected_output_path(path_str, selected_dir=selected_dir)
        results[str(inspected["path"])] = {
            "exists": bool(inspected["exists"]),
            "is_dir": bool(inspected["is_dir"]),
            "size": int(inspected["size"]),
            "valid": bool(inspected["valid"]),
        }
    return results


def infer_resumable_step_index(
    selected_dir: Path,
    plan: dict[str, Any],
) -> int:
    """Find the first plan step whose expected outputs don't exist.

    Returns the 0-based index of the step to resume from. If all steps
    have outputs, returns len(steps) (i.e., plan is complete).
    """
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return 0

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            return idx
        outputs = _extract_expected_outputs(step)
        if not outputs:
            # Steps with no expected outputs (e.g. pure analysis) — can't skip
            continue
        all_valid = all(
            bool(_inspect_expected_output_path(path_str, selected_dir=selected_dir).get("valid", False))
            for path_str in outputs
        )
        if not all_valid:
            return idx
    return len(steps)


def can_resume_after_failed_step(
    selected_dir: Path,
    plan: dict[str, Any],
    failed_step_idx: int,
) -> bool:
    """Return whether artifact-aware resume may legally skip a failed step.

    Args:
        selected_dir: Run output directory for resolving relative paths.
        plan: Normalized execution plan.
        failed_step_idx: Zero-based failed step index.

    Returns:
        True only when the failed step has an explicit expected-output contract
        and every expected output is already materially present.
    """

    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list) or failed_step_idx < 0 or failed_step_idx >= len(steps):
        return False
    step = steps[failed_step_idx]
    if not isinstance(step, dict):
        return False
    outputs = _extract_expected_outputs(step)
    if not outputs:
        return False
    return all(
        bool(_inspect_expected_output_path(path_str, selected_dir=selected_dir).get("valid", False))
        for path_str in outputs
    )
