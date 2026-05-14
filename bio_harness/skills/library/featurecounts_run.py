from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "run_featurecounts.py"


def _normalize_input_bams(value: object) -> list[str]:
    if value is None:
        return []
    tokens: list[str] = []
    if isinstance(value, (list, tuple, set)):
        tokens = [str(x).strip() for x in value if str(x).strip()]
    else:
        raw = str(value).strip()
        if raw:
            # Handle stringified JSON lists: "['/path/a.bam', '/path/b.bam']"
            if raw.startswith("[") and raw.endswith("]"):
                import json as _json
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, list):
                        tokens = [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    # Try stripping brackets and splitting on comma
                    inner = raw[1:-1]
                    tokens = [x.strip().strip("'\"") for x in inner.split(",") if x.strip().strip("'\"")]
            else:
                try:
                    tokens = [str(x).strip() for x in shlex.split(raw, posix=True) if str(x).strip()]
                except Exception:
                    tokens = [x for x in raw.split() if x]
    return tokens


def _render_input_bams(value: object) -> str:
    tokens = _normalize_input_bams(value)
    return " ".join(shlex.quote(x) for x in tokens)


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _render_shell_parts(parts: list[str]) -> str:
    """Render one shell-safe command from raw parts."""

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def featurecounts_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    annotation_gtf = str(kwargs.get("annotation_gtf", "")).strip()
    output_counts = str(kwargs.get("output_counts", "")).strip()
    threads = str(kwargs.get("threads", "")).strip()
    if not annotation_gtf or not output_counts or not threads:
        raise ValueError("Missing required parameter(s) for template: threads, annotation_gtf, output_counts")

    extra_flags: list[str] = []
    annotation_path = str(kwargs.get("annotation_gtf", "")).strip().lower()
    annotation_format = str(kwargs.get("annotation_format", "")).strip()
    feature_type = str(kwargs.get("feature_type", "")).strip()
    attribute_type = str(kwargs.get("attribute_type", "")).strip()

    if not annotation_format and annotation_path.endswith((".gff", ".gff3", ".gff.gz", ".gff3.gz")):
        annotation_format = "GFF"
    if annotation_format:
        extra_flags.extend(["-F", shlex.quote(annotation_format)])
    if feature_type:
        extra_flags.extend(["-t", shlex.quote(feature_type)])
    elif annotation_format.upper() == "GFF":
        extra_flags.extend(["-t", "gene"])
    if attribute_type:
        extra_flags.extend(["-g", shlex.quote(attribute_type)])
    elif annotation_format.upper() == "GFF":
        extra_flags.extend(["-g", "ID"])
    if _is_truthy(kwargs.get("is_paired_end")):
        extra_flags.append("-p")
        if _is_truthy(kwargs.get("count_read_pairs", True)):
            extra_flags.append("--countReadPairs")
    strandedness = str(kwargs.get("strand_specificity", kwargs.get("strandedness", ""))).strip()
    input_bam_tokens = _normalize_input_bams(kwargs.get("input_bams"))
    if not input_bam_tokens:
        raise ValueError("Missing required parameter(s) for template: input_bams")

    command_parts = managed_python_command_parts(
        python_executable=str(preferred_helper_python_executable()),
        script_path=str(_SCRIPT_PATH),
    )
    command_parts.extend(
        [
            "--threads",
            threads,
            "--annotation-gtf",
            annotation_gtf,
            "--output-counts",
            output_counts,
        ]
    )
    for input_bam in input_bam_tokens:
        command_parts.extend(["--input-bam", input_bam])
    if annotation_format:
        command_parts.extend(["--annotation-format", annotation_format])
    if feature_type:
        command_parts.extend(["--feature-type", feature_type])
    if attribute_type:
        command_parts.extend(["--attribute-type", attribute_type])
    explicit_paired_end = kwargs.get("is_paired_end")
    if explicit_paired_end is not None and str(explicit_paired_end).strip() != "":
        paired_end_enabled = _is_truthy(explicit_paired_end)
        command_parts.append("--paired-end" if paired_end_enabled else "--single-end")
        count_read_pairs_enabled = _is_truthy(kwargs.get("count_read_pairs", paired_end_enabled))
    else:
        count_read_pairs_enabled = _is_truthy(kwargs.get("count_read_pairs", False))
    if count_read_pairs_enabled:
        command_parts.append("--count-read-pairs")
    if strandedness:
        command_parts.extend(["--strand-specificity", strandedness])
    return _render_shell_parts(command_parts)
