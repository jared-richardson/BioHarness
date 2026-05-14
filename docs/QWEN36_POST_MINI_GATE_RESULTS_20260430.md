# Qwen 3.6 Post-Mini-Gate Results - 2026-04-30

## Summary

The mini preflight blocker was fixed, the targeted tests passed, and the
overnight Qwen 3.6 testing sequence completed successfully.

Final outcome:

- Control-evolution strict sentinel: 1/1 passed.
- Strict stress sweep, `qwen_true_no_templates`: 24/24 passed.
- Public/template-assisted sweep, `qwen_full`: 24/24 passed.
- All benchmark phases returned exit code 0.
- No generic fallback, protocol fallback, planner fail-open, or repairs were
  reported in the sweep summaries.

## Fixes Under Test

Two fixes were in scope for this run:

- Branch-frontier rebinding in
  `scripts/run_agent_e2e_stepwise_loop.py`: stale same-tool branch candidates
  are rebound to the active branch-stage frontier when they do not advance any
  frontier cell.
- Prodigal strict binder in
  `bio_harness/core/strict_artifact_binding_variant_binders.py`: stale
  `mode=single` is rewritten to `mode=auto`, letting the wrapper select `meta`
  for short assemblies while preserving long-assembly behavior.

## Fast Gates Completed Before Long Runs

Targeted pytest:

```text
130 passed, 5 warnings
```

Focused mini preflight:

```text
workspace/studies/fast_model_preflight_control_evolution_branch_rebind_prodigal_auto_20260429.json
status: pass
```

Full mini preflight suite:

```text
workspace/studies/fast_model_preflight_mini_after_branch_rebind_prodigal_auto_20260429_201002.json
status: pass
cases:
- control_evolution_mini
- germline_vc_mini
- de_mini
```

## Overnight Sequence

Driver:

```text
workspace/studies/overnight_qwen36_post_mini_gate_20260429_210000.sh
```

Status log:

```text
workspace/studies/overnight_qwen36_post_mini_gate_20260429_210000.status.jsonl
```

Completed phases:

```text
control_evolution_sentinel  passed  2026-04-29T23:23:25
strict_full_sweep           passed  2026-04-30T08:15:01
public_full_sweep           passed  2026-04-30T14:42:50
overnight_sequence          completed 2026-04-30T14:42:50
```

Runs used Qwen 3.6 as planner and Qwen Coder as executor:

```text
--planner-model-name qwen3.6:35b-a3b
--executor-model-name qwen3-coder-next:latest
--execution-mode stepwise
--llm-backend ollama
```

## Long Benchmark Results

### Control-Evolution Sentinel

Path:

```text
workspace/ablation_results/domain_expansion_ablation/qwen36_post_mini_gate_control_evolution_20260430
```

Summary:

```text
variant: qwen_true_no_templates
cases: 1
passed: 1
failures: 0
pass_rate: 100%
mean_runtime_seconds: 1248.275
```

Critical artifacts were present, including:

```text
selected/variants/evol1.ancestor_subtracted.vcf.gz
selected/variants/evol1.ancestor_subtracted.vcf.gz.tbi
selected/variants/evol2.ancestor_subtracted.vcf.gz
selected/variants/evol2.ancestor_subtracted.vcf.gz.tbi
selected/final/variants_shared.csv
```

### Strict Stress Sweep

Path:

```text
workspace/ablation_results/domain_expansion_ablation/qwen36_post_mini_gate_true_no_templates_20260430
```

Summary:

```text
variant: qwen_true_no_templates
cases: 24
passed: 24
failures: 0
pass_rate: 100%
band_1: 6/6
band_2: 10/10
band_3: 8/8
mean_runtime_seconds: 303.6265
generic_fallback_rate: 0.0
protocol_fallback_rate: 0.0
planner_failopen_rate: 0.0
mean_repairs: 0.0
```

### Public Sweep

Path:

```text
workspace/ablation_results/domain_expansion_ablation/qwen36_post_mini_gate_public_full_20260430
```

Summary:

```text
variant: qwen_full
cases: 24
passed: 24
failures: 0
pass_rate: 100%
band_1: 6/6
band_2: 10/10
band_3: 8/8
mean_runtime_seconds: 299.22
generic_fallback_rate: 0.0
protocol_fallback_rate: 0.0
planner_failopen_rate: 0.0
mean_repairs: 0.0
```

## Edge-Case Notes

- `stress_assembly_malformed` has `case_result.passed == true` in both full
  sweeps, while the underlying run state is `status == failed` because the
  planned Flye output was not produced from malformed inputs. This is an
  expected case-level pass according to the manifest evaluator, not a sweep
  failure.
- GATK emits native-library architecture warnings on Apple Silicon and falls
  back to the slower Java path. The germline cases still passed.
- SnpEff emits optional missing-file warnings for regulation/protein/motif
  side inputs. The relevant annotated VCF and final CSV artifacts were still
  produced, and the cases passed.

## Process State

No `qwen36_post_mini_gate` benchmark processes remained after completion.

