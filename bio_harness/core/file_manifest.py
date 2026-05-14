"""FileManifest — deterministic path resolution for bioinformatics pipelines.

The LLM plans with symbolic role names (e.g. ``{reference_genome}``),
and the system resolves them to real absolute paths.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File roles
# ---------------------------------------------------------------------------

class FileRole(str, Enum):
    """Well-known file roles used across bioinformatics pipelines."""

    REFERENCE_GENOME = "reference_genome"
    ANNOTATION_GFF = "annotation_gff"
    ANNOTATION_GTF = "annotation_gtf"
    INPUT_VCF = "input_vcf"
    INPUT_FASTQ_R1 = "input_fastq_r1"
    INPUT_FASTQ_R2 = "input_fastq_r2"
    INPUT_BAM = "input_bam"
    INPUT_FASTA = "input_fasta"
    INPUT_H5AD = "input_h5ad"
    SAMPLE_METADATA = "sample_metadata"
    TRANSCRIPTOME_FASTA = "fasta_transcriptome"
    OUTPUT_DIR = "output_dir"


# ---------------------------------------------------------------------------
# Manifest entries
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManifestEntry:
    """A single discovered file with its assigned role."""

    role: str  # FileRole value or custom string
    resolved_path: str  # absolute path on disk
    file_type: str  # "fasta", "fastq", "vcf", "gff", "bam", etc.
    sample_id: Optional[str] = None  # for per-sample files (FASTQ pairs, BAMs)

    def as_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "role": self.role,
            "path": self.resolved_path,
            "file_type": self.file_type,
        }
        if self.sample_id:
            d["sample_id"] = self.sample_id
        return d


# ---------------------------------------------------------------------------
# Extension → file type mapping
# ---------------------------------------------------------------------------

_EXT_TO_TYPE: Dict[str, str] = {
    ".fastq": "fastq",
    ".fastq.gz": "fastq",
    ".fq": "fastq",
    ".fq.gz": "fastq",
    ".fa": "fasta",
    ".fa.gz": "fasta",
    ".fasta": "fasta",
    ".fasta.gz": "fasta",
    ".fna": "fasta",
    ".fna.gz": "fasta",
    ".gtf": "gtf",
    ".gtf.gz": "gtf",
    ".gff": "gff",
    ".gff.gz": "gff",
    ".gff3": "gff",
    ".gff3.gz": "gff",
    ".bam": "bam",
    ".cram": "bam",
    ".vcf": "vcf",
    ".vcf.gz": "vcf",
    ".bed": "bed",
    ".bed.gz": "bed",
    ".csv": "csv",
    ".tsv": "tsv",
    ".txt": "txt",
    ".sam": "sam",
    ".h5": "h5",
    ".h5ad": "h5ad",
    ".loom": "loom",
}


def _classify_file_type(name: str) -> Optional[str]:
    """Return the file type string for a filename, or None if unrecognised."""
    nl = name.lower()
    # Check compound extensions first (e.g. .fastq.gz)
    for ext in sorted(_EXT_TO_TYPE, key=len, reverse=True):
        if nl.endswith(ext):
            return _EXT_TO_TYPE[ext]
    return None


# ---------------------------------------------------------------------------
# Role assignment heuristics
# ---------------------------------------------------------------------------

# Patterns used to guess role from filename when no context is available
_REFERENCE_PATTERNS = re.compile(
    r"(genome|reference|ref_?seq|assembly|chromosome)", re.I
)
_ANNOTATION_GFF_PATTERNS = re.compile(r"(genes|annotation|features).*\.(gff|gff3)", re.I)
_ANNOTATION_GTF_PATTERNS = re.compile(r"(genes|annotation|features).*\.gtf", re.I)
_R1_PATTERNS = re.compile(r"(_R1[_.]|_1\.f)", re.I)
_R2_PATTERNS = re.compile(r"(_R2[_.]|_2\.f)", re.I)
_METADATA_PATTERNS = re.compile(r"(metadata|sample|phenotype|coldata)", re.I)
_VCF_PATTERNS = re.compile(r"\.(vcf|vcf\.gz)$", re.I)


def _guess_sample_id_from_fastq(path: Path) -> str:
    """Extract a sample ID from a FASTQ filename.

    Examples:
        sample1_R1.fastq.gz → sample1
        SRR123_1.fq.gz → SRR123
    """
    stem = path.name
    # Remove known extensions
    for ext in (".fastq.gz", ".fq.gz", ".fastq", ".fq"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    # Remove _R1/_R2/_1/_2 suffix
    stem = re.sub(r"[._]?(R?[12])$", "", stem, flags=re.I)
    return stem or path.stem


def _assign_role(
    path: Path,
    file_type: str,
    analysis_type: str,
) -> tuple[str, Optional[str]]:
    """Heuristically assign a role and optional sample_id to a file.

    Returns (role_string, sample_id_or_None).
    """
    name = path.name
    name_l = name.lower()

    # FASTQ files
    if file_type == "fastq":
        sample_id = _guess_sample_id_from_fastq(path)
        if _R2_PATTERNS.search(name):
            return FileRole.INPUT_FASTQ_R2.value, sample_id
        return FileRole.INPUT_FASTQ_R1.value, sample_id

    # FASTA files — distinguish reference vs transcript vs generic
    if file_type == "fasta":
        if _REFERENCE_PATTERNS.search(name):
            return FileRole.REFERENCE_GENOME.value, None
        if "transcriptome" in name_l or "cdna" in name_l:
            return FileRole.TRANSCRIPTOME_FASTA.value, None
        # Context-dependent: variant annotation / evolution => reference
        if analysis_type in (
            "variant_annotation",
            "bacterial_evolution_variant_calling",
            "germline_variant_calling",
            "rna_seq_differential_expression",
            "transcript_quantification",
            "alternative_splicing",
        ):
            return FileRole.REFERENCE_GENOME.value, None
        return FileRole.INPUT_FASTA.value, None

    # GFF/GFF3
    if file_type == "gff":
        return FileRole.ANNOTATION_GFF.value, None

    # GTF
    if file_type == "gtf":
        return FileRole.ANNOTATION_GTF.value, None

    # VCF
    if file_type == "vcf":
        return FileRole.INPUT_VCF.value, None

    # BAM
    if file_type == "bam":
        sample_id = path.stem.replace("_sorted", "").replace(".sorted", "")
        return FileRole.INPUT_BAM.value, sample_id

    # H5AD
    if file_type == "h5ad":
        return FileRole.INPUT_H5AD.value, None

    # CSV/TSV — might be metadata
    if file_type in ("csv", "tsv", "txt"):
        if _METADATA_PATTERNS.search(name):
            return FileRole.SAMPLE_METADATA.value, None
        return f"input_{file_type}", None

    return f"input_{file_type}", None


# ---------------------------------------------------------------------------
# FileManifest
# ---------------------------------------------------------------------------

@dataclass
class FileManifest:
    """Registry of discovered data files with assigned semantic roles.

    Provides deterministic path resolution for bioinformatics pipelines:
    the LLM plans with role names, the manifest resolves to real paths.
    """

    entries: List[ManifestEntry] = field(default_factory=list)
    output_dir: str = ""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_data_root(
        cls,
        data_root: Path | str,
        analysis_type: str = "",
        *,
        output_dir: str = "",
        max_files: int = 50,
    ) -> "FileManifest":
        """Auto-discover files in *data_root* and assign roles."""
        root = Path(data_root).expanduser().resolve(strict=False)
        manifest = cls(output_dir=output_dir)
        if not root.is_dir():
            return manifest

        for p in sorted(root.rglob("*")):
            if any(part.startswith(".") for part in p.parts):
                continue
            if not p.is_file():
                continue
            file_type = _classify_file_type(p.name)
            if file_type is None:
                continue
            role, sample_id = _assign_role(p, file_type, analysis_type)
            manifest.entries.append(
                ManifestEntry(
                    role=role,
                    resolved_path=str(p),
                    file_type=file_type,
                    sample_id=sample_id,
                )
            )
            if len(manifest.entries) >= max_files:
                break
        return manifest

    @classmethod
    def from_discovered_files(
        cls,
        discovered: Sequence[Dict[str, str]],
        analysis_type: str = "",
        *,
        output_dir: str = "",
    ) -> "FileManifest":
        """Build manifest from already-discovered file dicts
        (as returned by ``analysis_spec.discover_data_files()``).
        """
        manifest = cls(output_dir=output_dir)
        for entry in discovered:
            path_str = str(entry.get("path", "")).strip()
            if not path_str:
                continue
            p = Path(path_str)
            file_type = _classify_file_type(p.name)
            if file_type is None:
                file_type = "unknown"
            role, sample_id = _assign_role(p, file_type, analysis_type)
            manifest.entries.append(
                ManifestEntry(
                    role=role,
                    resolved_path=path_str,
                    file_type=file_type,
                    sample_id=sample_id,
                )
            )
        return manifest

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        role: str,
        *,
        sample_id: Optional[str] = None,
    ) -> Optional[str]:
        """Return absolute path for a role (and optional sample_id).

        Returns None if not found.
        """
        for entry in self.entries:
            if entry.role != role:
                continue
            if sample_id is not None and entry.sample_id != sample_id:
                continue
            return entry.resolved_path
        return None

    def resolve_all(self, role: str) -> List[str]:
        """Return all paths matching *role*."""
        return [e.resolved_path for e in self.entries if e.role == role]

    def sample_ids(self) -> List[str]:
        """Return unique sample IDs in discovery order."""
        seen: set[str] = set()
        result: List[str] = []
        for e in self.entries:
            if e.sample_id and e.sample_id not in seen:
                seen.add(e.sample_id)
                result.append(e.sample_id)
        return result

    def has_role(self, role: str) -> bool:
        return any(e.role == role for e in self.entries)

    def file_types(self) -> set[str]:
        """Return all unique file types in the manifest."""
        return {e.file_type for e in self.entries}

    # ------------------------------------------------------------------
    # LLM prompt formatting
    # ------------------------------------------------------------------

    def as_brief_block(self) -> str:
        """Format manifest for inclusion in the LLM analysis brief.

        Shows role → path mappings the LLM can reference by role name.
        """
        lines = ["available_input_files=USE THESE EXACT PATHS in your plan:"]
        for entry in self.entries:
            label = entry.role
            if entry.sample_id:
                label = f"{entry.role}[{entry.sample_id}]"
            lines.append(f"  {label} -> {entry.resolved_path}")
        if self.output_dir:
            lines.append(f"  output_dir -> {self.output_dir}")
        return "\n".join(lines)

    def as_role_instructions(self) -> str:
        """Short instruction block telling the LLM what roles are available."""
        roles = sorted({e.role for e in self.entries})
        if self.output_dir:
            roles.append("output_dir")
        return (
            "FILE ROLES (use these role names when referencing files): "
            + ", ".join(roles)
        )

    # ------------------------------------------------------------------
    # Plan step injection
    # ------------------------------------------------------------------

    def inject_into_step(self, step: dict) -> dict:
        """Replace role tokens like ``{reference_genome}`` in step arguments
        with resolved absolute paths.

        Handles both ``arguments`` dict (structured plan) and ``command``
        string (bash_run fallback).
        """
        step = dict(step)  # shallow copy
        arguments = step.get("arguments")
        if isinstance(arguments, dict):
            step["arguments"] = self._inject_into_dict(dict(arguments))
        command = step.get("command")
        if isinstance(command, str):
            step["command"] = self._inject_into_string(command)
        return step

    def inject_into_plan(self, steps: List[dict]) -> List[dict]:
        """Apply role-token resolution to every step in a plan."""
        return [self.inject_into_step(s) for s in steps]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_replacement_map(self) -> Dict[str, str]:
        """Build token → path replacement map.

        For roles with multiple files (e.g. per-sample FASTQs), picks the
        first entry.  Sample-specific resolution happens in inject_into_step
        when sample_id context is available.
        """
        rep: Dict[str, str] = {}
        for entry in self.entries:
            token = f"{{{entry.role}}}"
            if token not in rep:
                rep[token] = entry.resolved_path
            # Also provide sample-specific tokens
            if entry.sample_id:
                sample_token = f"{{{entry.role}[{entry.sample_id}]}}"
                rep[sample_token] = entry.resolved_path
        if self.output_dir:
            rep["{output_dir}"] = self.output_dir
        return rep

    def _inject_into_string(self, text: str) -> str:
        rmap = self._build_replacement_map()
        for token, path in rmap.items():
            text = text.replace(token, path)
        return text

    def _inject_into_dict(self, d: Dict[str, Any]) -> Dict[str, Any]:
        rmap = self._build_replacement_map()
        result: Dict[str, Any] = {}
        for key, value in d.items():
            if isinstance(value, str):
                for token, path in rmap.items():
                    value = value.replace(token, path)
            result[key] = value
        return result
