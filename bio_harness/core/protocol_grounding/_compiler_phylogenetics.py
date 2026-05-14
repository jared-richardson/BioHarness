"""Template compiler for phylogenetics."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _compile_phylogenetics_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic template for phylogenetics: MAFFT alignment → IQ-TREE inference."""
    sd = str(selected_dir)
    # Find sequence FASTA files (protein or nucleotide)
    seq_files: list[str] = []
    for candidate in sorted(data_root.rglob("*")):
        if candidate.is_file() and re.search(r"\.(fa|fasta|faa|fna)(\.gz)?$", candidate.name, re.I):
            seq_files.append(str(candidate.resolve()))
    if not seq_files:
        return plan, {"changed": False, "why": "no_sequence_files_found"}

    # Use the first (or only) multi-sequence FASTA
    input_fasta = seq_files[0]
    aligned_fasta = f"{sd}/aligned_sequences.fasta"
    output_prefix = f"{sd}/phylogeny"
    output_tree = f"{sd}/final/phylogeny.treefile"

    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "tool_name": "mafft_align",
            "purpose": "Align sequences with MAFFT",
            "arguments": {
                "input_fasta": input_fasta,
                "output_fasta": aligned_fasta,
                "threads": 2,
                "strategy_mode": "auto",
            },
        },
        {
            "step_id": 2,
            "tool_name": "phylogenetics_iqtree_style",
            "purpose": "Infer phylogenetic tree with IQ-TREE",
            "arguments": {
                "alignment_fasta": aligned_fasta,
                "output_dir": sd,
                "output_prefix": output_prefix,
                "output_tree": output_tree,
                "model": "MFP",
                "threads": "2",
                "seed": "42",
            },
        },
    ]

    compiled = {
        "thought_process": f"[phylogenetics_template] MAFFT align + IQ-TREE for {len(seq_files)} input file(s). " + str(plan.get("thought_process", "")),
        "plan": steps,
    }
    return compiled, {
        "changed": True,
        "why": "compiled_phylogenetics_protocol",
        "input_fasta": input_fasta,
        "sequence_files": len(seq_files),
    }
