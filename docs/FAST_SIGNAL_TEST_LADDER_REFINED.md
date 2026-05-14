# Fast Signal Test Ladder for BioHarness — Refined

**Status:** Refinement of the previous fast-signal plan, integrating behavioral-characterization and calibration observations. Structure preserved from the prior draft; additions marked inline where they change intent.

## Summary

Build a predictive test ladder so most harness regressions fail in seconds, not after a 20-minute Qwen 3.6 benchmark. Three fast gates before any full run:

1. Historical replay tests for real LLM emissions and candidate plans.
2. Scripted stepwise dry-runs for control-flow bugs like exp44.
3. Tiny real-tool mini-benchmarks for subprocess/artifact-contract confidence.

The full Qwen 3.6 benchmark remains the release gate — the final confirmation, not the main debugging loop.

**Refinement:** The prior draft's scorecard mechanism is preserved, but it requires a **reproduction-rate baseline** to calibrate correctly. Without it, the scorecard labels (pass/fail on full run) are noisy samples from a stochastic distribution, not ground truth. A reproduction-rate study runs in parallel with fixture building so scorecard data is well-calibrated from day one.

**Second refinement:** The fixture library is extended from "historical convenience sample" to "representative sample drawn from the empirical plan-shape distribution," seeded by a 600-emission plan-shape corpus study. This widens coverage beyond the ~12 runs we've observed.

## Key Changes

- Add a historical fixture extractor that mines `workspace/runs/<run_id>/planner/*_raw_response.*`, structured outputs, state snapshots, and completed run context into compact replay fixtures.

- Split replay coverage into two fixture kinds:
  - `planner_shape`: raw LLM response through parser, workflow normalization, compiler, repair, binder, verifier.
  - `candidate_gate`: saved stepwise prefix plus proposed candidate through candidate normalization, duplicate detection, strict binding, and contract checks.

- Add a pytest replay harness under test support code, with fixtures labeled by source experiment, turn, expected outcome, failure class, and fix number covered.

- Add a scripted stepwise dry-run harness that monkeypatches planner responses and subprocess execution while running the actual stepwise loop, duplicate detector, repair pass, candidate gate, binders, and progress tracking.

- **Refined:** Expand the tiny mini-benchmark from a single `control_evolution_mini` into a **mini-benchmark suite** covering at least three analysis types (evolution, variant-calling, DE) with synthetic small inputs. Each mini targets 60–120 seconds real tool time using actual BWA, samtools, freebayes, bcftools, STAR, featureCounts — **not simulators, not "-style" mocks**. Whole suite under 10 minutes.

- Add a fast-model preflight using `BIO_HARNESS_MODEL_HEAVY=qwen3-coder-next:latest`. **Refined:** documented as serving two distinct purposes: (a) **smoke test** before Qwen 3.6 commitment — never counts as "will pass Qwen 3.6"; (b) **regression loop** for harness changes that are provably model-agnostic (e.g. path-resolution bugs, artifact-contract checks). Refinement (b) has a hard asterisk — any fix touching planner prompts, LLM-output parsing, or retry policy cannot rely on preflight alone.

- Add a fast-signal scorecard script that compares replay/dry-run/mini-benchmark results against historical full-run outcomes, so we can learn which gates best predict benchmark success.

- **NEW:** Add a **reproduction-rate baseline study**. For the three most recent experiments (exp42, exp43, exp44), run each 10 times with identical config and fresh LLM sampling. Measure the {pass, fail-same-class, fail-different-class} distribution. The scorecard uses these baseline rates to compute **calibrated** gate predictiveness (i.e. "this gate's false-positive rate vs. the 23% inherent stochasticity of this experiment"), not raw pass/fail counts.

- **NEW:** Add a **plan-shape corpus study** for fixture seeding. Run Qwen 3.6's planner turn only (no tool execution) for 6–10 representative benchmark prompts × 20 repetitions × 3 temperatures ≈ 600 plan emissions in ~2h of LLM wall-clock. Extract per-emission idiom catalog (schema shape, tool distribution, argument forms, path styles, branch identification). Fixtures derived from this corpus cover the empirical distribution of Qwen 3.6 outputs, not just the 12 runs we've accumulated.

- **NEW:** Add **prompt-sensitivity probes** to the scripted dry-run harness. For prompt-based features we're considering (e.g. branch-stage progress hints from Pillar 9 of the stabilization plan), run 20 scripted scenarios with the feature present vs. absent and measure whether the model's emission shape actually changes. Features failing this probe (e.g. hint present but output unchanged) are kill-candidates.

## Implementation Plan

- **Phase 0 (overnight, parallel to Phase 1 setup): Reproduction-rate baseline.**
  - Launch 30 re-runs: 10× exp42, 10× exp43, 10× exp44, identical configs, fresh LLM sampling.
  - Unattended wall-clock ~8–10 hours.
  - Output: `workspace/studies/reproduction_rates.json` with {pass, fail-same-class, fail-different-class} distribution per experiment.
  - Acceptance: the scorecard implementation consumes this artifact before labeling any full-run outcome as "ground truth."

- **Phase 1: Historical fixture extraction and Tier 2 replay (highest immediate ROI).**
  - Extract exp33–exp44 raw planner emissions into versioned fixtures with the full metadata schema (source experiment, turn, expected outcome, failure class, fix number covered).
  - Include at least the known failure shapes from fixes #15–#28.
  - Add a regression fixture for exp44 where `bwa_mem_align[evol2]` must not be rejected as a duplicate of `bwa_mem_align[evol1]`.
  - **Refined:** split each fixture into `planner_shape` and `candidate_gate` kinds as applicable to the captured state.
  - Acceptance: the replay suite runs in about one second and catches current duplicate-detector behavior before the tactical fix.

- **Phase 2 (parallel to Phase 1): Plan-shape corpus study.**
  - ~2h Qwen 3.6 wall-clock, unattended, planner-only (no tool execution).
  - Output: `workspace/studies/plan_shape_corpus_qwen36.json` + a summary idiom distribution table.
  - Derive ~30–50 synthesized fixtures representing the most common idioms + edge-case tails.
  - Acceptance: fixture library coverage is provably representative, not convenience-sampled.

- **Phase 3: Tier 4 scripted dry-run as pytest before CLI.**
  - Script planner turns for an exp44-like evolution scenario.
  - Fake subprocess/tool results with realistic artifacts and return codes.
  - Exercise the real stepwise loop and candidate gate.
  - **Refined:** include prompt-sensitivity probes — scripted scenarios with prompt-feature-X on/off to validate that prompt-based pillars respond before we build them.
  - Acceptance: scripted exp44 reaches/accepts the next `evol2` branch work in under five seconds; prompt-sensitivity probes show a measurable delta (≥15% change in emission shape) for any prompt feature we keep.

- **Phase 4: Tier 5 fast-model preflight as documented gate.**
  - Use the existing benchmark command with model env overrides.
  - Treat failures as useful harness signals.
  - Treat passes as "safe to try Qwen 3.6," **not** as benchmark success.
  - **Refined:** maintain a "model-agnostic change" allowlist (e.g. strict binder fixes, path resolution, contract adapters) — changes within the allowlist may treat preflight as primary regression signal. Everything else (prompt edits, retry policy, LLM-output parsing) still requires Qwen 3.6.

- **Phase 5: Tier 3 mini-benchmark suite.**
  - Generate tiny deterministic biological inputs — **not** deterministic scientific answers.
  - Run real typed wrappers and real tool binaries over synthetic inputs.
  - **Refined:** build **three mini cases** at launch: `control_evolution_mini` (bacterial evolution shape), `germline_vc_mini` (single-sample variant calling), `de_mini` (two-condition RNA-seq DE). One per analysis-type family.
  - **Refined:** assertion granularity — assert *contract invariants*, not *scientific outputs*:
    - Correct: `variants_shared.csv` exists, non-empty, schema matches expected columns, row count > 0.
    - Avoid: asserting specific variant coordinates, specific log2FC values, specific p-values.
  - Rationale: tight scientific assertions false-fire on any legitimate change to the scientific pipeline; loose-but-contractual assertions catch harness bugs without overfitting.
  - Each mini targets 60–120 seconds; whole suite under 10 minutes.

- **Phase 6: Predictive scorecard with calibrated labels.**
  - For each historical and future experiment, record: fast gate outcomes, full-run outcome, failure class, elapsed time, and whether the fast gate would have blocked the run.
  - **Refined:** label each full-run outcome with a **reproduction-rate-weighted** confidence (from Phase 0 study). A single failing run with 80% reproduction rate counts differently from a single failing run with 30% reproduction rate.
  - Track false positives (gate rejected, full run would have passed) and false negatives (gate accepted, full run failed).
  - **Refined:** emit a monthly gate-effectiveness report: per-gate precision, recall, and false-discovery rate against reproduction-rate-calibrated ground truth.
  - Use this to decide whether future fixes need replay only, replay plus dry-run, or full mini-benchmark before Qwen 3.6.

- **Phase 7 (ongoing): Re-run studies as regression baselines.**
  - After any significant harness change (new pillar, new fix batch, prompt edit), re-run the plan-shape corpus study and diff against the previous artifact.
  - A detected shift in the empirical distribution is a signal the harness change altered planner behaviour — possibly unintentionally (e.g. new error message biased the retry distribution).
  - Cost: 2h LLM time per re-run.

## Test Cases And Scenarios

**Planner-shape fixtures:**
- `exp33_plan_key_shape`: raw `plan:` emission normalizes successfully (Fix #15 regression).
- `exp40_empty_branch_id`: plan with empty `branch_id` but objective-identifiable branch normalizes correctly (Fix #24 regression).
- `exp43_tabix_index_producer`: `bcftools_filter_run` with `output_type=z` contract-requires tabix post-step (Fix #27 regression).
- `corpus_qwen36_top10_idioms`: parameterized fixture covering top 10 idioms from the plan-shape corpus study — every idiom must produce a non-silently-corrupted plan.

**Candidate-gate fixtures:**
- `exp44_duplicate_branch`: candidate `evol2_aligned.bam` is not treated as a duplicate of completed `evol1_aligned.bam`.
- `exp44_work_masking`: completed `bwa_mem_align[evol1]` does not globally forbid `bwa_mem_align[evol2]`.
- `exp44_branch_progress`: next incomplete branch-stage cell points to `evol2 align` before downstream annotation.
- `corpus_qwen36_bare_filenames`: candidate emitting bare filenames is resolved correctly against at least one of the configured anchor directories (Fix #22b-refine regression).

**Dry-run scenarios:**
- `freebayes_concat_diagnostic`: plain VCF concat fallback does not produce misleading failure diagnosis in state.json.
- `prompt_sensitivity_branch_hint`: branch-stage hint on/off produces measurable change in candidate shape (Pillar 9 validation probe).
- `prompt_sensitivity_forbidden_work_wording`: "Forbidden tools" vs "Forbidden repeated work" wording changes the model's tool-substitution behaviour.

**Mini-benchmarks:**
- `control_evolution_mini`: real-tool miniature evolution run produces `variants_shared.csv` with correct schema and non-zero rows.
- `germline_vc_mini`: real-tool single-sample variant calling produces an indexed bgzipped VCF with correct header.
- `de_mini`: real-tool two-condition DE produces a result table with the expected column schema and non-zero rows.

**Preflight:**
- `fast_model_preflight_evolution`: fast-model run of `control_evolution` — tracked separately; passes are "safe to try Qwen 3.6," never "passed."

**Reproduction-rate baseline:**
- `reproduction_exp42`, `reproduction_exp43`, `reproduction_exp44`: 10 runs each, {pass, fail-same-class, fail-different-class} distribution recorded.

## Defaults And Assumptions

- Build the fast gates before launching the next expensive exp45-style Qwen 3.6 run.
- Use historical failures for harness-shape regression tests, not to hard-code benchmark scientific plans.
- Keep `qwen_true_no_templates` as a stress test, not the release gate.
- The release gate remains a clean full benchmark on the intended small open-source model after replay, dry-run, and mini-benchmark gates pass.
- Prefer pytest fixtures first; only promote dry-run behavior into a user-facing CLI once the test harness proves useful.
- **New:** assertion granularity in mini-benchmarks is *contract-level* (schema, existence, non-empty), not *scientific-level* (specific values). Scientific correctness is validated by the full benchmark.
- **New:** the scorecard's "ground truth" is reproduction-rate-weighted, not raw. A fix is "confirmed working" only when its failure class drops below the pre-fix reproduction rate minus the stochasticity interval.
- **New:** the plan-shape corpus is re-run after any prompt edit or analysis-spec change; a measurable distribution shift is a signal the harness change altered planner behaviour and should be investigated.
- **New:** mini-benchmark suite expansion ordering — start with evolution + germline + DE (three most diverse); add transcript-quant, phylogenetics, single-cell as bandwidth allows. Never more than 15 min total.

## Notes on Integration with Behavioral Studies Plan

Four of the studies described in `docs/HARNESS_BEHAVIORAL_STUDIES_PLAN.md` feed directly into this test ladder:

- **Study 1 (plan shape corpus)** → seeds Phase 2 fixture library expansion.
- **Study 2 (prompt sensitivity)** → validates Phase 3 prompt-sensitivity probes.
- **Study 3 (defense ablation)** → feeds Phase 6 scorecard's feature-flag-based gate evaluation.
- **Study 4 (reproduction rate)** → produces Phase 0 baseline, required for scorecard calibration.

The fifth study (Study 5, cross-model diff) feeds the stabilization plan's model-portability analysis but is not directly consumed by this test ladder — lower urgency.

## Open Questions Worth Deciding Before Phase 6

- **Scorecard storage and versioning.** The scorecard accumulates per-experiment labels over time. Where does it live — JSON file in `workspace/studies/`, SQLite in the repo, or external? Recommendation: JSON under `workspace/studies/scorecard.json`, one row per experiment, append-only, versioned in git-ignored workspace dir with weekly snapshots into `docs/scorecard_snapshots/` for historical analysis.
- **Gate precision-recall tradeoff.** Default stance when a gate has low precision (high false positives)? Options: (a) loosen the gate, (b) make it advisory not blocking, (c) accept the cost. Recommendation: start with (b) advisory-only for any gate with < 80% precision; promote to blocking only after the scorecard shows sustained precision.
- **What counts as "representative" in the plan-shape corpus.** Coverage threshold for when we consider the corpus sufficient? Recommendation: the corpus is "sufficient for fixture seeding" when the top-10 idioms cover ≥80% of emissions and the long tail is explicitly catalogued. Re-evaluate quarterly.

---

*End of refined plan. Substantive additions: reproduction-rate baseline (Phase 0), plan-shape corpus study (Phase 2), prompt-sensitivity probes (Phase 3), three-way mini-benchmark suite (Phase 5), scorecard calibration (Phase 6), regression study re-runs (Phase 7). Integration notes with the behavioral studies plan included.*
