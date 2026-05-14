"""Authoritative runtime metadata for executable tools.

This module consolidates the metadata the harness uses to reason about
tool selection, validation, recovery, stall policy, and deliverables.
Callers should query the registry instead of reading scattered constant
tables directly.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set

import yaml

from bio_harness.core.artifact_roles import (
    is_input_like_file_role,
    is_primary_output_file_role,
    is_output_like_file_role,
    is_required_existing_input,
    normalize_file_role,
)
from bio_harness.core.constants import (
    HEAVY_TOOL_NAMES,
    PLAN_INPUT_PATH_KEYS,
    PLAN_TOOL_EXEC_HINTS,
    TOOL_STALL_GRACE_HINTS,
)
from bio_harness.core.parameter_ownership import (
    is_execution_output_parameter,
    is_harness_managed_parameter,
    normalize_parameter_ownership,
)
from bio_harness.core.stage_semantics import _ALLOWED_STAGES

logger = logging.getLogger(__name__)


def render_expected_output_path(*, key: str, output_root: str, relative_name: str) -> str:
    """Render one expected output path for an output root or prefix.

    Args:
        key: Output argument key that owns the expected output.
        output_root: Declared output root or prefix value.
        relative_name: Expected emitted basename or suffix.

    Returns:
        Rendered expected output path string.
    """

    root = str(output_root or "").strip()
    relative = str(relative_name or "").strip()
    if not root or not relative:
        return ""
    key_l = str(key or "").strip().lower()
    if key_l.endswith("prefix") or "_prefix" in key_l:
        return f"{root}{relative}"
    return str(Path(root).expanduser() / relative)

_DEFAULT_EXPECTED_OUTPUT_FILES: dict[str, Any] = {
    "deseq2_run": ["deseq2_results.tsv"],
    "edger_run": ["edger_results.tsv"],
    "flye_assemble": ["assembly.fasta"],
    "kallisto_quant": ["abundance.tsv"],
    "limma_voom_run": ["limma_voom_results.tsv"],
    "metabolomics_diff_abundance": [
        "metabolomics_differential_abundance.csv",
        "metabolomics_qc_summary.json",
        "normalized_feature_matrix.tsv",
        "volcano_plot_data.tsv",
        "metabolomics_summary.md",
    ],
    "proteomics_diff_abundance": [
        "proteomics_differential_abundance.csv",
        "proteomics_qc_summary.json",
        "normalized_abundance_matrix.tsv",
        "volcano_plot_data.tsv",
        "proteomics_summary.md",
    ],
    "salmon_quant": ["quant.sf"],
    "sc_count_and_cluster": [
        "cluster_assignments.json",
        "marker_genes.json",
        "raw_counts.json",
    ],
    "spatial_transcriptomics_workflow": [
        "spatial_domain_assignments.csv",
        "spatial_marker_genes.csv",
        "spatial_results.h5ad",
    ],
    "spades_assemble": ["contigs.fasta", "scaffolds.fasta"],
    "star_align": {"output_prefix": ["Aligned.out.bam", "Aligned.sortedByCoord.out.bam"]},
    "star_2pass_align": {"output_prefix": ["Aligned.out.bam", "Aligned.sortedByCoord.out.bam"]},
}

_DEFAULT_TOOL_ALTERNATIVES_FORWARD: dict[str, list[str]] = {
    "bwa_mem_align": ["bowtie2_align", "minimap2_align"],
    "bcftools_filter_run": ["vcffilter"],
    "deseq2_run": ["edger_run", "limma_voom_run"],
    "freebayes_call": ["bcftools_call", "gatk_haplotypecaller"],
    "prokka_annotate": ["prodigal_annotate"],
    "salmon_quant": ["kallisto_quant"],
    "spades_assemble": ["flye_assemble"],
    "star_align": ["hisat2_align", "star_2pass_align"],
    "trimmomatic": ["fastp_run"],
    "vcffilter": ["bcftools filter"],
}

_DEFAULT_STAGE_METADATA: dict[str, dict[str, list[str]]] = {
    "spades_assemble": {"produces_stages": ["assembled"]},
    "bwa_mem_align": {"produces_stages": ["aligned"]},
    "subread_align": {"produces_stages": ["aligned"]},
    "star_align": {"produces_stages": ["aligned"]},
    "star_2pass_align": {"produces_stages": ["aligned"]},
    "hisat2_align": {"produces_stages": ["aligned"]},
    "freebayes_call": {"consumes_stages": ["aligned"], "produces_stages": ["raw"]},
    "bcftools_call": {"consumes_stages": ["aligned"], "produces_stages": ["raw"]},
    "bcftools_filter_run": {"consumes_stages": ["raw"], "produces_stages": ["filtered"]},
    "bcftools_norm_run": {
        "consumes_stages": ["raw", "filtered", "subtracted", "annotated"],
        "produces_stages": ["normalized"],
    },
    "gatk_haplotypecaller": {"consumes_stages": ["aligned"], "produces_stages": ["raw"]},
    "snpeff_annotate": {
        "consumes_stages": ["raw", "filtered", "subtracted"],
        "produces_stages": ["annotated"],
    },
    "shared_variants_export_run": {
        "consumes_stages": ["annotated", "normalized"],
        "produces_stages": ["shared"],
    },
    "tabix_index_run": {"produces_stages": ["indexed"]},
    "featurecounts_run": {"consumes_stages": ["aligned"], "produces_stages": ["counts"]},
    "deseq2_run": {"consumes_stages": ["counts"], "produces_stages": ["expression"]},
    "edger_run": {"consumes_stages": ["counts"], "produces_stages": ["expression"]},
    "limma_voom_run": {"consumes_stages": ["counts"], "produces_stages": ["expression"]},
}


def _build_bidirectional_alternatives(
    forward: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Build a symmetric tool-alternative mapping."""

    groups: dict[str, set[str]] = {}
    for key, alternatives in forward.items():
        group = {key} | {str(item).strip() for item in alternatives if str(item).strip()}
        anchor: str | None = None
        for member in group:
            if member in groups:
                if anchor is None:
                    anchor = member
                elif member != anchor:
                    groups[anchor] |= groups.pop(member)
        if anchor is None:
            anchor = key
        groups.setdefault(anchor, set()).update(group)
    rendered: dict[str, list[str]] = {}
    for members in groups.values():
        for member in members:
            rendered[member] = sorted(members - {member})
    return rendered


def _load_markdown_frontmatter(path: Path) -> dict[str, Any]:
    """Return YAML frontmatter metadata from a skill definition when present.

    Args:
        path: Candidate Markdown definition path.

    Returns:
        Parsed frontmatter mapping, or an empty mapping when the file does not
        contain valid top-of-file frontmatter.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    lines = raw.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}
    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}
    try:
        payload = yaml.safe_load("\n".join(lines[1:end_idx])) or {}
    except yaml.YAMLError:
        return {}
    return payload if isinstance(payload, dict) else {}


# Scalar parameter types that MUST NOT be treated as filesystem paths even
# when the parameter name happens to match a path-like naming heuristic.
# Fix #26 (2026-04-23, post-exp42): ``bcftools_filter_run`` declares
# ``output_type: {"type": "string"}`` (emits a bcftools encoding flag `z` /
# `v` / `b`). Without this allowlist, the name-based fallback below would
# classify ``output_type`` as a path parameter because it starts with
# ``output_`` — then downstream path-rewriting passes resolve the literal
# value "z" against ``selected_dir``, producing ``/selected/z`` and
# crashing the wrapper with "output_type must be one of: b, v, z".
# The allowlist is intentionally small and covers JSON-schema scalar
# families so the fix generalizes to every wrapper that declares a
# non-path parameter whose name resembles a path — not just
# ``output_type``.
_NON_PATH_SCALAR_TYPES = frozenset(
    {
        "string",
        "str",
        "number",
        "integer",
        "int",
        "float",
        "boolean",
        "bool",
    }
)


def _is_output_parameter(name: str, spec: dict[str, Any]) -> bool:
    """Return whether one skill parameter represents an output path."""

    param_name = str(name or "").strip().lower()
    file_role = normalize_file_role(spec)
    if is_execution_output_parameter(spec):
        return True
    if file_role:
        return is_output_like_file_role(file_role)
    # Fix #26: if the parameter spec declares a scalar (non-path) type,
    # respect that declaration and do NOT fall back to the name-based
    # heuristic. See ``_NON_PATH_SCALAR_TYPES`` docstring for rationale.
    param_type = str(spec.get("type", "") or "").strip().lower()
    if param_type in _NON_PATH_SCALAR_TYPES:
        return False
    return param_name.startswith("output") or param_name.endswith(
        (
            "_output",
            "_output_dir",
            "_output_file",
            "_out",
            "_outdir",
            "_gtf",
            "_csv",
            "_tsv",
            "_json",
            "_jsonl",
            "_vcf",
            "_vcf_gz",
            "_bam",
            "_bed",
            "_bw",
            "_png",
            "_svg",
            "_html",
            "_pdf",
            "_txt",
            "_md",
        )
    )


def _is_path_parameter(name: str, spec: dict[str, Any]) -> bool:
    """Return whether one skill parameter carries a filesystem path."""

    param_name = str(name or "").strip().lower()
    param_type = str(spec.get("type", "") or "").strip().lower()
    file_role = normalize_file_role(spec)
    if param_type == "path" or file_role:
        return True
    # Fix #26: honour explicit scalar type declarations. See
    # ``_NON_PATH_SCALAR_TYPES`` docstring for rationale.
    if param_type in _NON_PATH_SCALAR_TYPES:
        return False
    return param_name.startswith(("input_", "output_")) or param_name.endswith(
        (
            "_path",
            "_paths",
            "_dir",
            "_dirs",
            "_file",
            "_files",
            "_fasta",
            "_fa",
            "_fna",
            "_gff",
            "_gff3",
            "_gtf",
            "_vcf",
            "_vcf_gz",
            "_bam",
            "_cram",
            "_counts",
        )
    )


def _is_existing_input_parameter(name: str, spec: dict[str, Any]) -> bool:
    """Return whether a supplied path parameter should exist before execution."""

    file_role = normalize_file_role(spec)
    if file_role and is_input_like_file_role(file_role):
        return _is_path_parameter(name, spec)
    if is_required_existing_input(name, spec) and _is_path_parameter(name, spec):
        return True
    param_name = str(name or "").strip().lower()
    return param_name.startswith("input_") and _is_path_parameter(name, spec)


@dataclass
class ToolParameterMeta:
    """Structured metadata for one declared tool parameter."""

    name: str
    required: bool = False
    param_type: str = ""
    file_role: str = ""
    ownership: str = ""
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "name": self.name,
            "required": self.required,
            "param_type": self.param_type,
            "file_role": self.file_role,
            "ownership": self.ownership,
            "description": self.description,
        }


@dataclass
class ToolMeta:
    """Runtime metadata for one executable tool."""

    name: str
    exec_hints: List[str] = field(default_factory=list)
    stall_grace: int = 0
    input_path_keys: List[str] = field(default_factory=list)
    output_argument_keys: List[str] = field(default_factory=list)
    buildable_path_keys: List[str] = field(default_factory=list)
    canonical_output_filenames: Dict[str, Any] = field(default_factory=dict)
    expected_output_files: List[str] = field(default_factory=list)
    expected_output_files_by_key: Dict[str, List[str]] = field(default_factory=dict)
    required_parameters: List[str] = field(default_factory=list)
    harness_managed_parameters: List[str] = field(default_factory=list)
    execution_output_parameters: List[str] = field(default_factory=list)
    parameter_schema: Dict[str, ToolParameterMeta] = field(default_factory=dict)
    is_heavy: bool = False
    signal_equivalences: List[str] = field(default_factory=list)
    parameter_defaults: Dict[str, Any] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    alternative_tools: List[str] = field(default_factory=list)
    description: str = ""
    consumes_stages: List[str] = field(default_factory=list)
    produces_stages: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "name": self.name,
            "exec_hints": list(self.exec_hints),
            "stall_grace": int(self.stall_grace),
            "input_path_keys": list(self.input_path_keys),
            "output_argument_keys": list(self.output_argument_keys),
            "buildable_path_keys": list(self.buildable_path_keys),
            "canonical_output_filenames": dict(self.canonical_output_filenames),
            "expected_output_files": list(self.expected_output_files),
            "expected_output_files_by_key": {
                key: list(value)
                for key, value in self.expected_output_files_by_key.items()
            },
            "required_parameters": list(self.required_parameters),
            "harness_managed_parameters": list(self.harness_managed_parameters),
            "execution_output_parameters": list(self.execution_output_parameters),
            "parameter_schema": {
                key: value.as_dict() for key, value in self.parameter_schema.items()
            },
            "is_heavy": bool(self.is_heavy),
            "signal_equivalences": list(self.signal_equivalences),
            "parameter_defaults": dict(self.parameter_defaults),
            "capabilities": list(self.capabilities),
            "alternative_tools": list(self.alternative_tools),
            "description": self.description,
            "consumes_stages": list(self.consumes_stages),
            "produces_stages": list(self.produces_stages),
        }


class ToolRegistry:
    """Consolidated runtime metadata for known executable tools."""

    def __init__(
        self,
        *,
        skill_index_path: Optional[Path] = None,
        skill_library_dir: Optional[Path] = None,
    ) -> None:
        self._tools: Dict[str, ToolMeta] = {}
        self._skill_index_path = skill_index_path
        self._skill_library_dir = skill_library_dir

    @classmethod
    def from_defaults(
        cls,
        *,
        skill_index_path: Optional[Path] = None,
        skill_library_dir: Optional[Path] = None,
        signal_equivalences: Optional[Dict[str, List[str]]] = None,
        parameter_knowledge_base: Optional[Dict[str, Dict[str, Any]]] = None,
        expected_output_files: Optional[Dict[str, Any]] = None,
        tool_alternatives: Optional[Dict[str, List[str]]] = None,
        stage_metadata: Optional[Dict[str, Dict[str, List[str]]]] = None,
    ) -> "ToolRegistry":
        """Build a registry from repository defaults."""

        registry = cls(
            skill_index_path=skill_index_path,
            skill_library_dir=skill_library_dir,
        )
        registry._load_constants()
        registry._load_skill_index()
        registry._load_markdown_skill_definitions()
        registry._load_signal_equivalences(signal_equivalences)
        registry._load_parameter_defaults(parameter_knowledge_base)
        registry._load_expected_output_files(expected_output_files)
        registry._load_tool_alternatives(tool_alternatives)
        registry._load_stage_metadata(stage_metadata)
        return registry

    def get(self, name: str) -> Optional[ToolMeta]:
        """Return metadata for *name*, or ``None`` if unknown."""

        return self._tools.get(str(name or "").strip())

    def known_tool_names(self) -> FrozenSet[str]:
        """Return every registered tool name."""

        return frozenset(self._tools.keys())

    def tools_with_exec_hints(self) -> Dict[str, List[str]]:
        """Return a mapping of tool names to executable hints."""

        return {
            name: list(meta.exec_hints)
            for name, meta in self._tools.items()
            if meta.exec_hints
        }

    def heavy_tools(self) -> FrozenSet[str]:
        """Return the names of resource-intensive tools."""

        return frozenset(name for name, meta in self._tools.items() if meta.is_heavy)

    def stall_grace_for(self, name: str) -> int:
        """Return the configured stall grace period for *name*."""

        meta = self.get(name)
        return int(meta.stall_grace) if meta is not None else 0

    def exec_hints_for(self, name: str) -> List[str]:
        """Return executable hints for *name*."""

        meta = self.get(name)
        return list(meta.exec_hints) if meta is not None else []

    def input_keys_for(self, name: str) -> List[str]:
        """Return declared input-path parameters for *name*."""

        meta = self.get(name)
        return list(meta.input_path_keys) if meta is not None else []

    def output_argument_keys_for(self, name: str) -> List[str]:
        """Return declared output-path parameters for *name*."""

        meta = self.get(name)
        return list(meta.output_argument_keys) if meta is not None else []

    def buildable_path_keys_for(self, name: str) -> List[str]:
        """Return declared tool-built path parameters for *name*."""

        meta = self.get(name)
        return list(meta.buildable_path_keys) if meta is not None else []

    def canonical_output_filenames_for(self, name: str) -> Dict[str, Any]:
        """Return canonical output basenames declared for *name*."""

        meta = self.get(name)
        return dict(meta.canonical_output_filenames) if meta is not None else {}

    def expected_output_files_for(self, name: str) -> List[str]:
        """Return expected file basenames emitted beneath an output root."""

        meta = self.get(name)
        return list(meta.expected_output_files) if meta is not None else []

    def expected_output_files_by_key_for(self, name: str) -> Dict[str, List[str]]:
        """Return expected output files scoped by output argument key."""

        meta = self.get(name)
        if meta is None:
            return {}
        return {
            key: list(value)
            for key, value in meta.expected_output_files_by_key.items()
        }

    def parameter_defaults_for(self, name: str) -> Dict[str, Any]:
        """Return default parameter values for *name*."""

        meta = self.get(name)
        return dict(meta.parameter_defaults) if meta is not None else {}

    def required_parameters_for(self, name: str) -> List[str]:
        """Return required argument names for *name*."""

        meta = self.get(name)
        return list(meta.required_parameters) if meta is not None else []

    def harness_managed_parameters_for(self, name: str) -> List[str]:
        """Return planner-sanitized harness-managed argument names for *name*."""

        meta = self.get(name)
        return list(meta.harness_managed_parameters) if meta is not None else []

    def execution_output_parameters_for(self, name: str) -> List[str]:
        """Return declared execution-output parameter names for *name*."""

        meta = self.get(name)
        return list(meta.execution_output_parameters) if meta is not None else []

    def primary_output_parameter_for(self, name: str) -> str:
        """Return the preferred primary output parameter for *name* when known."""

        meta = self.get(name)
        if meta is None:
            return ""
        output_keys = list(meta.output_argument_keys)
        if not output_keys:
            return ""
        if "output_dir" in output_keys:
            return "output_dir"
        preferred = [key for key in output_keys if str(key).startswith("output")]
        if preferred:
            return sorted(preferred)[0]
        by_key = list(meta.expected_output_files_by_key)
        if len(by_key) == 1 and by_key[0] in output_keys:
            return by_key[0]
        canonical_keys = [
            key
            for key, value in meta.canonical_output_filenames.items()
            if key in output_keys and isinstance(value, str) and str(value).strip()
        ]
        if canonical_keys:
            preferred = [key for key in canonical_keys if str(key).startswith("output")]
            if preferred:
                return sorted(preferred)[0]
            return sorted(canonical_keys)[0]
        return output_keys[0]

    def parameter_schema_for(self, name: str) -> Dict[str, ToolParameterMeta]:
        """Return declared parameter metadata for *name*."""

        meta = self.get(name)
        return dict(meta.parameter_schema) if meta is not None else {}

    def capabilities_for(self, name: str) -> List[str]:
        """Return declared capability identifiers for *name*."""

        meta = self.get(name)
        return list(meta.capabilities) if meta is not None else []

    def consumes_stages_for(self, name: str) -> List[str]:
        """Return declared consumed artifact stages for *name*."""

        meta = self.get(name)
        return list(meta.consumes_stages) if meta is not None else []

    def produces_stages_for(self, name: str) -> List[str]:
        """Return declared produced artifact stages for *name*."""

        meta = self.get(name)
        return list(meta.produces_stages) if meta is not None else []

    def signal_equivalences_for(self, name: str) -> List[str]:
        """Return canonical signal aliases satisfied by *name*."""

        meta = self.get(name)
        return list(meta.signal_equivalences) if meta is not None else []

    def alternative_tools_for(self, name: str) -> List[str]:
        """Return registered alternative tools for repair flows."""

        meta = self.get(name)
        return list(meta.alternative_tools) if meta is not None else []

    def wrapper_parameter_names_for(self, name: str) -> FrozenSet[str]:
        """Return explicit wrapper parameters when available."""

        func = self._load_wrapper_function(name)
        if func is None:
            return frozenset()
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return frozenset()
        return frozenset(
            param_name
            for param_name, param in signature.parameters.items()
            if param.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        )

    def wrapper_accepts_var_keyword(self, name: str) -> bool:
        """Return whether the wrapper accepts arbitrary keyword arguments."""

        func = self._load_wrapper_function(name)
        if func is None:
            return False
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return False
        return any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )

    def all_input_path_keys(self) -> Dict[str, List[str]]:
        """Return the full input-path lookup table."""

        return {
            name: list(meta.input_path_keys)
            for name, meta in self._tools.items()
            if meta.input_path_keys
        }

    def all_required_parameters(self) -> Dict[str, List[str]]:
        """Return the required-parameter lookup table."""

        return {
            name: list(meta.required_parameters)
            for name, meta in self._tools.items()
            if meta.required_parameters
        }

    def _ensure(self, name: str) -> ToolMeta:
        key = str(name or "").strip()
        if key not in self._tools:
            self._tools[key] = ToolMeta(name=key)
        return self._tools[key]

    def _load_constants(self) -> None:
        all_tool_names: Set[str] = set()
        all_tool_names.update(PLAN_TOOL_EXEC_HINTS.keys())
        all_tool_names.update(TOOL_STALL_GRACE_HINTS.keys())
        all_tool_names.update(PLAN_INPUT_PATH_KEYS.keys())
        all_tool_names.update(HEAVY_TOOL_NAMES)
        for name in all_tool_names:
            meta = self._ensure(name)
            meta.exec_hints = list(PLAN_TOOL_EXEC_HINTS.get(name, []))
            meta.stall_grace = int(TOOL_STALL_GRACE_HINTS.get(name, 0) or 0)
            meta.input_path_keys = list(PLAN_INPUT_PATH_KEYS.get(name, []))
            meta.is_heavy = name in HEAVY_TOOL_NAMES

    def _load_signal_equivalences(
        self,
        signal_equivalences: Optional[Dict[str, List[str]]],
    ) -> None:
        if signal_equivalences is None:
            try:
                from bio_harness.core.protocol_grounding._shared import (
                    SIGNAL_EQUIVALENCES,
                )

                signal_equivalences = SIGNAL_EQUIVALENCES
            except ImportError:
                signal_equivalences = {}
        for canonical, aliases in (signal_equivalences or {}).items():
            canonical_name = str(canonical or "").strip()
            if not canonical_name:
                continue
            for alias in aliases:
                alias_name = str(alias or "").strip()
                if not alias_name or alias_name not in self._tools:
                    continue
                meta = self._tools[alias_name]
                if canonical_name not in meta.signal_equivalences:
                    meta.signal_equivalences.append(canonical_name)

    def _load_parameter_defaults(
        self,
        parameter_knowledge_base: Optional[Dict[str, Dict[str, Any]]],
    ) -> None:
        if parameter_knowledge_base is None:
            try:
                from bio_harness.core.protocol_grounding._shared import (
                    PARAMETER_KNOWLEDGE_BASE,
                )

                parameter_knowledge_base = PARAMETER_KNOWLEDGE_BASE
            except ImportError:
                parameter_knowledge_base = {}
        for tool_name, defaults in (parameter_knowledge_base or {}).items():
            meta = self._ensure(tool_name)
            meta.parameter_defaults = dict(defaults)

    def _load_skill_index(self) -> None:
        skill_index_path = self._resolved_skill_index_path()
        if skill_index_path is None or not skill_index_path.is_file():
            return
        try:
            payload = json.loads(skill_index_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "Failed to load skill index from %s",
                skill_index_path,
                exc_info=True,
            )
            return
        for entry in payload.get("skills", []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "") or "").strip()
            if not name:
                continue
            meta = self._ensure(name)
            if not meta.exec_hints:
                meta.exec_hints = [
                    str(item).strip()
                    for item in (entry.get("tools_required") or [])
                    if str(item).strip()
                ]
            meta.capabilities = [
                str(item).strip()
                for item in (entry.get("capabilities") or [])
                if str(item).strip()
            ]
            meta.description = str(entry.get("description", "") or "").strip()
            canonical = entry.get("canonical_output_filenames", {})
            if isinstance(canonical, dict):
                meta.canonical_output_filenames = {
                    str(key).strip(): (
                        [
                            str(item).strip()
                            for item in value
                            if str(item).strip()
                        ]
                        if isinstance(value, list)
                        else str(value).strip()
                    )
                    for key, value in canonical.items()
                    if str(key).strip()
                    and (
                        (isinstance(value, list) and any(str(item).strip() for item in value))
                        or (not isinstance(value, list) and str(value).strip())
                    )
                }
            self._merge_parameter_schema(meta, entry.get("parameters", {}))
            self._merge_skill_definition_parameters(
                meta,
                file_path=str(entry.get("file_path", "") or "").strip(),
                skill_index_path=skill_index_path,
            )

    def _load_expected_output_files(
        self,
        expected_output_files: Optional[Dict[str, Any]],
    ) -> None:
        for tool_name, outputs in (
            expected_output_files or _DEFAULT_EXPECTED_OUTPUT_FILES
        ).items():
            meta = self._ensure(tool_name)
            if isinstance(outputs, dict):
                normalized_by_key = {
                    str(key).strip(): [
                        str(item).strip()
                        for item in (values or [])
                        if str(item).strip()
                    ]
                    for key, values in outputs.items()
                    if str(key).strip()
                }
                meta.expected_output_files_by_key = {
                    key: value for key, value in normalized_by_key.items() if value
                }
                meta.expected_output_files = sorted(
                    {
                        item
                        for values in meta.expected_output_files_by_key.values()
                        for item in values
                    }
                )
                continue
            normalized_outputs = [
                str(item).strip() for item in outputs if str(item).strip()
            ]
            meta.expected_output_files = normalized_outputs
            meta.expected_output_files_by_key = self._infer_expected_output_files_by_key(
                meta,
                normalized_outputs,
            )

    def _load_tool_alternatives(
        self,
        tool_alternatives: Optional[Dict[str, List[str]]],
    ) -> None:
        for tool_name, alternatives in _build_bidirectional_alternatives(
            tool_alternatives or _DEFAULT_TOOL_ALTERNATIVES_FORWARD
        ).items():
            meta = self._ensure(tool_name)
            meta.alternative_tools = [
                str(item).strip() for item in alternatives if str(item).strip()
            ]

    def _load_stage_metadata(
        self,
        stage_metadata: Optional[Dict[str, Dict[str, List[str]]]],
    ) -> None:
        """Load optional runtime stage metadata for registered tools."""

        for tool_name, metadata in (stage_metadata or _DEFAULT_STAGE_METADATA).items():
            normalized_tool = str(tool_name or "").strip()
            if not normalized_tool:
                continue
            meta = self.get(normalized_tool)
            if meta is None:
                logger.debug("Skipping stage metadata for unknown tool %s", normalized_tool)
                continue
            if not isinstance(metadata, dict):
                continue
            for field_name in ("consumes_stages", "produces_stages"):
                values = [
                    str(item).strip()
                    for item in (metadata.get(field_name, []) or [])
                    if str(item).strip()
                ]
                if any(value not in _ALLOWED_STAGES for value in values):
                    invalid = sorted({value for value in values if value not in _ALLOWED_STAGES})
                    raise ValueError(
                        f"Unknown stage metadata for {normalized_tool}: {field_name}={invalid}"
                    )
                setattr(meta, field_name, values)

    def _merge_parameter_schema(self, meta: ToolMeta, raw_parameters: Any) -> None:
        if not isinstance(raw_parameters, dict):
            return
        declared_required: list[str] = []
        declared_inputs: set[str] = set(meta.input_path_keys)
        declared_outputs: set[str] = set(meta.output_argument_keys)
        declared_buildable: set[str] = set(meta.buildable_path_keys)
        declared_harness_managed: set[str] = set(meta.harness_managed_parameters)
        declared_execution_outputs: set[str] = set(meta.execution_output_parameters)
        for raw_name, raw_spec in raw_parameters.items():
            name = str(raw_name or "").strip()
            spec = raw_spec if isinstance(raw_spec, dict) else {}
            if not name:
                continue
            parameter = ToolParameterMeta(
                name=name,
                required=bool(spec.get("required", False)),
                param_type=str(spec.get("type", "") or "").strip(),
                file_role=normalize_file_role(spec),
                ownership=normalize_parameter_ownership(spec),
                description=str(spec.get("description", "") or "").strip(),
            )
            meta.parameter_schema[name] = parameter
            harness_managed = is_harness_managed_parameter(spec)
            execution_output = is_execution_output_parameter(spec)
            if parameter.required and not harness_managed:
                declared_required.append(name)
            if harness_managed:
                declared_harness_managed.add(name)
                declared_inputs.discard(name)
                declared_outputs.discard(name)
                declared_buildable.discard(name)
                declared_execution_outputs.discard(name)
                continue
            declared_harness_managed.discard(name)
            if execution_output:
                declared_execution_outputs.add(name)
            else:
                declared_execution_outputs.discard(name)
            if _is_output_parameter(name, spec):
                declared_inputs.discard(name)
                if is_primary_output_file_role(parameter.file_role) or not parameter.file_role:
                    declared_outputs.add(name)
                    declared_buildable.discard(name)
                else:
                    declared_buildable.add(name)
                    declared_outputs.discard(name)
            elif _is_existing_input_parameter(name, spec):
                declared_inputs.add(name)
            else:
                declared_inputs.discard(name)
        if declared_required:
            meta.required_parameters = sorted(set(meta.required_parameters) | set(declared_required))
        if declared_inputs:
            meta.input_path_keys = sorted(declared_inputs)
        else:
            meta.input_path_keys = []
        if declared_outputs:
            meta.output_argument_keys = sorted(declared_outputs)
        else:
            meta.output_argument_keys = []
        if declared_buildable:
            meta.buildable_path_keys = sorted(declared_buildable)
        else:
            meta.buildable_path_keys = []
        if declared_harness_managed:
            meta.harness_managed_parameters = sorted(declared_harness_managed)
        else:
            meta.harness_managed_parameters = []
        if declared_execution_outputs:
            meta.execution_output_parameters = sorted(declared_execution_outputs)
        else:
            meta.execution_output_parameters = []

    def _infer_expected_output_files_by_key(
        self,
        meta: ToolMeta,
        outputs: List[str],
    ) -> Dict[str, List[str]]:
        """Infer output-file bindings for tools with one primary output root."""

        normalized_outputs = [str(item).strip() for item in outputs if str(item).strip()]
        if not normalized_outputs:
            return {}
        output_keys = list(meta.output_argument_keys)
        if len(output_keys) == 1:
            return {output_keys[0]: normalized_outputs}
        if "output_dir" in output_keys:
            return {"output_dir": normalized_outputs}
        return {}

    def _resolved_skill_index_path(self) -> Optional[Path]:
        if self._skill_index_path is not None:
            return self._skill_index_path
        candidate = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "definitions"
            / "index.json"
        )
        return candidate if candidate.is_file() else None

    def _resolved_skill_library_dir(self) -> Optional[Path]:
        if self._skill_library_dir is not None:
            return self._skill_library_dir
        candidate = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "library"
        )
        return candidate if candidate.is_dir() else None

    def _resolved_skill_definitions_dir(self) -> Optional[Path]:
        """Return the repository skill-definition directory when available."""

        candidate = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "definitions"
        )
        return candidate if candidate.is_dir() else None

    def _load_markdown_skill_definitions(self) -> None:
        """Overlay runtime metadata from current Markdown skill definitions.

        The generated skill index is convenient for search and static assets, but
        it can lag behind hand-edited definition files. Runtime validation should
        prefer the current Markdown frontmatter so new or updated wrappers remain
        executable even before the index is regenerated.
        """

        definitions_dir = self._resolved_skill_definitions_dir()
        if definitions_dir is None:
            return
        for definition_path in sorted(definitions_dir.glob("*.md")):
            if definition_path.name == "template.md":
                continue
            definition = _load_markdown_frontmatter(definition_path)
            if not definition:
                continue
            tool_name = str(
                definition.get("name", "") or definition_path.stem
            ).strip()
            if not tool_name:
                continue
            meta = self._ensure(tool_name)
            if not meta.exec_hints:
                meta.exec_hints = [
                    str(item).strip()
                    for item in (definition.get("tools_required") or [])
                    if str(item).strip()
                ]
            capabilities = [
                str(item).strip()
                for item in (definition.get("capabilities") or [])
                if str(item).strip()
            ]
            if capabilities:
                meta.capabilities = capabilities
            if not meta.description:
                meta.description = str(
                    definition.get("description", "") or ""
                ).strip()
            canonical = definition.get("canonical_output_filenames", {})
            if isinstance(canonical, dict):
                meta.canonical_output_filenames = {
                    str(key).strip(): (
                        [
                            str(item).strip()
                            for item in value
                            if str(item).strip()
                        ]
                        if isinstance(value, list)
                        else str(value).strip()
                    )
                    for key, value in canonical.items()
                    if str(key).strip()
                    and (
                        (isinstance(value, list) and any(str(item).strip() for item in value))
                        or (not isinstance(value, list) and str(value).strip())
                    )
                }
            self._merge_parameter_schema(meta, definition.get("parameters", {}))

    def _merge_skill_definition_parameters(
        self,
        meta: ToolMeta,
        *,
        file_path: str,
        skill_index_path: Path,
    ) -> None:
        """Merge current Markdown definition parameters over index metadata.

        The generated ``skills/definitions/index.json`` is operationally useful
        but can lag behind hand-edited definition files during development. The
        runtime registry should prefer current tool contracts from the source
        definition when they are available so validators and binders do not
        drift on stale generated metadata.

        Args:
            meta: Tool metadata object to update.
            file_path: Relative or absolute definition path from the index.
            skill_index_path: Loaded index path used to resolve relative paths.
        """

        definition_path = self._resolve_skill_definition_path(
            file_path=file_path,
            skill_index_path=skill_index_path,
        )
        if definition_path is None:
            return
        definition = _load_markdown_frontmatter(definition_path)
        if not definition:
            return
        self._merge_parameter_schema(meta, definition.get("parameters", {}))

    def _resolve_skill_definition_path(
        self,
        *,
        file_path: str,
        skill_index_path: Path,
    ) -> Optional[Path]:
        """Resolve an indexed skill-definition path to a real Markdown file.

        Args:
            file_path: Relative or absolute path recorded in the skill index.
            skill_index_path: Concrete path to the loaded index file.

        Returns:
            Resolved definition path when one exists, otherwise ``None``.
        """

        normalized_file_path = str(file_path or "").strip()
        if not normalized_file_path:
            return None
        raw_path = Path(normalized_file_path).expanduser()
        candidates: list[Path] = []
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.append((skill_index_path.parent / raw_path).resolve())
            candidates.extend((parent / raw_path).resolve() for parent in skill_index_path.resolve().parents)
        seen: set[Path] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                return candidate
        return None

    @lru_cache(maxsize=256)
    def _load_wrapper_function(self, name: str) -> Any | None:
        tool_name = str(name or "").strip()
        if not tool_name:
            return None
        library_dir = self._resolved_skill_library_dir()
        if library_dir is None:
            return None
        module_path = library_dir / f"{tool_name}.py"
        if not module_path.is_file():
            return None
        try:
            spec = importlib.util.spec_from_file_location(
                f"bio_harness.skills.library.{tool_name}",
                module_path,
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            logger.debug(
                "Failed to import wrapper for %s from %s",
                tool_name,
                module_path,
                exc_info=True,
            )
            return None
        return getattr(module, tool_name, None)


@lru_cache(maxsize=1)
def default_tool_registry() -> ToolRegistry:
    """Return the cached default tool registry."""

    return ToolRegistry.from_defaults()


def iter_tool_equivalence_signals() -> Iterable[tuple[str, list[str]]]:
    """Yield canonical signal groups for compatibility code."""

    registry = default_tool_registry()
    groups: dict[str, set[str]] = {}
    for tool_name in registry.known_tool_names():
        meta = registry.get(tool_name)
        if meta is None:
            continue
        for canonical in meta.signal_equivalences:
            groups.setdefault(canonical, set()).add(tool_name)
    for canonical, tools in sorted(groups.items()):
        yield canonical, sorted(tools)
