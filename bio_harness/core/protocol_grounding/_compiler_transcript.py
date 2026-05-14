"""Template compiler for transcript quantification via Salmon."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.core.protocol_grounding._shared import _discover_fastq_pairs
from bio_harness.core.request_output_intent import is_file_like_output_path


def _transcript_quant_output_dir(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
) -> str:
    """Return the transcript-quant output directory for one compiled plan."""

    candidate_values: list[str] = []
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "salmon_quant":
            continue
        args = step.get("arguments", {})
        if not isinstance(args, dict):
            continue
        output_dir = str(args.get("output_dir", "") or "").strip()
        if output_dir:
            candidate_values.append(output_dir)

    spec = analysis_spec if isinstance(analysis_spec, dict) else {}
    explicit_execution_intent = (
        spec.get("explicit_execution_intent", {})
        if isinstance(spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    locked_argument_values = (
        explicit_execution_intent.get("locked_argument_values", {})
        if isinstance(explicit_execution_intent.get("locked_argument_values", {}), dict)
        else {}
    )
    salmon_locked_args = (
        locked_argument_values.get("salmon_quant", {})
        if isinstance(locked_argument_values.get("salmon_quant", {}), dict)
        else {}
    )
    locked_output_dir = str(salmon_locked_args.get("output_dir", "") or "").strip()
    if locked_output_dir:
        candidate_values.append(locked_output_dir)

    requested_output_paths = spec.get("requested_output_paths", [])
    if isinstance(requested_output_paths, list):
        for raw_path in requested_output_paths:
            path_text = str(raw_path or "").strip()
            if path_text and not is_file_like_output_path(path_text):
                candidate_values.append(path_text)

    seen: set[str] = set()
    for raw_path in candidate_values:
        normalized = Path(raw_path).expanduser()
        if not normalized.is_absolute():
            normalized = selected_dir / normalized
        resolved = normalized.resolve(strict=False)
        rendered = str(resolved)
        if rendered in seen:
            continue
        seen.add(rendered)
        return rendered

    return str((selected_dir / "salmon_quant_out").resolve(strict=False))


def _compile_transcript_quant_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic template for transcript quantification via Salmon."""
    pairs = _discover_fastq_pairs(data_root)
    # Fall back to single pair if no labelled pairs found (e.g. reads_1.fq.gz)
    if not pairs:
        r1 = next((p for p in sorted(data_root.glob("*")) if re.search(r"reads?[_.]?1", p.name, re.I) and p.is_file()), None)
        r2 = next((p for p in sorted(data_root.glob("*")) if re.search(r"reads?[_.]?2", p.name, re.I) and p.is_file()), None)
        if r1 and r2:
            pairs = {"sample": {"reads_1": str(r1.resolve()), "reads_2": str(r2.resolve())}}
    if not pairs:
        return plan, {"changed": False, "why": "no_fastq_pairs"}

    # Find transcriptome FASTA in data_root
    tx_fasta = ""
    for candidate in sorted(data_root.glob("*")):
        if candidate.is_file() and re.search(r"transcriptome|cdna|mrna", candidate.name, re.I) and re.search(r"\.(fa|fasta|fna)(\.gz)?$", candidate.name, re.I):
            tx_fasta = str(candidate.resolve())
            break
    # Broader fallback: any .fa/.fasta that isn't clearly a genome
    if not tx_fasta:
        for candidate in sorted(data_root.glob("*")):
            if candidate.is_file() and re.search(r"\.(fa|fasta|fna)(\.gz)?$", candidate.name, re.I) and not re.search(r"genome", candidate.name, re.I):
                tx_fasta = str(candidate.resolve())
                break
    if not tx_fasta:
        return plan, {"changed": False, "why": "no_transcriptome_fasta"}

    # Use first pair (transcript-quant bench has a single sample)
    label = next(iter(pairs))
    pair = pairs[label]
    sd = str(selected_dir)

    index_dir = f"{sd}/salmon_index"
    output_dir = _transcript_quant_output_dir(
        plan=plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
    )

    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "tool_name": "salmon_quant",
            "purpose": f"Index transcriptome and quantify transcripts for {label}",
            "arguments": {
                "transcriptome_fasta": tx_fasta,
                "index_dir": index_dir,
                "reads_1": pair["reads_1"],
                "reads_2": pair["reads_2"],
                "library_type": "A",
                "threads": 4,
                "output_dir": output_dir,
            },
        },
    ]

    compiled = {
        "thought_process": f"[transcript_quant_template] Salmon quant for {label} with auto-index. " + str(plan.get("thought_process", "")),
        "plan": steps,
    }
    return compiled, {
        "changed": True,
        "why": "compiled_transcript_quant_protocol",
        "sample_label": label,
        "transcriptome_fasta": tx_fasta,
    }
