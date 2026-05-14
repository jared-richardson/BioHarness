# BioAgentBench Plan

## Purpose

This document defines how BioHarness should support BioAgentBench without benchmark leakage while still keeping the current protocol-grounded scientific harness useful for real users.

The core rule is simple:

- `official_bioagentbench` mode must be blind to benchmark truth files, benchmark results files, and task-specific hidden recipes.
- `scientific_harness` mode may keep deterministic compilers, stronger validators, and protocol grounding for real-world use.

We should report these as two different scoreboards.

## Current State

BioHarness is already strong at:

- turning weak model outputs into executable pipelines
- enforcing bioinformatics structure and file-path correctness
- validating outputs with deterministic scripts
- recovering from bad plans

BioHarness is not yet clean for official BioAgentBench reporting because:

- `bio_harness/core/protocol_grounding.py` reads task-local benchmark recipes from `external/*/tasks/*`
- `bio_harness/core/protocol_grounding.py` also reads `tasks/*/results/*.csv`
- `bio_harness/core/analysis_spec.py` and `bio_harness/core/llm.py` pass protocol-derived benchmark hints into planning
- `scripts/run_agent_e2e.py` allows deterministic protocol repair and full template fallback before execution
- several current compilers intentionally replace official task recipes with easier or more controlled pipelines

Those choices are valid for a scientific harness, but they are not benchmark-clean.

## Leakage Policy

### Allowed in `official_bioagentbench`

- the benchmark task prompt
- the benchmark input files
- user-visible file names, file structure, and file formats
- generic bioinformatics knowledge
- public tool manuals, `--help`, package vignettes, and assay best practices
- task-agnostic assay templates
- execution-time checks that do not inspect truth outputs
- generic output-format normalizers that are not benchmark-specific

### Not allowed in `official_bioagentbench`

- `external/bioagent-bench/tasks/*/run_script.sh`
- any file under benchmark `results/`
- any truth file used for planning or repair
- task-specific compiled plans derived from official benchmark recipes
- benchmark-specific column/header extraction from truth outputs
- tuning the planner with benchmark result files in the prompt context

### Allowed in `scientific_harness`

- everything above, plus protocol grounding, deterministic repair, benchmark-specific validators, and stronger output checks

## Product Split

We should support two explicit execution policies:

- `scientific_harness`
- `official_bioagentbench`

Recommended default:

- default to `scientific_harness` for local users
- require an explicit flag or env var for `official_bioagentbench`

Proposed env var:

- `BIO_HARNESS_BENCHMARK_POLICY=scientific_harness|official_bioagentbench`

## How To Help Weak Models Without Leakage

Weak models are the main practical problem. The solution is to make the harness smarter in generic ways, not to show the model the benchmark recipe.

### Assistance Ladder

Use this ladder in `official_bioagentbench` mode:

1. Infer assay type from the prompt and input files.
2. Build a generic analysis contract for that assay.
3. Generate multiple candidate plans.
4. Rank candidates with a non-truth plan critic.
5. Expand steps with tool-aware argument filling.
6. Run pre-execution lint checks.
7. Run the plan.
8. If execution fails, repair using generic assay rules only.

Never jump from a weak plan to a benchmark-specific hidden template.

### Generic Performance Improvements

- Assay-level templates, not task-level templates.
  - Example: "paired-end transcript quantification with transcriptome FASTA" is allowed.
  - Example: "the exact `transcript-quant` benchmark Salmon recipe" is not allowed.
- Candidate plan generation.
  - Ask the planner for 2-4 short candidate workflows.
  - Rank them with a second open-source model or a deterministic plan critic.
- Tool-aware step expansion.
  - Fill required arguments from discovered files, tool docs, and capability metadata.
- Pre-execution linting.
  - Check tool availability.
  - Check file-role compatibility.
  - Check that outputs flow into downstream inputs.
  - Check that deliverable type matches the task prompt.
- Runtime artifact critics.
  - Detect missing BAM/VCF/index/count artifacts.
  - Detect empty or clearly malformed outputs.
  - Trigger generic repair, not benchmark recipe substitution.
- Generic output normalizers.
  - Convert Salmon output to a simple TSV.
  - Convert VCF summaries to the requested CSV schema.
  - Normalize headers based on the task prompt, not truth files.

### Open-Source Model Strategy

Recommended planner stack for harder benchmark tasks:

- small fast model for draft planning
- stronger open-source model for critique or repair
- deterministic lint and artifact checks between them

This keeps the model loop narrow and reduces hallucinated pipelines without using benchmark leakage.

## Architecture Changes

### 1. Add Benchmark Policy

Files to change:

- `scripts/run_agent_e2e.py`
- `bio_harness/core/analysis_spec.py`
- `bio_harness/core/protocol_grounding.py`
- `bio_harness/core/llm.py`

Work:

- add a normalized benchmark policy value to run config and analysis spec
- propagate policy into planning, grounding, repair, and reporting
- make policy visible in run artifacts

Acceptance criteria:

- every run records whether it used `scientific_harness` or `official_bioagentbench`
- planning code can branch on policy without guessing from prompt text

### 2. Add Leakage Guard

Files to change:

- `bio_harness/core/protocol_grounding.py`
- `bio_harness/core/analysis_spec.py`
- `bio_harness/core/llm.py`

Work:

- disable `external/*/tasks/*` recipe discovery in `official_bioagentbench`
- disable `tasks/*/results/*` discovery in `official_bioagentbench`
- suppress benchmark profile IDs and benchmark-derived output columns in the analysis brief
- block benchmark-specific grounding hints from reaching the planner in official mode

Acceptance criteria:

- in official mode, the prompt context contains no benchmark recipe path and no truth/results path
- a unit test proves that `discover_protocol_files()` returns no `results/*.csv` in official mode

### 3. Add Assistance Manifest

Files to change:

- `scripts/run_agent_e2e.py`
- possibly `bio_harness/core/protocol_grounding.py`

Work:

- write `assistance_manifest.json` into each run directory
- record:
  - benchmark policy
  - whether protocol grounding fired
  - whether deterministic repair fired
  - whether a full template fallback was used
  - which source files were exposed to planning
  - whether any benchmark truth/results files were visible

Acceptance criteria:

- every benchmark run can be audited after the fact
- official runs fail closed if forbidden sources were exposed

### 4. Split Compilers Into Generic vs Benchmark-Specific

Files to change:

- `bio_harness/core/protocol_grounding.py`
- `tests/core/test_protocol_grounding.py`

Work:

- classify current compilers:
  - generic assay compilers
  - benchmark-specific compilers
- only allow generic compilers in official mode
- reserve benchmark-specific compilers for scientific mode

Likely generic:

- transcript quantification
- generic RNA-seq differential expression
- generic germline variant calling
- generic variant annotation
- generic phylogenetics

Likely benchmark-specific or currently too benchmark-shaped:

- bacterial shared evolution variant export
- current metagenomics benchmark path
- current viral metagenomics benchmark path
- current comparative-genomics minimap2 ANI path when used as a substitute for the official task

Acceptance criteria:

- official mode never applies a task-specific compiled plan derived from the benchmark repository

### 5. Add Official Benchmark Manifest

New file:

- `benchmark_data/bioagentbench_official_manifest.json`

Manifest should contain:

- task ID
- official prompt
- input root
- reference root
- expected deliverable type
- official output file pattern
- official evaluator command
- policy requirement
- current parity status

Acceptance criteria:

- official runs are launched from the manifest rather than ad hoc shell history

### 6. Add Official Runner

New script:

- `scripts/run_bioagentbench_official.py`

Responsibilities:

- load the official manifest
- stage each task under a clean run directory
- force `BIO_HARNESS_BENCHMARK_POLICY=official_bioagentbench`
- run the harness
- save run metadata and assistance manifest
- optionally hand off results to the official evaluator

Acceptance criteria:

- one command can execute the official benchmark suite in blind mode

### 7. Add Official Scoring Wrapper

New script:

- `scripts/score_bioagentbench_official.py`

Responsibilities:

- collect run outputs
- call the official evaluator if available
- produce:
  - machine-readable JSON
  - Markdown scorecard
  - per-task completion summary

Acceptance criteria:

- official reporting is reproducible from a clean run directory

## Task Parity Plan

Current rough status:

- `evolution`: close to official intent, but currently leaks recipe/results and uses benchmark-shaped export logic
- `transcript-quant`: close
- `giab`: partial
- `metagenomics`: diverged
- `viral-metagenomics`: diverged
- `comparative-genomics`: diverged
- `single-cell`: diverged
- `deseq`: partial
- `alzheimer-mouse`: not wired into current harness benchmark flow
- `cystic-fibrosis`: not wired into current harness benchmark flow

Harness-only tasks that should stay in the scientific suite:

- `variant_annotation`
- `phylogenetics`
- `dge_pathway`

### Parity Order

1. transcript-quant
2. evolution
3. giab
4. deseq
5. metagenomics
6. viral-metagenomics
7. comparative-genomics
8. single-cell
9. cystic-fibrosis
10. alzheimer-mouse

Reason:

- start with the tasks closest to current capability
- get an honest official baseline early
- add the hardest divergent tasks after the leakage guard is in place

## Reporting Plan

Always publish two tables.

### Official BioAgentBench

- policy: `official_bioagentbench`
- evaluator: official judge
- metric: `steps_completed / steps_to_completion`
- includes robustness runs when available

### BioHarness Scientific Suite

- policy: `scientific_harness`
- evaluator: deterministic validators
- metrics:
  - scientific accuracy
  - exact-match or overlap metrics
  - sanity checks
  - multi-run stability

We should not merge these into one number.

## Immediate PR Sequence

### PR1: Leakage Guard

- add benchmark policy plumbing
- disable recipe/results discovery in official mode
- add assistance manifest

### PR2: Official Manifest + Runner Skeleton

- add `benchmark_data/bioagentbench_official_manifest.json`
- add `scripts/run_bioagentbench_official.py`

### PR3: Generic Solver Upgrades

- add multi-candidate planning
- add plan critic
- add pre-execution lint

### PR4: First Parity Tasks

- make transcript-quant official-clean
- make evolution official-clean
- make giab official-clean enough to run honestly

### PR5: Reporting

- add official scorer wrapper
- add Markdown + JSON scorecards
- document how to reproduce

## Definition Of Done

We can claim official BioAgentBench compatibility only when:

- official mode is blind to benchmark truth/results/recipes
- assistance is fully logged
- the official runner is one command
- the official evaluator can score the outputs
- reported numbers are separated from the scientific harness numbers
- another user can reproduce the run from clone + setup instructions

Until then, the correct label is:

- "BioHarness scientific benchmark suite with BioAgentBench-inspired tasks"

