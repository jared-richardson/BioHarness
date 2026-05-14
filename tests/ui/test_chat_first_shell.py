from __future__ import annotations

from pathlib import Path

from bio_harness.ui.chat_first_shell import (
    artifact_kind,
    build_chat_result_summary,
    chat_empty_state_sections,
    chat_status_cards,
    compact_model_name_for_rail,
    collect_run_artifacts,
    current_step_label,
    format_structured_chat_message,
    normalize_dock_view,
    preferred_dock_view,
    preferred_chat_run,
    select_primary_artifact,
    summarize_artifacts_for_chat,
    suggest_dock_view_from_request,
    status_badge,
    summarize_recent_runs,
)


def test_compact_model_name_for_rail_drops_latest_and_truncates() -> None:
    assert compact_model_name_for_rail("qwen3-coder-next:latest") == "qwen3-coder-next"
    assert compact_model_name_for_rail("very-long-model-name-with-tag", max_chars=12) == "very-long-m..."


def test_status_badge_maps_known_and_unknown_states() -> None:
    assert status_badge("running") == {"label": "Running", "icon": "R", "tone": "live"}
    assert status_badge("blocked_input") == {"label": "Needs Input", "icon": "I", "tone": "warning"}
    assert status_badge("mystery") == {"label": "Mystery", "icon": "?", "tone": "muted"}


def test_current_step_label_prefers_running_process_tracker() -> None:
    run = {
        "process_order": ["p1", "p2"],
        "process_tracker": {
            "p1": {"status": "completed", "title": "FastQC"},
            "p2": {"status": "running", "title": "STAR alignment", "step_id": 2},
        },
        "step_statuses": ["completed", "running", "pending"],
    }
    assert current_step_label(run) == "STAR alignment"


def test_summarize_recent_runs_marks_active_and_truncates_request() -> None:
    runs = [
        {"id": 1, "status": "completed", "user_request": "First", "events_tail": []},
        {"id": 2, "status": "running", "user_request": "A" * 90, "events_tail": [{"event_type": "heartbeat"}]},
    ]
    rows = summarize_recent_runs(runs, active_run_id=2, limit=5)
    assert [row["id"] for row in rows] == [2, 1]
    assert rows[0]["active"] is True
    assert rows[0]["request"].endswith("...")
    assert rows[0]["event_count"] == 1


def test_collect_run_artifacts_prefers_interesting_existing_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_001"
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    png = final_dir / "volcano.png"
    png.write_bytes(b"png")
    txt = run_dir / "stdout.txt"
    txt.write_text("hello", encoding="utf-8")
    ignored = run_dir / "tmp.bin"
    ignored.write_bytes(b"raw")

    run = {
        "run_dir": str(run_dir),
        "run_files": {"stdout": str(txt)},
    }
    artifacts = collect_run_artifacts(run, limit=10)
    names = {path.name for path in artifacts}
    assert "volcano.png" in names
    assert "stdout.txt" in names
    assert "tmp.bin" not in names


def test_collect_run_artifacts_includes_newick_like_outputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_002"
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    tree_path = final_dir / "phylogeny.treefile"
    tree_path.write_text("(a:0.1,b:0.2,c:0.3);\n", encoding="utf-8")

    artifacts = collect_run_artifacts({"run_dir": str(run_dir)}, limit=10)

    assert tree_path in artifacts


def test_collect_run_artifacts_prefers_final_outputs_over_bookkeeping_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_003"
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    tree_path = final_dir / "phylogeny.treefile"
    tree_path.write_text("(a:0.1,b:0.2,c:0.3);\n", encoding="utf-8")
    state_path = run_dir / "state.json"
    state_path.write_text("{}", encoding="utf-8")

    artifacts = collect_run_artifacts({"run_dir": str(run_dir)}, limit=4)

    assert artifacts[0] == tree_path


def test_artifact_kind_detects_basic_preview_types() -> None:
    assert artifact_kind(Path("plot.png")) == "image"
    assert artifact_kind(Path("counts.tsv")) == "table"
    assert artifact_kind(Path("report.json")) == "json"
    assert artifact_kind(Path("paper.pdf")) == "pdf"
    assert artifact_kind(Path("notes.md")) == "text"
    assert artifact_kind(Path("phylogeny.treefile")) == "text"


def test_normalize_dock_view_maps_aliases_and_unknown_values() -> None:
    assert normalize_dock_view("overview") == "Activity"
    assert normalize_dock_view("visuals") == "Visuals"
    assert normalize_dock_view("mystery") == "Hidden"


def test_preferred_dock_view_opens_activity_for_active_or_eventful_runs() -> None:
    assert preferred_dock_view("hidden", {"status": "running"}) == "Activity"
    assert preferred_dock_view(
        "Hidden",
        {"status": "draft", "events_tail": [{"event_type": "STEP_STARTED"}]},
    ) == "Activity"
    assert preferred_dock_view("guide", {"status": "running"}) == "Guide"


def test_suggest_dock_view_from_request_routes_common_context_needs() -> None:
    assert suggest_dock_view_from_request("Can I upload files for this run?") == "Files"
    assert suggest_dock_view_from_request("Show me the output plot") == "Visuals"
    assert suggest_dock_view_from_request("What capabilities does the harness have?") == "Guide"
    assert suggest_dock_view_from_request("Help me create a skill") == "Extend"
    assert suggest_dock_view_from_request("Run the analysis now") is None


def test_chat_empty_state_sections_offer_expected_guidance_buckets() -> None:
    sections = chat_empty_state_sections()
    assert len(sections) == 4
    assert sections[0]["title"] == "Plan or run an analysis"
    assert "files" in sections[1]["description"].lower()
    assert "manual" in sections[2]["description"].lower()
    assert "skill" in sections[3]["example"].lower()


def test_chat_status_cards_hide_blank_draft_and_summarize_active_runs() -> None:
    assert chat_status_cards({"status": "draft"}) == []

    cards = chat_status_cards(
        {
            "status": "running",
            "run_uid": "run_001",
            "step_statuses": ["completed", "running", "pending"],
            "auto_repair_attempts": {"missing_tool": 1},
        },
        heartbeat_label="4s ago",
        heartbeat_note="STAR alignment is streaming progress",
        artifact_count=2,
    )
    assert [card["label"] for card in cards] == [
        "Run status",
        "Current step",
        "Heartbeat",
        "Results",
    ]
    assert cards[0]["value"] == "Running"
    assert cards[1]["detail"] == "1/3 complete"
    assert cards[2]["detail"] == "STAR alignment is streaming progress"
    assert cards[3]["value"] == "2 artifacts"


def test_summarize_artifacts_for_chat_prefers_run_relative_labels(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_001"
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    plot_path = final_dir / "plot.png"
    plot_path.write_bytes(b"png")
    report_path = run_dir / "reports" / "summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("{}", encoding="utf-8")

    labels = summarize_artifacts_for_chat([plot_path, report_path], run_dir=str(run_dir), limit=4)
    assert labels == ["final/plot.png", "reports/summary.json"]


def test_build_chat_result_summary_includes_outputs_and_next_action(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_001"
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    quant_path = final_dir / "quant.sf"
    quant_path.write_text("name\tvalue\n", encoding="utf-8")

    summary = build_chat_result_summary(
        {
            "status": "completed",
            "run_dir": str(run_dir),
            "step_statuses": ["completed", "completed"],
        },
        [quant_path],
        limit=4,
    )
    assert "Run update:" in summary
    assert "- Status: `Completed`" in summary
    assert "`final/quant.sf`" in summary
    assert "summarize the outputs" in summary


def test_format_structured_chat_message_renders_small_json_sections() -> None:
    formatted = format_structured_chat_message(
        '{"1) Direct answer":"BioHarness supports phylogenetics.","2) Relevant files":"bio_harness/skills/library/mafft_align.py"}'
    )
    assert formatted is not None
    assert "**Direct answer**" in formatted
    assert "BioHarness supports phylogenetics." in formatted
    assert "**Relevant files**" in formatted


def test_format_structured_chat_message_ignores_plain_text() -> None:
    assert format_structured_chat_message("plain text response") is None


def test_format_structured_chat_message_parses_dict_like_fallback() -> None:
    payload = '{"1) Direct answer": "Line one with `code`", "2) Notes": "Line two"}'
    formatted = format_structured_chat_message(payload)
    assert formatted is not None
    assert "**Direct answer**" in formatted
    assert "Line one with `code`" in formatted


def test_select_primary_artifact_prefers_visual_preview_types() -> None:
    artifacts = [
        Path("final/summary.md"),
        Path("final/plot.png"),
        Path("final/counts.tsv"),
    ]
    assert select_primary_artifact(artifacts) == Path("final/plot.png")


def test_preferred_chat_run_uses_recent_completed_run_when_active_is_blank(tmp_path: Path) -> None:
    completed_dir = tmp_path / "run_001"
    completed_dir.mkdir(parents=True, exist_ok=True)
    (completed_dir / "summary.md").write_text("done", encoding="utf-8")
    active_run = {"status": "draft", "events_tail": [], "run_dir": ""}
    completed_run = {"status": "completed", "events_tail": [], "run_dir": str(completed_dir)}
    assert preferred_chat_run(active_run, [active_run, completed_run]) is completed_run
