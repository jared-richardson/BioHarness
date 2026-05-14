"""Chat-first UI shell helpers for the Streamlit application.

These helpers keep presentation-oriented logic out of ``app.py`` while staying
deterministic and backend-driven. They summarize run state, recent events, and
artifact candidates from persisted run metadata rather than browser-only state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


_STATUS_STYLES: dict[str, dict[str, str]] = {
    "draft": {"label": "Draft", "icon": "D", "tone": "muted"},
    "planning": {"label": "Planning", "icon": "G", "tone": "info"},
    "planned": {"label": "Planned", "icon": "P", "tone": "info"},
    "planning_failed": {"label": "Plan Failed", "icon": "F", "tone": "danger"},
    "planning_timed_out": {"label": "Plan Timed Out", "icon": "T", "tone": "danger"},
    "running": {"label": "Running", "icon": "R", "tone": "live"},
    "completed": {"label": "Completed", "icon": "C", "tone": "success"},
    "failed": {"label": "Failed", "icon": "F", "tone": "danger"},
    "blocked_missing_tools": {"label": "Blocked", "icon": "B", "tone": "warning"},
    "blocked_input": {"label": "Needs Input", "icon": "I", "tone": "warning"},
    "repairing": {"label": "Repairing", "icon": "U", "tone": "warning"},
}
_ARTIFACT_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".treefile",
    ".nwk",
    ".newick",
    ".pdf",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".txt",
    ".md",
    ".html",
    ".vcf",
    ".vcf.gz",
    ".bed",
    ".bedgraph",
    ".bw",
    ".bam",
    ".cram",
}
_BOOKKEEPING_FILENAMES = {
    "state.json",
    "exit.json",
    "events.jsonl",
    "manifest.json",
    "assistance_manifest.json",
    "path_decisions.json",
    "scripts_manifest.json",
    "plan.json",
    "summary.md",
    "stdout.log",
    "stderr.log",
    "execution.log",
}


def compact_model_name_for_rail(model_name: str, *, max_chars: int = 18) -> str:
    """Return a shorter model label that fits the narrow left rail."""
    text = str(model_name).strip()
    if not text:
        return "Unknown model"
    if text.endswith(":latest"):
        text = text[: -len(":latest")]
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1].rstrip()}..."


def chat_first_css() -> str:
    """Return custom CSS for the chat-first shell."""
    return """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=Source+Sans+3:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

    :root {
        --bh-bg: #edf3fb;
        --bh-surface: rgba(255, 255, 255, 0.96);
        --bh-surface-soft: #f5f8fd;
        --bh-border: #d6e1ef;
        --bh-text: #162033;
        --bh-subtle: #60728b;
        --bh-accent: #0e7490;
        --bh-accent-soft: rgba(14, 116, 144, 0.10);
        --bh-success: #1d7a58;
        --bh-success-soft: rgba(29, 122, 88, 0.12);
        --bh-warning: #b26a00;
        --bh-warning-soft: rgba(178, 106, 0, 0.12);
        --bh-danger: #b33b4f;
        --bh-danger-soft: rgba(179, 59, 79, 0.12);
        --bh-shadow: 0 16px 38px rgba(44, 72, 112, 0.10);
    }

    .stApp {
        background:
            radial-gradient(circle at top left, rgba(14, 116, 144, 0.10), transparent 28%),
            radial-gradient(circle at top right, rgba(59, 130, 246, 0.08), transparent 24%),
            linear-gradient(180deg, #f9fbff 0%, var(--bh-bg) 100%);
        color: var(--bh-text);
        font-family: "Source Sans 3", "Avenir Next", "Segoe UI", sans-serif;
    }

    [data-testid="stHeader"],
    [data-testid="stToolbar"],
    .stAppToolbar,
    button[kind="header"] {
        display: none !important;
    }

    .block-container {
        padding-top: 1.25rem !important;
        padding-bottom: 1.25rem !important;
        max-width: 1500px;
    }

    h1, h2, h3, h4 {
        font-family: "Sora", "Avenir Next", sans-serif !important;
        color: var(--bh-text);
        letter-spacing: -0.02em;
    }

    code, pre, .stCodeBlock {
        font-family: "IBM Plex Mono", "SFMono-Regular", monospace !important;
    }

    [data-testid="stSidebar"] {
        background: rgba(255, 255, 255, 0.96);
        border-right: 1px solid var(--bh-border);
    }

    .bh-hero {
        background:
            linear-gradient(135deg, rgba(14, 116, 144, 0.13), rgba(255, 255, 255, 0.82)),
            var(--bh-surface);
        border: 1px solid var(--bh-border);
        border-radius: 24px;
        box-shadow: var(--bh-shadow);
        padding: 1.05rem 1.2rem 0.95rem 1.2rem;
        margin-bottom: 0.85rem;
    }

    .bh-kicker {
        font-size: 0.74rem;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        color: var(--bh-accent);
        font-weight: 700;
        margin-bottom: 0.28rem;
    }

    .bh-hero-title {
        font-size: 2.2rem;
        line-height: 1;
        margin: 0;
    }

    .bh-hero-subtitle {
        color: var(--bh-subtle);
        font-size: 1rem;
        margin-top: 0.5rem;
        max-width: 46rem;
    }

    .bh-chat-header {
        display: grid;
        grid-template-columns: minmax(0, 1.55fr) minmax(280px, 0.9fr);
        gap: 1rem;
        align-items: start;
        margin-bottom: 0.8rem;
    }

    .bh-chat-intro {
        background: rgba(255, 255, 255, 0.98);
        border: 1px solid var(--bh-border);
        border-radius: 22px;
        box-shadow: var(--bh-shadow);
        padding: 1rem 1.05rem 0.9rem 1.05rem;
    }

    .bh-chat-panel-controls {
        background: rgba(255, 255, 255, 0.95);
        border: 1px solid var(--bh-border);
        border-radius: 20px;
        box-shadow: var(--bh-shadow);
        padding: 0.78rem 0.9rem 0.72rem 0.9rem;
    }

    .bh-status-strip {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.8rem;
        margin-bottom: 1rem;
    }

    .bh-card,
    .bh-rail-card,
    .bh-strip-card {
        background: rgba(255, 255, 255, 0.97);
        border: 1px solid var(--bh-border);
        box-shadow: var(--bh-shadow);
        border-radius: 20px;
    }

    .bh-strip-card {
        padding: 0.95rem 1rem;
    }

    .bh-strip-label {
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--bh-subtle);
        margin-bottom: 0.3rem;
    }

    .bh-strip-value {
        font-size: 1.1rem;
        font-weight: 600;
    }

    .bh-shell-section {
        background: rgba(255, 255, 255, 0.97);
        border: 1px solid var(--bh-border);
        border-radius: 22px;
        box-shadow: var(--bh-shadow);
        padding: 0.95rem 1rem 0.85rem 1rem;
        margin-bottom: 0.9rem;
    }

    .bh-shell-heading {
        font-family: "Sora", "Avenir Next", sans-serif;
        font-size: 1.35rem;
        margin: 0 0 0.22rem 0;
    }

    .bh-shell-caption {
        color: var(--bh-subtle);
        font-size: 0.95rem;
        margin-bottom: 0.65rem;
    }

    .bh-rail-card {
        padding: 0.72rem 0.78rem;
        margin-bottom: 0.68rem;
    }

    .bh-rail-label {
        color: var(--bh-subtle);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.74rem;
        margin-bottom: 0.25rem;
    }

    .bh-rail-title {
        font-size: 0.96rem;
        font-weight: 600;
        margin-bottom: 0.15rem;
        line-height: 1.3;
    }

    .bh-rail-meta {
        color: var(--bh-subtle);
        font-size: 0.88rem;
        line-height: 1.4;
    }

    .bh-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.28rem 0.55rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.01em;
        border: 1px solid transparent;
    }

    .bh-badge-muted { background: rgba(91, 86, 76, 0.10); color: var(--bh-subtle); border-color: rgba(91, 86, 76, 0.16); }
    .bh-badge-info  { background: var(--bh-accent-soft); color: var(--bh-accent); border-color: rgba(15, 107, 111, 0.18); }
    .bh-badge-live  { background: linear-gradient(135deg, rgba(15,107,111,0.18), rgba(45,106,79,0.12)); color: var(--bh-accent); border-color: rgba(15,107,111,0.22); }
    .bh-badge-success { background: var(--bh-success-soft); color: var(--bh-success); border-color: rgba(45,106,79,0.18); }
    .bh-badge-warning { background: var(--bh-warning-soft); color: var(--bh-warning); border-color: rgba(166,106,0,0.18); }
    .bh-badge-danger { background: var(--bh-danger-soft); color: var(--bh-danger); border-color: rgba(163,58,43,0.18); }

    .bh-dock-tabs .stRadio > div {
        flex-direction: row;
        flex-wrap: wrap;
        gap: 0.4rem;
        padding-bottom: 0.1rem;
    }

    .bh-dock-tabs [data-baseweb="radio"] > div {
        background: rgba(245, 248, 253, 0.95);
        border: 1px solid var(--bh-border);
        border-radius: 999px;
        padding: 0.28rem 0.72rem;
    }

    .bh-chat-frame {
        background: rgba(255, 255, 255, 0.98);
        border: 1px solid var(--bh-border);
        border-radius: 26px;
        box-shadow: var(--bh-shadow);
        padding: 0.85rem 0.95rem 0.75rem 0.95rem;
        margin-bottom: 0.7rem;
    }

    .bh-chat-toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        margin-bottom: 0.7rem;
    }

    .bh-chat-title {
        font-family: "Sora", "Avenir Next", sans-serif;
        font-size: 1.42rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        margin: 0;
    }

    .bh-chat-subtitle {
        color: var(--bh-subtle);
        font-size: 0.96rem;
        margin-top: 0.18rem;
    }

    .bh-chat-controls {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 0.5rem;
    }

    .bh-mini-stat-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 0.45rem;
        margin-bottom: 0.7rem;
    }

    .bh-mini-stat {
        background: var(--bh-surface);
        border: 1px solid var(--bh-border);
        border-radius: 15px;
        padding: 0.56rem 0.68rem;
        display: block;
    }

    .bh-mini-stat-label {
        color: var(--bh-subtle);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.14rem;
    }

    .bh-mini-stat-value {
        font-size: 0.94rem;
        font-weight: 600;
        color: var(--bh-text);
        line-height: 1.3;
        text-align: left;
    }

    .bh-pref-card {
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid var(--bh-border);
        border-radius: 18px;
        box-shadow: var(--bh-shadow);
        padding: 0.82rem 0.9rem 0.6rem 0.9rem;
        margin-bottom: 0.8rem;
    }

    .bh-session-line {
        color: var(--bh-subtle);
        font-size: 0.94rem;
        margin-bottom: 0.55rem;
    }

    .bh-chat-status-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.65rem;
        margin-bottom: 0.8rem;
    }

    .bh-chat-status-card {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(245, 248, 253, 0.96));
        border: 1px solid var(--bh-border);
        border-radius: 18px;
        box-shadow: var(--bh-shadow);
        padding: 0.75rem 0.8rem 0.7rem 0.8rem;
        min-height: 104px;
    }

    .bh-chat-status-label {
        color: var(--bh-subtle);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.32rem;
    }

    .bh-chat-status-value {
        color: var(--bh-text);
        font-size: 1rem;
        font-weight: 700;
        line-height: 1.18;
        margin-bottom: 0.28rem;
    }

    .bh-chat-status-detail {
        color: var(--bh-subtle);
        font-size: 0.88rem;
        line-height: 1.38;
    }

    .bh-inline-summary {
        background: rgba(14, 116, 144, 0.06);
        border: 1px solid rgba(14, 116, 144, 0.16);
        border-radius: 18px;
        padding: 0.85rem 0.95rem;
        margin-bottom: 0.8rem;
    }

    .bh-inline-summary-title {
        font-family: "Sora", "Avenir Next", sans-serif;
        font-size: 1rem;
        font-weight: 700;
        color: var(--bh-text);
        margin-bottom: 0.22rem;
    }

    .bh-inline-summary-copy {
        color: var(--bh-subtle);
        font-size: 0.92rem;
        line-height: 1.5;
    }

    .bh-inline-run-card {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.99), rgba(245, 248, 253, 0.96));
        border: 1px solid var(--bh-border);
        border-radius: 20px;
        box-shadow: var(--bh-shadow);
        padding: 0.9rem 0.98rem 0.82rem 0.98rem;
        margin-bottom: 0.85rem;
    }

    .bh-inline-run-title {
        font-family: "Sora", "Avenir Next", sans-serif;
        font-size: 0.98rem;
        font-weight: 700;
        color: var(--bh-text);
        margin-bottom: 0.18rem;
    }

    .bh-inline-run-copy {
        color: var(--bh-subtle);
        font-size: 0.92rem;
        line-height: 1.5;
        margin-bottom: 0.55rem;
    }

    .bh-inline-chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem;
        margin-top: 0.25rem;
    }

    .bh-inline-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.34rem 0.58rem;
        border-radius: 999px;
        background: rgba(14, 116, 144, 0.08);
        border: 1px solid rgba(14, 116, 144, 0.14);
        color: var(--bh-text);
        font-size: 0.76rem;
        line-height: 1.2;
    }

    .bh-empty-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.7rem;
        margin: 0.75rem 0 0.85rem 0;
    }

    .bh-empty-card {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(245, 248, 253, 0.92));
        border: 1px solid var(--bh-border);
        border-radius: 18px;
        box-shadow: var(--bh-shadow);
        padding: 0.82rem 0.9rem;
        min-height: 155px;
    }

    .bh-empty-title {
        color: var(--bh-text);
        font-size: 1rem;
        font-weight: 700;
        margin-bottom: 0.24rem;
    }

    .bh-empty-copy {
        color: var(--bh-subtle);
        font-size: 0.92rem;
        line-height: 1.48;
        margin-bottom: 0.55rem;
    }

    .bh-empty-example {
        background: rgba(14, 116, 144, 0.07);
        border-radius: 12px;
        color: var(--bh-text);
        font-family: "IBM Plex Mono", "SFMono-Regular", monospace;
        font-size: 0.8rem;
        line-height: 1.45;
        padding: 0.55rem 0.65rem;
    }

    .bh-run-pulse {
        position: relative;
        overflow: hidden;
    }

    .bh-run-pulse::after {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(90deg, transparent, rgba(15, 107, 111, 0.10), transparent);
        animation: bh-sweep 2.2s ease-in-out infinite;
    }

    @keyframes bh-sweep {
        0% { transform: translateX(-100%); }
        100% { transform: translateX(100%); }
    }

    @media (max-width: 1180px) {
        .bh-chat-header {
            grid-template-columns: minmax(0, 1fr);
        }

        .bh-chat-status-grid,
        .bh-empty-grid {
            grid-template-columns: minmax(0, 1fr);
        }
    }
    </style>
    """


def normalize_dock_view(raw_view: str) -> str:
    """Normalize one dock-view token to the supported UI set.

    Args:
        raw_view: Candidate dock-view token from session state or UI controls.

    Returns:
        A normalized title-cased dock label.
    """
    mapping = {
        "hidden": "Hidden",
        "activity": "Activity",
        "overview": "Activity",
        "files": "Files",
        "visuals": "Visuals",
        "guide": "Guide",
        "extend": "Extend",
    }
    return mapping.get(str(raw_view or "").strip().lower(), "Hidden")


def preferred_dock_view(raw_view: str, run: Mapping[str, Any]) -> str:
    """Resolve the currently preferred dock view for one run.

    Args:
        raw_view: Existing dock-view token.
        run: Active run mapping.

    Returns:
        A normalized dock label. The current value is preserved unless it is
        invalid, or the panel is hidden while active work is underway.
    """
    current = normalize_dock_view(raw_view)
    if current != "Hidden":
        return current
    run_status = str(run.get("status", "")).strip().lower()
    if run_status in {"running", "failed", "blocked_missing_tools", "repairing", "remediating_tools"}:
        return "Activity"
    if recent_event_rows(run, limit=1):
        return "Activity"
    return "Hidden"


def suggest_dock_view_from_request(user_text: str) -> str | None:
    """Infer one helpful context panel from a user request.

    Args:
        user_text: Raw user request text.

    Returns:
        One normalized dock-view token when the request clearly implies a
        contextual panel, otherwise ``None``.
    """
    text = str(user_text or "").strip().lower()
    if not text:
        return None

    extend_phrases = (
        "create skill",
        "create a skill",
        "make a skill",
        "add capability",
        "new capability",
        "extend the harness",
        "onboard tool",
    )
    guide_phrases = (
        "what can you do",
        "what tools",
        "what capabilities",
        "help me use",
        "manual",
        "documentation",
        "paper",
        "guide",
    )
    file_phrases = (
        "upload",
        "add file",
        "add files",
        "stage file",
        "stage files",
        "attach file",
        "attach data",
        "load data",
        "use these files",
    )
    visual_phrases = (
        "show plot",
        "show me the plot",
        "output plot",
        "plot",
        "visualize",
        "visualise",
        "chart",
        "graph",
        "figure",
        "artifact",
        "output file",
        "preview result",
    )

    if any(phrase in text for phrase in extend_phrases):
        return "Extend"
    if any(phrase in text for phrase in file_phrases):
        return "Files"
    if any(phrase in text for phrase in visual_phrases):
        return "Visuals"
    if any(phrase in text for phrase in guide_phrases):
        return "Guide"
    return None


def status_badge(status: str) -> dict[str, str]:
    """Return a stable badge description for a run status.

    Args:
        status: Raw run status token.

    Returns:
        A mapping containing ``label``, ``icon``, and ``tone``.
    """
    token = str(status or "").strip().lower()
    return dict(_STATUS_STYLES.get(token, {"label": token.replace("_", " ").title() or "Unknown", "icon": "?", "tone": "muted"}))


def current_step_label(run: Mapping[str, Any]) -> str:
    """Return a human-friendly current-step summary for one run."""
    run_status = str(run.get("status", "")).strip().lower() if isinstance(run, Mapping) else ""
    if run_status == "planning":
        return "Planning"
    if run_status in {"planning_failed", "planning_timed_out"}:
        return "Plan failed"
    tracker = run.get("process_tracker", {}) if isinstance(run, Mapping) else {}
    order = run.get("process_order", []) if isinstance(run, Mapping) else []
    if isinstance(order, Sequence):
        for key in order:
            proc = tracker.get(key, {}) if isinstance(tracker, Mapping) else {}
            if str(proc.get("status", "")).strip().lower() == "running":
                title = str(proc.get("title", "")).strip() or str(proc.get("tool_name", "")).strip()
                return title or f"Step {proc.get('step_id', '?')}"
    step_statuses = list(run.get("step_statuses", []) or []) if isinstance(run, Mapping) else []
    if any(str(status).strip().lower() == "running" for status in step_statuses):
        index = next(
            idx
            for idx, status in enumerate(step_statuses, start=1)
            if str(status).strip().lower() == "running"
        )
        return f"Step {index}"
    next_idx = int(run.get("next_step_idx", 0) or 0) if isinstance(run, Mapping) else 0
    if next_idx > 0:
        return f"Step {next_idx + 1}"
    return "Awaiting work"


def summarize_run_row(run: Mapping[str, Any], *, active_run_id: int | None = None) -> dict[str, Any]:
    """Build a compact summary row for recent-run navigation."""
    badge = status_badge(str(run.get("status", "")))
    request = str(run.get("user_request", "")).strip() or "Untitled run"
    truncated = request if len(request) <= 72 else request[:69].rstrip() + "..."
    events = list(run.get("events_tail", []) or [])
    return {
        "id": int(run.get("id", 0) or 0),
        "request": truncated,
        "status": str(run.get("status", "")).strip().lower() or "draft",
        "badge": badge,
        "step_label": current_step_label(run),
        "event_count": len(events),
        "active": int(run.get("id", 0) or 0) == int(active_run_id or -1),
    }


def summarize_recent_runs(
    plan_runs: Sequence[Mapping[str, Any]],
    *,
    active_run_id: int | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Summarize recent runs for left-rail navigation."""
    rows = [summarize_run_row(run, active_run_id=active_run_id) for run in reversed(list(plan_runs or []))]
    return rows[: max(1, int(limit))]


def recent_event_rows(run: Mapping[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
    """Return the most recent structured events for overview rendering."""
    rows = [dict(event) for event in list(run.get("events_tail", []) or []) if isinstance(event, Mapping)]
    return rows[-max(1, int(limit)) :]


def preferred_chat_run(
    active_run: Mapping[str, Any],
    plan_runs: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Choose the run that should drive pinned chat-side status context.

    Args:
        active_run: Nominally active run mapping.
        plan_runs: All runs currently tracked in the session.

    Returns:
        The active run when it already has meaningful execution state, otherwise
        the most recent non-draft run or artifact-bearing run.
    """
    artifacts = collect_run_artifacts(active_run, limit=2)
    if (
        str(active_run.get("status", "")).strip().lower() not in {"", "draft"}
        or artifacts
        or recent_event_rows(active_run, limit=1)
    ):
        return active_run

    for candidate in reversed(list(plan_runs or [])):
        if candidate is active_run:
            continue
        if str(candidate.get("status", "")).strip().lower() not in {"", "draft"}:
            return candidate
        if collect_run_artifacts(candidate, limit=1):
            return candidate
    return active_run


def collect_run_artifacts(run: Mapping[str, Any], *, limit: int = 18) -> list[Path]:
    """Collect likely artifact files from a run directory.

    Args:
        run: Run state mapping.
        limit: Maximum number of artifact paths to return.

    Returns:
        A list of existing file paths ordered by recency.
    """
    roots: list[Path] = []
    run_dir = str(run.get("run_dir", "")).strip()
    if run_dir:
        roots.append(Path(run_dir))
        roots.extend(
            [
                Path(run_dir) / "final",
                Path(run_dir) / "outputs",
                Path(run_dir) / "reports",
            ]
        )
    run_files = run.get("run_files", {}) if isinstance(run, Mapping) else {}
    if isinstance(run_files, Mapping):
        for value in run_files.values():
            token = str(value or "").strip()
            if token:
                roots.append(Path(token))

    files: list[Path] = []
    seen: set[Path] = set()
    primary_run_dir: Path | None = None
    if run_dir:
        try:
            primary_run_dir = Path(run_dir).expanduser().resolve()
        except Exception:
            primary_run_dir = None

    def _artifact_sort_key(path: Path) -> tuple[int, int, int, float]:
        kind_rank = {
            "image": 0,
            "table": 1,
            "text": 2,
            "json": 3,
            "pdf": 4,
            "file": 5,
        }.get(artifact_kind(path), 6)
        location_rank = 1
        if primary_run_dir is not None:
            try:
                rel_path = path.relative_to(primary_run_dir)
                rel_parts = rel_path.parts
                if rel_parts and rel_parts[0] == "final":
                    location_rank = 0
                elif rel_parts and rel_parts[0] in {"outputs", "reports"}:
                    location_rank = 1
                else:
                    location_rank = 2
            except Exception:
                location_rank = 2
        bookkeeping_rank = 1 if path.name.lower() in _BOOKKEEPING_FILENAMES or "scripts" in path.parts else 0
        return (
            bookkeeping_rank,
            location_rank,
            kind_rank,
            -(path.stat().st_mtime if path.exists() else 0.0),
        )

    for root in roots:
        try:
            resolved = root.expanduser().resolve()
        except Exception:
            continue
        if not resolved.exists():
            continue
        if resolved.is_file():
            candidates = [resolved]
        else:
            try:
                candidates = [path for path in resolved.rglob("*") if path.is_file()]
            except Exception:
                candidates = []
        for candidate in candidates:
            if candidate in seen:
                continue
            suffix = "".join(candidate.suffixes[-2:]).lower() if candidate.suffix.lower() == ".gz" else candidate.suffix.lower()
            if suffix not in _ARTIFACT_SUFFIXES and candidate.suffix.lower() not in _ARTIFACT_SUFFIXES:
                continue
            seen.add(candidate)
            files.append(candidate)
    files.sort(key=_artifact_sort_key)
    return files[: max(1, int(limit))]


def artifact_kind(path: Path) -> str:
    """Return a simple artifact kind token from a path suffix."""
    name = path.name.lower()
    if name.endswith((".png", ".jpg", ".jpeg", ".svg")):
        return "image"
    if name.endswith((".csv", ".tsv")):
        return "table"
    if name.endswith((".json", ".jsonl")):
        return "json"
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith((".md", ".txt", ".html", ".treefile", ".nwk", ".newick")):
        return "text"
    return "file"


def select_primary_artifact(artifacts: Sequence[Path]) -> Path | None:
    """Choose the best artifact to preview inline.

    Args:
        artifacts: Candidate artifact paths ordered by recency.

    Returns:
        The first artifact ranked by preview usefulness, or ``None`` when no
        artifact exists.
    """
    if not artifacts:
        return None
    priority = {
        "image": 0,
        "table": 1,
        "json": 2,
        "text": 3,
        "pdf": 4,
        "file": 5,
    }
    ordered = list(artifacts)
    return min(
        ordered,
        key=lambda path: (priority.get(artifact_kind(path), 99), ordered.index(path)),
    )


def format_structured_chat_message(content: str) -> str | None:
    """Render small structured JSON chat responses as readable Markdown.

    Args:
        content: Raw assistant message content.

    Returns:
        A Markdown rendering when the content is a small string-to-string JSON
        object, otherwise ``None``.
    """
    text = str(content or "").strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        pairs = re.findall(r'"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', text)
        if not pairs:
            return None
        payload = {
            key: bytes(value, "utf-8").decode("unicode_escape")
            for key, value in pairs
        }
    if not isinstance(payload, dict) or not payload or len(payload) > 8:
        return None
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in payload.items()):
        return None

    sections: list[str] = []
    for key, value in payload.items():
        heading = key
        if ")" in heading:
            heading = heading.split(")", 1)[1].strip()
        sections.append(f"**{heading}**\n{value}")
    return "\n\n".join(sections)


def chat_empty_state_sections() -> list[dict[str, str]]:
    """Return starter guidance cards for the empty chat state.

    Returns:
        A deterministic list of chat-empty-state sections with titles,
        descriptions, and example prompts.
    """
    return [
        {
            "title": "Plan or run an analysis",
            "description": (
                "Ask for QC, alignment, transcript quantification, differential "
                "expression, phylogenetics, peak calling, variant calling, or a "
                "workflow recommendation."
            ),
            "example": (
                "Review the files in workspace/inputs_readonly/project_a and "
                "tell me which analysis workflow fits best."
            ),
        },
        {
            "title": "Add data or stage inputs",
            "description": (
                "Tell BioHarness you need to add files, stage local paths, or "
                "attach a dataset. The UI will surface the right workspace path "
                "instead of making you hunt for it."
            ),
            "example": "I need to add local files for this run and keep them read-only.",
        },
        {
            "title": "Find references, papers, or manuals",
            "description": (
                "Ask for trusted papers, tool manuals, or reference assets. "
                "Use this when you need documentation, citations, or a guided "
                "download path into the workspace."
            ),
            "example": "Find the official Salmon manual and explain the key index options.",
        },
        {
            "title": "Understand or extend the harness",
            "description": (
                "Ask what capabilities are already wrapped, how to create a new "
                "skill, or how to onboard another bioinformatics tool cleanly."
            ),
            "example": "How would I create a new skill for a tool that is not wrapped yet?",
        },
    ]


def chat_status_cards(
    run: Mapping[str, Any],
    *,
    heartbeat_label: str = "",
    heartbeat_note: str = "",
    artifact_count: int = 0,
) -> list[dict[str, str]]:
    """Build compact status cards for the chat surface.

    Args:
        run: Active run mapping.
        heartbeat_label: Human-readable heartbeat age or state.
        heartbeat_note: Latest heartbeat note to show in detail text.
        artifact_count: Number of current artifact candidates.

    Returns:
        A deterministic set of card descriptors. Draft runs without progress,
        messages, or artifacts return an empty list.
    """
    status = str(run.get("status", "")).strip().lower()
    steps = list(run.get("step_statuses", []) or [])
    completed = sum(1 for item in steps if str(item).strip().lower() == "completed")
    total = len(steps)
    repairs = sum(int(value or 0) for value in dict(run.get("auto_repair_attempts", {})).values())
    events = recent_event_rows(run, limit=1)
    if status in {"", "draft"} and not steps and artifact_count <= 0 and not events:
        return []

    cards = [
        {
            "label": "Run status",
            "value": status_badge(status)["label"],
            "detail": str(run.get("run_uid", "")).strip() or "No persisted run id yet",
            "tone": status_badge(status)["tone"],
        },
        {
            "label": "Current step",
            "value": current_step_label(run),
            "detail": f"{completed}/{total} complete" if total else "No executable plan yet",
            "tone": "info" if total else "muted",
        },
        {
            "label": "Heartbeat",
            "value": heartbeat_label or "Idle",
            "detail": heartbeat_note.strip() or "No executor heartbeat note yet",
            "tone": "live" if status in {"running", "repairing"} else "muted",
        },
        {
            "label": "Results",
            "value": f"{artifact_count} artifact{'s' if artifact_count != 1 else ''}",
            "detail": (
                f"{repairs} repair attempt{'s' if repairs != 1 else ''}"
                if repairs
                else "Ready to summarize outputs when they appear"
            ),
            "tone": "success" if artifact_count else "muted",
        },
    ]
    return cards


def summarize_artifacts_for_chat(
    artifacts: Sequence[Path],
    *,
    run_dir: str = "",
    limit: int = 4,
) -> list[str]:
    """Return readable artifact labels for chat summaries.

    Args:
        artifacts: Candidate artifact paths.
        run_dir: Optional run directory used for shorter relative labels.
        limit: Maximum number of labels to return.

    Returns:
        A list of short artifact labels suitable for chat copy.
    """
    base_path: Path | None = None
    if run_dir:
        try:
            base_path = Path(run_dir).expanduser().resolve()
        except Exception:
            base_path = None

    labels: list[str] = []
    for path in list(artifacts or [])[: max(1, int(limit))]:
        if base_path is not None:
            try:
                labels.append(str(path.resolve().relative_to(base_path)))
                continue
            except Exception:
                pass
        labels.append(path.name)
    return labels


def build_chat_result_summary(run: Mapping[str, Any], artifacts: Sequence[Path], *, limit: int = 4) -> str:
    """Build one assistant-facing run/result summary for the chat transcript.

    Args:
        run: Active run mapping.
        artifacts: Candidate artifact paths.
        limit: Maximum number of artifact labels to include.

    Returns:
        One Markdown summary string for terminal run states, or an empty string
        when no summary should be posted.
    """
    status = str(run.get("status", "")).strip().lower()
    if status not in {
        "completed",
        "failed",
        "blocked_missing_tools",
        "blocked_input",
    }:
        return ""

    badge = status_badge(status)["label"]
    steps = list(run.get("step_statuses", []) or [])
    completed = sum(1 for item in steps if str(item).strip().lower() == "completed")
    labels = summarize_artifacts_for_chat(artifacts, run_dir=str(run.get("run_dir", "")), limit=limit)
    lines = [
        "Run update:",
        f"- Status: `{badge}`",
        f"- Step progress: `{completed}/{len(steps)}`" if steps else "- Step progress: `No plan recorded`",
    ]
    error_text = str(run.get("error", "")).strip()
    if error_text:
        lines.append(f"- Error: `{error_text}`")
    if labels:
        lines.append("- Key outputs:")
        lines.extend(f"  - `{label}`" for label in labels)
    else:
        lines.append("- Key outputs: `No previewable artifacts yet`")

    if status == "completed":
        lines.append("- Next: ask me to summarize the outputs, inspect one artifact, or explain the result.")
    elif status in {"blocked_missing_tools", "blocked_input"}:
        lines.append("- Next: ask me to resolve the blocking issue or inspect the latest activity.")
    else:
        lines.append("- Next: ask me to inspect the failure, review logs, or retry the run.")
    return "\n".join(lines)
