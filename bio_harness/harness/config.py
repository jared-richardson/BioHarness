"""Harness configuration and constants.

Provides the ``HarnessConfig`` dataclass and file-scoped constants
used throughout the harness sub-package.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from bio_harness.core.benchmark_policy import SCIENTIFIC_HARNESS_POLICY

# ---------------------------------------------------------------------------
# Project root (computed from this file's location)
# bio_harness/harness/config.py  →  parents[2] = project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
READONLY_LINKS_ROOT = WORKSPACE_ROOT / "inputs_readonly"
SKILLS_DEFINITIONS = PROJECT_ROOT / "bio_harness" / "skills" / "definitions"
SKILLS_LIBRARY = PROJECT_ROOT / "bio_harness" / "skills" / "library"
SHARED_VARIANT_EXPORTER = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "export_shared_variants_csv.py"
CF_CAUSAL_VARIANT_EXPORTER = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "export_cystic_fibrosis_csv.py"
COMPARE_PATHWAYS_SCRIPT = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "compare_pathways.py"
SINGLE_CELL_RESULTS_EXPORTER = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "export_single_cell_results_csv.py"
CAPABILITY_CATALOG_PATH = PROJECT_ROOT / "bio_harness" / "capabilities" / "catalog.json"

# ---------------------------------------------------------------------------
# Timing / numeric constants
# ---------------------------------------------------------------------------

DEFAULT_PLANNER_HEARTBEAT_SECONDS = 8
DEFAULT_PLANNER_MAX_ATTEMPTS = 3
POST_COMPLETION_DRAIN_SECONDS = 3
MAX_REPLAN_STEP_DELTA = 2
MAX_COMPOSED_FALLBACK_SEGMENTS = 3

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

STEP_COMMAND_RE = re.compile(r"^\[Step\s+(\d+)\s+Output\]\s+\[command\]\s+(.*)$")
STEP_EXEC_START_RE = re.compile(r"^--- Executing Step (\d+):\s*([A-Za-z0-9_./-]+)\s*---$")
STREAM_MARKER_RE = re.compile(r"__([A-Z][A-Z0-9_]*)__")
BAM_LIST_TOKEN_RE = re.compile(r"([A-Za-z0-9_./-]*(?:bam|bams)\.txt)")

# ---------------------------------------------------------------------------
# Tool sets
# ---------------------------------------------------------------------------

PROVENANCE_CRITICAL_TOOLS = {
    "spades_assemble",
    "flye_assemble",
    "trinity_assemble",
    "bwa_mem_align",
    "bowtie2_align",
    "hisat2_align",
    "star_align",
    "star_2pass_align",
    "subread_align",
    "minimap2_align",
    "freebayes_call",
    "bcftools_call",
    "gatk_haplotypecaller",
    "gatk_mutect2_call",
    "varscan_call",
    "snpeff_annotate",
    "vep_annotate",
    "featurecounts_run",
    "salmon_quant",
    "kallisto_quant",
    "rmats_run",
    "dexseq_run",
    "majiq_run",
}

# ---------------------------------------------------------------------------
# Output path keys
# ---------------------------------------------------------------------------

_OUTPUT_PATH_KEYS = frozenset({
    "output_dir", "output_bam", "output_vcf", "output_vcf_gz", "output_csv",
    "output_gff", "output_gtf", "output_faa", "output_counts",
    "output_unmapped_bam", "output_sam", "output_tsv", "output_file",
    "output_prefix", "gene_abundance_tsv",
})

# ---------------------------------------------------------------------------
# HarnessConfig dataclass
# ---------------------------------------------------------------------------


@dataclass
class HarnessConfig:
    prompt: str
    selected_dir: Path
    data_root: Path
    workspace_root: Path
    max_repairs: int
    heartbeat_seconds: int
    stall_timeout_seconds: int
    live_process_grace_seconds: int
    model_name: str | None
    host: str | None
    auto_install_missing_tools: bool
    allow_replan: bool
    allow_canonicalize: bool
    plan_path: Path | None
    result_json: Path | None
    quiet: bool
    print_plan: bool
    benchmark_policy: str = SCIENTIFIC_HARNESS_POLICY
    llm_backend: str | None = None
    path_graph_db: Path | None = None
    path_graph_user_key: str = "default"
    path_graph_scope: str = "global"
    path_graph_persist_preference_updates: bool = False
    auto_setup_isolated_tools: bool = False
    execution_mode: str = "batch"
    analysis_type: str = ""
