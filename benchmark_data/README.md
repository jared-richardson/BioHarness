# Benchmark Data

This directory contains public-safe benchmark metadata and generated-data entry
points.

## Included

- `manifests/ablation_manifest_24.json`: the 24-case manifest metadata.
- `fast_signal_mini/README.md`: instructions for generating tiny synthetic
  mini-benchmark inputs.

## Generated On Demand

Mini-benchmark FASTQ/FASTA inputs are generated locally:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \
  --output-root workspace/benchmark_data/fast_signal_mini
```

The generated `manifest.json` contains absolute paths for the machine that
created it, so it is intentionally not staged from the private workspace.
