# Fast Signal Implementation Notes

This document records the executable surfaces added for the BioHarness Fast
Signal Plan v4. The infrastructure is deliberately fixture-first: the exp44
candidate-gate fixture is installed before the duplicate-detector behavior is
changed, so the full replay command currently reports that gate as red.

## Commands

Replay curated fixtures:

```bash
python3 scripts/replay_fast_signal_fixtures.py tests/fixtures/fast_signal
```

Replay only planner-shape fixtures:

```bash
python3 scripts/replay_fast_signal_fixtures.py tests/fixtures/fast_signal/planner_shape
```

Extract planner-shape fixtures from stored runs:

```bash
python3 scripts/extract_fast_signal_fixtures.py \
  --runs-root workspace/runs \
  --output-dir tests/fixtures/fast_signal/planner_shape \
  --run-glob '20260423_*' \
  --limit 50 \
  --dedupe
```

Summarize corpus idioms:

```bash
python3 scripts/summarize_fast_signal_plan_corpus.py \
  --fixtures tests/fixtures/fast_signal/planner_shape \
  --output-json workspace/studies/plan_shape_corpus_summary.json
```

Measure prompt-feature sensitivity from paired fixture sets:

```bash
python3 scripts/measure_fast_signal_prompt_sensitivity.py \
  --feature-name branch_stage_hint \
  --control-fixtures workspace/studies/prompt_probe/control \
  --treatment-fixtures workspace/studies/prompt_probe/treatment
```

Run the reproduction baseline with explicit commands:

```bash
python3 scripts/run_fast_signal_reproduction_baseline.py \
  --experiment 'exp44::python3 scripts/run_domain_expansion_ablation.py ... --attempt-label exp44_repro_{replicate_id}' \
  --same-class-marker 'Candidate duplicates completed step_id' \
  --replicates 10 \
  --measurement-purpose reproduction_baseline \
  --override-reason 'calibrating release gate' \
  --optimization-profile safe_local \
  --ollama-keep-alive 12h \
  --resume
```

Dry-run the fast-model preflight against the strict-layout mini suite:

```bash
python3 scripts/run_fast_model_preflight.py \
  --suite mini \
  --dry-run \
  --case-id control_evolution_mini
```

Run the advisory mini-suite preflight and record scorecard rows:

```bash
python3 scripts/run_fast_model_preflight.py \
  --suite mini \
  --record-scorecard \
  --optimization-profile safe_local \
  --measurement-purpose fast_model_preflight
```

Dry-run the legacy 24-case domain-manifest preflight:

```bash
python3 scripts/run_fast_model_preflight.py \
  --suite domain \
  --dry-run \
  --case-id control_evolution
```

Record and summarize scorecard observations:

```bash
python3 scripts/fast_signal_scorecard.py record \
  --experiment-id exp44 \
  --gate replay \
  --status fail \
  --full-run-status fail_same_class \
  --failure-class duplicate_detector_granularity \
  --reproduction-rate 0.7 \
  --model qwen3.6:35b-a3b \
  --model-digest '<ollama-digest>' \
  --backend-version '<backend-version>' \
  --optimization-profile safe_local

python3 scripts/fast_signal_scorecard.py summarize

python3 scripts/fast_signal_scorecard.py snapshot

python3 scripts/fast_signal_scorecard.py gate-status \
  --experiment-id exp44 \
  --evidence-json '[{"evidence_id":"exp44_duplicate_branch","kind":"fixture","status":"pass"}]'
```

Validate mini-benchmark outputs at contract level:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \
  --output-root workspace/benchmark_data/fast_signal_mini

python3 scripts/validate_fast_signal_mini_benchmarks.py \
  --case-id control_evolution_mini \
  --selected-dir workspace/benchmark_data/fast_signal_mini/official_runs/evolution/attempt1
```

## Current Gate State

- Planner-shape seed fixtures are green.
- `exp44_duplicate_branch` is green after the sample-aware output alias
  refinement; the live r02 sentinel accepted and executed `evol2` alignment
  after completing `evol1`.
- `exp44_branch_progress` captures the next failure class from r02: a premature
  `snpeff_annotate[evol2]` candidate is rejected until branch-local filtering
  and ancestor subtraction catch up. The replay gate now surfaces the next
  branch-stage frontier as structured state.
- Candidate-gate replay now records duplicate detection, missing-input
  rejection, and branch-stage progress observations for each fixture.

## Policy

- Do not launch exp45 while a relevant fast-signal fixture is red.
- Promote scorecard gates from advisory to blocking only after calibrated
  reproduction data shows sustained precision and low false-negative rate.
- Mini-benchmark assertions should stay contractual: existence, schema,
  sidecars, and non-empty payloads, not exact scientific values.

## A0/A1/A6 Implementation Slice

- `bio_harness/core/failure_classes.py` defines the initial registry-backed
  fast-signal failure-class IDs. Unknown non-empty classes are normalized to
  `unclassified` so spelling drift cannot inflate distinct-class counts.
- `ScorecardRow` now carries schema/version metadata needed by the completion
  plan: `failure_class_id`, `failure_class_unclassified`, `model`,
  `model_digest`, `backend_version`, `optimization_profile`,
  `override_gate_status`, `override_reason`, and `measurement_purpose`.
- `release_gate_status()` returns `go`, `wait`, or `blocked` with explicit
  reasons. Missing reproduction data returns `wait` with
  `no_reproduction_baseline`; red relevant evidence returns `blocked`.
- `scripts/fast_signal_scorecard.py record` accepts override and measurement
  fields, and `gate-status` exposes the release-gate decision from the CLI.
- `scripts/run_fast_signal_reproduction_baseline.py` now records replicate IDs,
  shard IDs, measurement purpose, override reason, optimization profile, model
  digest, and backend version in dry-run and real rows. Timeout and common
  backend failures classify as `infra_error`.
- The B1 scorecard migration uses an explicit canonical replacement cutover,
  not a legacy-row backfill. `scripts/fast_signal_scorecard.py summarize`,
  `snapshot`, and `gate-status` load
  `workspace/studies/reproduction_rates_b1_exp42_exp43_exp44_current_20260430.json`
  by default when present, exclude superseded reproduction rows for
  `exp42_current_release`, `exp43_current_release`, and `exp44`, then append
  the canonical B1 rows. See
  `FAST_SIGNAL_SCORECARD_CUTOVER_POLICY_20260501.md`.

## A2/A8 Implementation Slice

- Replay fixtures now carry derived metadata for release-gate relevance and
  deduplication: `analysis_family`, `failure_class_id`,
  `captured_against_model_digest`, `backend_version`,
  `fixture_signature_hash`, and `tags`. Existing v1 fixtures remain readable;
  missing fields are filled deterministically on load.
- `scripts/extract_fast_signal_fixtures.py` deduplicates extracted
  planner-shape fixtures by `fixture_signature_hash` by default. Use
  `--no-dedupe` only for diagnostic extraction where near-duplicate examples
  are intentionally wanted.
- The reproduction driver supports speed-safe study controls:
  `--ollama-keep-alive`, `--ollama-num-parallel`, `--resume`, `--shard-id`,
  and `--optimization-profile`. These settings are recorded in row metadata so
  speed changes are visible to the scorecard.
- Resume mode skips replicate IDs already present in the study output JSON and
  reports `skipped_replicate_ids`; this prevents interrupted measurement
  studies from double-counting rows.

## B2 Implementation Slice

- `scripts/run_fast_signal_plan_shape_corpus.py` collects planner-only Qwen
  emissions without running bioinformatics tools. The accepted adaptive B2
  corpus is stored in `workspace/studies/plan_shape_corpus_qwen36.jsonl` with
  summary `workspace/studies/plan_shape_corpus_qwen36_summary.json`.
- The accepted corpus covers 30 emissions across 10 representative manifest
  cases, 3 temperatures, and 9 analysis types. Top-10 idiom coverage is 0.90
  with 29/30 parse success, clearing the adaptive fixture-seeding criterion.
- The fixed 600-emission corpus is explicitly deferred, not claimed complete.
  The decision note is `FAST_SIGNAL_PLAN_SHAPE_CORPUS_DECISION_20260501.md`;
  the machine-readable baseline anchor is
  `docs/scorecard_snapshots/corpus_baseline_20260501.json`.

## A3 Implementation Slice

- Candidate-gate fixtures can now opt into the live stepwise candidate
  evaluator with `metadata.replay_mode: "stepwise_candidate_evaluation"` or an
  `expected_outcome.accepted` field. The replay CLI dispatches these fixtures
  through `_evaluate_stepwise_candidate()` rather than only sampling individual
  guard methods.
- `r17_norm_stale_args_evaluation` captures the normalization-tail failure
  shape where Qwen emitted stale assembly-style arguments for
  `bcftools_norm_run`. The fixture asserts that the live evaluator accepts the
  candidate after rebinding it to `evol1.annotated.vcf`,
  `evol1.annotated.normalized.vcf.gz`, and `assembly/scaffolds.fasta`.
- `bio_harness/core/fast_signal_dry_run.py` adds an ordered scripted scenario
  runner for candidate fixtures. It keeps the scenario pytest-first and
  LLM-free while still using the real stepwise gate/evaluator logic for each
  scripted turn.

## A7 Implementation Slice

- Terminal CLI runs now rewrite `summary.md` from the final run state whenever
  `_write_exit()` writes a terminal status. This prevents stale summaries that
  still say "Run in progress" after `exit.json` reports completion.
- Completed stepwise runs that satisfy their contract now clear stale transient
  errors from earlier repaired attempts. The terminal summary reports contract
  pass/fail status and counts repaired failed attempts that remain in the
  execution trace for audit.
- Stepwise candidate rejections now persist structured audit records in
  `state.json` under `stepwise_rejected_candidates` and emit a
  `STEPWISE_CANDIDATE_REJECTED` event. Each record includes the raw candidate,
  rebound candidate step, rejection reason, rejecting gate label, branch
  frontier state, and a `fixture_seed` block with enough prefix/candidate data
  for a candidate-gate fixture extractor.

## A5 Implementation Slice

- `prepare_mini_benchmark_suite()` and
  `scripts/prepare_fast_signal_mini_benchmarks.py` now generate the three
  required mini cases: `control_evolution_mini`, `germline_vc_mini`, and
  `de_mini`. The generated suite includes tiny real FASTA/FASTQ/GFF/metadata
  inputs plus a domain-runner-compatible `manifest.json`.
- Each manifest case records a strict-binder-compatible `selected_dir` under
  `official_runs/<task>/attempt1`, so local mini runs can use the same artifact
  binding paths as strict benchmark runs.
- Mini contracts now accept canonical harness artifact names as well as the
  originally proposed names where they differ. For example, `de_mini` accepts
  the current strict binder output `final/deseq_results.csv`, while
  `germline_vc_mini` accepts either `final/variants.vcf` or an indexed
  `final/variants.vcf.gz`.

## A4 Implementation Slice

- `scripts/run_fast_model_preflight.py` now defaults to the mini-benchmark
  suite instead of only printing the legacy 24-case domain command. It prepares
  deterministic tiny inputs, runs `scripts/run_agent_e2e.py` once per selected
  mini case with the Qwen Coder fast model, and validates the contract-level
  mini-benchmark outputs after each run.
- Mini preflight selected directories are cleaned before execution by default
  to prevent stale artifacts from making a failed run look green. The cleanup
  is constrained to the configured mini-suite root.
- The legacy domain-manifest path remains available with `--suite domain` for
  compatibility, but the strict-layout mini suite is the day-to-day advisory
  preflight for model-agnostic changes.
- `--record-scorecard` appends one advisory `fast_model_preflight` row per mini
  case, including model digest/backend/version fields when supplied, so A4 can
  feed the same scorecard snapshots as replay, dry-run, and mini-benchmark
  gates.
- Live smoke note: the first `control_evolution_mini` run exposed a SPAdes
  PHRED auto-detection failure on tiny synthetic reads. The `spades_assemble`
  wrapper now emits `--phred-offset 33` by default, with `64` and `auto`
  override support. A constrained one-turn rerun produced real SPAdes assembly
  artifacts and then failed only because the one-turn cap prevented the full
  variant/annotation/export contract from completing.
- Live smoke note: the next full `control_evolution_mini` run exposed a
  fixture-quality problem rather than a planner problem. The old repetitive
  mini reference collapsed to an 83 bp SPAdes contig, so Prodigal emitted no
  genes and SnpEff could not build a database. The evolution mini fixture now
  uses a tiny non-repetitive coding reference, and `prodigal_annotate` now
  routes through an atomic helper that uses Prodigal `meta` mode for short
  contigs and fails early on empty CDS output.
- Current A4 result: `control_evolution_mini` passes the advisory fast-model
  preflight with Qwen Coder. The run produced branch-local filtered,
  ancestor-subtracted, annotated, and normalized VCFs, then wrote
  `final/variants_shared.csv`. Contract validation passed with a non-empty
  table containing `CHROM` and `POS`.
