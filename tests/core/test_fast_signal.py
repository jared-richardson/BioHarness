"""Regression tests for fast-signal fixture infrastructure."""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.core.fast_signal import (
    ReplayFixture,
    load_replay_fixture,
    load_replay_fixtures,
    plan_idiom_summary,
    run_planner_shape_replay,
)
from bio_harness.core.fast_signal_dry_run import run_scripted_candidate_gate_scenario
from bio_harness.core.fast_signal_minibench import (
    DEFAULT_MINI_BENCHMARK_CONTRACTS,
    validate_mini_benchmark_contract,
)
from bio_harness.core.fast_signal_prompt_sensitivity import measure_prompt_sensitivity
from bio_harness.core.fast_signal_scorecard import (
    ScorecardRow,
    compute_gate_effectiveness,
    summarize_reproduction_rates,
)
from bio_harness.core.fast_signal_stepwise import (
    run_candidate_evaluation_replay,
    run_candidate_gate_duplicate_replay,
    run_candidate_gate_replay,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "fast_signal"


def test_planner_shape_fixtures_replay_without_silent_corruption() -> None:
    fixtures = load_replay_fixtures(FIXTURE_ROOT / "planner_shape")

    assert {fixture.id for fixture in fixtures} >= {
        "exp33_plan_key_shape",
        "exp40_empty_branch_id",
        "exp43_tabix_index_producer",
        "corpus_qwen36_top10_idioms",
    }
    for fixture in fixtures:
        result = run_planner_shape_replay(fixture)
        assert result.passed, f"{fixture.id}: {result.reason}"


def test_planner_shape_replay_can_expect_parse_failure() -> None:
    fixture = ReplayFixture(
        schema_version=1,
        id="malformed_json",
        kind="planner_shape",
        raw_emission='{"workflow": [{"tool_name": "bash_run", "objective": "bad\ntext"}]}',
        expected_outcome={
            "passed": True,
            "expect_parse_failure": True,
        },
    )

    result = run_planner_shape_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["parsed_ok"] is False


def test_planner_shape_replay_checks_bare_path_count() -> None:
    fixture = ReplayFixture(
        schema_version=1,
        id="bare_paths",
        kind="planner_shape",
        raw_emission={
            "workflow": [
                {
                    "step_id": 1,
                    "tool_name": "bwa_mem_align",
                    "parameter_hints": {
                        "input_fastq": "reads.fastq",
                        "output_bam": "aligned.bam",
                    },
                }
            ]
        },
        expected_outcome={
            "passed": True,
            "min_steps": 1,
            "min_bare_paths": 2,
        },
    )

    result = run_planner_shape_replay(fixture)

    assert result.passed, result.reason


def test_plan_idiom_summary_from_parsed_payload_tracks_corpus_dimensions() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT / "planner_shape" / "corpus_qwen36_top10_idioms.json"
    )
    result = run_planner_shape_replay(fixture)
    idioms = result.observed["idioms"]

    assert idioms["top_level_step_key"] == "plan_outline"
    assert idioms["argument_forms"]["parameter_hints"] == 2
    assert idioms["path_styles"]["relative"] >= 1


def test_plan_idiom_summary_accepts_nested_workflow_steps_and_tool_alias() -> None:
    idioms = plan_idiom_summary(
        {
            "workflow": {
                "steps": [
                    {
                        "step_id": "salmon_quantification",
                        "tool": "salmon_quant",
                        "parameter_hints": "validateMappings=True",
                    }
                ]
            }
        }
    )

    assert idioms["top_level_step_key"] == "workflow"
    assert idioms["step_count"] == 1
    assert idioms["tool_names"] == ["salmon_quant"]


def test_candidate_gate_bare_filename_fixture_is_green() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT / "candidate_gate" / "corpus_qwen36_bare_filenames.json"
    )
    result = run_candidate_gate_duplicate_replay(fixture)

    assert result.passed, result.reason


def test_exp37_shared_export_before_evol2_fixture_rejects_missing_inputs() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "exp37_shared_export_before_evol2_chain.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["missing_inputs_rejected"] is True
    assert any(
        "annotated.normalized" in item for item in result.observed["missing_inputs"]
    )


def test_exp42_bcftools_filter_output_type_fixture_keeps_scalar_flag() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "exp42_bcftools_filter_output_type_scalar.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["bound_candidate_arguments"]["output_type"] == "z"


def test_exp44_candidate_gate_duplicate_branch_fixture_is_green() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT / "candidate_gate" / "exp44_duplicate_branch.json"
    )
    result = run_candidate_gate_duplicate_replay(fixture)

    assert result.passed, result.reason


def test_exp44_branch_progress_fixture_surfaces_next_frontier() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT / "candidate_gate" / "exp44_branch_progress.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["missing_inputs_rejected"] is True
    assert result.observed["branch_stage_progress"]["next_cell"] == {
        "branch_id": "ancestor",
        "stage": "filtered_vcf",
        "suggested_tool": "bcftools_filter_run",
    }


def test_exp44_branch_frontier_fixture_rejects_premature_annotation() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT / "candidate_gate" / "exp44_branch_frontier_isec.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["branch_stage_rejected"] is True
    assert result.observed["branch_stage_progress"]["next_cell"] == {
        "branch_id": "evol2",
        "stage": "ancestor_subtracted_vcf",
        "suggested_tool": "bcftools_isec_run",
    }


def test_exp44_isec_fixture_preserves_evol2_path_binding() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT / "candidate_gate" / "exp44_isec_evol2_path_binding.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["branch_stage_rejected"] is False
    assert "evol2.ancestor_subtracted.vcf.gz" in str(
        result.observed["bound_candidate_arguments"]["output_vcf"]
    )


def test_exp44_annotation_fixture_requires_gff_producer() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "exp44_annotation_requires_gff_producer.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["prerequisite_rejected"] is True
    assert result.observed["allowed_tool_names"] == [
        "prodigal_annotate",
        "prokka_annotate",
    ]


def test_exp44_prokka_gff_fixture_allows_snpeff_binding() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "exp44_prokka_gff_snpeff_binding.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["prerequisite_rejected"] is False
    assert result.observed["missing_inputs_rejected"] is False


def test_exp44_prokka_gff_fixture_recovers_empty_analysis_spec() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "exp44_prokka_gff_snpeff_binding_empty_analysis_spec.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["prerequisite_rejected"] is False
    assert result.observed["missing_inputs_rejected"] is False
    assert "annotation/ancestor.gff" in str(
        result.observed["bound_candidate_arguments"]["annotation_gff"]
    )


def test_exp44_completed_prefix_restart_fixture_surfaces_norm_frontier() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "exp44_completed_prefix_restart_after_norm.json"
    )
    result = run_candidate_gate_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["duplicate_rejected"] is True
    assert result.observed["branch_stage_rejected"] is True
    assert result.observed["branch_stage_progress"]["next_cell"] == {
        "branch_id": "evol2",
        "stage": "normalized_vcf",
        "suggested_tool": "bcftools_norm_run",
    }


def test_r17_norm_tail_fixture_replays_live_candidate_evaluator() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "r17_norm_stale_args_evaluation.json"
    )
    result = run_candidate_evaluation_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["accepted"] is True
    assert result.observed["accepted_branch_id"] == "evol1"
    assert result.observed["accepted_tool_name"] == "bcftools_norm_run"
    assert "evol1.annotated.normalized.vcf.gz" in str(
        result.observed["accepted_arguments"]["output_vcf"]
    )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "qwen36_minimap2_missing_required_args.json",
        "qwen36_compiled_sv_minimap2_missing_args.json",
        "qwen36_flye_missing_required_args.json",
        "qwen36_metagenomics_missing_required_args.json",
        "qwen36_metagenomics_spades_missing_required_args.json",
        "qwen36_spatial_missing_required_args.json",
        "qwen36_proteomics_missing_required_args.json",
        "qwen36_metabolomics_missing_required_args.json",
        "qwen36_transcript_salmon_missing_required_args.json",
        "qwen36_snpeff_data_root_vcf_disambiguation.json",
        "qwen36_snpeff_local_reference_binding.json",
        "qwen36_sniffles_output_bam_alias.json",
        "qwen36_alzheimer_bash_helper_missing_command.json",
        "qwen36_phylogenetics_bash_helper_missing_command.json",
        "qwen36_viral_bash_helper_missing_command.json",
        "qwen36_viral_fastp_missing_required_args.json",
    ],
)
def test_qwen36_missing_required_argument_fixtures_are_bound(
    fixture_name: str,
) -> None:
    fixture = load_replay_fixture(FIXTURE_ROOT / "candidate_gate" / fixture_name)
    result = run_candidate_evaluation_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["accepted"] is True


def test_qwen36_viral_off_skeleton_alignment_fixture_is_rejected() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "qwen36_viral_off_skeleton_alignment.json"
    )
    result = run_candidate_evaluation_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["accepted"] is False


def test_qwen36_sniffles_raw_fastq_input_bam_fixture_is_rejected() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "qwen36_sniffles_rejects_raw_fastq_input_bam.json"
    )
    result = run_candidate_evaluation_replay(fixture)

    assert result.passed, result.reason
    assert result.observed["accepted"] is False


def test_scripted_dry_run_runs_ordered_candidate_fixture_scenario() -> None:
    fixture = load_replay_fixture(
        FIXTURE_ROOT
        / "candidate_gate"
        / "r17_norm_stale_args_evaluation.json"
    )
    result = run_scripted_candidate_gate_scenario(
        scenario_id="r17_norm_tail",
        fixtures=[fixture],
    )

    assert result.passed, result.reason
    assert result.turn_results[0].observed["accepted"] is True


def test_scorecard_summarizes_reproduction_and_gate_effectiveness() -> None:
    rows = [
        ScorecardRow("exp44", "reproduction", "fail_same_class"),
        ScorecardRow("exp44", "reproduction", "pass"),
        ScorecardRow("exp44", "reproduction", "fail_different_class"),
        ScorecardRow(
            "exp44",
            "replay",
            "fail",
            full_run_status="fail_same_class",
            reproduction_rate=0.7,
        ),
        ScorecardRow(
            "exp43",
            "replay",
            "pass",
            full_run_status="pass",
            reproduction_rate=0.7,
        ),
    ]

    reproduction = summarize_reproduction_rates(rows)
    effectiveness = compute_gate_effectiveness(rows, min_observations=1)

    assert reproduction["exp44"]["total"] == 3
    assert reproduction["exp44"]["same_class_reproduction_rate"] == pytest.approx(1 / 3)
    assert effectiveness[0].gate == "replay"
    assert effectiveness[0].precision == pytest.approx(1.0)


def test_prompt_sensitivity_keeps_features_with_shape_delta() -> None:
    control = [
        {
            "plan": [
                {
                    "tool_name": "snpeff_annotate",
                    "objective": "Annotate evol1 before branch progress hint.",
                }
            ]
        }
    ]
    treatment = [
        {
            "plan": [
                {
                    "tool_name": "bwa_mem_align",
                    "branch_id": "evol2",
                    "objective": "Align evol2 after branch progress hint.",
                }
            ]
        }
    ]

    result = measure_prompt_sensitivity(
        feature_name="branch_stage_hint",
        control_payloads=control,
        treatment_payloads=treatment,
    )

    assert result.effect_size == pytest.approx(1.0)
    assert result.keep_feature is True


def test_mini_benchmark_contracts_are_contract_level(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    final_dir = selected / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "variants_shared.csv").write_text(
        "CHROM,POS,REF,ALT\nchr1,12,A,G\n",
        encoding="utf-8",
    )

    result = validate_mini_benchmark_contract(
        selected,
        DEFAULT_MINI_BENCHMARK_CONTRACTS["control_evolution_mini"],
    )

    assert result["passed"] is True
    assert not result["issues"]


def test_replay_fixture_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="invalid kind"):
        ReplayFixture.from_mapping(
            {
                "schema_version": 1,
                "id": "bad_kind",
                "kind": "unsupported",
            }
        )
