"""Template compiler for viral metagenomics."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.analysis_spec_support import (
    VIRAL_KMER_HELPER_SCRIPT,
    preferred_helper_python_executable,
)
from bio_harness.core.protocol_grounding._shared import (
    _discover_fastq_pairs,
    _renumber_plan,
)


def _compile_viral_metagenomics_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile a deterministic viral metagenomics helper-backed template.

    Args:
        plan: Planner-produced plan payload.
        analysis_spec: Optional normalized analysis spec.
        selected_dir: Run output directory.
        data_root: Task data directory.

    Returns:
        Tuple of the compiled plan and compiler metadata.
    """
    pairs = _discover_fastq_pairs(data_root)
    if not pairs:
        return plan, {"changed": False, "why": "no_fastq_pairs"}
    label = next(iter(pairs))
    pair = pairs[label]
    sd = str(selected_dir)

    # Discover viral reference FASTA files.
    # Search data_root first, then task_dir/references (some benchmarks
    # stage references outside data_root).
    ref_fastas: list[str] = []
    search_dirs = [data_root]
    task_refs = data_root.parent / "references"
    if task_refs.is_dir() and task_refs != data_root:
        search_dirs.append(task_refs)
    for search_dir in search_dirs:
        for candidate in sorted(search_dir.rglob("*")):
            if candidate.is_file() and re.search(r"\.(fa|fasta|fna)(\.gz)?$", candidate.name, re.I):
                resolved = str(candidate.resolve())
                if resolved not in ref_fastas:
                    ref_fastas.append(resolved)
    if not ref_fastas:
        return plan, {"changed": False, "why": "no_reference_fastas"}

    trimmed_1 = f"{sd}/trimmed/{label}_trimmed_1.fastq.gz"
    trimmed_2 = f"{sd}/trimmed/{label}_trimmed_2.fastq.gz"
    reference_dir = next(
        (
            candidate
            for candidate in search_dirs
            if any(
                item.is_file() and re.search(r"\.(fa|fasta|fna)(\.gz)?$", item.name, re.I)
                for item in candidate.iterdir()
            )
        ),
        task_refs if task_refs.is_dir() else data_root,
    )

    helper_python = shlex.quote(str(preferred_helper_python_executable()))
    helper_script = shlex.quote(str(VIRAL_KMER_HELPER_SCRIPT.resolve(strict=False)))
    project_root = shlex.quote(str(VIRAL_KMER_HELPER_SCRIPT.resolve(strict=False).parents[2]))
    analysis_script = (
        f"env PYTHONPATH={project_root} {helper_python} {helper_script} "
        f"--reads-1 {shlex.quote(trimmed_1)} "
        f"--reads-2 {shlex.quote(trimmed_2)} "
        f"--reference-dir {shlex.quote(str(reference_dir.resolve(strict=False)))} "
        f"--output-report {shlex.quote(f'{sd}/output/classification_report.tsv')} "
        f"--output-detected {shlex.quote(f'{sd}/output/detected_viruses.txt')} "
        "--coverage-threshold 50 --kmer-size 21"
    )

    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "tool_name": "fastp_run",
            "purpose": "Quality trim reads with fastp",
            "arguments": {
                "reads_1": pair["reads_1"],
                "reads_2": pair["reads_2"],
                "output_reads_1": trimmed_1,
                "output_reads_2": trimmed_2,
                "detect_adapter_for_pe": True,
                "length_required": 30,
                "threads": 4,
                "json_report": f"{sd}/trimmed/{label}_fastp.json",
                "html_report": f"{sd}/trimmed/{label}_fastp.html",
            },
        },
        {
            "step_id": 2,
            "tool_name": "bash_run",
            "purpose": "Classify trimmed reads against the staged viral reference panel",
            "arguments": {"command": analysis_script},
        },
    ]

    compiled = {
        "thought_process": (
            f"[viral_meta_template] fastp->helper-backed viral reference classification for {label} "
            f"({len(ref_fastas)} reference(s)). " + str(plan.get("thought_process", ""))
        ),
        "plan": steps,
    }
    return _renumber_plan(compiled), {
        "changed": True,
        "why": "compiled_viral_metagenomics_protocol",
        "sample_label": label,
        "n_references": len(ref_fastas),
    }
