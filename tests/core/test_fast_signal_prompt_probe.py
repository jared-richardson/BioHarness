"""Tests for the fast-signal prompt-probe runner."""

from __future__ import annotations

import pytest

from bio_harness.core.domain_expansion_ablation import DomainExpansionCase
from scripts.run_fast_signal_prompt_probe import (
    build_probe_pairs,
    feature_prompt_extra,
    summarize_probe_rows,
)


def test_build_probe_pairs_uses_stable_ids_and_selected_dirs(tmp_path) -> None:
    case = DomainExpansionCase(
        case_id="control_evolution",
        band=1,
        data_root="/tmp/data",
        prompt_file="/tmp/prompt.txt",
        expected_outcome="completed",
    )

    pairs = build_probe_pairs(
        cases=[case],
        features=("branch_stage_hint", "forbidden_work_wording"),
        temperatures=(0.3,),
        repetitions=2,
        selected_root=tmp_path,
    )

    assert [pair.probe_id for pair in pairs] == [
        "branch_stage_hint.control_evolution.t0p3.r01",
        "branch_stage_hint.control_evolution.t0p3.r02",
        "forbidden_work_wording.control_evolution.t0p3.r01",
        "forbidden_work_wording.control_evolution.t0p3.r02",
    ]
    assert pairs[0].selected_dir == (
        tmp_path
        / "branch_stage_hint"
        / "control_evolution"
        / "t0p3"
        / "rep_01"
    )


def test_build_probe_pairs_honors_smoke_cap(tmp_path) -> None:
    case = DomainExpansionCase(
        case_id="control_deseq",
        band=1,
        data_root="/tmp/data",
        prompt_file="/tmp/prompt.txt",
        expected_outcome="completed",
    )

    pairs = build_probe_pairs(
        cases=[case],
        features=("branch_stage_hint", "forbidden_work_wording"),
        temperatures=(0.3,),
        repetitions=20,
        selected_root=tmp_path,
        max_pairs=3,
    )

    assert len(pairs) == 3
    assert pairs[-1].probe_id == "branch_stage_hint.control_deseq.t0p3.r03"


def test_feature_prompt_extra_names_expected_constraints() -> None:
    branch_hint = feature_prompt_extra(
        feature_name="branch_stage_hint",
        variant="treatment",
    )
    forbidden = feature_prompt_extra(
        feature_name="forbidden_work_wording",
        variant="treatment",
    )

    assert "Current next incomplete cell" in branch_hint
    assert "`evol2`" in branch_hint
    assert "`bwa_mem_align`" in branch_hint
    assert "Forbidden repeated work" in forbidden
    assert "identical arguments" in forbidden


def test_feature_prompt_extra_rejects_unknown_feature() -> None:
    with pytest.raises(ValueError, match="Unknown prompt-probe feature"):
        feature_prompt_extra(feature_name="unknown", variant="control")


def test_summarize_probe_rows_measures_changed_pairs() -> None:
    rows = [
        {
            "feature_name": "branch_stage_hint",
            "case_id": "control_evolution",
            "temperature": 0.3,
            "control": {
                "parsed_ok": True,
                "raw_emission": {
                    "workflow": [{"tool_name": "snpeff_annotate"}],
                },
            },
            "treatment": {
                "parsed_ok": True,
                "raw_emission": {
                    "workflow": [
                        {
                            "tool_name": "bwa_mem_align",
                            "branch_id": "evol2",
                        }
                    ],
                },
            },
        },
        {
            "feature_name": "branch_stage_hint",
            "case_id": "control_evolution",
            "temperature": 0.3,
            "control": {
                "parsed_ok": True,
                "raw_emission": {
                    "workflow": [{"tool_name": "bwa_mem_align"}],
                },
            },
            "treatment": {
                "parsed_ok": True,
                "raw_emission": {
                    "workflow": [{"tool_name": "bwa_mem_align"}],
                },
            },
        },
    ]

    summary = summarize_probe_rows(rows, keep_threshold=0.15)

    assert summary["pair_count"] == 2
    assert summary["feature_count"] == 1
    feature = summary["features"][0]
    assert feature["feature_name"] == "branch_stage_hint"
    assert feature["pairs_compared"] == 2
    assert feature["changed_pairs"] == 1
    assert feature["effect_size"] == 0.5
    assert feature["keep_feature"] is True
