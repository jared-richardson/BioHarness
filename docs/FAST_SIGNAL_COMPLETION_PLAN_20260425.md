# Fast-Signal Completion Plan - Close The Ladder Before More Long Sentinels

**Status:** Living v6 tracker; release evidence updated 2026-04-30
**Date:** 2026-04-25
**Owner:** Bio-Harness team
**Companion docs:**

- `FAST_SIGNAL_TEST_LADDER_REFINED.md` - original ladder
- `FAST_SIGNAL_IMPLEMENTATION.md` - as-built partial implementation
- `FAST_SIGNAL_TESTING_LEARNINGS_20260425.md` - post-r18 evidence record
- `FAST_SIGNAL_LITERATURE_AND_NOVELTY_ASSESSMENT.md` - literature and novelty audit
- `FAST_SIGNAL_SPEED_OPTIMIZATIONS.md` - speed-safe execution options and guardrails
- `QWEN36_R18_RELEASE_CANDIDATE_STATUS_20260428.md` - full 24-case release-candidate evidence
- `QWEN36_TESTING_CONTEXT_20260428.md` - current testing context and non-claims
- `QWEN36_POST_MINI_GATE_RESULTS_20260430.md` - post-mini-gate Qwen 3.6 validation evidence
- `EVALUATION_COMPLETED_CHECKLIST_20260501.md` - current completed-evaluation
  checklist for Qwen 3.6, Gemma 4 26B, fast-signal gates, and deferred
  non-claims

---

## 0. TL;DR

The fast-signal loop worked tactically: r16 and r17 exposed real harness bugs,
we converted them into fast replay/unit checks, r18 completed
`control_evolution` under `qwen_true_no_templates` with Qwen 3.6 planning, and
the post-mini-gate 2026-04-30 validation completed another Qwen 3.6 sentinel
plus both full 24-case sweeps at 24/24.

The strategic problem is narrower now: release benchmark evidence is strong,
but the measurement ladder still needs the remaining calibration artifacts
before future harness changes can be treated as scorecard-calibrated by
default. The scorecard is implemented, but B1 reconciliation and B2 corpus
evidence determine how trustworthy future `go` decisions are.

This plan completes the missing ladder pieces in dependency order. It avoids
calendar estimates on purpose. Work proceeds as quickly as dependencies allow.

The core rule:

```text
No new long Qwen 3.6 sentinel launches unless release_gate_status() == "go",
or the run is explicitly marked as a measurement override with an audit reason.
```

Measurement runs are allowed when they exist to calibrate the gate itself, such
as reproduction-baseline replicates. They must be labeled as measurement, not as
validation.

---

## 0.1. Current Completion Tracker - 2026-04-28

This tracker records what has actually been completed since the original v5
draft. It intentionally separates release evidence from ladder completion.

Release benchmark evidence is green:

- `qwen36_release_sentinel_r18b_20260428` passed all 24 manifest cases under
  `qwen_true_no_templates`.
- `qwen36_release_public_full_r19_20260428` passed all 24 manifest cases under
  `qwen_full`.
- Both release runs were manifest-verified: 24 expected cases, 24 result rows,
  no missing cases, no extra cases, no non-pass benchmark statuses.
- Both release runs reported zero repairs, zero generic fallback, zero protocol
  fallback, and zero planner fail-open.
- Focused fast-signal pytest gates passed after both release runs:
  `tests/core/test_fast_signal.py`, `tests/core/test_stepwise_loop.py`, and
  `tests/core/test_release_gate.py` reported `127 passed`.

Completed or substantially implemented ladder pieces:

| Item | Current Status | Evidence / Artifact |
| --- | --- | --- |
| A0 - launch policy metadata | Substantially implemented | Scorecard rows now carry measurement purpose, override fields, optimization profile, model digest, and backend version. |
| A1 - reproduction driver | Implemented as tooling; study incomplete | `scripts/run_fast_signal_reproduction_baseline.py`; reproduction rows record replicate IDs, shard IDs, keep-alive, optimization profile, and override metadata. |
| A2 - historical fixture expansion | Partial but materially expanded | Fixture library is now 32 fixtures: 4 `planner_shape`, 28 `candidate_gate`. |
| A3 - scripted dry-run scenarios | Partial | `bio_harness/core/fast_signal_dry_run.py`; r17 normalization-tail candidate evaluation fixture exists. Prompt probes are not complete as release evidence. |
| A4 - fast-model preflight | Implemented as advisory gate | `scripts/run_fast_model_preflight.py`; final green mini-suite artifact at `workspace/studies/fast_model_preflight_mini_suite_after_de_mini_green.json`. |
| A5 - mini-benchmark suite | Implemented and green | `control_evolution_mini`, `germline_vc_mini`, and `de_mini` exist and pass contract-level Qwen Coder preflight. |
| A6 - scorecard gate implementation | Implemented but not fully calibrated | `scripts/fast_signal_scorecard.py`, `bio_harness/core/failure_classes.py`, `tests/core/test_release_gate.py`, and `docs/scorecard_snapshots/fast_signal_scorecard_latest.json`. Missing B1/B2 calibration evidence still causes `wait` for generic future launches. |
| A7 - reporting and observability | Implemented | `summary.md` rewrite on terminal `_write_exit()`, structured `stepwise_rejected_candidates`, `STEPWISE_CANDIDATE_REJECTED` events, and `fixture_seed` payloads are documented in `FAST_SIGNAL_IMPLEMENTATION.md` and covered by tests. |
| A8 - speed-safe controls | Partial/substantial | `optimization_profile`, long keep-alive metadata, resume support, shard IDs, and `OLLAMA_NUM_PARALLEL` measurement controls are present. Riskier methodology changes remain deferred. |

Measurement and study status:

| Study | Current Status | Evidence / Gap |
| --- | --- | --- |
| B1 - exp42/exp43/exp44 reproduction baseline | In progress | `exp44` has a 10/10 current-harness post-fix sample at `workspace/studies/reproduction_rates_exp44_after_parameter_profile_filter.json`. The exp42/exp43 10x measurement run is active in detached screen session `bioharness_b1_exp42_exp43_20260428`; corrected output target is `workspace/studies/reproduction_rates_exp42_exp43_current_release_abs_python_20260428.json`. |
| Post-fix transcript reproduction | Complete for touched class | `workspace/studies/reproduction_control_transcript_after_r17_20260428.json`: 10/10 pass, same-class reproduction 0.0. |
| Post-fix noisy evolution reproduction | Partial but useful | `workspace/studies/reproduction_stress_noisy_evolution_after_r17_20260428.json`: 3/3 pass, same-class reproduction 0.0. |
| B2 - plan-shape corpus | Seed summary complete; full sampler still open | `workspace/studies/plan_shape_corpus_summary_20260428.json` summarizes the existing 4 planner fixtures with top-10 coverage 1.0. This is not the planned 600-emission Qwen 3.6 corpus and should not be treated as complete B2 evidence. |
| B3 - baseline snapshot for future reruns | Complete as baseline anchor | `docs/scorecard_snapshots/corpus_baseline_20260428.json`, `docs/scorecard_snapshots/phase7_triggers.txt`, `docs/scorecard_snapshots/scorecard_20260428.jsonl.gz`, and `docs/scorecard_snapshots/fast_signal_scorecard_latest.json` now exist. The baseline explicitly records that the full B2 corpus is still incomplete. |
| Cross-model idiom diff | Deferred | Still tracked as optional portability evidence. |

Operational interpretation:

- The current release benchmark claim is valid for the 24-case manifest and the
  current Qwen 3.6 digest.
- The v5 ladder should still be completed before treating future harness
  changes as calibrated by default.
- Future launches for uncalibrated cases or touched failure families should
  receive `wait` unless explicitly marked as measurement overrides.

Next steps to complete the plan:

1. Monitor and complete B1 for the original plan scope: run or explicitly replace the planned
   exp42/exp43/exp44 reproduction baseline. If exp42/exp43 are no longer the
   right case labels, document their current artifact equivalents before
   sampling.
2. Close B2 with an adaptive plan-shape corpus: run planner-only Qwen 3.6
   emissions until top-10 idiom coverage reaches >= 80%, or document the tail
   risk and keep affected launch categories at `wait`.
3. Tighten A2 coverage accounting: map the 32 fixtures to fixes #15-#28 and
   add explicit "no fixture exists because..." notes for any uncovered fix
   class.
4. Finish A3 prompt probes: run branch-stage hint and forbidden-work wording
   probes, keep only prompt features with at least a 15% absolute emission-shape
   delta, and record non-results as evidence.
5. Verify A6 migration semantics: document whether legacy scorecard rows were
   backfilled or whether calibration uses an explicit cutover. Add that note to
   `FAST_SIGNAL_IMPLEMENTATION.md`.
6. Run a final scorecard snapshot after B1/B2/B3 so `release_gate_status()` can
   distinguish `go`, `wait`, and `blocked` using calibrated evidence rather
   than missing-data placeholders.

Overnight execution note, 2026-04-28:

- A first detached B1 bootstrap wrote
  `workspace/studies/reproduction_rates_exp42_exp43_current_release_20260428.json`
  with 20 immediate `fail_different_class` rows because the detached shell used
  a Python environment without `pydantic`. Treat that file as an environment
  bootstrap artifact, not calibrated reproduction evidence.
- The corrected B1 run pins `/opt/homebrew/bin/python3` for both the
  reproduction driver and each ablation subprocess, uses attempt labels
  `phase0_exp42_current_release_abs_python_r{replicate}` and
  `phase0_exp43_current_release_abs_python_r{replicate}`, and writes to
  `workspace/studies/reproduction_rates_exp42_exp43_current_release_abs_python_20260428.json`.

## 0.2. Current Completion Tracker - 2026-04-30 Addendum

This addendum updates the 2026-04-28 tracker without rewriting the historical
record above.

Completed since the 2026-04-28 tracker:

- Post-mini-gate contract fixes were applied for stale same-tool branch
  rebinding and Prodigal mode binding. Focused regression coverage was added
  in `tests/core/test_stepwise_loop.py` and
  `tests/core/test_strict_artifact_binding.py`.
- Focused fast-signal tests passed after the fixes: `130 passed`.
- The mini-benchmark preflight suite passed for `control_evolution_mini`,
  `germline_vc_mini`, and `de_mini`.
- `qwen36_post_mini_gate_control_evolution_20260430` passed the Qwen 3.6
  control-evolution sentinel.
- `qwen36_post_mini_gate_true_no_templates_20260430` passed all 24 manifest
  cases under the strict `qwen_true_no_templates` stress variant.
- `qwen36_post_mini_gate_public_full_20260430` passed all 24 manifest cases
  under the public `qwen_full` variant.
- Latest full-sweep summaries report zero repairs, zero generic fallback, zero
  protocol fallback, and zero planner fail-open.
- Human-readable `summary.md` artifacts for the latest runs are completed and
  no longer show stale "in progress" status.
- A post-mini-gate evidence record was written to
  `docs/QWEN36_POST_MINI_GATE_RESULTS_20260430.md`.
- A canonical B1 aggregate was written to
  `workspace/studies/reproduction_rates_b1_exp42_exp43_exp44_current_20260430.json`;
  it includes the corrected exp42/exp43/exp44 rows and explicitly excludes the
  bad-env bootstrap artifact plus the stale restored `reproduction_rates.json`.
- B2 now has an executable planner-only corpus collector:
  `scripts/run_fast_signal_plan_shape_corpus.py`. It writes raw study rows to
  `workspace/studies/plan_shape_corpus_qwen36.jsonl` and a summary to
  `workspace/studies/plan_shape_corpus_qwen36_summary.json`.
- The B2 collector smoke run succeeded with a parseable Qwen 3.6
  `control_evolution` workflow emission at
  `workspace/studies/plan_shape_corpus_qwen36_smoke2_20260430.jsonl`.
- The first adaptive B2 pilot produced 30 planner-only Qwen 3.6 emissions
  across 10 representative cases and 3 temperatures at
  `workspace/studies/plan_shape_corpus_qwen36.jsonl`. Summary:
  29/30 parseable, top-10 idiom coverage 0.90, one high-temperature
  noisy-evolution malformed JSON emission, and model digest
  `07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522`.
- The B2 pilot exposed and fixed a generalized planner-shape issue:
  `workflow` can arrive as an object containing `steps`, and steps can use
  `tool` instead of `tool_name`. The normalizer and idiom summarizer now accept
  that shape, and a curated fixture was added at
  `tests/fixtures/fast_signal/planner_shape/corpus_qwen36_nested_workflow_steps.json`.
- A6 now consumes the canonical B1 aggregate by default when running
  `scripts/fast_signal_scorecard.py summarize`, `snapshot`, or `gate-status`.
  The merge layer excludes superseded reproduction rows such as
  `exp42_current_release`, `exp43_current_release`, and old `exp44` rows before
  appending the canonical `*_abs_python` / post-fix baseline rows.
- Two more B2 pilot emissions were promoted into planner-shape fixtures:
  `tests/fixtures/fast_signal/planner_shape/corpus_qwen36_evolution_bare_filename_hints.json`
  and
  `tests/fixtures/fast_signal/planner_shape/corpus_qwen36_noisy_evolution_malformed_json.json`.
  Replay expectations now support parse-failure fixtures and minimum bare-path
  counts so malformed JSON and bare filename hint idioms are checked directly.
- A3 now has a live paired-emission prompt-probe runner:
  `scripts/run_fast_signal_prompt_probe.py`. The first Qwen 3.6 smoke recorded
  one branch-stage hint pair and one forbidden-work wording pair in
  `workspace/studies/prompt_sensitivity_qwen36_smoke_20260430.jsonl`, with
  summary artifact
  `workspace/studies/prompt_sensitivity_qwen36_smoke_20260430_summary.json`.
  Both pairs parsed successfully and changed planner emission shape under the
  15% retention rule. This is evidence that the probe machinery works, not a
  replacement for the original planned larger prompt-sensitivity sample.
- A3 full prompt-sensitivity sample is now complete:
  `workspace/studies/prompt_sensitivity_qwen36_a3_20260430.jsonl` and
  `workspace/studies/prompt_sensitivity_qwen36_a3_20260430_summary.json`
  contain 40 paired Qwen 3.6 emissions. Branch-stage hints changed 20/20 pairs
  and forbidden-work wording changed 20/20 pairs, so both prompt features clear
  the 15% retention threshold. Branch-stage treatment emissions also parsed
  20/20 versus 16/20 for the no-hint control.
- A2 now has an explicit fixture coverage accounting document:
  `docs/FAST_SIGNAL_FIXTURE_COVERAGE_20260430.md`. It maps fixes #15-#28 to
  replay fixtures where present, names focused unit/pipeline coverage where no
  replay fixture exists, and calls out the remaining optional fixture gaps.
- The highest-value A2 gap from that map was closed immediately with
  `tests/fixtures/fast_signal/candidate_gate/exp37_shared_export_before_evol2_chain.json`,
  which covers the original #22b aggregator-before-producer rejection and adds
  replay coverage for shared-export #19/#22a behavior.
- The full curated fast-signal fixture replay passed after the A2/A3 updates:
  37/37 green after the #26 fixture addition, recorded in
  `workspace/studies/replay_after_fix26_fixture_20260430.jsonl`.
- The optional #26 direct-wrapper fixture gap was closed with
  `tests/fixtures/fast_signal/candidate_gate/exp42_bcftools_filter_output_type_scalar.json`.
  It pins `bcftools_filter_run.output_type` as the scalar flag `z` after
  candidate binding.
- A6 B1 cutover semantics are now documented and exposed in scorecard
  snapshots. The accepted policy is canonical replacement, not legacy-row
  backfill: raw reproduction rows for canonical or superseded B1 experiment
  IDs are excluded, then the canonical B1 rows are appended. See
  `docs/FAST_SIGNAL_SCORECARD_CUTOVER_POLICY_20260501.md`.
- A6 verification after the cutover policy update: focused fast-signal pytest
  passed 61/61, curated fixture replay passed 37/37, and CLI gate checks
  returned `go` for `exp42_current_release_abs_python` but `wait` with
  `no_reproduction_baseline` for superseded `exp42_current_release`.
- B2 adaptive corpus decision is now recorded. The 30-emission Qwen 3.6
  corpus covers 10 representative cases, 3 temperatures, and 9 analysis types,
  with top-10 idiom coverage of 0.90 and parse success of 29/30. This clears
  the adaptive fixture-seeding criterion. The fixed 600-emission corpus remains
  deferred, not claimed complete. See
  `docs/FAST_SIGNAL_PLAN_SHAPE_CORPUS_DECISION_20260501.md` and
  `docs/scorecard_snapshots/corpus_baseline_20260501.json`.
- The post-B2 scorecard snapshot was refreshed. The current snapshot consumes
  the canonical B1 aggregate, reports `go` for
  `exp42_current_release_abs_python`, and is archived alongside a compressed
  scorecard copy at `docs/scorecard_snapshots/scorecard_20260501.jsonl.gz`.

Reinterpreted completion status after checking local artifacts:

| Item | 2026-04-30 Status | Evidence / Remaining Work |
| --- | --- | --- |
| Release benchmark evidence | Green, strengthened | Two additional post-mini-gate Qwen 3.6 full sweeps passed 24/24. See `QWEN36_POST_MINI_GATE_RESULTS_20260430.md`. |
| B1 - exp42/exp43/exp44 reproduction baseline | Measurement artifacts complete; canonical aggregate created | Corrected exp42/exp43 artifact is 10/10 pass each; exp44 post-fix artifact is 10/10 pass. Canonical aggregate: `workspace/studies/reproduction_rates_b1_exp42_exp43_exp44_current_20260430.json`. A6 consumes this by explicit canonical replacement cutover. |
| B2 - plan-shape corpus | Adaptive corpus accepted for fixture seeding | The collector produced 30 Qwen 3.6 emissions across 10 cases, 3 temperatures, and 9 analysis types with 0.90 top-10 coverage and 0.967 parse success. Four corpus-backed planner-shape fixtures are present. The original 600-emission corpus is explicitly deferred, not claimed complete. |
| A2 - fixture coverage accounting | Coverage map created; top fixture gaps closed | `docs/FAST_SIGNAL_FIXTURE_COVERAGE_20260430.md` maps fixes #15-#28 across 37 curated fixtures and focused non-fixture tests. The original #22b aggregator-before-producer gap and the #26 output-type scalar gap now have candidate-gate fixtures. Remaining gaps are explicit and optional unless those classes recur. |
| A3 - prompt probes | Full sample complete | `scripts/run_fast_signal_prompt_probe.py` writes paired control/treatment emissions and summaries. Full A3 artifact: `workspace/studies/prompt_sensitivity_qwen36_a3_20260430_summary.json`. Both tested prompt features changed 20/20 pairs and clear the 15% retention threshold. |
| A6 - scorecard calibration | Implemented with documented B1 cutover | Gate code, tests, and snapshot consume the canonical B1 aggregate and exclude polluted bootstrap rows. Cutover policy: `docs/FAST_SIGNAL_SCORECARD_CUTOVER_POLICY_20260501.md`. Snapshot exposes `cutover_policy=canonical_replacement` and `legacy_rows_backfilled=false`. |
| B3 - baseline snapshot for future reruns | Refreshed after B2 decision | Current anchors: `docs/scorecard_snapshots/corpus_baseline_20260501.json`, `docs/scorecard_snapshots/phase7_triggers.txt`, `docs/scorecard_snapshots/fast_signal_scorecard_latest.json`, and `docs/scorecard_snapshots/scorecard_20260501.jsonl.gz`. |

Current next sequence:

1. Leave the remaining low-priority A2 replay fixture gaps deferred unless
   those classes recur as live LLM-output failures. They remain covered by
   focused unit or pipeline tests.
2. Treat A5 as green for the three required mini families
   (`control_evolution_mini`, `germline_vc_mini`, and `de_mini`). Add new
   mini cases only when a future launch family is not represented by those
   families.
3. Defer the fixed 600-emission B2 corpus unless a Phase 7 staleness trigger
   fires or a new launch family exposes uncataloged planner idioms.
4. For the next harness-changing PR, run the fast ladder in order: relevant
   replay fixtures, relevant dry-run/prompt checks, mini preflight when the
   touched surface is model-agnostic, then a Qwen 3.6 sentinel only if the
   scorecard remains `go`.

---

## 0.3. Current Completed Evaluation Checklist - 2026-05-01

The current completed-evaluation ledger is now:

- `docs/EVALUATION_COMPLETED_CHECKLIST_20260501.md`

That file is the handoff checklist for reviewer/model verification. It records:

- Qwen 3.6 final full-manifest evidence:
  `qwen_true_no_templates` 24/24 and `qwen_full` 24/24.
- Qwen 3.6 post-mini-gate evidence:
  `qwen36_post_mini_gate_true_no_templates_20260430` 24/24 and
  `qwen36_post_mini_gate_public_full_20260430` 24/24.
- Gemma `gemma4:26b` final generalization evidence:
  `gemma26_true_no_templates` 24/24 and `gemma26_full` 24/24.
- Fast-signal gate status:
  canonical B1 30/30 pass, adaptive B2 accepted, A3 prompt probes complete,
  mini preflight green, scorecard snapshot refreshed, and fixture replay green.
- Current fixture count:
  7 `planner_shape` fixtures, 31 `candidate_gate` fixtures, 38 total.
- The Gemma-discovered `annotated_vcf_handoff_binding` fix, fixture, unit test,
  mini-preflight validation, and full-sweep validation.
- Explicit non-claims:
  fixed 600-emission Qwen corpus, defense ablation, cross-model idiom diff, and
  per-case 10x reproduction for all 24 manifest cases remain deferred.

Treat the checklist as the current audit index. This plan remains the broader
strategy/history document.

---

## 1. What r16-r18 taught us

The completed testing is summarized in
`FAST_SIGNAL_TESTING_LEARNINGS_20260425.md`. The short version:

- r16 failed because cumulative stepwise validation let broad scientific repairs
  rewrite completed history.
- r17 cleared that class, then failed because the planner selected the right
  frontier tool (`bcftools_norm_run`) with stale arguments from an unrelated
  earlier step.
- r18 passed after the prefix-freeze and frontier-aware binding fixes.

The important general lessons:

- Replay fixtures are the cheapest way to turn a live failure into a durable
  regression check.
- Branch-stage frontier logic must be deterministic control logic, not only
  prompt text.
- Candidate gates should usually evaluate policy-bound semantic candidates, not
  raw LLM argument blobs, when the tool choice matches a known safe frontier.
- Completed stepwise prefixes are execution evidence and need stronger
  immutability than batch-mode draft plans.
- One passing r18 run is strong evidence that the last two fixes were useful,
  but it is not enough evidence to declare the campaign stable.

---

## 2. The Problem This Plan Solves

The current pattern is still too reactive:

1. Launch a long sentinel.
2. Observe one failure sample.
3. Diagnose and patch.
4. Add a narrow replay fixture.
5. Launch another long sentinel.

That loop can produce correct fixes, but it samples the model distribution too
slowly and makes one passing run feel more conclusive than it is.

The replacement pattern is:

1. Capture or generate fixtures before each feature or fix.
2. Run fast replay, scripted dry-run, preflight, and mini-benchmark gates.
3. Use reproduction-rate context to calibrate pass/fail claims.
4. Let the scorecard decide whether a long sentinel is authorized.
5. Treat overrides as data collection, not as validation.

The structural fix is to promote the scorecard from "log of what happened" to
"policy object that decides whether another expensive run should start."

---

## 3. Current Assets To Reuse

Do not rebuild these from scratch:

| Phase | Current Asset | Keep / Extend |
| --- | --- | --- |
| Phase 1 | Replay infrastructure, fixture schema, fixture extractor, curated exp44 fixtures | Keep; expand coverage. |
| Phase 3 | Prompt-sensitivity probes and branch-frontier diagnostics | Keep; add full scripted dry-run scenarios. |
| Phase 5 | Contract-level mini-benchmark validator | Keep; fill missing mini cases. |
| Phase 6 | Scorecard recorder, snapshotter, summary command | Keep; add calibration and gate status. |

Also preserve the r16, r17, and r18 run artifacts as a case study. They are now
the clearest example of the intended workflow:

```text
live failure -> replay fixture -> generalized fix -> fast gates -> live pass
```

---

## 4. Completion Work, Dependency Ordered

The work is split into CPU/local infrastructure and LLM measurement studies.
The CPU work should proceed first because it creates the tools that label,
store, and interpret the LLM study outputs.

### A0. Freeze The Launch Policy

Before adding more machinery, make the operating rule explicit:

- No new long Qwen 3.6 sentinel launches while a relevant fast gate is red.
- Missing evidence produces `wait`, not `go`.
- Known red evidence produces `blocked`.
- Measurement runs may proceed only with explicit metadata:
  - `measurement_purpose`
  - `override_reason`
  - expected failure class or sampling goal
  - scorecard row linking the run to that purpose

Acceptance:

- The plan, scorecard CLI help, and run wrapper language distinguish
  `validation` runs from `measurement` runs.
- Overrides are visible in `workspace/studies/scorecard.jsonl`.

### A1. Build The Reproduction Baseline Driver

Create or finish `scripts/run_fast_signal_reproduction_baseline.py`.

Required behavior:

- Wrap `scripts/run_domain_expansion_ablation.py`.
- Run repeated samples for one or more experiments.
- Use fresh run directories and fresh model sampling for each replicate.
- Give every replicate a unique selected/output directory derived from
  `(experiment_id, replicate_id, shard_id)` so concurrent replicates cannot
  collide on artifacts.
- Record every replicate as a scorecard row under gate `reproduction`.
- Classify each replicate as:
  - `pass`
  - `fail_same_class`
  - `fail_different_class`
  - `infra_error`
- Write aggregate output to `workspace/studies/reproduction_rates.json`.
- Support a dry-run mode that prints commands and scorecard metadata without
  launching models.

Useful flags:

- `--experiment-id`
- `--case-id`
- `--variant`
- `--replicate-count`
- `--same-class-marker`
- `--attempt-label-prefix`
- `--output`
- `--measurement-purpose`
- `--override-reason`
- `--shard-id`
- `--optimization-profile`

Acceptance:

- Dry-run mode works without touching model or tool execution.
- The first single-replicate acceptance smoke is legal under A0: it records
  `measurement_purpose=A1_acceptance_smoke`, an explicit `override_reason`, an
  `optimization_profile`, and row-level plus aggregate outputs.
- Concurrent replicates cannot write to the same `selected_dir`, run directory,
  scorecard row identity, or study-output row identity.
- The output schema is stable enough for scorecard calibration.
- Resource caps are explicit. A launch default may use a cap derived from
  reproduction-baseline runtime percentiles; any measurement override that
  exceeds that cap must record the reason.

### A2. Expand Historical Fixtures

Use `scripts/extract_fast_signal_fixtures.py` to mine existing historical runs,
especially beyond exp44.

Target coverage:

- exp33 through exp44 where traces exist.
- Multiple analysis types where available.
- Fixes #15 through #28.
- Both `planner_shape` and `candidate_gate` fixture kinds.

Fixture metadata must include:

- `schema_version`
- `id`
- `kind`
- `source_run`
- `model`
- `captured_against_model_digest`
- `backend_version`
- `temperature`
- `analysis_family`
- `analysis_type`
- `failure_class_id`
- `raw_emission`
- `prefix_state`
- `candidate`
- `expected_outcome`
- `failure_class`
- `covers_fix`
- `fixture_signature_hash`
- `tags`

Acceptance:

- Each currently documented fix class has at least one durable fixture, or an
  explicit note explaining why no fixture exists.
- Fixture replay remains fast enough to be a routine preflight check.
- The suite is no longer dominated by exp44-only examples.
- Fixture extraction deduplicates near-identical failures by a stable signature
  hash over planner-emission shape, rejection reason, and relevant frontier
  state.
- Fixtures captured against an older model digest are reported as advisory
  staleness when the active model digest changes; they are not silently treated
  as fresh evidence.

### A3. Finish Full Scripted Dry-Run Scenarios

Add a pytest-first dry-run harness that exercises the real stepwise control loop
without LLM calls or real tool execution.

Required properties:

- Planner emissions come from scripted fixtures.
- Tool execution is mocked but produces realistic expected artifacts.
- Duplicate detection, branch frontier logic, artifact binding, contract checks,
  and scorecard hooks run normally.
- Rejected candidates and rejection reasons are persisted for inspection.

Initial scenarios:

- `freebayes_concat_diagnostic`
- `branch_frontier_progression`
- `bcftools_norm_stale_args_frontier_binding`
- `prompt_sensitivity_branch_hint`
- `prompt_sensitivity_forbidden_work_wording`

Acceptance:

- The exp44-shaped branch scenario walks through the r17 failure point and
  accepts branch-local normalization work.
- Adding a new dry-run scenario is mostly fixture data, not custom harness code.
- The prompt-sensitivity probes report effect sizes and do not silently become
  release gates.

### A4. Add Fast-Model Preflight As An Advisory Gate

Create or finish `scripts/run_fast_model_preflight.py`.

Purpose:

- Quickly smoke-test model-agnostic changes.
- Never replace Qwen 3.6 validation for prompt, parser, routing, or retry
  behavior.

Allowed primary-regression categories:

- strict binder fixes
- path resolution
- contract adapters
- deterministic wrapper logic
- trace and scorecard code
- mini-benchmark validator code

Not sufficient for:

- prompt changes
- retry policy changes
- planner routing changes
- LLM-output parsing changes
- changes that rely on Qwen 3.6-specific emission shape

Acceptance:

- `--dry-run` shows the model env overrides and scorecard row that would be
  recorded.
- A real preflight can record an advisory `preflight` gate row.
- `release_gate_status()` treats preflight as advisory unless the changed files
  are explicitly model-agnostic.
- Model-agnostic classification comes from a checked-in path-glob map. Unmapped
  changed paths default to `not_model_agnostic`, so the gate stays strict.
- The preflight row records the matched category, matched globs, unmatched
  paths, model name, model digest, and backend version.

### A5. Fill Mini-Benchmark Gaps

Keep mini-benchmark assertions contractual, not scientific-value-specific.

Required mini cases:

- `control_evolution_mini`
- `germline_vc_mini`
- `de_mini`

Contract assertions:

- expected files exist
- files are non-empty where appropriate
- tabix indexes exist where required
- schemas are parseable and contain required columns
- wrapper contracts are satisfied

Avoid assertions on:

- exact variant coordinates
- exact p-values
- exact fold changes
- exact scientific interpretation

Acceptance:

- The mini-benchmark validator can run all available mini cases.
- Missing mini cases are represented as explicit `wait` reasons in the release
  gate, not silently ignored.
- Each mini case is small enough for routine local use.

### A6. Upgrade The Scorecard Into A Gate

Extend the scorecard implementation behind `scripts/fast_signal_scorecard.py`.

Required outputs:

- `calibrated_precision`
- `calibrated_false_positive_rate`
- `calibrated_false_negative_rate`
- `observation_count`
- `distinct_failure_class_count`
- `failure_class_id`
- `failure_class_registry_version`
- `checked_fixture_ids`
- `skipped_not_relevant_fixture_ids`
- `blocking_ready`
- `release_gate_status`
- `release_gate_reasons`
- `override_count_last_30_days`
- `override_rate_last_30_days`
- `corpus_baseline_status`
- `model_digest`
- `backend_version`
- `scorecard_schema_version`

Required methods or commands:

- `calibrate()` - consumes `workspace/studies/reproduction_rates.json`.
- `release_gate_status()` - returns `go`, `wait`, or `blocked`.
- `snapshot` - writes all calibrated fields to
  `docs/scorecard_snapshots/fast_signal_scorecard_latest.json`.
- `validate_failure_class()` - validates scorecard rows against a checked-in
  failure-class registry.
- Scorecard CLI row insertion accepts override and measurement metadata:
  `--override-gate-status`, `--override-reason`, and `--measurement-purpose`.

Acceptance:

- Add a checked-in failure-class registry, such as
  `bio_harness/core/failure_classes.py`. Scorecard insertion validates
  `failure_class` against this registry. Unknown classes become
  `failure_class_id=unclassified` with an explicit flag; they are not silently
  accepted as distinct calibrated classes.
- Seed the initial registry from the existing scorecard classes:
  `duplicate_detector_granularity`, `branch_stage_progress`,
  `prokka_gff_binding`, `isec_path_branch_binding`,
  `stepwise_prefix_normalization_state_serialization`,
  `stepwise_prefix_scientific_repair_after_prokka`, and
  `planner_completed_prefix_restart`.
- `distinct_failure_class_count` is computed from registry IDs, not free-form
  strings.
- Scorecard rows include model digest and backend version, not only model tag.
  If the active Ollama tag resolves to a different digest, calibration treats it
  as a distinct model identity unless explicitly bridged.
- Legacy scorecard rows are handled by a documented migration path: either a
  one-time backfill adds default schema fields, or a cutover row tells
  calibration to read only rows at or after the cutover point.
- If `workspace/studies/reproduction_rates.json` is absent,
  `release_gate_status()` returns `wait` with reason
  `no_reproduction_baseline`; it must never return `go` from missing
  calibration data.
- Missing evidence is visible as `wait`.
- Red relevant fixtures, mini-benches, or dry-run scenarios produce `blocked`.
- The output explains the reason, not just the status.
- `release_gate_reasons` names the fixture IDs, dry-run scenario IDs,
  mini-benchmark IDs, and corpus IDs that were checked, plus the IDs skipped as
  not relevant.
- Scorecard rows can record explicit launch overrides.
- Snapshots report both override count and override rate, where
  `override_rate_last_30_days = overrides_last_30_days / total_launches_last_30_days`.
- Observations with `optimization_profile=exploratory_only` are visible in
  snapshots but excluded from observation counts, calibrated rates,
  `distinct_failure_class_count`, and any `go`-eligible calculation.
- Add focused tests for `release_gate_status()` in
  `tests/core/test_release_gate.py`. Minimum cases:
  - all hard preconditions met returns `go`;
  - missing reproduction baseline returns `wait` with
    `no_reproduction_baseline`;
  - red relevant fixture returns `blocked` with the fixture ID;
  - stale corpus relative to `phase7_triggers.txt` returns `wait`;
  - `exploratory_only` rows are excluded from gate math;
  - missing override metadata never silently grants `go`;
  - unknown failure classes become `unclassified`, not new distinct classes.

### A7. Fix Reporting And Observability Gaps Found During r17/r18

These are not optional polish; they affect whether the ladder produces useful
evidence.

Known gaps:

- r18 `exit.json` reported `completed`, but `summary.md` still said
  "Run in progress."
- r17 rejected candidate attempts were not fully persisted in an easy-to-audit
  state row.

Acceptance:

- Successful runs update human-readable summaries after completion.
- Rejected stepwise candidates persist:
  - raw candidate
  - bound candidate, if binding occurred
  - rejection reason
  - gate that rejected it
  - frontier state at rejection time
- The dry-run harness can assert this persistence behavior.
- A persisted rejection contains enough structured data for an automated
  extractor to create a `candidate_gate` fixture without manual re-derivation.

### A8. Add Speed-Safe Execution Controls

Integrate the safe optimizations from `FAST_SIGNAL_SPEED_OPTIMIZATIONS.md`
without weakening gate quality.

Tier 1 controls to adopt with fallbacks:

- `optimization_profile` scorecard metadata.
- long Ollama keep-alive for measurement studies.
- prompt-prefix cache stability without changing prompt text.
- pytest-xdist auto-detection with serial fallback.
- hash-pinned synthetic mini-benchmark inputs.
- read-only prepared reference/index cache keyed by input hash, tool version,
  wrapper version, and environment metadata.
- checkpoint/resume for reproduction and corpus studies.

Tier 2 controls to adopt only after one-time checks:

- `OLLAMA_NUM_PARALLEL` for measurement runs only.
- B1/B2 measurement pipelining.
- speculative decoding only if the backend demonstrates target-preserving
  verification.
- diagnostic early termination for measurement runs only.
- same-shape retry coalescing only as a tested harness behavior change.

Rejected for calibrated gates:

- quantization downgrades for routine validation.
- global context-window shrinkage.
- caching final artifacts instead of running tools.
- treating fast-model preflight as Qwen 3.6 proof.
- counting early-terminated measurement runs as validation passes.

Acceptance:

- Speed settings are visible in scorecard metadata.
- Missing optional speed dependencies, such as `pytest-xdist`, degrade to safe
  serial behavior.
- Cached inputs/indexes cannot mask real tool execution.
- Concurrency settings are measurement-only unless explicitly reviewed.
- Early termination records `interrupted_after_signal`, not `pass`.
- The release gate always prefers slower honest evidence over faster ambiguous
  evidence.

---

## 5. Measurement Studies

Run these after the local infrastructure can store and classify their outputs.

### B1. Reproduction Baseline

Use the A1 driver for repeated samples of exp42, exp43, and exp44.

Required output:

- `workspace/studies/reproduction_rates.json`
- scorecard rows for every replicate
- aggregate status per experiment:
  - pass count
  - same-class fail count
  - different-class fail count
  - infra error count
  - same-class reproduction rate
  - confidence interval or bootstrap interval, if implemented
- model tag, model digest, backend version, optimization profile, warm-state
  policy, timeout policy, and resource cap used for the study

Interpretation rules:

- If a failure class reproduces often, one passing run is not enough evidence.
- If failures scatter across unrelated classes, the fix loop is sampling broad
  instability rather than a single blocker.
- If infra errors dominate, pause model conclusions and fix measurement.
- Warm-state policy must be explicit. Either document that a hot loaded model is
  accepted between experiments, or force a reload between experiments and record
  that policy. Do not leave model warm-state independence implicit.

### B2. Plan-Shape Corpus

Run planner-only Qwen 3.6 emissions across representative prompts, repetitions,
and temperatures.

Start with a bounded corpus, then adapt:

- If top-10 idioms cover >= 80% of emissions, promote representative fixtures.
- If top-10 idioms cover < 80%, sample more or explicitly catalog the tail.
- Do not call the corpus sufficient until the coverage criterion is met or the
  tail is intentionally accepted as risk.
- Use trend as the tiebreaker: if cumulative top-10 coverage keeps improving
  with each batch, sample more; if coverage plateaus below 80%, stop sampling,
  catalog the tail, and keep affected launch categories at `wait`.

Required outputs:

- raw emissions in `workspace/studies/`
- idiom histogram
- promoted curated fixtures under `tests/fixtures/`
- a note explaining which idioms are covered and which remain tail risk

Extract:

- schema shape
- path style
- tool order
- argument idioms
- branch identifiers
- duplicate patterns
- stale-argument patterns

### B3. Baseline Snapshot For Future Reruns

Hash-pin the reproduction and corpus outputs.

Required output:

- `docs/scorecard_snapshots/corpus_baseline_20260425.json`
- `docs/scorecard_snapshots/phase7_triggers.txt`
- a compressed copy of the scorecard JSONL used to generate the snapshot

The snapshot should include:

- artifact paths
- sha256 hashes
- model names
- prompt hashes
- temperature settings
- fixture promotion list
- known exclusions
- git commit or tree hash for planner-touching files at collection time
- trigger file-glob list used to decide whether the baseline is stale

This becomes the baseline for Phase 7 reruns after prompt edits, analysis-spec
changes, or major repair/binder changes.

`phase7_triggers.txt` should list the file globs whose changes invalidate the
corpus baseline. The initial trigger set should include planner prompts,
LLM-output parsing, planner routing, analysis-spec generation, repair logic,
strict binders, and branch-stage frontier logic.

Because `workspace/studies/scorecard.jsonl` is workspace data, snapshots should
also preserve a compressed scorecard copy in `docs/scorecard_snapshots/` at
least once per day when new rows are recorded. Snapshot retention can stay
simple for now, but should be revisited once the directory grows past roughly 50
snapshots.

---

## 6. Release Gate Policy

`release_gate_status()` returns one of:

- `go` - launch is authorized.
- `wait` - evidence is missing or advisory checks need attention.
- `blocked` - relevant known evidence is red.

### 6.1 Hard Preconditions For `go`

All must be true:

1. Reproduction baseline exists for the case or failure family being launched.
2. Relevant replay fixtures are green.
3. Relevant scripted dry-run scenario is green, when such a scenario exists.
4. Relevant mini-benchmark is green for the analysis family.
5. No relevant `blocking_ready` gate has regressed.
6. No relevant known red fixture is being bypassed.

If a relevant fixture or scenario does not exist yet, status is `wait`, not
`go`, unless the launch is explicitly a measurement override.

Relevant means scoped to the launch's analysis family, failure family, and
changed code surface. Missing `de_mini` coverage does not block an evolution
launch, but missing `control_evolution_mini` coverage does. Missing prompt
corpus coverage matters for prompt/parser/routing changes, but not for a purely
deterministic path-formatting fix unless that fix touches planner-facing output.

Operationally, relevance is computed from a tuple:

```text
(analysis_family, failure_class_id, changed_code_category)
```

Fixture, dry-run, mini-benchmark, and corpus metadata must expose compatible
tags. `release_gate_reasons` must list which evidence IDs were checked and which
were skipped as not relevant, so two operators can audit the same launch and see
the same decision.

### 6.2 Advisory Preconditions

These produce `wait` unless the launch metadata explains why they do not apply:

- Fast-model preflight is red or missing for model-agnostic code changes.
- Corpus baseline is stale after prompt, parser, routing, or repair changes.
- Scorecard snapshot is stale relative to the relevant fixture/test run.

A corpus baseline is stale when the current git state differs from the baseline
snapshot and the changed paths intersect the globs listed in
`docs/scorecard_snapshots/phase7_triggers.txt`. If the repository is dirty,
staleness should be computed from both committed changes and working-tree
changes.

### 6.3 Blocking-Ready Threshold

A gate can become `blocking_ready` only when it has:

- at least 10 observations,
- calibrated precision >= 0.80,
- calibrated false-negative rate <= 0.10,
- evidence across at least 3 distinct failure classes,
- no unresolved labeling ambiguity for the relevant observations.
- no dependence on `exploratory_only` rows. Those rows are reported for
  visibility but excluded from observation counts, calibrated rates, and
  distinct failure class counts.

Until then, the gate may inform decisions but should not silently block by
itself.

### 6.4 Override Policy

Overrides are allowed only when the purpose is explicit.

Examples of acceptable overrides:

- collecting reproduction data,
- probing an unclassified stochastic failure,
- validating that the gate itself is too strict,
- running a deliberately labeled stress test.

Required metadata:

- `--override-gate-status wait`
- `--override-reason`
- `--measurement-purpose`
- source issue or failure class, when known

The scorecard must make overrides easy to audit.

Every snapshot should report override count and override rate over the last 30
days. Count alone is not enough: four overrides out of forty launches and four
overrides out of eight launches mean very different things.

---

## 7. Expected Benefits And How We Will Measure Them

Do not treat these as promises. They are the metrics this plan should improve.

| Metric | Current State | Target State |
| --- | --- | --- |
| Time to reject a bad fix | Often discovered by long sentinel | Usually rejected by replay, dry-run, preflight, or mini-bench |
| Confidence in a single passing sentinel | Low without reproduction context | Interpreted against reproduction baseline |
| Fixture coverage | Narrow and exp44-heavy | Cross-experiment plus corpus-derived |
| Prompt drift detection | Mostly discovered by later live runs | Detected by corpus diff and prompt probes |
| Scorecard role | Evidence log | Launch policy with reasons |
| Override visibility | Ad hoc | Explicit scorecard audit trail |

The most important measurable outcome is not raw speed. It is fewer long runs
whose result is ambiguous.

---

## 8. Differences From The Previous Draft

This version keeps the previous draft's strategic direction and changes the
parts most likely to cause confusion.

Edits made:

- Removed timeline estimates and day-by-day scheduling. The plan now uses
  dependency order and acceptance criteria.
- Clarified that no new long sentinels should launch unless the scorecard says
  `go` or the run is an explicitly labeled measurement override.
- Added A0 to freeze the launch policy before more infrastructure work.
- Added A7 for reporting and observability gaps discovered in r17/r18.
- Changed the scorecard language from "make at least one gate blocking-ready"
  to "only promote gates when thresholds are actually met."
- Tightened the blocking-ready false-negative threshold to <= 0.10, matching
  the original fast-signal policy.
- Reframed efficiency numbers as target metrics, not proven ROI claims.
- Changed Phase 2 corpus collection to adaptive sampling: start bounded, then
  continue or catalog tail risk until the top-10 idiom criterion is addressed.
- Made missing fixtures or scenarios produce `wait`, not accidental `go`.
- Explicitly separated validation runs from measurement runs.
- Defined `relevant` for launch gates so unrelated-family missing fixtures do
  not block the wrong launch.
- Defined corpus-baseline staleness using a baseline snapshot plus
  `phase7_triggers.txt` file-glob list.
- Required absent reproduction data to degrade to `wait` with
  `no_reproduction_baseline`, never `go`.
- Added override-rate reporting, not only override-count reporting.
- Added an adaptive corpus-sampling tiebreaker: keep sampling while top-10
  coverage improves, catalog tail risk once coverage plateaus.
- Added cross-model fixture replay as deferred-but-tracked work.
- Integrated `FAST_SIGNAL_SPEED_OPTIMIZATIONS.md` as A8, with quality-preserving
  guardrails and explicit rejection of optimizations that would compromise
  calibrated gates.
- Added enforcement mechanics from the v5 review: failure-class registry,
  relevance tuple, scorecard schema migration, model digest/backend version,
  release-gate tests, fixture dedup/staleness, and scorecard backups.

---

## 9. Risks

| Risk | What It Means | Response |
| --- | --- | --- |
| Reproduction baseline shows r18 was lucky | The fixes helped but did not stabilize the class | Keep gate at `wait` or `blocked`; add fixtures from repeated failures. |
| Failures scatter across unrelated classes | We are measuring broad instability, not one blocker | Stop treating one failure as the campaign root cause; classify and batch fixtures. |
| Corpus tail is large | Qwen 3.6 emits more plan idioms than current fixtures cover | Promote more corpus fixtures or keep launch status at `wait` for prompt/parser changes. |
| Mini-benchmark synthesis is incomplete | Cross-family contract coverage stays thin | Gate only the families with real mini-benches; mark others `wait`. |
| Overrides become routine | The scorecard becomes decorative | Report override count in every snapshot and review it before launches. |
| Scorecard labels are noisy | Calibration math gives false confidence | Keep ambiguous labels out of blocking-ready promotion. |
| Cross-model drift remains invisible | Qwen 3.6 fixes may not transfer to Qwen 3.5, Qwen Coder, or future models | Track cross-model fixture replay as deferred work after the single-model ladder is stable. |
| Failure classes fragment by spelling | Free-form strings overcount distinct classes | Validate against the failure-class registry and route unknowns to `unclassified`. |
| Model tags silently change digest | Calibration mixes different model binaries under one tag | Store model digest and backend version on every row and fixture. |
| Scorecard history is lost | Workspace cleanup deletes JSONL evidence | Preserve compressed scorecard copies with docs snapshots. |

---

## 10. Immediate Work Queue

Recommended order:

1. A0 - freeze launch policy and override metadata.
2. A1 - reproduction baseline driver.
3. A6 - scorecard calibration and `release_gate_status()`.
4. A8 - speed-safe execution controls and optimization metadata.
5. A2 - historical fixture expansion.
6. A3 - scripted dry-run harness, starting with r17 normalization tail.
7. A7 - reporting and rejected-candidate persistence.
8. A5 - mini-benchmark gap-fill.
9. A4 - fast-model preflight wrapper.
10. B1 - reproduction baseline study.
11. B2 - plan-shape corpus.
12. B3 - baseline hash snapshot.
13. Deferred - cross-model fixture replay across Qwen 3.5, Qwen 3.6, and Qwen
    Coder after the single-model ladder is stable.

Some items can run in parallel, but the ordering above reflects dependencies:
the studies are much more useful after the driver and scorecard can classify
their outputs.

---

## 11. Success Criteria

This plan succeeds when:

- `release_gate_status()` returns `go`, `wait`, or `blocked` with explicit
  reasons for `control_evolution`.
- `release_gate_status()` can also return `go`, `wait`, or `blocked` for any
  case in the 24-case ablation manifest, using case-specific relevance rules.
- Missing evidence no longer looks like a pass.
- The r16/r17/r18 failure classes are covered by replay or dry-run fixtures.
- Reproduction rates exist for exp42, exp43, and exp44, or the absence is an
  explicit `wait` reason.
- Corpus-derived fixtures cover the observed top Qwen 3.6 idioms, or tail risk
  is explicitly documented.
- Mini-benchmark coverage is present for the relevant launch family, or the
  launch remains `wait`.
- Overrides are recorded and auditable.
- Human-readable run summaries agree with machine-readable exit status.
- Corpus staleness is computed from `phase7_triggers.txt`, not from an
  undocumented human judgment.
- Override rate is visible in scorecard snapshots.
- Optimization profiles are visible in scorecard rows and snapshots.
- Optional speed dependencies fall back safely when unavailable.
- Failure classes are registry-backed, and unknown classes are visible as
  `unclassified`.
- Model digest and backend version are present in scorecard rows and fixture
  metadata.
- Legacy scorecard rows are either backfilled or excluded by an explicit cutover
  row.
- `tests/core/test_release_gate.py` covers the policy-critical gate outcomes.

The plan does not fail just because the scorecard says `blocked`. That is a
successful measurement outcome if the evidence is real.

---

## 12. Deferred But Tracked

Cross-model fixture replay is intentionally deferred, not dropped. Once the
Qwen 3.6 ladder is stable, replay the curated fixture suite against Qwen 3.5,
Qwen 3.6, and Qwen Coder planner emissions where possible.

Purpose:

- distinguish Qwen 3.6-specific harness hardening from portable robustness,
- estimate whether new models are likely to hit known idiom classes,
- provide cheap portability evidence before broader benchmark claims.

This should not block the immediate exp44 completion ladder unless a launch is
explicitly making cross-model claims.

---

## 13. Bottom Line

r18 is a real win. It proves the narrow fast-signal loop can turn live failures
into generalized fixes and get a hard case across the line.

The next job is to make that loop systematic. Build the reproduction driver,
expand fixtures, finish scripted dry-run coverage, calibrate the scorecard, and
make the gate decide whether another long Qwen 3.6 sentinel is justified.

The goal is not to stop running long benchmarks. The goal is to make every long
benchmark answer a calibrated question.
