"""Tests for the fast-signal plan-shape corpus runner."""

from __future__ import annotations

import json

from bio_harness.core.domain_expansion_ablation import DomainExpansionCase
from bio_harness.core.fast_signal import plan_idiom_key
from scripts.run_fast_signal_plan_shape_corpus import (
    append_jsonl,
    build_sample_grid,
    summarize_rows,
)


def test_build_sample_grid_uses_stable_ids_and_selected_dirs(tmp_path) -> None:
    case = DomainExpansionCase(
        case_id="control_evolution",
        band=1,
        data_root="/tmp/data",
        prompt_file="/tmp/prompt.txt",
        expected_outcome="completed",
    )

    samples = build_sample_grid(
        cases=[case],
        temperatures=(0.0, 0.3),
        repetitions=2,
        selected_root=tmp_path,
    )

    assert [sample.emission_id for sample in samples] == [
        "control_evolution.t0p0.r01",
        "control_evolution.t0p0.r02",
        "control_evolution.t0p3.r01",
        "control_evolution.t0p3.r02",
    ]
    assert samples[0].selected_dir == tmp_path / "control_evolution" / "t0p0" / "rep_01"


def test_build_sample_grid_honors_smoke_cap(tmp_path) -> None:
    case = DomainExpansionCase(
        case_id="control_deseq",
        band=1,
        data_root="/tmp/data",
        prompt_file="/tmp/prompt.txt",
        expected_outcome="completed",
    )

    samples = build_sample_grid(
        cases=[case],
        temperatures=(0.0, 0.3, 0.7),
        repetitions=20,
        selected_root=tmp_path,
        max_emissions=3,
    )

    assert len(samples) == 3
    assert samples[-1].emission_id == "control_deseq.t0p0.r03"


def test_summarize_rows_tracks_top_idiom_coverage() -> None:
    rows = [
        {"case_id": "a", "temperature": 0.0, "parsed_ok": True, "idiom_key": "x"},
        {"case_id": "a", "temperature": 0.3, "parsed_ok": True, "idiom_key": "x"},
        {"case_id": "b", "temperature": 0.0, "parsed_ok": False, "idiom_key": "bad"},
    ]

    summary = summarize_rows(rows, top_n=1)

    assert summary["emission_count"] == 3
    assert summary["parsed_count"] == 2
    assert summary["unparseable_count"] == 1
    assert summary["top_coverage"] == 2 / 3
    assert summary["sufficient_for_fixture_seeding"] is False


def test_append_jsonl_preserves_corpus_rows(tmp_path) -> None:
    output = tmp_path / "corpus.jsonl"

    append_jsonl(output, {"emission_id": "a", "idiom_key": "x"})
    append_jsonl(output, {"emission_id": "b", "idiom_key": "y"})

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {"emission_id": "a", "idiom_key": "x"},
        {"emission_id": "b", "idiom_key": "y"},
    ]


def test_plan_idiom_key_matches_corpus_bucket_dimensions() -> None:
    key = plan_idiom_key(
        {
            "top_level_step_key": "workflow",
            "step_count": 2,
            "path_styles": {"absolute": 1, "relative": 2, "bare": 3},
            "argument_forms": {"arguments": 4, "parameter_hints": 5},
            "branch_styles": {"branch_id": 6, "sample_name": 7},
        }
    )

    assert key == (
        "step_key=workflow|steps=2|abs=1|rel=2|bare=3|"
        "args=4|hints=5|branch=6|sample=7"
    )
