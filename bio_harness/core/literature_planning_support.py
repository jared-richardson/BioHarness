"""Audited planner-time literature assistance helpers.

This module executes a narrow advisory-only literature assistance pass for the
planner. It is intentionally conservative: the policy is deterministic,
benchmark-blind safe, and the assistance is persisted as its own artifact so
later reporting can explain when literature changed planner-visible context.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from bio_harness.core.literature_agent import LiteratureAgent, ResearchQuery, ResearchReport
from bio_harness.core.literature_planning_policy import (
    LiteraturePlanningDecision,
    decide_literature_planning_support,
    tool_candidates_from_analysis_spec,
)


def generate_literature_planning_support(
    *,
    user_query: str,
    analysis_spec: dict[str, Any] | None,
    benchmark_policy: str,
    run_dir: Path,
    librarian: Any | None,
    artifact_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Generate planner-time literature assistance and persist artifacts.

    Args:
        user_query: Raw planner request.
        analysis_spec: Current analysis-spec payload.
        benchmark_policy: Active benchmark policy string.
        run_dir: Active run directory.
        librarian: Librarian backend helper.
        artifact_paths: Optional explicit artifact paths.

    Returns:
        Stable summary payload for run-state and analysis-spec storage.
    """

    decision = decide_literature_planning_support(
        user_query,
        analysis_spec,
        benchmark_policy=benchmark_policy,
    )
    paths = _resolve_artifact_paths(run_dir, artifact_paths)
    if not decision.allowed:
        return _summary_payload(
            status="skipped",
            decision=decision,
            report=None,
            visible_to_planner=False,
            json_path=paths["json"],
            markdown_path=paths["md"],
        )
    if librarian is None:
        return _summary_payload(
            status="skipped",
            decision=LiteraturePlanningDecision(
                allowed=False,
                query_class=decision.query_class,
                trigger_reason="librarian_unavailable",
                advisory_only=decision.advisory_only,
                tool_name=decision.tool_name,
                parameter_name=decision.parameter_name,
            ),
            report=None,
            visible_to_planner=False,
            json_path=paths["json"],
            markdown_path=paths["md"],
        )

    agent = LiteratureAgent(librarian=librarian, biollm=None)
    try:
        report = _dispatch_research(
            decision=decision,
            user_query=user_query,
            analysis_spec=analysis_spec,
            agent=agent,
        )
    except Exception as exc:
        return _summary_payload(
            status="failed",
            decision=decision,
            report=None,
            visible_to_planner=False,
            json_path=paths["json"],
            markdown_path=paths["md"],
            error=str(exc),
        )

    payload = _summary_payload(
        status="applied" if _visible_to_planner(report) else "insufficient",
        decision=decision,
        report=report,
        visible_to_planner=_visible_to_planner(report),
        json_path=paths["json"],
        markdown_path=paths["md"],
    )
    _write_support_artifacts(paths["json"], paths["md"], payload, report)
    return payload


def literature_planning_support_brief_lines(summary: dict[str, Any] | None) -> list[str]:
    """Return planner-visible brief lines for analysis-brief injection.

    Args:
        summary: Planner-time literature support summary payload.

    Returns:
        Short stable lines for the planner prompt.
    """

    payload = summary if isinstance(summary, dict) else {}
    if not bool(payload.get("visible_to_planner", False)):
        return []
    lines = [
        f"literature_assistance_query_class={payload.get('query_class', '')}",
        f"literature_assistance_reason={payload.get('trigger_reason', '')}",
        (
            "literature_evidence="
            f"sources={int(payload.get('sources_consulted', 0) or 0)}, "
            f"primary={int(payload.get('primary_literature_count', 0) or 0)}, "
            f"trusted_web={int(payload.get('trusted_web_count', 0) or 0)}, "
            f"backend_diversity={int(payload.get('backend_diversity_count', 0) or 0)}"
        ),
    ]
    recommendations = [
        str(item).strip()
        for item in (payload.get("recommendations", []) or [])[:2]
        if str(item).strip()
    ]
    if recommendations:
        lines.append("literature_recommendations=" + " | ".join(recommendations))
    parameter_suggestions = [
        tuple(item)
        for item in (payload.get("parameter_suggestions", []) or [])[:2]
        if isinstance(item, (list, tuple)) and len(item) == 3
    ]
    if parameter_suggestions:
        rendered = [f"{tool}.{param}={value}" for tool, param, value in parameter_suggestions]
        lines.append("literature_parameter_suggestions=" + " | ".join(rendered))
    return lines


def _dispatch_research(
    *,
    decision: LiteraturePlanningDecision,
    user_query: str,
    analysis_spec: dict[str, Any] | None,
    agent: LiteratureAgent,
) -> ResearchReport:
    spec = analysis_spec if isinstance(analysis_spec, dict) else {}
    analysis_type = str(spec.get("analysis_type", "") or "").strip() or "literature_research"
    tool_candidates = tool_candidates_from_analysis_spec(spec)
    if decision.query_class == "parameter_recommendation" and decision.tool_name and decision.parameter_name:
        return agent.research_parameter_recommendation(
            decision.tool_name,
            decision.parameter_name,
            context=user_query,
        )
    if decision.query_class == "protocol_choice":
        return agent.research_protocol_choice(
            analysis_type,
            list(tool_candidates[:4]),
            context=user_query,
        )
    return agent.research(
        ResearchQuery(
            question=user_query,
            analysis_type=analysis_type,
            tools_in_use=tool_candidates[:4],
            max_results=8,
        )
    )


def _visible_to_planner(report: ResearchReport) -> bool:
    return bool(report.sources_consulted > 0 and report.evidence_sufficiency in {"sufficient", "partial"})


def _resolve_artifact_paths(run_dir: Path, artifact_paths: dict[str, Path] | None) -> dict[str, Path]:
    payload = artifact_paths if isinstance(artifact_paths, dict) else {}
    json_path = payload.get("json")
    md_path = payload.get("md")
    resolved_json = Path(json_path) if isinstance(json_path, Path) else run_dir / "literature_planning_support.json"
    resolved_md = Path(md_path) if isinstance(md_path, Path) else run_dir / "literature_planning_support.md"
    return {"json": resolved_json, "md": resolved_md}


def _summary_payload(
    *,
    status: str,
    decision: LiteraturePlanningDecision,
    report: ResearchReport | None,
    visible_to_planner: bool,
    json_path: Path,
    markdown_path: Path,
    error: str = "",
) -> dict[str, Any]:
    recommendations = list(report.recommendations) if report is not None else []
    parameter_suggestions = list(report.parameter_suggestions) if report is not None else []
    return {
        "status": str(status),
        "visible_to_planner": bool(visible_to_planner),
        "advisory_only": bool(decision.advisory_only),
        "query_class": str(decision.query_class or ""),
        "trigger_reason": str(decision.trigger_reason or ""),
        "tool_name": str(decision.tool_name or ""),
        "parameter_name": str(decision.parameter_name or ""),
        "question": str(report.query.question if report is not None else ""),
        "sources_consulted": int(report.sources_consulted if report is not None else 0),
        "evidence_sufficiency": str(report.evidence_sufficiency if report is not None else "unavailable"),
        "evidence_failure_reasons": list(report.evidence_failure_reasons if report is not None else ()),
        "primary_literature_count": int(report.primary_literature_count if report is not None else 0),
        "trusted_web_count": int(report.trusted_web_count if report is not None else 0),
        "backend_diversity_count": int(report.backend_diversity_count if report is not None else 0),
        "recommendations": recommendations,
        "parameter_suggestions": parameter_suggestions,
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "error": str(error or ""),
    }


def _write_support_artifacts(
    json_path: Path,
    markdown_path: Path,
    summary: dict[str, Any],
    report: ResearchReport,
) -> None:
    json_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "research_report": asdict(report),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Planner Literature Assistance",
        "",
        f"- Status: `{summary['status']}`",
        f"- Query class: `{summary['query_class']}`",
        f"- Trigger reason: `{summary['trigger_reason']}`",
        f"- Advisory only: `{summary['advisory_only']}`",
        f"- Visible to planner: `{summary['visible_to_planner']}`",
        f"- Sources consulted: `{summary['sources_consulted']}`",
        f"- Evidence sufficiency: `{summary['evidence_sufficiency']}`",
        "",
        "## Recommendations",
        "",
    ]
    if summary["recommendations"]:
        lines.extend(f"- {item}" for item in summary["recommendations"])
    else:
        lines.append("- None")
    if summary["parameter_suggestions"]:
        lines.extend(["", "## Parameter Suggestions", ""])
        lines.extend(
            f"- `{tool}.{parameter} = {value}`"
            for tool, parameter, value in summary["parameter_suggestions"]
        )
    lines.extend(["", "## Key Sources", ""])
    if report.hits:
        lines.extend(f"- [{hit.title}]({hit.url})" for hit in report.hits[:5])
    else:
        lines.append("- None")
    markdown_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


__all__ = [
    "generate_literature_planning_support",
    "literature_planning_support_brief_lines",
]
