from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.core.artifact_inspectors import inspect_variant_csv, inspect_vcf_annotation_namespace, inspect_vcf_header
from bio_harness.core.bcftools_shell_semantics import inspect_bcftools_expression_command
from bio_harness.harness.plan_semantic_guards import inspect_invalid_bcftools_view_command


def detect_stream_failure_signatures(text: str) -> list[str]:
    """Parse execution output for known failure patterns.

    Scans stderr/stdout text for signatures like missing VCF tags or
    missing tools that indicate a recoverable failure category.

    Args:
        text: Raw execution output text to scan.

    Returns:
        List of signature strings identifying the detected failure patterns.
    """
    lowered = str(text or "").lower()
    signatures: list[str] = []
    missing_tag_patterns = (
        r'the tag "(?P<tag>[a-z0-9_.-]+)" is not defined in the vcf header',
        r"no such tag defined in the vcf header:\s*(?:(?:info|format)/)?(?P<tag>[a-z0-9_.-]+)",
    )
    for pattern in missing_tag_patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        tag = str(match.groupdict().get("tag", "") or "").strip().lower()
        if "vcf_filter_tag_missing_in_header" not in signatures:
            signatures.append("vcf_filter_tag_missing_in_header")
        if tag:
            signatures.append(f"vcf_filter_tag_missing_in_header:{tag}")
        break
    ambiguous_expr_match = re.search(
        r"ambiguous filtering expression.*both\s+info/([a-z0-9_]+)\s+and\s+format/\1",
        lowered,
    )
    if ambiguous_expr_match:
        tag = str(ambiguous_expr_match.group(1) or "").strip().lower()
        signatures.append("bcftools_ambiguous_expression_namespace")
        if tag:
            signatures.append(f"bcftools_ambiguous_expression_namespace:{tag}")
    invalid_view_match = re.search(
        r"could not parse argument:\s*--(?P<option>min-alleles|max-alleles)\s+(?P<value>-[a-z])\b",
        lowered,
    )
    if invalid_view_match:
        option = str(invalid_view_match.group("option") or "").strip().lower()
        signatures.append("bcftools_invalid_view_cli")
        if option:
            signatures.append(f"bcftools_invalid_view_cli:{option}")
    missing_namespace_match = re.search(
        r"no such (?P<namespace>info|format) field:\s*(?P<tag>[a-z0-9_.-]+)",
        lowered,
    )
    if missing_namespace_match:
        namespace = str(missing_namespace_match.group("namespace") or "").strip().lower()
        tag = str(missing_namespace_match.group("tag") or "").strip().lower()
        signatures.append("bcftools_missing_expression_namespace_field")
        if namespace and tag:
            signatures.append(f"bcftools_missing_expression_namespace_field:{namespace}:{tag}")
    if re.search(r"cp:\s+['\"].*['\"]\s+and\s+['\"].*['\"]\s+are the same file", str(text or ""), flags=re.IGNORECASE):
        signatures.append("shell_copy_same_file")
    codon_table_match = re.search(
        r"(?:no such codon table|cannot find codon table)\s+'(?P<table>[^']+)'",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    if codon_table_match:
        table = str(codon_table_match.groupdict().get("table", "") or "").strip().lower()
        signatures.append("snpeff_invalid_codon_table")
        if table:
            signatures.append(f"snpeff_invalid_codon_table:{table}")
    if any(
        marker in lowered
        for marker in (
            "looks like the system ran out of memory",
            "not enough memory",
            "out of memory",
        )
    ):
        signatures.append("runtime_out_of_memory")
        if "flye" in lowered or "flye-modules" in lowered:
            signatures.append("flye_out_of_memory")
    if "estimated coverage: 0" in lowered and ("flye" in lowered or "flye-modules" in lowered):
        signatures.append("flye_zero_coverage_estimate")
    if "__validation_block__:missing_tool:" in lowered or "missing_tool:" in lowered:
        signatures.append("validation_block_missing_tool")
        for match in re.findall(r"missing_tool:([A-Za-z0-9._+-]+)", str(text or ""), flags=re.IGNORECASE):
            token = str(match or "").strip().lower()
            if token:
                signatures.append(f"validation_block_missing_tool:{token}")
    if "__format_input_error__:" in lowered:
        signatures.append("format_input_error_marker")
        if "spatial" in lowered and (
            "non-finite values" in lowered
            or "missing `obsm['spatial']` coordinates" in str(text or "")
            or "two-column matrix" in lowered
        ):
            signatures.append("spatial_coordinates_invalid")
    return signatures


def _normalize_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract and validate plan steps as a list of dicts.

    Args:
        plan: Plan dict with a 'plan' key containing a list of step dicts.

    Returns:
        List of step dicts, filtering out any non-dict entries.
    """
    raw_steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(raw_steps, list):
        return []
    return [step for step in raw_steps if isinstance(step, dict)]


def _first_failed_step_number(step_statuses: list[Any], fallback_next_idx: int = 0) -> int:
    """Find the 1-based step number of the first failed step.

    Args:
        step_statuses: List of status strings for each step.
        fallback_next_idx: Fallback step index if no explicit failure is found.

    Returns:
        1-based step number of the first failure, or 0 if none found.
    """
    for idx, status in enumerate(step_statuses or [], start=1):
        if str(status).strip().lower() == "failed":
            return idx
    if fallback_next_idx > 0:
        return int(fallback_next_idx)
    return 0


def _failed_step(plan: dict[str, Any], step_statuses: list[Any], next_step_idx: int) -> dict[str, Any]:
    """Retrieve the step dict for the first failed step in the plan.

    Args:
        plan: Plan dict containing the step list.
        step_statuses: List of status strings for each step.
        next_step_idx: Fallback index for identifying the failed step.

    Returns:
        The step dict of the first failed step, or empty dict if none found.
    """
    failed_step_number = _first_failed_step_number(step_statuses, fallback_next_idx=next_step_idx)
    if failed_step_number <= 0:
        return {}
    steps = _normalize_steps(plan)
    if failed_step_number > len(steps):
        return {}
    return steps[failed_step_number - 1]


def _extract_vcf_paths_from_command(command: str, selected_dir: Path) -> list[Path]:
    """Extract VCF file paths from a shell command string.

    Args:
        command: Shell command text to scan for VCF path patterns.
        selected_dir: Base directory for resolving relative paths.

    Returns:
        De-duplicated list of resolved Path objects for VCF files found.
    """
    hits: list[Path] = []
    seen: set[str] = set()
    for match in re.finditer(r"([A-Za-z0-9_./-]+\.vcf(?:\.gz)?)", str(command or "")):
        raw = str(match.group(1) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = selected_dir / path
        resolved = path.resolve(strict=False)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        hits.append(resolved)
    return hits


def _extract_csv_paths_from_command(command: str, selected_dir: Path) -> list[Path]:
    """Extract CSV file paths from a shell command string.

    Args:
        command: Shell command text to scan for CSV path patterns.
        selected_dir: Base directory for resolving relative paths.

    Returns:
        De-duplicated list of resolved Path objects for CSV files found.
    """
    hits: list[Path] = []
    seen: set[str] = set()
    for match in re.finditer(r"([A-Za-z0-9_./-]+\.csv)", str(command or "")):
        raw = str(match.group(1) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = selected_dir / path
        resolved = path.resolve(strict=False)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        hits.append(resolved)
    return hits


def detect_plan_artifact_failure_signatures(
    *,
    run: dict[str, Any],
    selected_dir: Path,
) -> list[str]:
    """Inspect plan outputs and artifacts for failure indicators.

    Examines VCF headers and CSV files referenced by the failed step to
    detect annotation mismatches, missing tags, and other recoverable issues.

    Args:
        run: Run state dict with plan, step_statuses, and next_step_idx.
        selected_dir: Working directory for resolving relative paths.

    Returns:
        Sorted list of failure signature strings.
    """
    plan = run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {}
    step_statuses = run.get("step_statuses", []) if isinstance(run.get("step_statuses", []), list) else []
    failed_step = _failed_step(plan, step_statuses, int(run.get("next_step_idx", 0) or 0))
    if not failed_step:
        return []

    tool_name = str(failed_step.get("tool_name", "")).strip().lower()
    args = failed_step.get("arguments", {}) if isinstance(failed_step.get("arguments", {}), dict) else {}
    command = str(args.get("command", "")).strip()
    command_lower = command.lower()
    signatures: set[str] = set()

    if tool_name == "bash_run" and "bcftools" in command_lower and 'impact=' in command_lower:
        signatures.add("shared_variant_export_shell_fragility")
    for issue in inspect_bcftools_expression_command(command, cwd=selected_dir):
        issue_name = str(issue.get("issue", "")).strip()
        tag = str(issue.get("tag", "")).strip().lower()
        if issue_name == "ambiguous_bcftools_expression_namespace":
            signatures.add("bcftools_ambiguous_expression_namespace")
            if tag:
                signatures.add(f"bcftools_ambiguous_expression_namespace:{tag}")
            continue
        if issue_name == "missing_bcftools_expression_namespace_field":
            missing_namespace = str(issue.get("missing_namespace", "")).strip().lower()
            signatures.add("bcftools_missing_expression_namespace_field")
            if missing_namespace and tag:
                signatures.add(f"bcftools_missing_expression_namespace_field:{missing_namespace}:{tag}")
    for issue in inspect_invalid_bcftools_view_command(command):
        if issue.get("issue") != "invalid_bcftools_view_cli":
            continue
        option = str(issue.get("option", "")).strip().lower().lstrip("-")
        signatures.add("bcftools_invalid_view_cli")
        if option:
            signatures.add(f"bcftools_invalid_view_cli:{option}")

    vcf_paths = _extract_vcf_paths_from_command(command, selected_dir)
    for path in vcf_paths:
        inspection = inspect_vcf_header(path)
        if not inspection.exists:
            continue
        if inspection.has_ann and "IMPACT" not in inspection.info_tags and 'impact=' in command_lower:
            signatures.add("vcf_filter_tag_missing_in_header")
            signatures.add("snpeff_ann_semantics_mismatch")
        namespace = inspect_vcf_annotation_namespace(path)
        if namespace.exists and namespace.prodigal_like_gene_fraction >= 0.5:
            signatures.add("annotation_namespace_prodigal_like")

    for path in _extract_csv_paths_from_command(command, selected_dir):
        csv_inspection = inspect_variant_csv(path)
        if not csv_inspection.exists:
            continue
        if csv_inspection.header_case == "lower":
            signatures.add("shared_variant_export_header_lowercase")
        if csv_inspection.prodigal_like_gene_fraction >= 0.5:
            signatures.add("annotation_namespace_prodigal_like")

    if signatures:
        annotated_outputs = []
        for step in _normalize_steps(plan):
            if str(step.get("tool_name", "")).strip().lower() != "snpeff_annotate":
                continue
            step_args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            output_vcf = str(step_args.get("output_vcf", "")).strip()
            if not output_vcf:
                continue
            vcf_path = Path(output_vcf).expanduser()
            if not vcf_path.is_absolute():
                vcf_path = selected_dir / vcf_path
            if vcf_path.resolve(strict=False).exists():
                annotated_outputs.append(str(vcf_path.resolve(strict=False)))
        if len(annotated_outputs) >= 2:
            signatures.add("local_tail_repair_viable")
            signatures.add("resume_from_existing_artifacts")

    return sorted(signatures)


_PLACEHOLDER_COMMAND_RE = re.compile(r"<[A-Za-z_][A-Za-z0-9_]*>")


def _normalize_issue_tokens(issues: list[Any] | None) -> set[str]:
    """Return normalized issue/signature tokens from heterogeneous inputs."""

    tokens: set[str] = set()
    for issue in issues or []:
        if isinstance(issue, dict):
            for key in ("issue", "type"):
                raw = str(issue.get(key, "") or "").strip().lower()
                if raw:
                    tokens.add(raw)
            continue
        raw = str(issue or "").strip().lower()
        if raw:
            tokens.add(raw)
    return tokens


def route_runtime_failure_signature(
    *,
    command: str,
    error_text: str = "",
    tool_name: str = "",
    issues: list[Any] | None = None,
) -> str | None:
    """Return the highest-priority runtime repair signature for one failure.

    Args:
        command: Failed command text when available.
        error_text: Aggregated stderr or validation text associated with the
            failure.
        tool_name: Failed tool name.
        issues: Optional semantic issue rows or signature-like strings.

    Returns:
        One normalized routing key, or ``None`` when no prioritized repair
        route matches the current failure.
    """

    command_text = str(command or "")
    command_lower = command_text.lower()
    error_lower = str(error_text or "").lower()
    tool_lower = str(tool_name or "").strip().lower()
    issue_tokens = _normalize_issue_tokens(issues)

    if _PLACEHOLDER_COMMAND_RE.search(command_text) or "placeholder_token_in_path" in error_lower:
        return "unresolved_placeholder_in_command"

    missing_input_match = re.search(r"missing_input:([^\s]+)", str(error_text or ""), flags=re.IGNORECASE)
    if missing_input_match and tool_lower == "bash_run":
        missing_path = str(missing_input_match.group(1) or "").strip()
        if missing_path and not Path(missing_path).expanduser().is_absolute():
            return "relative_path_in_bash_run"

    if "oops_violation" in issue_tokens:
        return "oops_violation"

    if "bcftools isec" in command_lower:
        bcftools_isec_tokens = {
            "invalid_bcftools_isec_output_mode",
            "bcftools_isec_semantic",
        }
        if bcftools_isec_tokens.intersection(issue_tokens) or "bcftools isec" in error_lower:
            return "bcftools_isec_semantic"

    return None


__all__ = [
    "detect_plan_artifact_failure_signatures",
    "detect_stream_failure_signatures",
    "route_runtime_failure_signature",
]
