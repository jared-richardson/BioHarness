"""Standardized preflight-summary helpers for run reporting.

This module turns persisted or reproducible preflight signals into one stable
summary artifact for completed runs. It is intentionally reporting-safe and
does not change executor gating behavior.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bio_harness.core.input_quality import InputIssue, InputScanResult, scan_plan_inputs
from bio_harness.core.resource_preflight import assess_resource_preflight

_GENERIC_TOOLS = {"bash_run", "python_run"}


@dataclass(frozen=True)
class PreflightSummary:
    """One deterministic preflight-summary artifact for a run.

    Attributes:
        analysis_type: Analysis family associated with the run.
        selected_dir: Selected output directory for the run.
        data_root: Input data root when known.
        tool_names: Ordered tool names extracted from the plan.
        input_scan_source: Source for the input scan (`persisted`, `rescanned`,
            or `unavailable`).
        input_scan: Input scan result when available.
        resource_report_source: Source for resource assessment
            (`estimated` or `unavailable`).
        resource_report: Resource preflight payload when available.
        recommendation: Reporting-safe recommendation such as `proceed`,
            `review_before_run`, `do_not_start`, or `unavailable`.
        rationale: Short explanation for the recommendation.
    """

    analysis_type: str
    selected_dir: str
    data_root: str
    tool_names: tuple[str, ...]
    input_scan_source: str
    input_scan: InputScanResult | None
    resource_report_source: str
    resource_report: dict[str, Any] | None
    recommendation: str
    rationale: str


def build_preflight_summary(
    plan: dict[str, Any],
    *,
    selected_dir: Path,
    analysis_type: str = "",
    data_root: Path | None = None,
    persisted_input_quality: InputScanResult | dict[str, Any] | None = None,
) -> PreflightSummary:
    """Build a standardized preflight summary for one run.

    Args:
        plan: Final structured plan for the run.
        selected_dir: Selected output directory for the run.
        analysis_type: Optional assay family for input scanning rules.
        data_root: Optional input data root for reproducible rescans.
        persisted_input_quality: Optional previously recorded input-quality
            payload from run state or result artifacts.

    Returns:
        A reporting-safe preflight summary.
    """

    normalized_selected_dir = Path(selected_dir).expanduser().resolve(strict=False)
    normalized_data_root = (
        Path(data_root).expanduser().resolve(strict=False)
        if data_root is not None and str(data_root).strip()
        else None
    )
    tool_names = _extract_tool_names(plan)
    input_scan, input_scan_source = _resolve_input_scan(
        plan,
        data_root=normalized_data_root,
        selected_dir=normalized_selected_dir,
        analysis_type=analysis_type,
        persisted_input_quality=persisted_input_quality,
    )
    resource_report, resource_report_source = _resolve_resource_report(
        tool_names,
        selected_dir=normalized_selected_dir,
    )
    recommendation, rationale = _recommend_preflight_action(
        input_scan=input_scan,
        resource_report=resource_report,
    )
    return PreflightSummary(
        analysis_type=str(analysis_type or "").strip(),
        selected_dir=str(normalized_selected_dir),
        data_root=str(normalized_data_root) if normalized_data_root is not None else "",
        tool_names=tool_names,
        input_scan_source=input_scan_source,
        input_scan=input_scan,
        resource_report_source=resource_report_source,
        resource_report=resource_report,
        recommendation=recommendation,
        rationale=rationale,
    )


def preflight_summary_to_json(summary: PreflightSummary) -> dict[str, Any]:
    """Serialize a preflight summary into JSON-friendly primitives.

    Args:
        summary: Preflight summary to serialize.

    Returns:
        JSON-friendly dictionary payload.
    """

    payload = asdict(summary)
    payload["tool_names"] = list(summary.tool_names)
    return payload


def preflight_summary_from_json(payload: dict[str, Any] | None) -> PreflightSummary | None:
    """Deserialize a persisted preflight-summary payload.

    Args:
        payload: JSON-compatible payload previously produced by
            ``preflight_summary_to_json``.

    Returns:
        A typed ``PreflightSummary`` when the payload is valid, otherwise
        ``None``.
    """

    if not isinstance(payload, dict):
        return None
    input_scan = _coerce_persisted_input_quality(payload.get("input_scan"))
    resource_report = payload.get("resource_report")
    if not isinstance(resource_report, dict):
        resource_report = None
    tool_names = payload.get("tool_names", [])
    if not isinstance(tool_names, list):
        tool_names = []
    return PreflightSummary(
        analysis_type=str(payload.get("analysis_type", "") or "").strip(),
        selected_dir=str(payload.get("selected_dir", "") or "").strip(),
        data_root=str(payload.get("data_root", "") or "").strip(),
        tool_names=tuple(str(item).strip() for item in tool_names if str(item).strip()),
        input_scan_source=str(payload.get("input_scan_source", "") or "unavailable").strip() or "unavailable",
        input_scan=input_scan,
        resource_report_source=str(payload.get("resource_report_source", "") or "unavailable").strip() or "unavailable",
        resource_report=resource_report,
        recommendation=str(payload.get("recommendation", "") or "unavailable").strip() or "unavailable",
        rationale=str(payload.get("rationale", "") or "").strip(),
    )


def preflight_summary_to_markdown(summary: PreflightSummary) -> str:
    """Render a researcher-facing Markdown summary for one preflight summary.

    Args:
        summary: Preflight summary to render.

    Returns:
        Markdown document suitable for report bundles.
    """

    lines = [
        "# Preflight Summary",
        "",
        f"- Analysis type: `{summary.analysis_type}`",
        f"- Selected dir: `{summary.selected_dir}`",
        f"- Data root: `{summary.data_root or 'unavailable'}`",
        f"- Recommendation: `{summary.recommendation}`",
        f"- Rationale: {summary.rationale}",
        "",
        "## Planned Tools",
        "",
    ]
    if summary.tool_names:
        lines.extend(f"- `{name}`" for name in summary.tool_names)
    else:
        lines.append("- No concrete tool wrappers were available for resource estimation.")

    lines.extend(["", "## Input Scan", ""])
    if summary.input_scan is None:
        lines.append(f"- Unavailable (`{summary.input_scan_source}`).")
    else:
        lines.append(f"- Source: `{summary.input_scan_source}`")
        lines.append(f"- Blocking: `{str(summary.input_scan.has_blocking).lower()}`")
        lines.append(f"- Summary: {summary.input_scan.summary}")
        if summary.input_scan.issues:
            lines.extend(["", "### Input Issues", ""])
            lines.extend(
                (
                    f"- `{issue.severity}` `{issue.category}` on `{issue.path}`: "
                    f"{issue.message} Suggested fix: {issue.suggestion}"
                )
                for issue in summary.input_scan.issues
            )
        else:
            lines.append("- No input-quality issues were recorded.")

    lines.extend(["", "## Resource Preflight", ""])
    if summary.resource_report is None:
        lines.append(f"- Unavailable (`{summary.resource_report_source}`).")
    else:
        lines.append(f"- Source: `{summary.resource_report_source}`")
        lines.append(f"- OK: `{str(bool(summary.resource_report.get('ok', False))).lower()}`")
        requirements = summary.resource_report.get("requirements", {})
        system = summary.resource_report.get("system", {})
        if isinstance(requirements, dict) and isinstance(system, dict):
            lines.extend(
                [
                    f"- Required RAM (GiB): `{requirements.get('min_ram_gb', 0)}`",
                    f"- Available RAM (GiB): `{system.get('available_mem_gb', 0)}`",
                    f"- Required cores: `{requirements.get('min_cores', 0)}`",
                    f"- Available cores: `{system.get('available_cores', 0)}`",
                    f"- Required free disk (GiB): `{requirements.get('estimated_free_disk_gb', 0)}`",
                    f"- Available free disk (GiB): `{system.get('free_disk_gb', 0)}`",
                ]
            )
        warnings = summary.resource_report.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.extend(["", "### Resource Warnings", ""])
            lines.extend(f"- {str(item)}" for item in warnings if str(item).strip())
        else:
            lines.append("- No resource warnings were recorded.")

    return "\n".join(lines)


def _extract_tool_names(plan: dict[str, Any]) -> tuple[str, ...]:
    """Return ordered unique wrapper names from one structured plan."""

    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    seen: set[str] = set()
    tool_names: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip()
        if not tool_name or tool_name in _GENERIC_TOOLS or tool_name in seen:
            continue
        seen.add(tool_name)
        tool_names.append(tool_name)
    return tuple(tool_names)


def _resolve_input_scan(
    plan: dict[str, Any],
    *,
    data_root: Path | None,
    selected_dir: Path,
    analysis_type: str,
    persisted_input_quality: InputScanResult | dict[str, Any] | None,
) -> tuple[InputScanResult | None, str]:
    """Resolve the most trustworthy available input scan."""

    persisted = _coerce_persisted_input_quality(persisted_input_quality)
    if persisted is not None:
        return persisted, "persisted"
    if data_root is None:
        return None, "unavailable"
    return (
        scan_plan_inputs(
            plan,
            data_root,
            selected_dir=selected_dir,
            analysis_type=analysis_type,
        ),
        "rescanned",
    )


def _coerce_persisted_input_quality(
    payload: InputScanResult | dict[str, Any] | None,
) -> InputScanResult | None:
    """Normalize one stored input-quality payload into a typed scan result."""

    if isinstance(payload, InputScanResult):
        return payload
    if not isinstance(payload, dict):
        return None
    raw_issues = payload.get("issues", [])
    issues: list[InputIssue] = []
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if not isinstance(item, dict):
                continue
            issues.append(
                InputIssue(
                    path=str(item.get("path", "") or ""),
                    severity=str(item.get("severity", "") or "warning"),
                    category=str(item.get("category", "") or "unknown_issue"),
                    message=str(item.get("message", "") or ""),
                    suggestion=str(item.get("suggestion", "") or ""),
                )
            )
    summary = str(payload.get("summary", "") or "").strip()
    if not issues and not summary:
        return None
    has_blocking = bool(
        payload.get("has_blocking", False)
        or any(issue.severity == "error" for issue in issues)
    )
    return InputScanResult(
        issues=tuple(issues),
        has_blocking=has_blocking,
        summary=summary or _input_scan_summary(tuple(issues), has_blocking=has_blocking),
    )


def _resolve_resource_report(
    tool_names: tuple[str, ...],
    *,
    selected_dir: Path,
) -> tuple[dict[str, Any] | None, str]:
    """Estimate resource preflight when wrapper metadata is available."""

    if not tool_names:
        return None, "unavailable"
    target_dir = selected_dir if selected_dir.exists() else selected_dir.parent
    report = assess_resource_preflight(list(tool_names), selected_dir=target_dir)
    if not report.get("skills_found") and report.get("missing_skills"):
        return None, "unavailable"
    return report, "estimated"


def _recommend_preflight_action(
    *,
    input_scan: InputScanResult | None,
    resource_report: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return a reporting-safe recommendation from available preflight signals."""

    if input_scan is not None and input_scan.has_blocking:
        return "do_not_start", _build_rationale(input_scan=input_scan, resource_report=resource_report)
    if _resource_has_warnings(resource_report) or _input_has_warnings(input_scan):
        return "review_before_run", _build_rationale(input_scan=input_scan, resource_report=resource_report)
    if input_scan is not None or resource_report is not None:
        return "proceed", _build_rationale(input_scan=input_scan, resource_report=resource_report)
    return "unavailable", "No reproducible preflight signals were available for this run."


def _input_has_warnings(input_scan: InputScanResult | None) -> bool:
    """Return whether an input scan has any non-blocking findings."""

    return input_scan is not None and bool(input_scan.issues)


def _resource_has_warnings(resource_report: dict[str, Any] | None) -> bool:
    """Return whether a resource report contains warnings."""

    if not isinstance(resource_report, dict):
        return False
    warnings = resource_report.get("warnings", [])
    return isinstance(warnings, list) and bool(warnings)


def _build_rationale(
    *,
    input_scan: InputScanResult | None,
    resource_report: dict[str, Any] | None,
) -> str:
    """Build a short rationale for one preflight recommendation."""

    parts: list[str] = []
    if input_scan is not None:
        error_count = sum(1 for issue in input_scan.issues if issue.severity == "error")
        warning_count = len(input_scan.issues) - error_count
        if input_scan.has_blocking:
            parts.append(f"Input scan found {error_count} blocking issue(s)")
        elif input_scan.issues:
            parts.append(f"input scan found {warning_count} warning issue(s)")
        else:
            parts.append("input scan found no issues")
    if isinstance(resource_report, dict):
        warnings = resource_report.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            parts.append(f"resource preflight reported {len(warnings)} warning(s)")
        else:
            parts.append("resource preflight found no warnings")
    if not parts:
        return "No reproducible preflight signals were available for this run."
    return "; ".join(parts) + "."


def _input_scan_summary(issues: tuple[InputIssue, ...], *, has_blocking: bool) -> str:
    """Build a fallback summary for coerced persisted input-quality payloads."""

    if not issues:
        return "No input quality issues detected."
    return f"Detected {len(issues)} input issue(s); blocking={str(has_blocking).lower()}."
