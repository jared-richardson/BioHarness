# Fast Signal Fixture Coverage Map, 2026-04-30

This is the A2 accounting pass for the Qwen 3.6 fix stack. It maps fixes #15
through #28 to the durable fast-signal fixture library and names remaining
non-fixture regression coverage.

Scope notes:

- "Fast-signal fixture" means a curated replay fixture under
  `tests/fixtures/fast_signal/`.
- "Other regression coverage" means focused unit or pipeline tests that cover
  the same behavior without using the replay fixture harness.
- A fixture gap does not mean the behavior is untested. It means the behavior
  is not yet represented as a replay/dry-run/mini-bench fixture.
- The current fixture library has 37 curated fixtures.

## Coverage Table

| Fix | Behavior | Fast-signal fixture coverage | Other regression coverage | Status / next action |
| --- | --- | --- | --- | --- |
| #15 | Accept alternate planner workflow keys such as `plan`, `steps`, and `plan_outline`. | `planner_shape/exp33_plan_key_shape.json`; generalized by `planner_shape/corpus_qwen36_nested_workflow_steps.json`. | `tests/core/test_hierarchical_planning.py`; `tests/core/test_fast_signal.py`. | Covered. |
| #16 | Prefer explicit `branch_id` over objective text in BWA/FreeBayes evolution binders. | No dedicated replay fixture. | `tests/core/test_strict_artifact_binding.py::test_bind_evolution_bwa_prefers_branch_id_over_objective_mentioning_ancestor_fix_16`; corresponding FreeBayes and ancestor fallback tests. | Unit-covered; optional replay fixture if this class reappears. |
| #17 | Strict binding for typed `bcftools_filter_run` and `bcftools_isec_run` wrappers. | Partially covered by `candidate_gate/exp44_isec_evol2_path_binding.json` and `candidate_gate/exp44_branch_frontier_isec.json`. | `tests/core/test_strict_artifact_binding.py::test_bind_evolution_bcftools_filter_run_rebinds_branch_paths_fix_17`; `test_bind_evolution_bcftools_isec_run_rebinds_branch_paths_fix_17`. | Covered, with stronger isec replay coverage than filter replay coverage. |
| #18a | Qualify ambiguous FreeBayes filter fields such as `AO`. | No replay fixture; this is wrapper-expression behavior. | `tests/pipeline_scripts/test_run_bcftools_filter.py::test_run_bcftools_filter_qualifies_single_sample_ambiguous_dp` and related validator tests. | Unit/pipeline-covered; no replay fixture needed unless the planner starts emitting this expression class again. |
| #18b | Evolution evaluator requires the primary `variants_shared.csv` artifact. | No replay fixture; this is benchmark evaluator logic. | `tests/core/test_domain_expansion_ablation.py` primary-artifact pass/fail tests. | Covered outside replay. |
| #19 | Add `shared_variant_export` capability and avoid premature done. | `candidate_gate/exp37_shared_export_before_evol2_chain.json` covers the aggregator-before-producer failure mode. | `tests/core/test_contract_inference.py::test_evolution_plan_with_shared_variants_export_run_passes_contract_fix_19`; protocol-grounding and planner-selection tests. | Covered. |
| #20 | Preserve `--multiallelic-mode -any` by using argparse-safe spelling. | No replay fixture; this is wrapper CLI behavior. | `tests/core/test_strict_artifact_binding.py` asserts canonical `multiallelic_mode`; wrapper behavior lives in `bio_harness/pipeline_scripts/run_bcftools_norm.py`. | Partial; add a direct pipeline-script test if this wrapper changes again. |
| #21 | Bind empty-branch `bcftools_isec_run` from objective fallback. | Covered by later exp44 isec/frontier fixtures: `candidate_gate/exp44_isec_evol2_path_binding.json`, `candidate_gate/exp44_branch_frontier_isec.json`. | `tests/core/test_strict_artifact_binding.py::test_bind_evolution_bcftools_isec_run_empty_args_empty_branch_id_fix_21` and objective/override guard tests. | Covered. |
| #22a | Strict binding for `shared_variants_export_run`. | `candidate_gate/exp37_shared_export_before_evol2_chain.json` exercises shared-export candidate binding through the missing-input guard. | `tests/core/test_strict_artifact_binding.py::test_bind_evolution_shared_variants_export_run_binds_canonical_paths_fix_22a`. | Covered. |
| #22b | Reject candidates whose absolute declared inputs do not exist and are not scheduled. | `candidate_gate/exp37_shared_export_before_evol2_chain.json` covers the original aggregator-before-producer rejection; `candidate_gate/corpus_qwen36_bare_filenames.json` covers the post-exp38 bare-filename refinement. | `tests/core/test_stepwise_loop.py` Fix #22b missing-input tests, including scheduled-output and bare-relative guards. | Covered. |
| #23 | Empty `requested_data_root` must not collapse to cwd and corrupt paths. | No dedicated replay fixture. | `tests/core/test_strict_artifact_binding.py::test_bind_evolution_empty_requested_data_root_does_not_corrupt_paths_fix_23`. | Unit-covered; replay fixture optional. |
| #24 | Empty `branch_id` objective fallback for BWA, FreeBayes, filter, and SnpEff. | `planner_shape/exp40_empty_branch_id.json` captures the planner-shape trigger. Later candidate fixtures exercise branch-local binding. | `tests/core/test_strict_artifact_binding.py` Fix #24 objective-fallback tests for BWA, FreeBayes, filter, and SnpEff. | Covered. |
| #25 | Canonical evolution BAM path uses `{slug}_aligned.bam`. | No dedicated replay fixture. | `tests/core/test_plan_repair_evolution.py::test_canonical_evolution_bam_path_matches_strict_binder_convention_fix_25`. | Unit-covered; replay fixture optional. |
| #26 | Honor `type: string` before name-based path heuristics such as `output_type`. | `candidate_gate/exp42_bcftools_filter_output_type_scalar.json` proves `bcftools_filter_run.output_type` remains exactly `z` after candidate binding. | `tests/core/test_tool_registry.py::test_fix_26_output_type_string_param_not_classified_as_output_path`; `test_fix_26_scalar_type_overrides_name_heuristic`. | Covered. |
| #27 | Auto-tabix bgzipped VCF producer/consumer artifacts. | `planner_shape/exp43_tabix_index_producer.json` captures the missing-index trigger. | `tests/pipeline_scripts/test_run_bcftools_filter.py` Fix #27 producer tests; `tests/pipeline_scripts/test_run_bcftools_isec.py` Fix #27 consumer tests. | Covered. |
| #28 | Duplicate detector granularity: sample-aware branch work is not a duplicate. | `candidate_gate/exp44_duplicate_branch.json`; reinforced by `candidate_gate/exp44_work_masking.json` if/when added. | `tests/core/test_fast_signal.py` candidate-gate replay tests. | Covered for the known exp44 duplicate class; `exp44_work_masking` remains an optional fixture gap if not present locally. |

## Newer Fixtures Outside The #15-#28 Stack

Several 2026-04-30 fixtures cover later release hardening rather than the
original #15-#28 loop:

- Branch-frontier and annotation prerequisites:
  `exp44_branch_progress`, `exp44_branch_frontier_isec`,
  `exp44_completed_prefix_restart_after_norm`,
  `exp44_annotation_requires_gff_producer`, and the Prokka/SnpEff binding
  fixtures.
- Qwen 3.6 post-release direct-wrapper argument binding:
  `qwen36_*_missing_required_args`, `qwen36_*_bash_helper_missing_command`,
  SnpEff local-reference fixtures, Sniffles input-role fixtures, and
  off-skeleton compiled-pipeline fixtures.
- B2 corpus idioms:
  `corpus_qwen36_top10_idioms`,
  `corpus_qwen36_nested_workflow_steps`,
  `corpus_qwen36_evolution_bare_filename_hints`, and
  `corpus_qwen36_noisy_evolution_malformed_json`.

## Remaining A2 Gaps

- The library does not currently contain dedicated replay fixtures for #16,
  #18a, #18b, #20, #23, or #25. These are covered by focused
  tests, but not by replay fixtures.
- The highest-value optional additions would be:
  1. A branch-id precedence candidate fixture for #16, if this class recurs
     outside strict-binder unit tests.
  2. A canonical BAM candidate fixture for #25, if repair/binder path
     divergence reappears.
  3. Wrapper/evaluator-only coverage can stay in unit/pipeline tests unless
     those classes recur as live LLM-output failures.
- The lower-value gaps are wrapper/evaluator-only behaviors (#18a, #18b, #20);
  keeping those in unit/pipeline tests is acceptable unless they reappear as
  LLM-output distribution failures.
