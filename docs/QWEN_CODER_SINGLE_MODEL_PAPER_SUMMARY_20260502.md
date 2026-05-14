# Qwen Coder Single-Model 24-Case Sweep - Paper Summary

## Reviewer TL;DR

After the main Qwen 3.6 split-local and Gemma single-model evaluations, we ran
an additional deployment-readiness sweep to answer a practical release question:
does the supported Qwen path require two local model downloads, or can a user run
Bio-Harness with one Qwen model?

The answer is positive for the supported/default Qwen path. Using
`qwen3-coder-next:latest` as both planner and executor, Bio-Harness passed the
full 24-case manifest under `qwen_full`: 24/24 cases passed, with zero automatic
repairs, zero generic fallbacks, zero protocol-template fallbacks, and zero
planner fail-open events.

This is not a replacement for the earlier Qwen 3.6 strict stress evidence. It is
a new single-model deployment validation for the public/default Qwen mode.

## What Was Tested

Run label:

```text
qwen_coder_single_model_full_20260501_r1
```

Configuration:

| Field | Value |
| --- | --- |
| Variant | `qwen_full` |
| Planner model | `qwen3-coder-next:latest` |
| Executor model | `qwen3-coder-next:latest` |
| Model digest prefix | `ca06e9e4087c` |
| Backend | Ollama `0.20.0` |
| Execution mode | `stepwise` |
| Manifest | 24-case domain-expansion ablation manifest |
| Attempt label | `qwen_coder_single_model_full_20260501_r1` |

Launch command:

```bash
PYTHONUNBUFFERED=1 OLLAMA_KEEP_ALIVE=12h python3 scripts/run_domain_expansion_ablation.py \
  --variant qwen_full \
  --attempt-label qwen_coder_single_model_full_20260501_r1 \
  --model-name qwen3-coder-next:latest \
  --execution-mode stepwise \
  --heartbeat-seconds 60 \
  --stall-timeout-seconds 900 \
  --live-process-grace-seconds 300 \
  --case-timeout-seconds 4200 \
  --llm-backend ollama
```

The important methodological detail is `--model-name qwen3-coder-next:latest`.
In this runner, `--model-name` overrides both the executor model
(`BIO_HARNESS_MODEL`) and the planner model (`BIO_HARNESS_MODEL_HEAVY`), making
this a true single-model Qwen Coder sweep.

## Main Result

| Metric | Result |
| --- | ---: |
| Cases | 24 |
| Passed | 24 |
| Failed | 0 |
| Pass rate | 1.000 |
| Band 1 | 6/6 |
| Band 2 | 10/10 |
| Band 3 | 8/8 |
| Mean runtime | 280.4067 s/case |
| Mean repairs | 0.0 |
| Generic fallback rate | 0.0 |
| Protocol-template fallback rate | 0.0 |
| Planner fail-open rate | 0.0 |

Primary evidence:

- `workspace/ablation_results/domain_expansion_ablation/qwen_coder_single_model_full_20260501_r1/summary.json`
- `workspace/ablation_results/domain_expansion_ablation/qwen_coder_single_model_full_20260501_r1/status.json`
- `workspace/ablation_results/domain_expansion_ablation/qwen_coder_single_model_full_20260501_r1/results.jsonl`
- `workspace/ablation_results/domain_expansion_ablation/qwen_coder_single_model_full_20260501_r1/qwen_full/suite_summary.json`
- `workspace/ablation_results/domain_expansion_ablation/qwen_coder_single_model_full_20260501_r1/qwen_full/ablation_summary.json`

Spot-checked primary artifacts:

- `qwen_full/control_evolution/selected/final/variants_shared.csv`
- `qwen_full/stress_noisy_evolution/selected/final/variants_shared.csv`
- `qwen_full/control_deseq/selected/final/deseq_results.csv`
- `qwen_full/control_germline/selected/final/variants.vcf`

## Per-Case Results

| Case | Status | Score | Repairs | Runtime (s) |
| --- | --- | ---: | ---: | ---: |
| `control_evolution` | pass | 1.0 | 0 | 1272.123 |
| `control_deseq` | pass | 1.0 | 0 | 2763.461 |
| `control_germline` | pass | 1.0 | 0 | 119.944 |
| `control_transcript` | pass | 1.0 | 0 | 83.849 |
| `control_phylogenetics` | pass | 1.0 | 0 | 52.188 |
| `control_singlecell` | pass | 1.0 | 0 | 58.253 |
| `domain_longread_sv` | pass | 1.0 | 0 | 85.208 |
| `domain_longread_assembly` | pass | 1.0 | 0 | 69.570 |
| `domain_spatial` | pass | 1.0 | 0 | 45.891 |
| `domain_proteomics` | pass | 1.0 | 0 | 46.926 |
| `domain_metabolomics` | pass | 1.0 | 0 | 82.005 |
| `domain_metagenomics` | pass | 1.0 | 0 | 109.203 |
| `domain_viral_meta` | pass | 1.0 | 0 | 138.111 |
| `domain_variant_annot` | pass | 1.0 | 0 | 67.856 |
| `domain_alzheimer` | pass | 1.0 | 0 | 50.041 |
| `domain_cystic_fibrosis` | pass | 1.0 | 0 | 49.978 |
| `stress_longread_wrong_preset` | pass | 1.0 | 0 | 86.268 |
| `stress_proteomics_missing` | pass | 1.0 | 0 | 48.030 |
| `stress_metabolomics_missing` | pass | 1.0 | 0 | 47.978 |
| `stress_spatial_fragment` | pass | 1.0 | 0 | 48.949 |
| `stress_noisy_evolution` | pass | 1.0 | 0 | 1253.778 |
| `stress_noisy_deseq` | pass | 1.0 | 0 | 8.710 |
| `stress_assembly_malformed` | pass | 1.0 | 0 | 51.120 |
| `stress_germline_no_rg` | pass | 1.0 | 0 | 90.321 |

The long runtimes for `control_evolution`, `stress_noisy_evolution`, and
`control_deseq` came from real tool execution and repeated stepwise planning,
not from harness failures. During monitoring, the run showed fresh tool/process
heartbeats and advanced through expected workflow stages.

## How This Changes The Paper Framing

Before this run, the cleanest statement was:

> Bio-Harness passed six 24-case variant sweeps: four Qwen split-local sweeps
> and two Gemma single-model sweeps, for 144/144 passing case-runs.

After this run, there are two honest framing options.

### Conservative Main-Result Framing

Keep the main six-sweep matrix as the balanced cross-configuration result:

> Bio-Harness passed six paired 24-case sweeps spanning Qwen split-local and
> Gemma single-model configurations, for 144/144 passing case-runs. An
> additional deployment-readiness sweep showed that the supported Qwen default
> mode also runs as a single-model configuration: `qwen3-coder-next:latest`
> passed 24/24 cases when used for both planning and execution.

This is the safest framing because the added Qwen Coder run is a single
`qwen_full` sweep, not a paired strict-plus-full Qwen Coder evaluation.

### Expanded Evidence-Count Framing

If the paper chooses to include this sweep in the headline evidence count:

> Across seven completed 24-case sweeps, Bio-Harness passed 168/168 case-runs.
> These sweeps include Qwen 3.6 split-local strict/default validation, Gemma
> 4 26B single-model strict/default validation, and a single-download Qwen Coder
> default-mode deployment validation.

This is accurate, but should be accompanied by a footnote or table note:

> The seventh sweep is a one-sided deployment-readiness validation of
> `qwen_full` with `qwen3-coder-next:latest` as both planner and executor; it is
> not a single-model `qwen_true_no_templates` stress sweep.

## Recommended Manuscript Insertion

Suggested Results paragraph:

> To test whether the public Qwen configuration requires separate local planner
> and executor models, we ran an additional deployment-readiness sweep using
> `qwen3-coder-next:latest` for both model roles. Under the default
> template-assisted `qwen_full` configuration, this single-model Qwen Coder run
> passed all 24 manifest cases, including 6/6 control cases, 10/10 domain
> extension cases, and 8/8 stress cases. The run completed with zero automatic
> repairs, zero generic fallbacks, zero protocol-template fallbacks, and zero
> planner fail-open events. This result establishes a one-download Qwen path for
> the supported public mode, while the earlier Qwen 3.6 plus Qwen Coder
> split-local sweeps remain the stricter hardening evidence for the
> no-template stress configuration.

Suggested Methods/Setup paragraph:

> Model roles are configurable. `BIO_HARNESS_MODEL` controls execution-side and
> repair/classification calls, while `BIO_HARNESS_MODEL_HEAVY` controls planner
> calls. The Qwen 3.6 stress campaign used a split-local configuration
> (`qwen3.6:35b-a3b` planner plus `qwen3-coder-next:latest` executor), whereas
> Gemma 4 26B and the added Qwen Coder deployment sweep used the same local model
> for both roles. In the Qwen Coder single-model sweep, both roles were set with
> `--model-name qwen3-coder-next:latest`, corresponding to Ollama digest prefix
> `ca06e9e4087c`.

Suggested Discussion paragraph:

> The single-model Qwen Coder result is practically important for public
> release. It means external users can reproduce the supported Qwen default path
> with one model download rather than a planner/executor pair. At the same time,
> this does not eliminate the value of the split-local Qwen 3.6 experiments:
> the weaker planner stress setting surfaced many of the harness-hardening
> failures that the final system now guards against. We therefore interpret the
> Qwen Coder run as deployment validation, and the Qwen 3.6 no-template runs as
> stress-test evidence.

## Claims This Supports

Supported:

- The public/default Qwen mode can run with a single local model download.
- `qwen3-coder-next:latest` passed the full 24-case manifest when used as both
  planner and executor.
- The one-model Qwen default sweep had no repairs, no generic fallbacks, no
  protocol-template fallbacks, and no planner fail-open events.
- The deployment story can now present `qwen3-coder-next:latest` as the simplest
  Qwen setup path.

Not supported by this run alone:

- It does not show that single-model Qwen Coder passes `qwen_true_no_templates`.
- It does not replace the Qwen 3.6 split-local stress result.
- It does not prove every future Qwen Coder digest will reproduce the same
  behavior.
- It does not prove that all small open-source models can run single-model.

## Recommended Updates To Existing Paper Plan

1. Update model-configuration language:
   - Old: "Qwen evidence is split-local; Gemma is the single-model evidence."
   - New: "Qwen 3.6 strict/default evidence is split-local; Gemma strict/default
     and Qwen Coder default-mode evidence include single-model operation."

2. Update the results table:
   - Add row: `qwen_coder_single_model_full_20260501_r1`,
     variant `qwen_full`, configuration `single-model`, 24 cases, 24 passed,
     0 failures, 0 repairs.

3. Update setup/public-release framing:
   - Recommended default for public Qwen users:
     `ollama pull qwen3-coder-next:latest`.
   - Optional research/stress configuration:
     `qwen3.6:35b-a3b` planner plus `qwen3-coder-next:latest` executor.

4. Update headline carefully:
   - Use 144/144 if discussing the balanced six-sweep Qwen/Gemma matrix.
   - Use 168/168 only if the new one-sided Qwen Coder deployment sweep is
     explicitly included and labeled.

5. Update limitations:
   - Single-model Qwen Coder has been validated for the supported `qwen_full`
     path, not the no-template stress path.

