"""Template compiler for single-cell RNA-seq."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.core.protocol_grounding._shared import _preferred_argument_value


def _matches_fastq_read(candidate_name: str, *, read_index: int) -> bool:
    """Return whether one filename is a FASTQ for the requested read mate."""
    name_l = str(candidate_name or "").lower()
    if not re.search(r"\.f(?:ast)?q(?:\.gz)?$", name_l):
        return False
    return re.search(rf"(^|[^a-z0-9])r{int(read_index)}([^a-z0-9]|$)", name_l) is not None


def _compile_single_cell_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic template for single-cell RNA-seq.

    Supports two input modes:
      1. FASTQ input (10x Chromium): sc_count_and_cluster for end-to-end pipeline
      2. Pre-counted input (h5ad/mtx): scanpy_workflow for clustering only
    """
    sd = str(selected_dir)

    # ── Mode 1: Check for 10x FASTQ input ──
    r1_fastq = ""
    r2_fastq = ""
    whitelist = ""
    reference_fa = ""
    gtf_file = ""

    for candidate in sorted(data_root.rglob("*")):
        if not candidate.is_file():
            continue
        name_l = candidate.name.lower()
        if _matches_fastq_read(name_l, read_index=1):
            r1_fastq = str(candidate.resolve())
        elif _matches_fastq_read(name_l, read_index=2):
            r2_fastq = str(candidate.resolve())
        elif "whitelist" in name_l and name_l.endswith(".txt"):
            whitelist = str(candidate.resolve())
        elif re.search(r"\.(fa|fasta|fna)(\.gz)?$", name_l) and "reference" in name_l:
            reference_fa = str(candidate.resolve())
        elif name_l.endswith(".gtf"):
            gtf_file = str(candidate.resolve())

    # If no reference with 'reference' in name, try any .fa file
    if not reference_fa:
        for candidate in sorted(data_root.rglob("*")):
            if candidate.is_file() and re.search(r"\.(fa|fasta|fna)$", candidate.name, re.I):
                reference_fa = str(candidate.resolve())
                break

    has_10x_input = all([r1_fastq, r2_fastq, whitelist, reference_fa, gtf_file])

    if has_10x_input:
        output_dir = _preferred_argument_value(
            plan=plan,
            analysis_spec=analysis_spec,
            tool_name="sc_count_and_cluster",
            argument_key="output_dir",
            default=f"{sd}/sc_output",
        )
        steps: list[dict[str, Any]] = [
            {
                "step_id": 1,
                "tool_name": "sc_count_and_cluster",
                "purpose": "Demultiplex, count UMIs, cluster cells, find marker genes",
                "arguments": {
                    "r1": r1_fastq,
                    "r2": r2_fastq,
                    "whitelist": whitelist,
                    "reference": reference_fa,
                    "gtf": gtf_file,
                    "output_dir": output_dir,
                    "min_genes": _preferred_argument_value(
                        plan=plan,
                        analysis_spec=analysis_spec,
                        tool_name="sc_count_and_cluster",
                        argument_key="min_genes",
                        default=3,
                    ),
                    "min_cells": _preferred_argument_value(
                        plan=plan,
                        analysis_spec=analysis_spec,
                        tool_name="sc_count_and_cluster",
                        argument_key="min_cells",
                        default=1,
                    ),
                    "kmer_size": _preferred_argument_value(
                        plan=plan,
                        analysis_spec=analysis_spec,
                        tool_name="sc_count_and_cluster",
                        argument_key="kmer_size",
                        default=25,
                    ),
                    "leiden_resolution": _preferred_argument_value(
                        plan=plan,
                        analysis_spec=analysis_spec,
                        tool_name="sc_count_and_cluster",
                        argument_key="leiden_resolution",
                        default=0.5,
                    ),
                },
            },
        ]
        compiled = {
            "thought_process": "[single_cell_template] 10x FASTQ input → sc_count_and_cluster pipeline. "
            + str(plan.get("thought_process", "")),
            "plan": steps,
        }
        return compiled, {
            "changed": True,
            "why": "compiled_single_cell_10x_fastq",
            "r1": r1_fastq,
            "r2": r2_fastq,
            "whitelist": whitelist,
            "reference": reference_fa,
            "gtf": gtf_file,
        }

    # ── Mode 2: Pre-counted input (h5ad/loom/mtx) ──
    input_file = ""
    for ext_pattern in (r"\.h5ad$", r"\.loom$", r"\.h5$", r"matrix\.mtx(\.gz)?$"):
        for candidate in sorted(data_root.rglob("*")):
            if candidate.is_file() and re.search(ext_pattern, candidate.name, re.I):
                input_file = str(candidate.resolve())
                break
        if input_file:
            break
    # Also check for 10X directory containing barcodes/genes/matrix
    if not input_file:
        for d in sorted(data_root.rglob("*")):
            if d.is_dir() and (d / "matrix.mtx.gz").exists():
                input_file = str(d.resolve())
                break
    if not input_file:
        return plan, {"changed": False, "why": "no_single_cell_input"}

    steps = [
        {
            "step_id": 1,
            "tool_name": "scanpy_workflow",
            "purpose": "Preprocess, cluster, and find marker genes",
            "arguments": {
                "input_path": _preferred_argument_value(
                    plan=plan,
                    analysis_spec=analysis_spec,
                    tool_name="scanpy_workflow",
                    argument_key="input_path",
                    default=input_file,
                ),
                "output_dir": _preferred_argument_value(
                    plan=plan,
                    analysis_spec=analysis_spec,
                    tool_name="scanpy_workflow",
                    argument_key="output_dir",
                    default=f"{sd}/output",
                ),
                "min_genes": _preferred_argument_value(
                    plan=plan,
                    analysis_spec=analysis_spec,
                    tool_name="scanpy_workflow",
                    argument_key="min_genes",
                    default=300,
                ),
                "min_cells": _preferred_argument_value(
                    plan=plan,
                    analysis_spec=analysis_spec,
                    tool_name="scanpy_workflow",
                    argument_key="min_cells",
                    default=20,
                ),
                "max_mito_pct": _preferred_argument_value(
                    plan=plan,
                    analysis_spec=analysis_spec,
                    tool_name="scanpy_workflow",
                    argument_key="max_mito_pct",
                    default=15,
                ),
                "n_hvgs": _preferred_argument_value(
                    plan=plan,
                    analysis_spec=analysis_spec,
                    tool_name="scanpy_workflow",
                    argument_key="n_hvgs",
                    default=2000,
                ),
                "leiden_resolution": _preferred_argument_value(
                    plan=plan,
                    analysis_spec=analysis_spec,
                    tool_name="scanpy_workflow",
                    argument_key="leiden_resolution",
                    default=0.3,
                ),
            },
        },
    ]

    compiled = {
        "thought_process": "[single_cell_template] scanpy preprocessing+clustering. " + str(plan.get("thought_process", "")),
        "plan": steps,
    }
    return compiled, {
        "changed": True,
        "why": "compiled_single_cell_protocol",
        "input_file": input_file,
    }
