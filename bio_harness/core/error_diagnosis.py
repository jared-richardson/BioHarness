"""Structured step-failure diagnosis helpers.

This module enriches step failure handling with deterministic heuristics and an
optional LLM fallback. The initial implementation is standalone and does not
alter recovery policy or retry behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorDiagnosis:
    """Diagnosis of a step failure.

    Attributes:
        tool_name: Tool associated with the failure.
        failure_class: Recovery-policy failure class.
        root_cause: Short explanation of the likely root cause.
        suggested_fix: Actionable remediation guidance.
        confidence: Confidence label (`high`, `medium`, or `low`).
        diagnosed_by: `heuristic` or `llm`.
    """

    tool_name: str
    failure_class: str
    root_cause: str
    suggested_fix: str
    confidence: str
    diagnosed_by: str


_HEURISTIC_PATTERNS: list[tuple[re.Pattern[str], str, str, str]] = [
    (
        re.compile(r"hts_idx_load|failed to open .*\.fai|could not load local index file", re.IGNORECASE),
        "missing_index",
        "Required index is missing.",
        "Create the missing index before rerunning, for example with samtools index.",
    ),
    (
        re.compile(r"No such file or directory[:\s]+(\S+)", re.IGNORECASE),
        "corrupt_input",
        "Input file not found: {1}",
        "Check that the file path is correct and the file exists.",
    ),
    (
        re.compile(r"Permission denied[:\s]+(\S+)", re.IGNORECASE),
        "permission_filesystem",
        "Permission denied for: {1}",
        "Check file permissions and ownership with ls -la.",
    ),
    (
        re.compile(
            r"(Cannot allocate memory|cannot allocate vector|Out of memory|oom-kill|OutOfMemoryError|bad_alloc|not enough memory|heap space|limitBAMsortRAM)",
            re.IGNORECASE,
        ),
        "out_of_memory",
        "Process ran out of memory.",
        "Increase memory or reduce threads, chunk size, or dataset size before retrying.",
    ),
    (
        re.compile(r"(?:not indexed|missing index|index file.+missing)", re.IGNORECASE),
        "missing_index",
        "Required index is missing.",
        "Create the missing index before rerunning, for example with samtools index.",
    ),
    (
        re.compile(r"not sorted", re.IGNORECASE),
        "incompatible_parameters",
        "Input file is not sorted.",
        "Sort the input with samtools sort before rerunning.",
    ),
    (
        re.compile(r"(unrecognized|invalid|unknown) option", re.IGNORECASE),
        "incompatible_parameters",
        "Invalid command-line option.",
        "Check the installed tool version and available flags with --help.",
    ),
    (
        re.compile(r"fail to read the header|not a BAM file|wrong format", re.IGNORECASE),
        "corrupt_input",
        "Input file is corrupt or in the wrong format.",
        "Verify that the input file format matches the tool's expectation.",
    ),
    (
        re.compile(r"design matrix.*not full rank", re.IGNORECASE),
        "incompatible_parameters",
        "Design formula has redundant variables.",
        "Simplify the design formula or inspect metadata for confounded variables.",
    ),
    (
        re.compile(r"ModuleNotFoundError: No module named '([^']+)'", re.IGNORECASE),
        "missing_dependency",
        "Missing Python package: {1}",
        "Install the missing package or run the script inside the expected environment.",
    ),
    (
        re.compile(r"error while loading shared libraries|cannot open shared object file", re.IGNORECASE),
        "missing_dependency",
        "A required shared library is missing.",
        "Install the missing dependency or fix the runtime library path before rerunning.",
    ),
    (
        re.compile(r"(?:command not found|java:\s+command not found)", re.IGNORECASE),
        "missing_dependency",
        "A required runtime dependency is missing.",
        "Install the missing dependency or run the step inside the expected environment.",
    ),
    (
        re.compile(r"fail to locate the index files|failed to open fai index|genomedir exists|could not open genomic fasta file", re.IGNORECASE),
        "missing_index",
        "Required index or reference bundle is missing.",
        "Build or stage the required reference index bundle before rerunning.",
    ),
    (
        re.compile(r"truncated file|invalid bgzf header|parse error|quality string length.+not equal|input file is empty", re.IGNORECASE),
        "corrupt_input",
        "Input data is truncated, malformed, or empty.",
        "Validate the input file integrity and regenerate or restage the corrupted input before rerunning.",
    ),
    (
        re.compile(r"cannot .* simultaneously|cannot both be specified|missing codon table|error_missing_codon_table|0\.0%\)\s*$|reads are not counted", re.IGNORECASE),
        "incompatible_parameters",
        "Input parameters or annotations are incompatible with the requested analysis.",
        "Review the tool arguments, codon-table setting, annotation settings, or strandedness configuration before rerunning.",
    ),
    (
        re.compile(r"no space left on device|path length|could not create output file|maximum/allowed/path/length", re.IGNORECASE),
        "permission_filesystem",
        "The filesystem could not create or access the required output path.",
        "Check free disk space, writable directories, and path constraints before rerunning.",
    ),
    (
        re.compile(r"Segmentation fault", re.IGNORECASE),
        "novel_unknown",
        "Tool crashed with a segmentation fault.",
        "Check for corrupt inputs or known tool bugs before retrying.",
    ),
]


def diagnose_step_failure(
    tool_name: str,
    failure_class: str,
    exit_code: int,
    stderr: str,
    stdout: str = "",
    step_arguments: dict[str, Any] | None = None,
    llm: Any | None = None,
) -> ErrorDiagnosis:
    """Diagnose one step failure.

    Args:
        tool_name: Tool associated with the failed step.
        failure_class: Failure class from recovery policy.
        exit_code: Process exit code.
        stderr: Captured standard error.
        stdout: Captured standard output.
        step_arguments: Optional step arguments for additional context.
        llm: Optional LLM object exposing `summarize_text`.

    Returns:
        Structured diagnosis with heuristic-first behavior.
    """

    heuristic = _heuristic_diagnosis(tool_name, stderr, exit_code)
    if heuristic is not None:
        return heuristic

    if llm is not None:
        prompt = _build_diagnosis_instruction(
            tool_name=tool_name,
            failure_class=failure_class,
            exit_code=exit_code,
            step_arguments=step_arguments or {},
        )
        stderr_tail = str(stderr or "")[-2000:]
        stdout_tail = str(stdout or "")[-1000:]
        try:
            response = str(
                llm.summarize_text(
                    f"stderr:\n{stderr_tail}\n\nstdout:\n{stdout_tail}",
                    prompt,
                )
            ).strip()
        except Exception:
            pass
        else:
            root_cause, suggested_fix = _parse_llm_diagnosis(response)
            return ErrorDiagnosis(
                tool_name=str(tool_name or ""),
                failure_class=_fallback_failure_class(failure_class),
                root_cause=root_cause,
                suggested_fix=suggested_fix,
                confidence="medium",
                diagnosed_by="llm",
            )

    generic_text = str(stderr or stdout or "").strip()
    generic_tail = generic_text[-160:] if generic_text else ""
    return ErrorDiagnosis(
        tool_name=str(tool_name or ""),
        failure_class=_fallback_failure_class(failure_class),
        root_cause=(
            f"Step failed with exit code {exit_code}. "
            f"{generic_tail}" if generic_tail else f"Step failed with exit code {exit_code}."
        ),
        suggested_fix="Inspect stderr/stdout and validate the step inputs before retrying.",
        confidence="low",
        diagnosed_by="heuristic",
    )


def _heuristic_diagnosis(
    tool_name: str,
    stderr: str,
    exit_code: int,
) -> ErrorDiagnosis | None:
    """Pattern-match common failure signatures.

    Args:
        tool_name: Tool associated with the failed step.
        stderr: Captured standard error text.
        exit_code: Process exit code.

    Returns:
        Structured diagnosis when a known pattern matches, else `None`.
    """

    stderr_text = str(stderr or "")
    if not stderr_text.strip():
        return None
    for pattern, heuristic_class, root_template, fix_template in _HEURISTIC_PATTERNS:
        match = pattern.search(stderr_text)
        if not match:
            continue
        root_cause = _format_match_template(root_template, match)
        suggested_fix = _format_match_template(fix_template, match)
        return ErrorDiagnosis(
            tool_name=str(tool_name or ""),
            failure_class=heuristic_class,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            confidence="high",
            diagnosed_by="heuristic",
        )

    lowered = stderr_text.lower()
    if "user error" in lowered and ("gatk" in lowered or str(tool_name).lower().startswith("gatk")):
        return ErrorDiagnosis(
            tool_name=str(tool_name or ""),
            failure_class="incompatible_parameters",
            root_cause="GATK reported a USER ERROR, usually indicating invalid inputs or arguments.",
            suggested_fix="Read the USER ERROR details and correct the reported argument or prerequisite.",
            confidence="high",
            diagnosed_by="heuristic",
        )
    if "fatal error" in lowered and "genome generation" in lowered:
        return ErrorDiagnosis(
            tool_name=str(tool_name or ""),
            failure_class="incompatible_parameters",
            root_cause="STAR reported a fatal genome-generation or genome-index problem.",
            suggested_fix="Verify that the STAR genome index matches the reference and annotation inputs.",
            confidence="high",
            diagnosed_by="heuristic",
        )
    if "no filter or info tag" in lowered:
        return ErrorDiagnosis(
            tool_name=str(tool_name or ""),
            failure_class="incompatible_parameters",
            root_cause="VCF annotation references a missing FILTER or INFO tag.",
            suggested_fix="Validate the VCF header and confirm the referenced tag exists before annotation.",
            confidence="high",
            diagnosed_by="heuristic",
        )
    if "java.lang.outofmemoryerror" in lowered:
        return ErrorDiagnosis(
            tool_name=str(tool_name or ""),
            failure_class="out_of_memory",
            root_cause="Java process ran out of memory.",
            suggested_fix="Increase Java heap memory or reduce dataset size before rerunning.",
            confidence="high",
            diagnosed_by="heuristic",
        )
    return None


def _build_diagnosis_instruction(
    *,
    tool_name: str,
    failure_class: str,
    exit_code: int,
    step_arguments: dict[str, Any],
) -> str:
    """Build the LLM diagnosis instruction."""

    return (
        "You are diagnosing a bioinformatics workflow failure. "
        "Explain the most likely root cause in 1-2 sentences and provide one actionable fix. "
        "Prefer operational explanations over speculation.\n\n"
        f"Tool: {tool_name}\n"
        f"Failure class: {failure_class}\n"
        f"Exit code: {exit_code}\n"
        f"Arguments: {step_arguments}"
    )


def _parse_llm_diagnosis(response: str) -> tuple[str, str]:
    """Parse an LLM diagnosis response into cause and fix strings."""

    text = str(response or "").strip()
    if not text:
        return "LLM did not return a diagnosis.", "Inspect the stderr/stdout manually."
    lines = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
    root_cause = lines[0]
    suggested_fix = lines[1] if len(lines) > 1 else "Inspect the reported error details and retry with corrected inputs."

    for line in lines:
        lowered = line.lower()
        if lowered.startswith("root cause:"):
            root_cause = line.split(":", 1)[1].strip() or root_cause
        if lowered.startswith(("suggested fix:", "fix:", "recommendation:")):
            suggested_fix = line.split(":", 1)[1].strip() or suggested_fix
    return root_cause, suggested_fix


def _format_match_template(template: str, match: re.Match[str]) -> str:
    """Fill a regex match template using numbered capture groups."""

    rendered = template
    for index, value in enumerate(match.groups(), start=1):
        rendered = rendered.replace(f"{{{index}}}", str(value))
    return rendered


def _fallback_failure_class(failure_class: str) -> str:
    """Return a stable fallback failure class for generic diagnoses."""

    token = str(failure_class or "").strip()
    if not token or token == "tool_error":
        return "novel_unknown"
    return token


__all__ = [
    "ErrorDiagnosis",
    "diagnose_step_failure",
]
