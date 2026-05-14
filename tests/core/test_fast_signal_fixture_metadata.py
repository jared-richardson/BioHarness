"""Tests for fast-signal fixture metadata and deduplication."""

from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.fast_signal import (
    ReplayFixture,
    analysis_family_for_type,
    build_planner_shape_fixture,
    load_replay_fixtures,
    write_replay_fixture,
)
from scripts.extract_fast_signal_fixtures import _existing_signature_hashes


def test_replay_fixture_populates_registry_and_signature_metadata() -> None:
    fixture = ReplayFixture.from_mapping(
        {
            "schema_version": 1,
            "id": "fixture",
            "kind": "planner_shape",
            "analysis_type": "bacterial_evolution_variant_calling",
            "failure_class": "prokka_gff_binding",
            "raw_emission": {"plan": [{"tool_name": "prokka_annotate"}]},
        }
    )

    assert fixture.analysis_family == "evolution"
    assert fixture.failure_class_id == "prokka_gff_binding"
    assert len(fixture.fixture_signature_hash) == 64


def test_analysis_family_helper_groups_common_analysis_types() -> None:
    assert analysis_family_for_type("bacterial_evolution_variant_calling") == "evolution"
    assert analysis_family_for_type("germline_variant_calling") == "germline_vc"
    assert analysis_family_for_type("rna_seq_differential_expression") == "de"


def test_build_planner_shape_fixture_carries_tags_and_digest(tmp_path: Path) -> None:
    raw = tmp_path / "raw.txt"
    raw.write_text('{"plan":[{"tool_name":"bwa_mem_align"}]}', encoding="utf-8")
    trace = {
        "raw_content_file": str(raw),
        "model_name": "qwen3.6:35b-a3b",
        "model_digest": "digest",
        "backend_version": "ollama-test",
    }

    fixture = build_planner_shape_fixture(
        fixture_id="fixture",
        source_run="run",
        trace_payload=trace,
        run_dir=tmp_path,
        analysis_type="bacterial_evolution_variant_calling",
        failure_class="branch_stage_progress",
        tags=["analysis_family:evolution"],
    )

    assert fixture.captured_against_model_digest == "digest"
    assert fixture.backend_version == "ollama-test"
    assert fixture.failure_class_id == "branch_stage_progress"
    assert "analysis_family:evolution" in fixture.tags


def test_existing_signature_hashes_supports_extractor_dedup(tmp_path: Path) -> None:
    fixture = ReplayFixture.from_mapping(
        {
            "schema_version": 1,
            "id": "fixture",
            "kind": "planner_shape",
            "analysis_type": "bacterial_evolution_variant_calling",
            "raw_emission": {"plan": [{"tool_name": "bwa_mem_align"}]},
        }
    )
    write_replay_fixture(fixture, tmp_path / "fixture.json")

    signatures = _existing_signature_hashes(tmp_path)

    assert signatures == {fixture.fixture_signature_hash}
    loaded = load_replay_fixtures(tmp_path)
    assert loaded[0].fixture_signature_hash == fixture.fixture_signature_hash


def test_written_fixture_includes_new_metadata_fields(tmp_path: Path) -> None:
    fixture = ReplayFixture.from_mapping(
        {
            "schema_version": 1,
            "id": "fixture",
            "kind": "planner_shape",
            "analysis_type": "bacterial_evolution_variant_calling",
            "raw_emission": {"plan": [{"tool_name": "bwa_mem_align"}]},
        }
    )
    path = tmp_path / "fixture.json"
    write_replay_fixture(fixture, path)

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["analysis_family"] == "evolution"
    assert "fixture_signature_hash" in payload
    assert "failure_class_id" in payload
