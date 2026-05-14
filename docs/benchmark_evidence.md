# Benchmark Evidence

This public tree includes compact evidence and fixtures, not full historical
run workspaces.

## Headline Evaluation

The balanced Qwen/Gemma campaign completed six 24-case variant sweeps:

- four Qwen split-local sweeps,
- two Gemma single-model sweeps,
- 144/144 case-runs passed,
- zero automatic repairs,
- zero generic fallbacks,
- zero protocol fallbacks,
- zero planner fail-open events.

An additional deployment-readiness sweep validates the recommended one-model
public Qwen path:

- `qwen_coder_single_model_full_20260501_r1`,
- variant `qwen_full`,
- `qwen3-coder-next:latest` used for both planner and executor,
- 24/24 cases passed,
- zero automatic repairs,
- zero generic fallbacks,
- zero protocol fallbacks,
- zero planner fail-open events.

Counted together, the balanced campaign plus this labeled deployment sweep
represent 168/168 passing case-runs. Keep the seventh sweep labeled: it tests
the supported/default `qwen_full` path, not single-model `qwen_true_no_templates`.

Primary evidence docs staged here include:

- `docs/EVALUATION_COMPLETED_CHECKLIST_20260501.md`
- `docs/QWEN36_TESTING_CONTEXT_20260428.md`
- `docs/QWEN36_POST_MINI_GATE_RESULTS_20260430.md`
- `docs/QWEN_CODER_SINGLE_MODEL_PAPER_SUMMARY_20260502.md`
- `docs/FAST_SIGNAL_FIXTURE_COVERAGE_20260430.md`

## Public Reproduction Surface

Included:

- `benchmark_data/manifests/ablation_manifest_24.json`
- `tests/fixtures/fast_signal/`
- focused tests under `tests/`
- mini-benchmark generator and validator scripts

Not included:

- full `workspace/ablation_results/`
- full `workspace/runs/`
- generated FASTQ/FASTA mini-benchmark data
- local model caches or tool environments

Generate mini-benchmark inputs with:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \
  --output-root workspace/benchmark_data/fast_signal_mini
```
