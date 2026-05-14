# Evaluation Completed Checklist - 2026-05-01

This is the audit checklist for the current Qwen 3.6 and Gemma 4 26B
evaluation campaign. It is written for another model or reviewer to verify
what has been completed, where the evidence lives, and what remains explicitly
deferred.

## Bottom Line

The practical release/generalization evaluation list is complete:

- Qwen 3.6 passed the defined 24-case manifest under both strict
  `qwen_true_no_templates` and public/default `qwen_full` variants.
- Qwen 3.6 passed the post-mini-gate reruns under the same two full variants.
- Gemma `gemma4:26b` passed the defined 24-case manifest under both
  `gemma26_true_no_templates` and `gemma26_full`.
- The fast-signal ladder pieces needed for this campaign are green:
  reproduction baseline, adaptive corpus decision, prompt probes, mini
  preflight, scorecard snapshot, fixture replay, and focused pytest gates.

This is not a claim that every optional behavioral study has been exhausted.
The fixed 600-emission corpus, defense ablation, cross-model idiom diff, and
per-case multi-replicate reproduction for all 24 manifest cases remain
deferred measurement work.

## Full Benchmark Sweeps

| Model / Attempt | Variant | Cases | Band 1 | Band 2 | Band 3 | Failures | Evidence |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Qwen 3.6 release sentinel `qwen36_release_sentinel_r18b_20260428` | `qwen_true_no_templates` | 24/24 | 6/6 | 10/10 | 8/8 | 0 | `workspace/ablation_results/domain_expansion_ablation/qwen36_release_sentinel_r18b_20260428/summary.json` |
| Qwen 3.6 public release `qwen36_release_public_full_r19_20260428` | `qwen_full` | 24/24 | 6/6 | 10/10 | 8/8 | 0 | `workspace/ablation_results/domain_expansion_ablation/qwen36_release_public_full_r19_20260428/summary.json` |
| Qwen 3.6 post-mini gate `qwen36_post_mini_gate_true_no_templates_20260430` | `qwen_true_no_templates` | 24/24 | 6/6 | 10/10 | 8/8 | 0 | `workspace/ablation_results/domain_expansion_ablation/qwen36_post_mini_gate_true_no_templates_20260430/summary.json` |
| Qwen 3.6 post-mini gate `qwen36_post_mini_gate_public_full_20260430` | `qwen_full` | 24/24 | 6/6 | 10/10 | 8/8 | 0 | `workspace/ablation_results/domain_expansion_ablation/qwen36_post_mini_gate_public_full_20260430/summary.json` |
| Gemma final generalization `gemma26_final_generalization_20260501` | `gemma26_true_no_templates` | 24/24 | 6/6 | 10/10 | 8/8 | 0 | `workspace/ablation_results/domain_expansion_ablation/gemma26_final_generalization_20260501/summary.json` |
| Gemma final generalization `gemma26_final_generalization_20260501` | `gemma26_full` | 24/24 | 6/6 | 10/10 | 8/8 | 0 | `workspace/ablation_results/domain_expansion_ablation/gemma26_final_generalization_20260501/summary.json` |

The latest Gemma summary reports:

- `gemma26_true_no_templates`: pass rate 1.0, failures 0, mean repairs 0.0,
  generic fallback 0.0, protocol fallback 0.0, planner fail-open 0.0.
- `gemma26_full`: pass rate 1.0, failures 0, mean repairs 0.0, generic
  fallback 0.0, protocol fallback 0.0, planner fail-open 0.0.

## Manifest Coverage

The 24-case manifest was completed for the final Qwen and Gemma full sweeps:

| Case | Band | Final Qwen strict | Final Qwen full | Final Gemma strict | Final Gemma full |
| --- | ---: | --- | --- | --- | --- |
| `control_evolution` | 1 | pass | pass | pass | pass |
| `control_deseq` | 1 | pass | pass | pass | pass |
| `control_germline` | 1 | pass | pass | pass | pass |
| `control_transcript` | 1 | pass | pass | pass | pass |
| `control_phylogenetics` | 1 | pass | pass | pass | pass |
| `control_singlecell` | 1 | pass | pass | pass | pass |
| `domain_longread_sv` | 2 | pass | pass | pass | pass |
| `domain_longread_assembly` | 2 | pass | pass | pass | pass |
| `domain_spatial` | 2 | pass | pass | pass | pass |
| `domain_proteomics` | 2 | pass | pass | pass | pass |
| `domain_metabolomics` | 2 | pass | pass | pass | pass |
| `domain_metagenomics` | 2 | pass | pass | pass | pass |
| `domain_viral_meta` | 2 | pass | pass | pass | pass |
| `domain_variant_annot` | 2 | pass | pass | pass | pass |
| `domain_alzheimer` | 2 | pass | pass | pass | pass |
| `domain_cystic_fibrosis` | 2 | pass | pass | pass | pass |
| `stress_longread_wrong_preset` | 3 | pass | pass | pass | pass |
| `stress_proteomics_missing` | 3 | pass | pass | pass | pass |
| `stress_metabolomics_missing` | 3 | pass | pass | pass | pass |
| `stress_spatial_fragment` | 3 | pass | pass | pass | pass |
| `stress_noisy_evolution` | 3 | pass | pass | pass | pass |
| `stress_noisy_deseq` | 3 | pass | pass | pass | pass |
| `stress_assembly_malformed` | 3 | pass | pass | pass | pass |
| `stress_germline_no_rg` | 3 | pass | pass | pass | pass |

`stress_assembly_malformed` is expected to complete as an expected bad-input
block. It is a pass when the harness refuses to fabricate an assembly.

## Critical Artifact Checks

The latest Gemma sweep was checked for the artifacts most relevant to recent
branch/artifact fixes:

| Variant | Case | Artifact | Present |
| --- | --- | --- | --- |
| `gemma26_true_no_templates` | `control_evolution` | `selected/final/variants_shared.csv` | yes |
| `gemma26_true_no_templates` | `stress_noisy_evolution` | `selected/final/variants_shared.csv` | yes |
| `gemma26_true_no_templates` | `control_deseq` | `selected/final/deseq_results.csv` | yes |
| `gemma26_true_no_templates` | `control_deseq` | `selected/counts/gene_counts.txt` | yes |
| `gemma26_full` | `control_evolution` | `selected/final/variants_shared.csv` | yes |
| `gemma26_full` | `stress_noisy_evolution` | `selected/final/variants_shared.csv` | yes |
| `gemma26_full` | `control_deseq` | `selected/final/deseq_results.csv` | yes |
| `gemma26_full` | `control_deseq` | `selected/counts/gene_counts.txt` | yes |

The Qwen critical branch-local artifact checks are recorded in
`docs/QWEN36_TESTING_CONTEXT_20260428.md` and
`docs/QWEN36_POST_MINI_GATE_RESULTS_20260430.md`.

## Fast-Signal Ladder Completion

| Item | Status | Evidence |
| --- | --- | --- |
| A0 launch policy metadata | Complete enough for this campaign | Scorecard rows carry measurement purpose, override fields, optimization profile, model digest, and backend version. |
| A1 reproduction driver | Implemented | `scripts/run_fast_signal_reproduction_baseline.py`. |
| B1 exp42/exp43/exp44 reproduction baseline | Complete | `workspace/studies/reproduction_rates_b1_exp42_exp43_exp44_current_20260430.json`: 30 rows, all pass. |
| A2 fixture coverage accounting | Complete for current campaign | `docs/FAST_SIGNAL_FIXTURE_COVERAGE_20260430.md`; current library has 38 fixtures. |
| A3 prompt probes | Complete for planned branch-stage and forbidden-work probes | `workspace/studies/prompt_sensitivity_qwen36_a3_20260430_summary.json`: 40 paired emissions, both features changed 20/20 pairs. |
| A4 fast-model preflight | Implemented as advisory gate | `scripts/run_fast_model_preflight.py`. |
| A5 mini-benchmark suite | Complete and green | `control_evolution_mini`, `germline_vc_mini`, and `de_mini` pass contract-level preflight. |
| A6 scorecard calibration and gate | Implemented with canonical B1 cutover | `docs/FAST_SIGNAL_SCORECARD_CUTOVER_POLICY_20260501.md`; `docs/scorecard_snapshots/fast_signal_scorecard_latest.json`. |
| A7 reporting/observability | Implemented | Structured rejected-candidate persistence, fixture seed payloads, terminal summary rewrite, and tests. |
| A8 speed-safe controls | Implemented where safe | Keep-alive metadata, optimization profiles, shard/replicate IDs, resume support, and measurement controls. |
| B2 adaptive plan-shape corpus | Accepted for fixture seeding | `workspace/studies/plan_shape_corpus_qwen36_summary.json`: 30 emissions, 10 cases, 3 temperatures, 9 analysis types, top-10 coverage 0.90, parse success 29/30. |
| B3 baseline snapshots | Complete | `docs/scorecard_snapshots/corpus_baseline_20260501.json`, `docs/scorecard_snapshots/phase7_triggers.txt`, `docs/scorecard_snapshots/scorecard_20260501.jsonl.gz`, `docs/scorecard_snapshots/fast_signal_scorecard_latest.json`. |

The canonical B1 aggregate contains:

| Experiment | Rows | Passes | Same-class failures | Infra errors |
| --- | ---: | ---: | ---: | ---: |
| `exp42_current_release_abs_python` | 10 | 10 | 0 | 0 |
| `exp43_current_release_abs_python` | 10 | 10 | 0 | 0 |
| `exp44_after_parameter_profile_filter` | 10 | 10 | 0 | 0 |

The fixed 600-emission B2 corpus is explicitly deferred. The accepted adaptive
B2 corpus is the current decision for fixture seeding unless a Phase 7
staleness trigger fires.

## Fixture And Test Status

Current fixture count:

- `tests/fixtures/fast_signal/planner_shape`: 7 fixtures.
- `tests/fixtures/fast_signal/candidate_gate`: 31 fixtures.
- Total curated fast-signal fixtures: 38.

Latest replay:

- Command:
  `python3 scripts/replay_fast_signal_fixtures.py --jsonl workspace/studies/replay_after_gemma26_sweep_20260501.jsonl tests/fixtures/fast_signal`
- Result: 38 rows, 38 passed, 0 failed.

Latest focused pytest gate:

- Command:
  `pytest -q tests/core/test_fast_signal.py tests/core/test_fast_signal_fixture_metadata.py tests/core/test_release_gate.py tests/core/test_strict_artifact_binding.py -q`
- Result: passed; warnings were dependency deprecation warnings.

## Fixes Added During Gemma Generalization

The Gemma mini preflight found a generalized handoff issue:

- Gemma produced `evol1.annotated.vcf.gz` at the selected-dir root in an
  evolution prefix.
- The normalization binder expected `variants/evol1.annotated.vcf`.
- The missing-input gate correctly blocked the bad handoff.

The generalized fix:

- `bio_harness/core/strict_artifact_binding_variant_binders.py` now discovers
  existing branch-local annotated VCFs in canonical and observed locations:
  `variants/{branch}.annotated.vcf`,
  `variants/{branch}.annotated.vcf.gz`,
  `{branch}.annotated.vcf`, and
  `{branch}.annotated.vcf.gz`.
- New failure class:
  `annotated_vcf_handoff_binding` in
  `bio_harness/core/failure_classes.py`.
- New fixture:
  `tests/fixtures/fast_signal/candidate_gate/gemma26_evolution_root_annotated_norm_binding.json`.
- New unit test:
  `tests/core/test_strict_artifact_binding.py::test_bind_evolution_bcftools_norm_run_uses_existing_root_annotated_vcf`.

Validation after that fix:

- The new fixture failed before the fix and passed after the fix.
- `control_evolution_mini` passed under `gemma4:26b`.
- Remaining Gemma mini cases, `germline_vc_mini` and `de_mini`, passed.
- The full Gemma 24-case strict and full variants both passed 24/24.

## Main Context Files

- `docs/FAST_SIGNAL_COMPLETION_PLAN_20260425.md`
- `docs/FAST_SIGNAL_TEST_LADDER_REFINED.md`
- `docs/FAST_SIGNAL_IMPLEMENTATION.md`
- `docs/FAST_SIGNAL_FIXTURE_COVERAGE_20260430.md`
- `docs/FAST_SIGNAL_PLAN_SHAPE_CORPUS_DECISION_20260501.md`
- `docs/FAST_SIGNAL_SCORECARD_CUTOVER_POLICY_20260501.md`
- `docs/QWEN36_TESTING_CONTEXT_20260428.md`
- `docs/QWEN36_POST_MINI_GATE_RESULTS_20260430.md`
- `docs/GEMMA_MIRRORED_ABLATIONS_STABILIZATION_PLAN.md`

## Deferred / Non-Claims

These are not release blockers for the current completed evaluation, but they
are useful future measurement work:

- The original fixed 600-emission Qwen 3.6 corpus was not run. The adaptive
  30-emission corpus was accepted for fixture seeding.
- Defense-ablation study is still deferred.
- Cross-model idiom diff across Qwen 3.5, Qwen 3.6, Qwen Coder, Gemma, and
  future models is still deferred.
- Most 24-case manifest cases do not have 10x per-case reproduction baselines.
  B1 is complete for exp42/exp43/exp44 and targeted post-fix cases.
- The old Gemma mirrored-ablation plan contains lanes beyond the final two
  Gemma 24-case variants run here. Those older mirrored lanes are not required
  for the current final generalization claim.
- Speculative decoding equivalence checking from the speed plan was not run.
  It remains exploratory-only until an equivalence check proves it preserves
  the target model output distribution.
- B1/B2 pipelined concurrency from the speed plan was not recorded as a
  completed one-time check. Measurement quality does not depend on it for this
  campaign because B1 and B2 were completed/accepted without relying on that
  concurrency optimization.
- Tier 4 speed infrastructure, including multi-machine offload, remains
  deferred. It is an execution-speed option, not a validation requirement.
- The GeneBench LDL pilot described in `docs/GENEBENCH_PILOT_PLAN.md` is
  intentionally outside the scope of this Qwen/Gemma 24-case harness
  evaluation campaign.

## Quick Reviewer Commands

```bash
python3 - <<'PY'
import json
from pathlib import Path
for path in [
    Path("workspace/ablation_results/domain_expansion_ablation/qwen36_post_mini_gate_true_no_templates_20260430/summary.json"),
    Path("workspace/ablation_results/domain_expansion_ablation/qwen36_post_mini_gate_public_full_20260430/summary.json"),
    Path("workspace/ablation_results/domain_expansion_ablation/gemma26_final_generalization_20260501/summary.json"),
]:
    data = json.loads(path.read_text())
    print(path)
    for variant in data["variants"]:
        print(" ", variant["variant_id"], variant["passed"], "/", variant["count"], "failures=", variant["failures"])
PY
```

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path("workspace/studies/replay_after_gemma26_sweep_20260501.jsonl")
rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
print(len(rows), "fixtures;", sum(1 for row in rows if row["passed"]), "passed")
PY
```

```bash
pytest -q \
  tests/core/test_fast_signal.py \
  tests/core/test_fast_signal_fixture_metadata.py \
  tests/core/test_release_gate.py \
  tests/core/test_strict_artifact_binding.py -q
```
