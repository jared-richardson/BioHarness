"""Input-discovery helpers for direct-wrapper plan binding."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.analysis_spec import discover_data_files
from bio_harness.core.artifact_roles import is_input_like_file_role
from bio_harness.core.direct_wrapper_argument_utils import _path_exists
from bio_harness.core.tool_registry import ToolRegistry
from bio_harness.harness.stream_utils import _extract_paths_from_text

_DE_WRAPPERS = frozenset({"deseq2_run", "edger_run", "limma_voom_run"})
_RAW_PATH_TOKEN = r"(?:~?/[^,\s;\"')]+|(?:workspace|benchmark_data|bio_harness|scripts|docs|tests)/[^,\s;\"')]+)"
_DIRECT_WRAPPER_INPUT_HINTS: dict[str, dict[str, tuple[str, ...]]] = {
    "flye_assemble": {
        "reads_fastq": (".fastq", ".fq", ".fastq.gz", ".fq.gz"),
    },
    "fastp_run": {
        "reads_1": (
            "_R1.fastq",
            "_R1.fq",
            "_R1.fastq.gz",
            "_R1.fq.gz",
            "_1.fastq",
            "_1.fq",
            "_1.fastq.gz",
            "_1.fq.gz",
        ),
        "reads_2": (
            "_R2.fastq",
            "_R2.fq",
            "_R2.fastq.gz",
            "_R2.fq.gz",
            "_2.fastq",
            "_2.fq",
            "_2.fastq.gz",
            "_2.fq.gz",
        ),
    },
    "metabolomics_diff_abundance": {
        "feature_table": (
            "feature_table.csv",
            "feature_table.tsv",
            "peak_table.csv",
            "peak_table.tsv",
            "metabolite_abundance.csv",
            "metabolite_abundance.tsv",
            "intensity_matrix.csv",
            "intensity_matrix.tsv",
        ),
        "metadata_table": (
            "metadata.csv",
            "metadata.tsv",
            "sample_metadata.csv",
            "sample_metadata.tsv",
        ),
    },
    "metagenomics_kraken2_bracken_style": {
        "reads_1": (
            "_R1.fastq",
            "_R1.fq",
            "_R1.fastq.gz",
            "_R1.fq.gz",
            "_1.fastq",
            "_1.fq",
            "_1.fastq.gz",
            "_1.fq.gz",
        ),
        "reads_2": (
            "_R2.fastq",
            "_R2.fq",
            "_R2.fastq.gz",
            "_R2.fq.gz",
            "_2.fastq",
            "_2.fq",
            "_2.fastq.gz",
            "_2.fq.gz",
        ),
    },
    "minimap2_align": {
        "reference_fasta": (".fasta", ".fa", ".fna"),
        "reads": (".fastq", ".fq", ".fastq.gz", ".fq.gz"),
    },
    "proteomics_diff_abundance": {
        "abundance_matrix": (
            "abundance_matrix.csv",
            "abundance_matrix.tsv",
            "protein_abundance.csv",
            "intensity_matrix.csv",
        ),
        "metadata_table": (
            "metadata.csv",
            "metadata.tsv",
            "sample_metadata.csv",
            "sample_metadata.tsv",
        ),
    },
    "scanpy_workflow": {
        "input_path": (".h5ad", ".loom", ".h5"),
    },
    "spatial_transcriptomics_workflow": {
        "input_path": (".h5ad", ".loom", ".h5"),
    },
    "sc_count_and_cluster": {
        "whitelist": ("barcodes_whitelist.txt", "whitelist.txt"),
    },
    "stringtie_quant": {
        "input_bam": (".bam", ".cram"),
        "annotation_gtf": (".gtf", ".gff", ".gff3"),
    },
    "snpeff_annotate": {
        "input_vcf": (".vcf", ".vcf.gz", ".bcf"),
        "reference_fasta": (".fasta", ".fa", ".fna"),
        "annotation_gff": (".gff", ".gff3", ".gtf"),
    },
    "sniffles_sv_call": {
        "input_bam": (".bam", ".cram"),
        "reference_fasta": (".fasta", ".fa", ".fna"),
    },
}
_COUNT_PATH_RE = re.compile(r"(?:^|[_\-/])(count|counts|gene_counts|featurecounts)(?:[_\-.]|$)")
_METADATA_PATH_RE = re.compile(r"(?:^|[_\-/])(meta|metadata|coldata|sample)(?:[_\-.]|$)")
_DIRECT_WRAPPER_CONTEXT_PATTERNS: dict[str, dict[str, tuple[re.Pattern[str], ...]]] = {
    "flye_assemble": {
        "reads_fastq": (
            re.compile(
                rf"(?:reads|fastq|long\s+reads)\s+(?:at|from)\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
    },
    "metabolomics_diff_abundance": {
        "feature_table": (
            re.compile(
                rf"(?:feature|peak|intensity)\s+table\s+at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
            re.compile(
                rf"(?:feature|intensity)\s+matrix\s+at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
        "metadata_table": (
            re.compile(
                rf"(?:metadata|sample\s+metadata)\s+(?:table\s+)?at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
    },
    "minimap2_align": {
        "reference_fasta": (
            re.compile(
                rf"(?:reference(?:\s+genome)?|fasta)\s+(?:at|from)\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
        "reads": (
            re.compile(
                rf"(?:reads|fastq|long\s+reads)\s+(?:at|from)\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
    },
    "proteomics_diff_abundance": {
        "abundance_matrix": (
            re.compile(
                rf"(?:abundance|intensity)\s+matrix\s+at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
        "metadata_table": (
            re.compile(
                rf"(?:metadata|sample\s+metadata)\s+(?:table\s+)?at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
    },
    "scanpy_workflow": {
        "input_path": (
            re.compile(
                rf"(?:processed\s+)?(?:h5ad|loom)\s+at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
    },
    "spatial_transcriptomics_workflow": {
        "input_path": (
            re.compile(
                rf"(?:h5ad|ann(?:data)?|spatial\s+data)\s+(?:at|from)\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
    },
    "stringtie_quant": {
        "input_bam": (
            re.compile(
                rf"(?:bam|cram)\s+at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
        "annotation_gtf": (
            re.compile(
                rf"(?:annotation(?:\s+gtf)?|reference(?:\s+annotation)?(?:\s+gtf)?)\s+at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
            re.compile(
                rf"with\s+(?:the\s+)?annotation(?:\s+gtf)?\s+at\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
    },
    "sniffles_sv_call": {
        "input_bam": (
            re.compile(
                rf"(?:aligned\s+)?(?:bam|cram)\s+(?:at|from)\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
        "reference_fasta": (
            re.compile(
                rf"(?:reference(?:\s+genome)?|fasta)\s+(?:at|from)\s+(?P<path>{_RAW_PATH_TOKEN})",
                flags=re.IGNORECASE,
            ),
        ),
    },
}


def _request_paths_from_text(request_text: str) -> list[str]:
    """Extract unique absolute or relative paths mentioned in the request."""

    ordered: list[str] = []
    seen: set[str] = set()
    for raw_path in _extract_paths_from_text(request_text):
        path_text = str(raw_path).strip()
        if not path_text or path_text in seen:
            continue
        seen.add(path_text)
        ordered.append(path_text)
    return ordered


def _contextual_request_bindings(
    *,
    tool_name: str,
    request_text: str,
) -> dict[str, str]:
    """Return request paths bound to parameter roles by prompt context."""

    bindings: dict[str, str] = {}
    for param_name, patterns in _DIRECT_WRAPPER_CONTEXT_PATTERNS.get(tool_name, {}).items():
        candidate = _first_contextual_path_match(request_text, patterns)
        if candidate:
            bindings[param_name] = candidate
    return bindings


def _first_contextual_path_match(
    request_text: str,
    patterns: tuple[re.Pattern[str], ...],
) -> str:
    """Return the first contextual path match from one prompt."""

    text = str(request_text or "")
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        path = str(match.group("path") or "").strip().strip(",.;")
        if path:
            return path
    return ""


def _parameter_accepts_request_input_binding(
    *,
    tool_name: str,
    param_name: str,
    registry: ToolRegistry,
) -> bool:
    """Return whether the parameter should bind from request input paths."""

    parameter = registry.parameter_schema_for(tool_name).get(param_name)
    if parameter is None:
        return False
    if param_name in _DIRECT_WRAPPER_INPUT_HINTS.get(tool_name, {}):
        return True
    return is_input_like_file_role(parameter.file_role)


def _deterministic_input_candidate(
    *,
    tool_name: str,
    param_name: str,
    suffixes: tuple[str, ...],
    request_paths: list[str],
    discovered_input_paths: list[str],
    contextual_bindings: Mapping[str, str],
    analysis_spec: Mapping[str, Any] | None,
    registry: ToolRegistry,
) -> str:
    """Return one deterministic input candidate for a direct-wrapper parameter."""

    candidate = str(contextual_bindings.get(param_name, "") or "").strip()
    if candidate:
        return candidate
    candidate = _unique_suffix_match(request_paths, suffixes)
    if candidate:
        return candidate
    candidate = _unique_manifest_role_match(
        analysis_spec,
        tool_name=tool_name,
        param_name=param_name,
        suffixes=suffixes,
        registry=registry,
    )
    if candidate:
        return candidate
    candidate = _unique_requested_data_root_suffix_match(
        analysis_spec,
        discovered_input_paths,
        suffixes,
    )
    if candidate:
        return candidate
    return _unique_suffix_match(discovered_input_paths, suffixes)


def _discovered_input_paths(
    analysis_spec: Mapping[str, Any] | None,
    *,
    data_root: str | None,
) -> list[str]:
    """Return deterministic discovered input paths from the analysis context."""

    ordered: list[str] = []
    seen: set[str] = set()

    def _add_path(raw_path: Any) -> None:
        path_text = str(raw_path or "").strip()
        if not path_text:
            return
        resolved = str(Path(path_text).expanduser().resolve(strict=False))
        if resolved in seen:
            return
        if not _path_exists(resolved):
            return
        seen.add(resolved)
        ordered.append(resolved)

    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    discovered = spec.get("discovered_data_files", [])
    if isinstance(discovered, list):
        for entry in discovered:
            if isinstance(entry, Mapping):
                _add_path(entry.get("path"))
            else:
                _add_path(entry)

    manifest = spec.get("file_manifest")
    manifest_entries = getattr(manifest, "entries", None)
    if isinstance(manifest_entries, list):
        for entry in manifest_entries:
            _add_path(getattr(entry, "resolved_path", ""))
            if isinstance(entry, Mapping):
                _add_path(entry.get("resolved_path"))
                _add_path(entry.get("path"))
    elif isinstance(manifest, Mapping):
        for entry in manifest.get("entries", []) or []:
            if isinstance(entry, Mapping):
                _add_path(entry.get("resolved_path"))
                _add_path(entry.get("path"))

    if not ordered and data_root:
        for entry in discover_data_files(data_root):
            if isinstance(entry, Mapping):
                _add_path(entry.get("path"))
            else:
                _add_path(entry)

    return ordered


def _unique_manifest_role_match(
    analysis_spec: Mapping[str, Any] | None,
    *,
    tool_name: str,
    param_name: str,
    suffixes: tuple[str, ...],
    registry: ToolRegistry,
) -> str:
    """Return a unique manifest-backed path for one input parameter."""

    manifest = analysis_spec.get("file_manifest") if isinstance(analysis_spec, Mapping) else None
    if manifest is None:
        return ""
    roles = _manifest_role_candidates(tool_name, param_name, registry)
    if not roles:
        return ""

    matches: list[str] = []
    for role in roles:
        for path_text in _manifest_paths_for_role(manifest, role):
            if suffixes and not str(path_text).strip().lower().endswith(
                tuple(s.lower() for s in suffixes)
            ):
                continue
            if _path_exists(path_text):
                matches.append(str(Path(path_text).expanduser().resolve(strict=False)))
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def _manifest_role_candidates(
    tool_name: str,
    param_name: str,
    registry: ToolRegistry,
) -> tuple[str, ...]:
    """Return candidate manifest roles for one input parameter."""

    parameter = registry.parameter_schema_for(tool_name).get(param_name)
    if parameter is None:
        return ()
    file_role = str(parameter.file_role or "").strip().lower()
    role_aliases = {
        "reference_annotation": ("annotation_gtf", "annotation_gff"),
        "annotation_gtf": ("annotation_gtf",),
        "annotation_gff": ("annotation_gff",),
        "input_bam": ("input_bam",),
        "input_vcf": ("input_vcf",),
        "input_h5ad": ("input_h5ad",),
        "input_csv": ("input_csv",),
        "input_fastq": ("input_fastq_r1", "input_fastq", "fastq"),
        "reference_genome": ("reference_genome", "reference_fasta", "input_fasta"),
        "sample_metadata": ("sample_metadata",),
    }
    aliases = role_aliases.get(file_role, ())
    if aliases:
        return aliases
    return (file_role,) if file_role else ()


def _manifest_paths_for_role(manifest: Any, role: str) -> list[str]:
    """Return manifest paths for one role from either object or dict forms."""

    resolver = getattr(manifest, "resolve_all", None)
    if callable(resolver):
        try:
            resolved = resolver(role)
            if isinstance(resolved, list):
                return [str(item).strip() for item in resolved if str(item).strip()]
        except Exception:
            return []
    if isinstance(manifest, Mapping):
        entries = manifest.get("entries", []) or []
        paths: list[str] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("role", "") or "").strip() != role:
                continue
            path_text = str(entry.get("resolved_path", "") or entry.get("path", "")).strip()
            if path_text:
                paths.append(path_text)
        return paths
    return []


def _unique_suffix_match(paths: list[str], suffixes: tuple[str, ...]) -> str:
    """Return a unique request path matching any provided suffix."""

    matches = [
        path
        for path in paths
        if str(path).strip().lower().endswith(tuple(s.lower() for s in suffixes))
    ]
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def _unique_requested_data_root_suffix_match(
    analysis_spec: Mapping[str, Any] | None,
    paths: list[str],
    suffixes: tuple[str, ...],
) -> str:
    """Return a unique matching input path under the requested data root."""

    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    data_root_text = str(spec.get("requested_data_root", "") or "").strip()
    if not data_root_text:
        return ""
    data_root = Path(data_root_text).expanduser().resolve(strict=False)
    matches: list[str] = []
    for path_text in paths:
        path = Path(str(path_text or "")).expanduser().resolve(strict=False)
        if suffixes and not str(path).lower().endswith(tuple(s.lower() for s in suffixes)):
            continue
        try:
            path.relative_to(data_root)
        except ValueError:
            continue
        matches.append(str(path))
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def _unique_de_input_path(paths: list[str], *, kind: str) -> str:
    """Return one deterministic DE input path when exactly one candidate exists."""

    candidates: list[str] = []
    for path_text in paths:
        path_obj = Path(str(path_text))
        name = path_obj.name.lower()
        if path_obj.suffix.lower() not in {".tsv", ".csv", ".txt"}:
            continue
        if kind == "counts" and _COUNT_PATH_RE.search(name):
            candidates.append(str(path_text))
        if kind == "metadata" and _METADATA_PATH_RE.search(name):
            candidates.append(str(path_text))
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else ""


def _unique_whitelist_input_path(paths: list[str]) -> str:
    """Return one deterministic whitelist path when exactly one exists."""

    candidates = [
        str(path_text)
        for path_text in paths
        if Path(str(path_text)).name.lower() in {"barcodes_whitelist.txt", "whitelist.txt"}
    ]
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else ""


def _should_drop_missing_sc_whitelist(
    current: Any,
    *,
    request_text: str,
) -> bool:
    """Return whether a missing scRNA-seq whitelist should be inferred locally."""

    current_text = str(current or "").strip()
    if not current_text:
        return False
    if _path_exists(current_text):
        return False
    if "whitelist" in str(request_text or "").lower():
        return False
    return True
