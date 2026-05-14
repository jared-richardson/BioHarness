# Fast-Signal Mini-Benchmarks

Generate the three tiny real-input mini-benchmark cases here:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \
  --output-root workspace/benchmark_data/fast_signal_mini
```

Cases:

- `control_evolution_mini`
- `germline_vc_mini`
- `de_mini`

Run the advisory fast-model preflight:

```bash
python3 scripts/run_fast_model_preflight.py \
  --suite mini \
  --mini-root workspace/benchmark_data/fast_signal_mini \
  --output-json workspace/studies/fast_model_preflight_latest.json
```

The mini suite uses generated synthetic inputs and contract-level checks. It is
not a replacement for the full 24-case benchmark sweeps.
