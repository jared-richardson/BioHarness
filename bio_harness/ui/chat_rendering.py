"""Chat rendering helpers for the Streamlit UI.

These helpers keep the chat-first presentation layer out of ``app.py`` while
remaining deterministic and artifact-driven.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from bio_harness.agents.orchestrator import Orchestrator
from bio_harness.ui.artifact_preview import artifact_preview_metadata, table_preview_profile
from bio_harness.ui.chat_first_shell import (
    artifact_kind,
    build_chat_result_summary,
    chat_empty_state_sections,
    chat_status_cards,
    collect_run_artifacts,
    current_step_label,
    recent_event_rows,
    select_primary_artifact,
    status_badge,
    summarize_artifacts_for_chat,
)


def render_workspace_header() -> None:
    """Render the compact chat-first workspace header."""
    st.markdown(
        (
            '<div class="bh-chat-intro">'
            '<div class="bh-chat-title">Conversation</div>'
            '<div class="bh-chat-subtitle">'
            'Ask for analysis, execution, references, file staging, downloads, capability help, or a result explanation. '
            'Important run state and outputs appear directly in the chat.'
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_chat_status_strip(
    active_run: dict[str, Any],
    *,
    heartbeat_label: str,
    heartbeat_note: str,
) -> None:
    """Render compact run/status cards directly above the transcript.

    Args:
        active_run: Active run mapping.
        heartbeat_label: Current executor heartbeat summary.
        heartbeat_note: Latest executor heartbeat note.
    """
    artifacts = collect_run_artifacts(active_run, limit=6)
    cards = chat_status_cards(
        active_run,
        heartbeat_label=heartbeat_label,
        heartbeat_note=str(heartbeat_note).strip(),
        artifact_count=len(artifacts),
    )
    if not cards:
        return
    columns = st.columns(len(cards), gap="small")
    for column, card in zip(columns, cards):
        with column:
            st.markdown(
                (
                    '<div class="bh-chat-status-card">'
                    f'<div class="bh-chat-status-label">{escape(card["label"])}</div>'
                    f'<div class="bh-chat-status-value">{escape(card["value"])}</div>'
                    f'<div class="bh-chat-status-detail">{escape(card["detail"])}</div>'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )


def render_chat_empty_state() -> None:
    """Render starter guidance when the chat transcript is empty."""
    st.markdown(
        (
            '<div class="bh-inline-summary">'
            '<div class="bh-inline-summary-title">Start with a normal request</div>'
            '<div class="bh-inline-summary-copy">'
            'BioHarness is chat-first. Ask for an analysis, describe a dataset, request a paper or manual, '
            'say you need to add files, or ask what the harness can already do. Context panels only open when useful.'
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    sections = chat_empty_state_sections()
    left_col, right_col = st.columns(2, gap="small")
    for idx, section in enumerate(sections):
        target = left_col if idx % 2 == 0 else right_col
        with target:
            st.markdown(
                (
                    '<div class="bh-empty-card">'
                    f'<div class="bh-empty-title">{escape(section["title"])}</div>'
                    f'<div class="bh-empty-copy">{escape(section["description"])}</div>'
                    f'<div class="bh-empty-example">{escape(section["example"])}</div>'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )


def render_model_setup_block(setup_report: dict[str, Any]) -> None:
    """Render deterministic model-backend setup help inside the chat column.

    Args:
        setup_report: Structured report from ``build_llm_setup_report``.
    """
    if not isinstance(setup_report, dict) or bool(setup_report.get("ready", False)):
        return
    status_text = str(setup_report.get("status_message", "")).strip() or "Model backend is not ready yet."
    next_steps = [str(step).strip() for step in list(setup_report.get("next_steps", []) or []) if str(step).strip()]
    commands = [str(cmd).strip() for cmd in list(setup_report.get("recommended_commands", []) or []) if str(cmd).strip()]
    available_models = [
        str(row.get("name", "")).strip()
        for row in list(setup_report.get("available_models", []) or [])[:4]
        if isinstance(row, dict) and str(row.get("name", "")).strip()
    ]
    st.markdown(
        (
            '<div class="bh-inline-summary">'
            '<div class="bh-inline-summary-title">Model runtime needs setup</div>'
            f'<div class="bh-inline-summary-copy">{escape(status_text)}</div>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    if next_steps:
        st.markdown("**What to do next**")
        for idx, step in enumerate(next_steps[:4], start=1):
            st.markdown(f"{idx}. {step}")
    if commands:
        st.markdown("**Commands**")
        st.code("\n".join(commands[:5]), language="bash")
    if available_models:
        chips = "".join(f'<span class="bh-inline-chip">{escape(name)}</span>' for name in available_models)
        st.markdown(
            (
                '<div class="bh-inline-chip-row" style="margin-bottom:0.7rem;">'
                f"{chips}"
                "</div>"
            ),
            unsafe_allow_html=True,
        )


def render_chat_artifact_hint(active_run: dict[str, Any]) -> None:
    """Render one inline artifact summary card above the transcript.

    Args:
        active_run: Active run mapping.
    """
    artifacts = collect_run_artifacts(active_run, limit=6)
    if not artifacts:
        return
    labels = summarize_artifacts_for_chat(
        artifacts,
        run_dir=str(active_run.get("run_dir", "")),
        limit=3,
    )
    if not labels:
        return
    label_text = ", ".join(f"`{label}`" for label in labels)
    status = status_badge(str(active_run.get("status", "")))["label"]
    suffix = (
        " Ask me to summarize these results or open the Visuals panel."
        if status == "Completed"
        else " Ask me to inspect the latest output or explain what changed."
    )
    st.markdown(
        (
            '<div class="bh-inline-summary">'
            '<div class="bh-inline-summary-title">Latest outputs</div>'
            f'<div class="bh-inline-summary-copy">Run status: <strong>{escape(status)}</strong>. '
            f"Recent artifacts: {label_text}.{suffix}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_artifact_preview_block(path: Path, *, preview_key: str, compact: bool = False) -> None:
    """Render one artifact preview block.

    Args:
        path: Artifact path to preview.
        preview_key: Stable widget-key prefix.
        compact: Whether to render the compact inline variant.
    """
    kind = artifact_kind(path)
    table_rows = 40 if compact else 200
    text_limit = 4000 if compact else 12000
    _render_preview_header(path, kind=kind, compact=compact)
    try:
        if kind == "image":
            st.image(str(path), use_container_width=True)
        elif kind == "table":
            frame = _read_table_preview(path, max_rows=table_rows)
            _render_table_preview(frame, compact=compact)
        elif kind == "json":
            if path.suffix.lower() == ".jsonl":
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()[:10]
                    if line.strip()
                ]
                st.json(rows)
            else:
                st.json(json.loads(path.read_text(encoding="utf-8")))
        elif kind == "text":
            st.code(path.read_text(encoding="utf-8", errors="replace")[:text_limit], language="text")
        elif kind == "pdf":
            st.info(f"PDF ready: `{path.name}`")
        else:
            st.info(f"Artifact ready: `{path.name}`")
    except Exception as exc:
        st.error(f"Preview failed for `{path.name}`: {exc}")


def render_chat_live_run_view(
    active_run: dict[str, Any],
    *,
    heartbeat_label: str,
    heartbeat_note: str,
) -> None:
    """Render a pinned live run/result view inside the chat transcript.

    Args:
        active_run: Currently active run mapping.
        heartbeat_label: Current executor heartbeat summary.
        heartbeat_note: Latest executor heartbeat note.
    """
    artifacts = collect_run_artifacts(active_run, limit=8)
    events = recent_event_rows(active_run, limit=3)
    status = str(active_run.get("status", "")).strip().lower()
    if status in {"", "draft"} and not artifacts and not events:
        return
    badge = status_badge(status)
    step_label = current_step_label(active_run)
    steps = list(active_run.get("step_statuses", []) or [])
    completed = sum(1 for item in steps if str(item).strip().lower() == "completed")
    artifact_labels = summarize_artifacts_for_chat(
        artifacts,
        run_dir=str(active_run.get("run_dir", "")),
        limit=4,
    )
    preview_target = select_primary_artifact(artifacts)
    preview_kind = artifact_kind(preview_target) if preview_target is not None else ""
    chips = [
        ("Status", badge["label"]),
        ("Current step", step_label),
        ("Heartbeat", heartbeat_label),
        ("Artifacts", str(len(artifacts))),
    ]
    with st.chat_message("assistant"):
        st.markdown(
            (
                '<div class="bh-inline-run-card">'
                '<div class="bh-inline-run-title">Live run view</div>'
                '<div class="bh-inline-run-copy">'
                'Current execution state, recent changes, and the most useful output preview are shown here automatically.'
                "</div>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            (
                '<div class="bh-inline-chip-row" style="margin-bottom:0.45rem;">'
                + "".join(
                    '<span class="bh-inline-chip">'
                    f'<strong>{escape(label)}:</strong> {escape(value)}'
                    '</span>'
                    for label, value in chips
                )
                + "</div>"
            ),
            unsafe_allow_html=True,
        )
        if steps:
            st.caption(f"{completed}/{len(steps)} steps complete")
        if heartbeat_note:
            st.caption(f"Latest note: {heartbeat_note}")
        if events:
            st.markdown("**Recent activity**")
            for event in events:
                label = str(event.get("message", event.get("title", "event"))).strip() or str(
                    event.get("event_type", "event")
                )
                st.markdown(f"- `{event.get('event_type', 'event')}` · {label}")
        if artifact_labels:
            chips_html = "".join(
                f'<span class="bh-inline-chip">{escape(label)}</span>'
                for label in artifact_labels
            )
            st.markdown(
                (
                    '<div class="bh-inline-summary" style="margin-top:0.7rem;">'
                    '<div class="bh-inline-summary-title">Current outputs</div>'
                    '<div class="bh-inline-summary-copy">BioHarness has materialized these recent artifacts.</div>'
                    f'<div class="bh-inline-chip-row">{chips_html}</div>'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
        if preview_target is not None:
            st.markdown(
                f"**Previewing `{preview_target.name}`**"
                f"{f' as {preview_kind}' if preview_kind else ''}"
            )
            render_artifact_preview_block(
                preview_target,
                preview_key=f"chat_inline_{int(active_run.get('id', 0) or 0)}",
                compact=True,
            )


def maybe_append_chat_result_summary(
    active_run: dict[str, Any],
    orchestrator: Orchestrator,
    *,
    session_id: str,
) -> None:
    """Append one deterministic result summary when a run materially changes.

    Args:
        active_run: Currently active run mapping.
        orchestrator: Shared orchestrator instance.
        session_id: Active orchestrator session identifier.
    """
    artifacts = collect_run_artifacts(active_run, limit=6)
    summary = build_chat_result_summary(active_run, artifacts, limit=4)
    if not summary:
        return
    signature = "|".join(
        [
            str(active_run.get("run_uid", "")).strip(),
            str(active_run.get("status", "")).strip().lower(),
            str(active_run.get("error", "")).strip(),
            summary,
        ]
    )
    if signature == str(active_run.get("last_chat_result_signature", "")).strip():
        return
    session = orchestrator.get_or_create_session(session_id)
    messages = session.setdefault("messages", [])
    if not messages or str(messages[-1].get("content", "")).strip() != summary:
        messages.append({"role": "assistant", "content": summary})
    active_run.setdefault("conversation", []).append({"user": "", "assistant": summary})
    active_run["last_chat_result_signature"] = signature


def render_visual_preview(active_run: dict[str, Any]) -> None:
    """Render a generic artifact preview/visual panel.

    Args:
        active_run: Active run mapping.
    """
    st.markdown(
        (
            '<div class="bh-shell-section">'
            '<div class="bh-shell-heading">Visuals</div>'
            '<div class="bh-shell-caption">Artifact-aware previews for images, tables, JSON, and reports.</div>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    artifacts = collect_run_artifacts(active_run, limit=20)
    if not artifacts:
        st.info("No previewable artifacts yet.")
        return
    selected = st.selectbox(
        "Preview artifact",
        options=artifacts,
        format_func=lambda path: path.name,
        key=f"artifact_preview_{int(active_run.get('id', 0) or 0)}",
    )
    kind = artifact_kind(selected)
    st.caption(f"Detected preview type: {kind}")
    render_artifact_preview_block(
        selected,
        preview_key=f"dock_preview_{int(active_run.get('id', 0) or 0)}",
        compact=False,
    )


def _render_preview_header(path: Path, *, kind: str, compact: bool = False) -> None:
    metadata = artifact_preview_metadata(path, kind)
    chips = "".join(
        f'<span class="bh-inline-chip">{escape(value)}</span>'
        for value in (metadata["kind"], metadata["size"], metadata["modified"])
    )
    margin = "0.55rem" if compact else "0.75rem"
    st.markdown(
        (
            f'<div class="bh-inline-summary" style="margin-top:{margin};">'
            f'<div class="bh-inline-summary-title">{escape(metadata["name"])}</div>'
            '<div class="bh-inline-summary-copy">Preview metadata and the most useful quick-look rendering.</div>'
            f'<div class="bh-inline-chip-row">{chips}</div>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_table_preview(frame: pd.DataFrame, *, compact: bool = False) -> None:
    profile = table_preview_profile(
        frame,
        max_series=3,
        chart_rows=40 if compact else 80,
        histogram_bins=8 if compact else 12,
    )
    chip_values = [
        f"{profile['row_count']} rows",
        f"{profile['column_count']} columns",
        f"{len(profile['numeric_columns'])} numeric",
    ]
    st.markdown(
        (
            '<div class="bh-inline-chip-row" style="margin-bottom:0.55rem;">'
            + "".join(f'<span class="bh-inline-chip">{escape(value)}</span>' for value in chip_values)
            + "</div>"
        ),
        unsafe_allow_html=True,
    )
    if profile["metric_rows"]:
        metric_frame = pd.DataFrame(profile["metric_rows"])
        st.dataframe(metric_frame, use_container_width=True, hide_index=True)
    if compact:
        st.dataframe(frame, use_container_width=True, height=220)
        if not profile["chart_frame"].empty:
            st.markdown("**Trend view**")
            st.line_chart(profile["chart_frame"])
        if not profile["histogram_frame"].empty:
            st.markdown("**Distribution**")
            st.bar_chart(profile["histogram_frame"].set_index("bin"))
        return
    tab_data, tab_trend, tab_distribution = st.tabs(["Preview", "Trend", "Distribution"])
    with tab_data:
        st.dataframe(frame, use_container_width=True, height=320)
    with tab_trend:
        if not profile["chart_frame"].empty:
            st.line_chart(profile["chart_frame"])
        else:
            st.info("No numeric columns were available for a trend chart.")
    with tab_distribution:
        if not profile["histogram_frame"].empty:
            st.bar_chart(profile["histogram_frame"].set_index("bin"))
        else:
            st.info("No numeric column distribution was available.")


def _read_table_preview(path: Path, *, max_rows: int = 200) -> pd.DataFrame:
    separator = "\t" if path.suffix.lower() == ".tsv" else ","
    return pd.read_csv(path, sep=separator, nrows=max_rows)
