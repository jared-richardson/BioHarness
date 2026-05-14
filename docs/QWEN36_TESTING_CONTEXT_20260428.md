# Qwen 3.6 Testing Context - 2026-04-28

## Bottom Line

The current Qwen 3.6 release-candidate evidence is complete for the defined
24-case ablation manifest:

- `qwen_true_no_templates` strict stress configuration: 24/24 cases passed.
- `qwen_full` public/template-assisted configuration: 24/24 cases passed.
- No manifest cases were missing from either result set.
- No extra cases appeared in either result set.
- No case had a non-pass benchmark status in either result set.
- Both runs reported zero repairs, zero generic fallback, zero protocol
  fallback, and zero planner fail-open.

This is a release benchmark claim for the current 24-case manifest and current
model digest. It is not a claim that every possible future prompt, model digest,
or optional behavioral study has been exhausted.

## Plan-Adherence Summary

We did not lose sight of the release benchmark goal, but we did narrow execution
from "complete every part of the v5 fast-signal ladder" to "prove the current
Qwen 3.6 release candidate on the full 24-case manifest."

That distinction matters:

- The release evidence is strong and complete for the defined 24-case manifest.
- The v5 measurement infrastructure is only partially complete.
- Future launches should still respect the v5 gate policy, especially for cases
  or failure classes without reproduction-rate baselines.

The complete v5 plan is:

- `docs/FAST_SIGNAL_COMPLETION_PLAN_20260425.md`

Companion context files:

- `docs/FAST_SIGNAL_TEST_LADDER_REFINED.md`
- `docs/FAST_SIGNAL_IMPLEMENTATION.md`
- `docs/FAST_SIGNAL_TESTING_LEARNINGS_20260425.md`
- `docs/FAST_SIGNAL_LITERATURE_AND_NOVELTY_ASSESSMENT.md`
- `docs/FAST_SIGNAL_SPEED_OPTIMIZATIONS.md`
- `docs/QWEN36_R18_RELEASE_CANDIDATE_STATUS_20260428.md`
- `docs/QWEN36_TESTING_CONTEXT_20260428.md`

Open v5 gaps that remain after the release evidence:

1. The original Phase 0/B1 reproduction baseline for `exp42`, `exp43`, and
   `exp44` was not completed as planned. We have a 3-row pre-fix exp44 sample,
   plus post-fix `control_transcript` and `stress_noisy_evolution`
   measurements, but not the planned 10x exp42/exp43/exp44 baseline.
2. B3 baseline snapshot artifacts are absent or undocumented. A local file
   search did not find `docs/scorecard_snapshots/corpus_baseline_20260428.json`,
   `docs/scorecard_snapshots/phase7_triggers.txt`, or a compressed scorecard
   copy. That means future Phase 7 corpus-staleness automation still needs an
   anchor.
3. Most release cases have N=1 Qwen 3.6 evidence per variant. The full manifest
   passed under both variants, but most individual cases are not
   reproduction-rate calibrated.
4. Optional behavioral studies such as prompt-sensitivity ablation,
   defense-ablation, and cross-model idiom diff remain deferred.

Implemented v5 infrastructure worth noting:

- `bio_harness/core/failure_classes.py` provides the failure-class registry
  needed to avoid spelling drift in scorecard calibration.
- `docs/FAST_SIGNAL_IMPLEMENTATION.md` documents the A7 observability fixes:
  terminal `summary.md` rewrite on `_write_exit()`, structured
  `stepwise_rejected_candidates`, `STEPWISE_CANDIDATE_REJECTED` events, and
  `fixture_seed` payloads for replay extraction.
- `tests/core/test_release_gate.py` and
  `tests/core/test_stepwise_loop.py::test_stepwise_rejected_candidate_persists_fixture_seed`
  cover the policy gate and rejected-candidate persistence behavior.

## Model And Backend Identity

| Field | Value |
| --- | --- |
| Planner model | `qwen3.6:35b-a3b` |
| Planner digest | `07d35212591f` |
| Executor model | `qwen3-coder-next:latest` |
| Executor digest | `ca06e9e4087c` |
| Backend | `ollama-0.20.0` |
| Execution mode | `stepwise` |
| Release benchmark manifest | `workspace/benchmark_data/ablation_manifest_24.json` |
| Scorecard | `workspace/studies/scorecard.jsonl` |
| Latest scorecard snapshot | `docs/scorecard_snapshots/fast_signal_scorecard_latest.json` |

## Full 24-Case Release Runs

| Attempt | Variant | Role | Optimization profile | Status | Cases | Pass rate | Mean runtime seconds | Mean repairs | Generic fallback | Protocol fallback | Planner fail-open |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `qwen36_release_sentinel_r18b_20260428` | `qwen_true_no_templates` | strict hardening stress run | `release_sentinel_serial_keepalive` | completed | 24/24 | 1.000 | 300.0592 | 0.0 | 0.0 | 0.0 | 0.0 |
| `qwen36_release_public_full_r19_20260428` | `qwen_full` | public release confirmation | `release_public_full_serial_keepalive` | completed | 24/24 | 1.000 | 297.1881 | 0.0 | 0.0 | 0.0 | 0.0 |

Primary artifacts:

- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_sentinel_r18b_20260428/summary.json`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_sentinel_r18b_20260428/status.json`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_sentinel_r18b_20260428/results.jsonl`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/summary.json`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/status.json`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/results.jsonl`
- `docs/QWEN36_R18_RELEASE_CANDIDATE_STATUS_20260428.md`

Verification performed after the runs compared `ablation_manifest_24.json`
against both `results.jsonl` files using the actual result schema
(`task_name`, `status`). Both comparisons returned:

- `manifest_cases: 24`
- `result_cases: 24`
- `missing: []`
- `extra: []`
- `notPassed: []`

## Manifest Coverage

| Case | Band | `qwen_true_no_templates` | `qwen_full` | Notes |
| --- | ---: | --- | --- | --- |
| `control_evolution` | 1 | pass | pass | Primary artifact present. |
| `control_deseq` | 1 | pass | pass | Completed. |
| `control_germline` | 1 | pass | pass | Completed. |
| `control_transcript` | 1 | pass | pass | Completed. |
| `control_phylogenetics` | 1 | pass | pass | Completed. |
| `control_singlecell` | 1 | pass | pass | Completed. |
| `domain_longread_sv` | 2 | pass | pass | Primary artifact present. |
| `domain_longread_assembly` | 2 | pass | pass | Primary artifact present. |
| `domain_spatial` | 2 | pass | pass | Primary artifact present. |
| `domain_proteomics` | 2 | pass | pass | Primary artifact present. |
| `domain_metabolomics` | 2 | pass | pass | Primary artifact present. |
| `domain_metagenomics` | 2 | pass | pass | Completed. |
| `domain_viral_meta` | 2 | pass | pass | Completed. |
| `domain_variant_annot` | 2 | pass | pass | Completed. |
| `domain_alzheimer` | 2 | pass | pass | Completed. |
| `domain_cystic_fibrosis` | 2 | pass | pass | Completed. |
| `stress_longread_wrong_preset` | 3 | pass | pass | Primary artifact present. |
| `stress_proteomics_missing` | 3 | pass | pass | Primary artifact present. |
| `stress_metabolomics_missing` | 3 | pass | pass | Primary artifact present. |
| `stress_spatial_fragment` | 3 | pass | pass | Primary artifact present. |
| `stress_noisy_evolution` | 3 | pass | pass | Primary artifact present. |
| `stress_noisy_deseq` | 3 | pass | pass | Completed. |
| `stress_assembly_malformed` | 3 | pass | pass | Expected bad-input block. Correct behavior is not to fabricate an assembly. |
| `stress_germline_no_rg` | 3 | pass | pass | Completed. |

Band-level result for both release runs:

- Band 1: 6/6 passed.
- Band 2: 10/10 passed.
- Band 3: 8/8 passed.

## Critical Artifact Checks

The final release evidence specifically checked the branch-local evolution
artifacts that had been unstable earlier:

- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/qwen_full/control_evolution/selected/final/variants_shared.csv`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/qwen_full/stress_noisy_evolution/selected/variants/evol1.ancestor_subtracted.vcf.gz`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/qwen_full/stress_noisy_evolution/selected/variants/evol1.ancestor_subtracted.vcf.gz.tbi`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/qwen_full/stress_noisy_evolution/selected/variants/evol2.ancestor_subtracted.vcf.gz`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/qwen_full/stress_noisy_evolution/selected/variants/evol2.ancestor_subtracted.vcf.gz.tbi`
- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/qwen_full/stress_noisy_evolution/selected/final/variants_shared.csv`

The strict stress run also produced the corresponding branch-local artifacts
under:

- `workspace/ablation_results/domain_expansion_ablation/qwen36_release_sentinel_r18b_20260428/qwen_true_no_templates/`

## Fast Signal Fixtures And Replay Coverage

The curated fast-signal fixture library currently contains 32 fixtures:

- 4 `planner_shape` fixtures.
- 28 `candidate_gate` fixtures.

Fixture roots:

- `tests/fixtures/fast_signal/planner_shape`
- `tests/fixtures/fast_signal/candidate_gate`

Planner-shape fixtures present:

- `exp33_plan_key_shape.json`
- `exp40_empty_branch_id.json`
- `exp43_tabix_index_producer.json`
- `corpus_qwen36_top10_idioms.json`

Candidate-gate fixtures present:

- `corpus_qwen36_bare_filenames.json`
- `exp44_annotation_requires_gff_producer.json`
- `exp44_branch_frontier_isec.json`
- `exp44_branch_progress.json`
- `exp44_completed_prefix_restart_after_norm.json`
- `exp44_duplicate_branch.json`
- `exp44_isec_evol2_path_binding.json`
- `exp44_prokka_gff_snpeff_binding.json`
- `exp44_prokka_gff_snpeff_binding_empty_analysis_spec.json`
- `qwen36_alzheimer_bash_helper_missing_command.json`
- `qwen36_compiled_sv_minimap2_missing_args.json`
- `qwen36_flye_missing_required_args.json`
- `qwen36_metagenomics_missing_required_args.json`
- `qwen36_metagenomics_spades_missing_required_args.json`
- `qwen36_metabolomics_missing_required_args.json`
- `qwen36_minimap2_missing_required_args.json`
- `qwen36_phylogenetics_bash_helper_missing_command.json`
- `qwen36_proteomics_missing_required_args.json`
- `qwen36_sniffles_output_bam_alias.json`
- `qwen36_sniffles_rejects_raw_fastq_input_bam.json`
- `qwen36_snpeff_data_root_vcf_disambiguation.json`
- `qwen36_snpeff_local_reference_binding.json`
- `qwen36_spatial_missing_required_args.json`
- `qwen36_transcript_salmon_missing_required_args.json`
- `qwen36_viral_bash_helper_missing_command.json`
- `qwen36_viral_fastp_missing_required_args.json`
- `qwen36_viral_off_skeleton_alignment.json`
- `r17_norm_stale_args_evaluation.json`

Replay and fixture infrastructure are documented in:

- `docs/FAST_SIGNAL_IMPLEMENTATION.md`
- `scripts/replay_fast_signal_fixtures.py`
- `scripts/extract_fast_signal_fixtures.py`
- `scripts/summarize_fast_signal_plan_corpus.py`

## Focused Pytest Gates

The focused fast-signal gate was run after the strict release sentinel and again
after the public release confirmation:

```text
python3 -m pytest -q tests/core/test_fast_signal.py tests/core/test_stepwise_loop.py tests/core/test_release_gate.py
```

Results recorded in `docs/QWEN36_R18_RELEASE_CANDIDATE_STATUS_20260428.md`:

- After `qwen36_release_sentinel_r18b_20260428`: `127 passed, 5 warnings in 112.45s`.
- After `qwen36_release_public_full_r19_20260428`: `127 passed, 5 warnings in 98.92s`.

Warnings were SWIG/PyMuPDF deprecation warnings from imported dependencies, not
harness assertion failures.

Earlier in the exp44 fast-signal loop, the final focused local gate before the
single-case r18 pass was:

- `157 passed, 5 warnings`

That earlier loop is summarized in:

- `docs/FAST_SIGNAL_TESTING_LEARNINGS_20260425.md`

## Reproduction Measurements

### Pre-fix exp44 reproduction sample

Artifact:

- `workspace/studies/reproduction_rates.json`

Observed records:

- 3 exp44 reproduction rows.
- 2 same-class failures.
- 1 different-class failure.
- 0 passes.
- Same-class reproduction rate: `0.6666666666666666`.

Interpreted classes:

- `duplicate_detector_granularity`
- `branch_stage_progress`

This was a manual/scorecard-restored reproduction sample, not the full original
10x exp42/exp43/exp44 automated baseline.

### Post-fix control transcript reproduction

Artifact:

- `workspace/studies/reproduction_control_transcript_after_r17_20260428.json`

Result:

- 10/10 passed.
- Same-class reproduction rate after fix: `0.0`.
- Variant: `qwen_true_no_templates`.
- Planner model digest: `07d35212591f`.
- Optimization profile: `measurement_serial_keepalive`.

### Post-fix noisy evolution reproduction

Artifact:

- `workspace/studies/reproduction_stress_noisy_evolution_after_r17_20260428.json`

Result:

- 3/3 passed.
- Same-class reproduction rate after fix: `0.0`.
- Variant: `qwen_true_no_templates`.
- Planner model digest: `07d35212591f`.
- Optimization profile: `measurement_serial_keepalive`.

## Mini-Benchmark And Fast-Model Preflight

The mini-benchmark suite exists under:

- `workspace/benchmark_data/fast_signal_mini/manifest.json`
- `workspace/benchmark_data/fast_signal_mini/tasks/evolution`
- `workspace/benchmark_data/fast_signal_mini/tasks/germline-vc`
- `workspace/benchmark_data/fast_signal_mini/tasks/deseq`

The final green Qwen Coder mini-suite artifact is:

- `workspace/studies/fast_model_preflight_mini_suite_after_de_mini_green.json`

Final mini-suite result:

| Mini case | Analysis family | Model | Status | Contract checked |
| --- | --- | --- | --- | --- |
| `control_evolution_mini` | evolution | `qwen3-coder-next:latest` | pass | non-empty `final/variants_shared.csv` with `CHROM`, `POS` |
| `germline_vc_mini` | germline VC | `qwen3-coder-next:latest` | pass | non-empty `final/variants.vcf` or indexed `final/variants.vcf.gz` |
| `de_mini` | differential expression | `qwen3-coder-next:latest` | pass | non-empty `final/deseq_results.csv` with `gene_id`, `log2FoldChange`, `pvalue` |

Earlier mini-suite/preflight attempts are also preserved in `workspace/studies/`
and intentionally show defects that were found and fixed, including:

- SPAdes PHRED auto-detection failure on tiny synthetic reads.
- Evolution mini reference collapse causing empty Prodigal/SnpEff artifacts.
- DE mini output packaging/binding issues.
- FeatureCounts strandedness/profile issues.

These failed preflights were diagnostic, not final release claims.

There is also an advisory fast-model domain preflight artifact:

- `workspace/studies/sweep46_fast_model_domain24.json`

Despite the filename, that artifact records a Qwen Coder advisory domain
preflight for the `control_evolution` case, not a full Qwen 3.6 release
benchmark. The real release evidence is the two full 24-case Qwen 3.6 runs
listed above.

## Scorecard State

As of this context note, `workspace/studies/scorecard.jsonl` contains 107 rows.
Major gate counts observed from the scorecard:

- `reproduction`: 26 rows.
- `fast_model_preflight`: 27 rows.
- `live_sentinel`: 24 rows.
- `replay`: 6 rows.
- `qwen36_band2_case`: 4 rows.
- `fast_model_domain_case`: 3 rows.
- Several one-row unit or replay gates for branch-frontier, contract capability,
  annotation prerequisite, prefix freeze, and path-binding checks.

The latest generated scorecard snapshot is:

- `docs/scorecard_snapshots/fast_signal_scorecard_latest.json`

The generic snapshot `release_gate` section can report `wait` with
`no_reproduction_baseline` when invoked without launch-specific evidence. That
does not contradict the two completed 24-case release benchmark passes. It means
the generic gate machinery still wants explicit reproduction-baseline context
for arbitrary future launches.

## Earlier Exp44 Live Testing Loop

Before the full 24-case release runs, the exp44/control-evolution loop tested
the active failure class through fast replay and live sentinels:

- r16: prefix mutation rejection. The live run failed on a completed-prefix
  mutation class; replay then validated prefix immutability and candidate
  acceptance for the intended branch-local work.
- r17: stale `bcftools_norm_run` arguments at the frontier. The run cleared the
  r16 failure class and exposed stale assembly-style arguments on VCF
  normalization; replay validated frontier rebinding to the branch-local
  normalized VCF target.
- r18: single-case `control_evolution` pass under `qwen_true_no_templates`.

Detailed notes live in:

- `docs/FAST_SIGNAL_TESTING_LEARNINGS_20260425.md`

## Explicit Non-Claims

- We did not complete the original full Phase 2 600-emission Qwen 3.6
  plan-shape corpus as a release blocker.
- We did not complete the optional cross-model idiom diff across Qwen 3.5,
  Qwen 3.6, and Qwen Coder as a release blocker.
- The mini-benchmark suite is a fast advisory gate and contract smoke test; it
  is not a replacement for the full 24-case Qwen 3.6 release benchmark.
- `qwen_true_no_templates` remains a strict hardening stress configuration, not
  necessarily the public default.
- The release claim is tied to the current model digest `07d35212591f`, current
  backend `ollama-0.20.0`, and the current 24-case manifest.
- A green 24-case manifest is strong release-candidate evidence, not a proof of
  deterministic success for every future sample from the model.
- `exp42` and `exp43` do not have the planned 10x Phase 0 reproduction
  baselines in the current evidence record.
- Most of the 24 release cases have one Qwen 3.6 run per variant, not
  per-case reproduction-rate calibration.
- The B3 future-rerun baseline artifacts
  `corpus_baseline_20260428.json`, `phase7_triggers.txt`, and a compressed
  scorecard copy were not found under `docs/scorecard_snapshots/` when this
  note was written.

## Practical Handoff

If a future agent needs to verify the release benchmark claim, check these
artifacts first:

1. `docs/FAST_SIGNAL_COMPLETION_PLAN_20260425.md`
2. `workspace/benchmark_data/ablation_manifest_24.json`
3. `workspace/ablation_results/domain_expansion_ablation/qwen36_release_sentinel_r18b_20260428/summary.json`
4. `workspace/ablation_results/domain_expansion_ablation/qwen36_release_sentinel_r18b_20260428/status.json`
5. `workspace/ablation_results/domain_expansion_ablation/qwen36_release_sentinel_r18b_20260428/results.jsonl`
6. `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/summary.json`
7. `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/status.json`
8. `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/results.jsonl`
9. `docs/QWEN36_R18_RELEASE_CANDIDATE_STATUS_20260428.md`
10. `docs/FAST_SIGNAL_IMPLEMENTATION.md`
11. `bio_harness/core/failure_classes.py`
12. `workspace/studies/scorecard.jsonl`
13. `docs/scorecard_snapshots/fast_signal_scorecard_latest.json`

The concise answer is: yes, the current defined 24-case release benchmark was
fully benchmarked in both the strict stress and public release configurations.
The remaining work is ongoing characterization and future-regression coverage,
not a missing case in the current release benchmark manifest.
