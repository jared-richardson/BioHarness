# Fast Signal Plan-Shape Corpus Decision, 2026-05-01

This records the B2 decision for the Qwen 3.6 planner-only corpus.

## Decision

Accept the 30-emission adaptive B2 corpus as sufficient for current fixture
seeding and current-gate calibration work. Do not run the original fixed
600-emission corpus unless a future prompt/parser/routing/repair change makes
the corpus stale or a new launch family exposes uncataloged planner idioms.

This is not a claim that the 600-emission study was completed. It is an
explicit adaptive stop based on the criterion in the plan.

## Evidence

Corpus artifacts:

- `workspace/studies/plan_shape_corpus_qwen36.jsonl`
- `workspace/studies/plan_shape_corpus_qwen36_summary.json`

Observed coverage:

- Emissions: 30
- Cases: 10 representative manifest cases
- Temperatures: 0.0, 0.3, 0.7
- Analysis types: 9 represented
- Parsed emissions: 29/30
- Parse success rate: 0.9667
- Top-10 idiom coverage: 0.90
- Adaptive criterion: passed
- Full 600-emission corpus: not completed

Representative cases covered:

- `control_evolution`
- `control_deseq`
- `control_germline`
- `control_transcript`
- `domain_longread_sv`
- `domain_spatial`
- `domain_proteomics`
- `domain_metabolomics`
- `domain_metagenomics`
- `stress_noisy_evolution`

Model identity:

- Planner model: `qwen3.6:35b-a3b`
- Model digest:
  `07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522`
- Backend version: `0.20.0`

## Promoted Fixtures

The corpus directly produced or justified these durable planner-shape fixtures:

- `tests/fixtures/fast_signal/planner_shape/corpus_qwen36_top10_idioms.json`
- `tests/fixtures/fast_signal/planner_shape/corpus_qwen36_nested_workflow_steps.json`
- `tests/fixtures/fast_signal/planner_shape/corpus_qwen36_evolution_bare_filename_hints.json`
- `tests/fixtures/fast_signal/planner_shape/corpus_qwen36_noisy_evolution_malformed_json.json`

These fixtures cover the observed generalized idioms that mattered during this
campaign: nested workflow steps, `tool` alias shape, bare filename hints, and
malformed JSON detection.

## Interpretation

The adaptive plan said to continue sampling only if the top-10 idiom criterion
was not met or if the tail needed cataloging. Here, the criterion is met:
top-10 coverage is 0.90, above the 0.80 threshold, with coverage across
representative cases and temperatures.

The 600-emission corpus remains a deferred measurement, not a release
precondition, for the current Qwen 3.6 digest. Re-run or expand B2 when:

- prompt text changes;
- planner/parser/routing/repair code changes under `phase7_triggers.txt`;
- the active Qwen model digest changes;
- a new analysis family becomes release-relevant and lacks fixture coverage;
- a future failure shows a planner idiom outside the current curated fixture
  set.

Until one of those triggers fires, the current B2 corpus is accepted as the
baseline for fixture seeding.
