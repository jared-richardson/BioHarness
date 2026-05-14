# Fast-Signal Methodology

The fast-signal ladder is the cheap regression layer used before long local LLM
benchmark sweeps.

Publicly staged pieces:

- 38 curated replay fixtures under `tests/fixtures/fast_signal/`
- fixture replay runner: `scripts/replay_fast_signal_fixtures.py`
- scorecard CLI: `scripts/fast_signal_scorecard.py`
- mini-benchmark generator: `scripts/prepare_fast_signal_mini_benchmarks.py`
- mini-benchmark validator: `scripts/validate_fast_signal_mini_benchmarks.py`
- fast-model preflight: `scripts/run_fast_model_preflight.py`

Fixture kinds:

- `planner_shape`: raw planner emissions through parse/normalize/compile/repair.
- `candidate_gate`: saved stepwise prefix plus candidate through duplicate,
  binding, masking, and contract checks.

The mini-benchmark suite uses generated tiny real inputs and contract-level
assertions. It checks existence, schema, sidecars, and non-empty outputs rather
than exact scientific coordinates or p-values.
