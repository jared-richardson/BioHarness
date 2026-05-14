"""Explicit literature-research routing for the run-agent entrypoint."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from bio_harness.core.literature_agent import LiteratureAgent, ResearchQuery
from bio_harness.core.runtime_repair_support import build_runtime_result_payload
from scripts.run_agent_e2e_support import _now_utc_iso


def is_explicit_research_prompt(prompt: str) -> bool:
    """Return whether a user prompt explicitly requests literature research."""

    return str(prompt or "").lstrip().lower().startswith("research:")


def handle_explicit_research_prompt(
    harness: Any,
    *,
    benchmark_policy: str,
) -> dict[str, Any]:
    """Execute one explicit ``Research:`` request without entering planning.

    Args:
        harness: Initialized harness instance with orchestrator and run-state
            helpers available.
        benchmark_policy: Active benchmark policy string for the result payload.

    Returns:
        Completed result payload with persisted research artifacts.
    """

    if not bool(getattr(harness, "run", {})):
        harness._init_run()

    prompt = str(harness.run.get("user_request", "") or "")
    research_question = prompt.split(":", 1)[1].strip() if ":" in prompt else prompt.strip()
    if not research_question:
        harness.run["status"] = "failed"
        harness.run["error"] = "Research prompt is missing a question after 'Research:'."
        harness.run["finished_at"] = _now_utc_iso()
        harness._persist_state()
        harness._write_exit()
        return build_runtime_result_payload(
            run=harness.run,
            data_root=harness.cfg.data_root,
            selected_dir=harness.cfg.selected_dir,
            path_graph_db_path=harness.path_graph.db_path,
            path_graph_user_key=str(harness.cfg.path_graph_user_key),
            path_graph_scope=str(harness.cfg.path_graph_scope),
            benchmark_policy=benchmark_policy,
            assistance_manifest=harness._assistance_manifest_payload(),
        )

    _mark_research_started(harness, research_question)
    agent = LiteratureAgent(
        librarian=harness.orchestrator._get_librarian(),
        biollm=harness.orchestrator.biollm,
    )
    try:
        report = agent.research(
            ResearchQuery(
                question=research_question,
                analysis_type="literature_research",
                max_results=8,
            )
        )
    except Exception as exc:
        harness.run["status"] = "failed"
        harness.run["error"] = f"Explicit research failed: {exc}"
        harness.run["finished_at"] = _now_utc_iso()
        harness._append_event(
            step_id=None,
            agent="ResearchAgent",
            event_type="RESEARCH_FAILED",
            severity="error",
            payload={"question": research_question, "error": str(exc)},
        )
        harness._persist_state()
        harness._write_exit()
        return build_runtime_result_payload(
            run=harness.run,
            data_root=harness.cfg.data_root,
            selected_dir=harness.cfg.selected_dir,
            path_graph_db_path=harness.path_graph.db_path,
            path_graph_user_key=str(harness.cfg.path_graph_user_key),
            path_graph_scope=str(harness.cfg.path_graph_scope),
            benchmark_policy=benchmark_policy,
            assistance_manifest=harness._assistance_manifest_payload(),
        )

    final_dir = harness.cfg.selected_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    report_payload = asdict(report)
    json_path = final_dir / "research_report.json"
    md_path = final_dir / "research_report.md"
    json_path.write_text(json.dumps(report_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_research_report_markdown(report).strip() + "\n", encoding="utf-8")

    harness.run["status"] = "completed"
    harness.run["error"] = ""
    harness.run["research_report"] = {
        "question": report.query.question,
        "analysis_type": report.query.analysis_type,
        "sources_consulted": int(report.sources_consulted),
        "confidence": float(report.confidence),
        "evidence_sufficiency": str(report.evidence_sufficiency or "insufficient"),
        "evidence_failure_reasons": list(report.evidence_failure_reasons),
        "primary_literature_count": int(report.primary_literature_count),
        "trusted_web_count": int(report.trusted_web_count),
        "unique_source_count": int(report.unique_source_count),
        "backend_diversity_count": int(report.backend_diversity_count),
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "recommendations": list(report.recommendations),
        "backend_statuses": [asdict(item) for item in report.backend_statuses],
        "backend_health_summary": [asdict(item) for item in report.backend_health_summary],
    }
    harness.run["finished_at"] = _now_utc_iso()
    harness._append_event(
        step_id=None,
        agent="ResearchAgent",
        event_type="RESEARCH_COMPLETED",
        severity="info",
        payload={
            "question": report.query.question,
            "sources_consulted": int(report.sources_consulted),
            "confidence": float(report.confidence),
            "json_path": str(json_path),
            "markdown_path": str(md_path),
        },
    )
    harness._persist_state()
    harness._write_exit()
    return build_runtime_result_payload(
        run=harness.run,
        data_root=harness.cfg.data_root,
        selected_dir=harness.cfg.selected_dir,
        path_graph_db_path=harness.path_graph.db_path,
        path_graph_user_key=str(harness.cfg.path_graph_user_key),
        path_graph_scope=str(harness.cfg.path_graph_scope),
        benchmark_policy=benchmark_policy,
        assistance_manifest=harness._assistance_manifest_payload(),
    )


def _mark_research_started(harness: Any, research_question: str) -> None:
    """Persist explicit-research state before backend work begins.

    Args:
        harness: Active harness instance.
        research_question: Normalized explicit research question.
    """

    harness.run["status"] = "running"
    harness.run["error"] = ""
    harness.run["research_report"] = {
        "question": research_question,
        "analysis_type": "literature_research",
        "status": "running",
        "sources_consulted": 0,
        "confidence": 0.0,
        "backend_statuses": [],
    }
    harness._append_event(
        step_id=None,
        agent="ResearchAgent",
        event_type="RESEARCH_STARTED",
        severity="info",
        payload={
            "question": research_question,
            "analysis_type": "literature_research",
        },
    )
    harness._persist_state()


def _research_report_markdown(report) -> str:
    """Render one research report as Markdown."""

    lines = [
        "# Literature Research Report",
        "",
        f"- Question: {report.query.question}",
        f"- Analysis type: `{report.query.analysis_type}`",
        f"- Sources consulted: `{report.sources_consulted}`",
        f"- Confidence: `{report.confidence:.2f}`",
        f"- Evidence sufficiency: `{report.evidence_sufficiency}`",
        f"- Primary literature count: `{report.primary_literature_count}`",
        f"- Trusted web count: `{report.trusted_web_count}`",
        f"- Unique source count: `{report.unique_source_count}`",
        f"- Backend diversity count: `{report.backend_diversity_count}`",
        "",
        "## Evidence Summary",
        "",
    ]
    if report.evidence_failure_reasons:
        lines.extend(f"- Failure reason: `{item}`" for item in report.evidence_failure_reasons)
    else:
        lines.append("- Failure reasons: none")
    lines.extend([
        "",
        "## Synthesis",
        "",
        report.synthesis or "No synthesis available.",
        "",
        "## Recommendations",
        "",
    ])
    if report.recommendations:
        lines.extend(f"- {item}" for item in report.recommendations)
    else:
        lines.append("- None")
    lines.extend(["", "## Backend Health", ""])
    if getattr(report, "backend_health_summary", ()):
        for row in report.backend_health_summary:
            lines.append(f"- `{row.backend}` tier=`{row.tier}` reason=`{row.reason or 'none'}`")
    else:
        lines.append("- None")
    lines.extend(["", "## Backend Status", ""])
    if getattr(report, "backend_statuses", ()):
        for status in report.backend_statuses:
            lines.append(
                "- "
                f"`{status.backend}` status=`{status.status}` "
                f"attempted=`{status.queries_attempted}` "
                f"hits=`{status.hit_count}` "
                f"timeouts=`{status.timeout_count}` "
                f"errors=`{status.error_count}`"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Key Sources", ""])
    if report.hits:
        for hit in report.hits:
            lines.append(f"- [{hit.title}]({hit.url})")
    else:
        lines.append("- None")
    return "\n".join(lines)


__all__ = [
    "handle_explicit_research_prompt",
    "is_explicit_research_prompt",
]
