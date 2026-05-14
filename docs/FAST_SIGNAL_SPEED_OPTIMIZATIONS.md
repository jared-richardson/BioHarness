# Fast-Signal Speed Optimizations

**Status:** Draft v2 for review
**Date:** 2026-04-25
**Owner:** Bio-Harness team
**Companion docs:**

- `FAST_SIGNAL_COMPLETION_PLAN_20260425.md` - completion plan and release gate
- `FAST_SIGNAL_TEST_LADDER_REFINED.md` - original ladder
- `FAST_SIGNAL_TESTING_LEARNINGS_20260425.md` - post-r18 evidence record

## Operating Principle

Quality first, harness improvement second, speed third.

An optimization is in scope only if it does not change the meaning of a
measurement, the calibration of a gate, or the correctness of a fix. If an
optimization changes sampling, retry behavior, precision, prompts, or tool
outputs, it is not a pure speed optimization. It becomes a methodology change
and must be labeled as such.

Every speed-related run should record an `optimization_profile` in the scorecard
metadata. Suggested profile names:

- `safe_local` - no semantic changes; only caching, keep-alive, fixture reuse,
  or parallel local tests.
- `measurement_parallel_checked` - measurement-only concurrency after a local
  stall-timeout check.
- `methodology_adaptive` - sequential sampling, adaptive stopping, or other
  changes to what is measured.
- `exploratory_only` - cannot contribute to `release_gate_status() == "go"`.

## Current Environment Check

These checks were performed while reviewing this plan:

- `scripts/run_fast_signal_reproduction_baseline.py` exists and has a working
  `--dry-run`/`--help` path, but it does not yet expose all A0/A1 metadata
  required by the completion plan, such as `measurement_purpose`,
  `override_reason`, or an explicit `infra_error` class.
- `scripts/run_fast_model_preflight.py` exists and prints a useful dry-run
  command, but it does not yet expose the full model-agnostic allowlist or
  scorecard metadata planned in A4.
- `pytest -n auto` does not work in the current environment because
  `pytest-xdist` is not installed. Any xdist optimization must auto-detect the
  plugin and fall back to serial pytest.

These are not blockers. They are implementation notes to prevent the plan from
assuming an optimization is already available when it is only planned.

## Where Time Actually Goes

The dominant cost is Qwen 3.6 LLM time. Long sentinels and reproduction
baselines are orders of magnitude more expensive than replay fixtures, dry-run
tests, or focused unit tests.

Therefore, the highest-value speedups either:

- reduce the number of ambiguous long runs,
- increase safe LLM throughput for measurement runs,
- avoid repeated setup work that does not affect scientific outputs,
- terminate measurement runs once their diagnostic evidence is already captured.

Optimizing an already-second-scale replay suite is useful, but it is not where
the big savings come from.

## Tier 1 - Safe Local Wins

These should be adopted routinely because they do not change model emissions,
tool outputs, gate semantics, or labels.

### 1.1 Ollama Keep-Alive For Studies

Set `OLLAMA_KEEP_ALIVE` long enough for Phase 0 and Phase 2 measurement studies
so Qwen 3.6 does not unload between replicates.

Guardrail:

- Record the keep-alive setting in scorecard metadata.
- Verify once with `/api/ps` or equivalent that the model remains loaded.

### 1.2 Prompt Prefix Cache Detection

Repeated reproduction and corpus calls often share a long prompt prefix. If the
backend supports prefix caching, let it work by keeping prompts stable and
avoiding unnecessary prefix churn.

Guardrail:

- Treat this as best-effort backend behavior, not a required feature.
- Do not change prompt text just to improve cache hits unless the change goes
  through prompt-probe measurement.

### 1.3 Pytest Parallelism With Auto Fallback

Replay and dry-run tests can use `pytest-xdist` when installed.

Guardrail:

- The runner should detect whether `pytest -n auto` is available.
- If not available, run serial pytest rather than failing the gate.
- Do not make xdist availability a correctness requirement.

Current status:

- `pytest-xdist` is not installed in this environment.

### 1.4 Hash-Pinned Synthetic Inputs For Mini-Benchmarks

Generate synthetic mini-benchmark inputs once, hash-pin them, and reuse them.

Guardrail:

- Cache inputs only, not final tool outputs.
- Include fixture-generation code version, input hashes, and relevant tool
  versions in the cache metadata.
- Real tools still run on every mini-benchmark execution.

### 1.5 Prepared Reference And Index Cache

Build deterministic reference indexes once per input/tool-version hash and
reuse them across reproduction replicates.

Guardrail:

- Cache only immutable inputs and deterministic indexes, such as BWA indexes and
  FASTA indexes.
- Never cache branch outputs, BAMs, VCFs, final CSVs, or any artifact whose
  production is part of the harness behavior under test.
- Prefer read-only copies or hardlinks into fresh replicate directories.
- Key the cache by input hash, tool version, wrapper version, and relevant
  environment metadata.

### 1.6 Checkpoint And Resume Measurement Studies

Reproduction and corpus studies should resume from completed scorecard rows and
study outputs instead of starting from scratch after interruption.

Guardrail:

- Resume logic must detect duplicate replicate IDs and refuse to double-count
  them.
- Resumed studies must preserve the original model, prompt, and optimization
  metadata.

## Tier 2 - Safe After One-Time Checks

These can save substantial time but need an environment-specific check before
adoption. They are primarily for measurement runs, not final validation.

### 2.1 Concurrent Qwen 3.6 Requests For Measurement Runs

Use `OLLAMA_NUM_PARALLEL` for Phase 0 and Phase 2 measurement runs after a
stall-timeout check.

One-time check:

- Run a single measurement replicate at serial settings.
- Run a small concurrent batch with the proposed `OLLAMA_NUM_PARALLEL`.
- Confirm no spurious stall timeouts, hangs, or infra errors.
- Confirm artifact classes and high-level step sequence remain comparable.

Guardrail:

- Use only for runs labeled `measurement`.
- Record `OLLAMA_NUM_PARALLEL`, timeout settings, and observed stall metrics in
  scorecard metadata.
- Do not use concurrent requests for final validation sentinels unless the team
  explicitly decides timing no longer matters for that run class.

### 2.2 B1/B2 Measurement Pipelining

Phase 0 reproduction and Phase 2 planner-only corpus collection may overlap
under controlled concurrency because their workloads differ.

One-time check:

- Run a small corpus batch while one reproduction replicate is active.
- Compare corpus idiom distribution against a solo corpus baseline.
- Confirm neither workload causes the other to hit false stall/timeout signals.

Guardrail:

- Use only after 2.1 passes.
- Record overlap metadata in both study outputs.

### 2.3 Speculative Decoding Only If Target-Preserving

Speculative decoding is acceptable for calibrated measurement only if the
backend documents and demonstrates target-distribution-preserving verification.

One-time check:

- Confirm the local backend supports speculative decoding for the exact model
  pair.
- Run a small equivalence check against non-speculative decoding.
- Confirm throughput improves.

Guardrail:

- If the backend cannot demonstrate target-preserving behavior, speculative
  decoding is `exploratory_only` and cannot contribute to release-gate `go`.
- Record draft model, verifier model, backend version, and settings in the
  scorecard row.

### 2.4 Diagnostic Early Termination For Measurement Runs

When a measurement run emits a structured diagnostic signal that already proves
the target failure class reproduced, terminate after a short grace period and
record `interrupted_after_signal`.

Guardrail:

- Use only for measurement runs, not validation sentinels.
- The diagnostic signal must include failure class, run ID, turn/step, raw
  candidate reference, and scorecard row metadata.
- The wrapper must preserve logs and state before termination.

### 2.5 Same-Shape Retry Coalescing

If a planner emits the exact same rejected shape repeatedly, retrying at the
same settings can waste LLM time. A coalescer can bump retry settings or stop
early.

Guardrail:

- Treat this as a harness behavior change, not a pure speedup.
- Require replay and dry-run coverage before enabling it.
- Record when a retry was coalesced.
- Do not use coalescing to hide a repeated valid rejection; the rejected shape
  must still become a fixture if it represents a real failure class.

## Tier 3 - Methodology Changes

These change what is measured. Adopt only after A0/A1/A6 are in place, because
the scorecard must be able to label the change.

### 3.1 Sequential Reproduction Sampling

Instead of a fixed replicate count, continue until the confidence interval is
tight enough or a maximum replicate budget is reached.

Guardrail:

- Document the stopping rule before running.
- Store the stopping rule, replicate count, interval, and decision in
  `reproduction_rates.json`.
- Fixed-N remains the default until this policy is accepted.

### 3.2 Adaptive Stop On Novel Class

If repeated replicates reveal a new failure class, stop the remaining replicates
and convert the class into fixtures before continuing.

Guardrail:

- Define "novel" as a failure class absent from the scorecard's known set.
- Record the stop as a measurement finding, not a validation failure.
- Resume the baseline only after the new class has fixture coverage or is
  explicitly accepted as tail risk.

### 3.3 Stratified Corpus Sampling

Oversample prompts or temperatures that produce rare but important idioms.

Guardrail:

- Preserve an unstratified baseline for the primary idiom histogram.
- Tag stratified emissions so corpus coverage calculations do not pretend they
  were uniformly sampled.

### 3.4 Change-Aware Gate Routing

Skip advisory gates when earlier evidence is sufficient for the changed code
surface.

Guardrail:

- The router must use the same model-agnostic allowlist as A4.
- It may skip advisory gates, but it must not bypass known red relevant
  fixtures.
- Snapshot output must distinguish "router skipped" from "operator overrode."

## Tier 4 - Infrastructure Options

These are useful only if the resources exist.

### 4.1 Second-Machine Offload

Run reproduction and corpus studies on separate machines with separate loaded
models.

Guardrail:

- Study outputs must be mergeable without double-counting rows.
- Scorecard rows must include machine ID, model ID, backend version, and commit
  or tree hash.

### 4.2 Lower-Precision Or Alternate Quantization

Lower precision may be useful for exploratory throughput checks.

Guardrail:

- It changes emission distribution.
- It must use `optimization_profile=exploratory_only`.
- It cannot contribute to `release_gate_status() == "go"` for the calibrated
  Qwen 3.6 gate.

## Tempting But Rejected

Do not adopt these as speedups:

- Downgrading quantization for routine validation.
- Shrinking context window globally.
- Skipping replay because a change "looks small."
- Caching final artifacts instead of rerunning real tools.
- Parallelizing dependency-ordered policy steps such as A0 before A1/A6.
- Treating fast-model preflight as proof that Qwen 3.6 will pass.
- Letting early-terminated measurement runs count as full validation passes.

## Extra Safe Optimizations To Add

These were not explicit in the first draft and should be part of the plan.

### Study Sharding With Deterministic Merge

Allow reproduction and corpus studies to be split into shards.

Guardrail:

- Shard IDs and replicate IDs must be globally unique.
- Merge refuses duplicate `(experiment_id, replicate_id, shard_id)` tuples.
- Merged scorecard order must not affect summaries.

### Fixture Replay Selection By Changed Surface

Run all relevant fixtures by default, but make the fixture runner able to report
which fixtures are relevant to a change surface.

Guardrail:

- This is a reporting optimization first, not a skipping policy.
- Skipping remains a Tier 3 change-aware routing decision.

### Log Tail And Artifact Probe Throttling

Long runs produce large logs. Throttle repeated artifact probes and store
bounded log tails where full logs are not needed for scoring.

Guardrail:

- Full raw logs for failed runs must remain available.
- Throttling must not suppress diagnostic events, candidate rejection records,
  or final artifact checks.

## Adoption Sequence

Recommended order:

1. Add scorecard metadata for `optimization_profile`.
2. Adopt Tier 1 with fallbacks, especially keep-alive, input/index caching, and
   study resume.
3. Add A8 in the completion plan to own these speed-safe execution controls.
4. Run the one-time concurrency check for `OLLAMA_NUM_PARALLEL`.
5. Adopt Tier 2.1 for measurement runs if the check passes.
6. Consider Tier 2.2 and Tier 2.3 only after Tier 2.1 is stable.
7. Defer Tier 3 until A0/A1/A6 are implemented.
8. Treat Tier 4 as opportunistic infrastructure, not a dependency.

## Bottom Line

The safest speed comes from avoiding ambiguous long runs and avoiding repeated
setup work, not from making the unit tests a few seconds faster.

Adopt Tier 1 immediately, but with auto-detection and fallbacks. Adopt Tier 2
only after one-time checks and only for labeled measurement runs. Treat Tier 3
as policy work, not engineering housekeeping.

The completion plan should include these optimizations, but the gate must always
prefer slower honest evidence over faster ambiguous evidence.
