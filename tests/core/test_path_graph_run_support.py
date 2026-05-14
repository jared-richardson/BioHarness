from __future__ import annotations

from bio_harness.harness.path_graph_run_support import (
    build_active_preference_profile,
    infer_selected_path_id,
    record_graph_outcome,
    record_graph_selection,
)


class _FakeGraph:
    def __init__(self) -> None:
        self.nodes: list[dict[str, object]] = []
        self.path_runs: list[dict[str, object]] = []
        self.annotations: list[dict[str, object]] = []
        self.preference_persists: list[dict[str, object]] = []

    def upsert_node(self, **kwargs) -> None:
        self.nodes.append(dict(kwargs))

    def record_path_run(self, **kwargs) -> None:
        self.path_runs.append(dict(kwargs))

    def add_annotation(self, **kwargs) -> None:
        self.annotations.append(dict(kwargs))

    def persist_success_preferences(self, **kwargs) -> None:
        self.preference_persists.append(dict(kwargs))


def test_build_active_preference_profile_merges_lists_and_scalar_overrides() -> None:
    merged = build_active_preference_profile(
        stored_preferences={"tool_blacklist": ["gatk"], "tool_whitelist": ["bcftools"], "mode": "conservative"},
        analysis_preferences={"tool_blacklist": ["bowtie2"], "preferred_tools": ["bcftools"], "mode": "guided"},
    )

    assert merged["mode"] == "guided"
    assert merged["preferred_tools"] == ["bcftools"]
    assert merged["discouraged_tools"] == ["gatk"]
    assert merged["tool_blacklist"] == ["bowtie2", "gatk"]


def test_infer_selected_path_id_uses_fallback_then_canonical_then_hash() -> None:
    selected = infer_selected_path_id(
        plan={"canonical_template": "rna_seq_de"},
        fallback_selection={"selected_pipeline_id": "preferred_path"},
        prompt_hash_fallback="abc123",
    )
    assert selected == "preferred_path"

    canonical = infer_selected_path_id(
        plan={"canonical_template": "rna_seq_de"},
        fallback_selection={},
        prompt_hash_fallback="abc123",
    )
    assert canonical == "rna_seq_de"

    fallback = infer_selected_path_id(
        plan={"canonical_template": "custom_freeform"},
        fallback_selection={},
        prompt_hash_fallback="abc123",
    )
    assert fallback == "llm_plan::abc123"


def test_record_graph_selection_records_node_run_and_annotations() -> None:
    graph = _FakeGraph()
    run = {
        "run_uid": "run-1",
        "prompt_hash": "hash-1",
        "started_at": "2025-01-01T00:00:00+00:00",
        "plan_contract": {"must_include_capabilities": ["alignment"]},
        "missing_tools_detected": ["bwa"],
        "fallback_selection": {
            "selected_pipeline_id": "preferred_path",
            "selection_reason": "highest_score",
            "selection_score": 0.9,
            "selection_graph_score": 0.7,
            "candidates": [
                {"pipeline_id": "rejected_path", "missing_caps": ["variant"], "missing_inputs": [], "missing_tools": ["gatk"]},
            ],
        },
    }

    record_graph_selection(path_graph=graph, run=run, path_id="preferred_path")

    assert run["selected_path_id"] == "preferred_path"
    assert graph.nodes[0]["node_id"] == "path:preferred_path"
    assert graph.path_runs[0]["run_id"] == "run-1:planned"
    assert len(graph.annotations) == 2


def test_record_graph_outcome_persists_success_preferences_when_enabled() -> None:
    graph = _FakeGraph()
    run = {
        "run_uid": "run-2",
        "selected_path_id": "preferred_path",
        "prompt_hash": "hash-2",
        "started_at": "2025-01-01T00:00:00+00:00",
        "finished_at": "2025-01-01T01:00:00+00:00",
        "status": "completed",
        "plan_contract": {"must_include_capabilities": ["alignment", "variant_calling"]},
        "missing_tools_detected": [],
        "missing_reference_detected": [],
        "missing_sample_groups": [],
        "error": "",
        "fallback_selection": {"selection_reason": "highest_score"},
    }

    record_graph_outcome(
        path_graph=graph,
        run=run,
        persist_preference_updates=True,
        path_graph_user_key="user",
        path_graph_scope="global",
    )

    assert graph.path_runs[0]["path_id"] == "preferred_path"
    assert graph.preference_persists[0]["requested_capabilities"] == ["alignment", "variant_calling"]
