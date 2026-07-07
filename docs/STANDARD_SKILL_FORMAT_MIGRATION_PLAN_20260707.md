# Standard Skill Format Migration Plan

## Purpose

Bio-Harness currently uses the word "skill" for two related but different
things:

- Standard agent skills: directories containing a `SKILL.md` file with
  instructions for an agent. These already exist in a few places, such as
  `docs/agent_skills/analysis-design-review/SKILL.md`.
- Bio-Harness executable tool contracts: YAML-frontmatter Markdown definitions
  in `bio_harness/skills/definitions/*.md`, paired with Python wrappers in
  `bio_harness/skills/library/*.py` and `bio_harness/pipeline_scripts/*.py`.

The second category is the main bioinformatics capability layer. It is useful
and execution-safe, but it is not standard `SKILL.md` packaging. The public
software, README, manuscript, and presentation should not imply that the 82
bioinformatics tool contracts are already standard agent skills unless we add
that packaging layer.

This plan fixes the mismatch without destabilizing the validated harness.

## Guiding Principles

1. Preserve the validated execution path first.
   The current contract-plus-wrapper system is what passed the release
   benchmarks. Do not replace it with free-form skill prose.

2. Add standard `SKILL.md` packaging as a facade before changing runtime
   behavior.
   The first implementation should create real skill directories but should not
   alter planner prompts, tool selection, or benchmark behavior.

3. Keep machine-readable contracts.
   `SKILL.md` is good for agent instruction and human review. Strict execution
   still needs structured parameters, output roles, risk metadata, and command
   templates.

4. Use generated files for broad conversion.
   The existing definitions already contain most of the content needed for
   `SKILL.md`. Manual conversion of 80+ capabilities is unnecessary and risky.

5. Treat any planner-facing wording change as benchmark-sensitive.
   If the model sees different tool descriptions or different prompt wording,
   run the fast-signal gates before trusting the change.

## Current State

Primary executable tool definitions:

```text
bio_harness/skills/definitions/*.md
```

Each file contains YAML frontmatter like:

```yaml
name: bcftools_call
description: Call variants via bcftools mpileup + call pipeline.
when_to_use: Use for variant calling via bcftools mpileup+call pipeline on aligned BAM data
when_not_to_use: Not for somatic calling or haplotype-aware germline calling
risk_level: medium
tools_required:
  - bcftools
  - samtools
parameters:
  reference_fasta:
    type: path
    required: true
command_template: python3 bio_harness/pipeline_scripts/run_bcftools_call.py ...
```

Executable wrappers:

```text
bio_harness/skills/library/*.py
bio_harness/pipeline_scripts/*.py
```

Existing standard-style agent skills:

```text
docs/agent_skills/analysis-design-review/SKILL.md
docs/agent_skills/analysis-output-review/SKILL.md
bio_harness/pipeline_scripts/SKILL.md
```

Immediate concern:

- README and other materials use "skills" for the tool-contract layer.
- The UI/readme mention creating `SKILL.md`, but the core tool library is not
  organized that way.

## Target Model

Use three precise terms:

- Capability: the scientific action Bio-Harness can perform, such as alignment,
  variant calling, annotation, quantification, or differential expression.
- Tool contract: the machine-readable execution contract used by the harness.
  It records parameters, file roles, output types, risk, requirements, and the
  wrapper command.
- Agent skill: a standard `SKILL.md` package that explains when and how an
  agent should use a capability.

Recommended target directory shape:

```text
bio_harness/skills/catalog/
  bcftools_call/
    SKILL.md
    contract.yaml
  bwa_mem_align/
    SKILL.md
    contract.yaml
  deseq2_run/
    SKILL.md
    contract.yaml
```

The current execution files stay in place:

```text
bio_harness/skills/definitions/*.md
bio_harness/skills/library/*.py
bio_harness/pipeline_scripts/*.py
```

The first migration stage should make `catalog/<name>/SKILL.md` and
`catalog/<name>/contract.yaml` generated outputs from the existing definitions.
Only after validation should we consider making `catalog/<name>/contract.yaml`
canonical.

## Standard `SKILL.md` Template

Each generated skill package should have minimum frontmatter:

```yaml
---
name: bcftools_call
description: Call variants with bcftools mpileup plus call on an aligned BAM or CRAM file.
---
```

Suggested body:

```markdown
# bcftools_call

## What This Does
Runs bcftools variant calling on an aligned BAM or CRAM file and produces a
compressed VCF.

## Use This Skill When
- You need lightweight germline variant calling from aligned reads.
- You have a reference FASTA and an input BAM or CRAM.
- The analysis does not require a haplotype-aware caller.

## Do Not Use This Skill When
- The task is somatic variant calling.
- The task requires haplotype-aware germline calling.
- The input reads have not been aligned yet.

## Required Inputs
- `reference_fasta`: reference genome FASTA.
- `input_bam`: aligned BAM or CRAM.
- `output_vcf_gz`: compressed output VCF path.

## Expected Outputs
- A compressed VCF file.

## Execution Contract
The harness executes the typed contract in `contract.yaml`; do not invent file
paths or bypass the wrapper.

## Validation Notes
The output should exist, be non-empty, and be compatible with downstream VCF
indexing or filtering steps when required by the workflow.
```

The exact sections can be generated from the current metadata fields:

- `description`
- `when_to_use`
- `when_not_to_use`
- `parameters`
- `input_types`
- `output_types`
- `tools_required`
- `system_requirements`
- `command_template`

## Phase 0: Terminology Safety Pass

Goal: stop making an inaccurate public claim while the migration is underway.

Actions:

1. Update public-facing language to distinguish "tool contracts" from
   "agent skills."
2. Replace broad claims like "82 skills" with "82 typed bioinformatics tool
   contracts" unless the claim specifically refers to real `SKILL.md` packages.
3. Keep "skills" only for:
   - standard `SKILL.md` agent skills, or
   - clearly qualified terms like "Bio-Harness tool skills" in internal code
     where renaming would be too disruptive.

Files to audit:

```text
README.md
docs/software_release/*
docs/manuscript*
docs/*Seminar*
ui_v2/*
ui_v2_api.py
bio_harness/analysis/harness_capabilities.py
scripts/build_manuscript_assets.py
```

Acceptance:

- A reader can tell that the benchmarked execution layer is a typed contract
  system.
- No public claim implies the current 82 files are already standard
  `SKILL.md` packages.

## Phase 1: Add Generated Standard Skill Packages

Goal: create real `SKILL.md` packages without changing runtime behavior.

Actions:

1. Add a generator script:

   ```text
   scripts/generate_standard_skill_packages.py
   ```

2. For each `bio_harness/skills/definitions/<name>.md`, generate:

   ```text
   bio_harness/skills/catalog/<name>/SKILL.md
   bio_harness/skills/catalog/<name>/contract.yaml
   ```

3. Preserve all structured contract fields in `contract.yaml`.
4. Render `SKILL.md` as human-readable instructions, not a dump of YAML.
5. Include a generated-file header so future edits are made in the right place.

Acceptance:

- Every non-template definition has a matching catalog directory.
- Every catalog directory has exactly one `SKILL.md` and one `contract.yaml`.
- Generated `contract.yaml` round-trips the required fields from the current
  definition.
- No runtime planner or execution behavior changes.

## Phase 2: Add Validation Tests

Goal: make the new standard skill layer hard to accidentally break.

Recommended tests:

```text
tests/skills/test_standard_skill_packages.py
tests/skills/test_standard_skill_generation.py
```

Test cases:

1. Every definition has a generated package.
2. Every `SKILL.md` has valid YAML frontmatter with `name` and `description`.
3. Every `SKILL.md` body includes:
   - `What This Does`
   - `Use This Skill When`
   - `Do Not Use This Skill When`
   - `Required Inputs`
   - `Expected Outputs`
   - `Execution Contract`
4. Every `contract.yaml` includes the current required fields:
   - `name`
   - `description`
   - `risk_level`
   - `parameters`
5. The generated package count equals the definition count, excluding
   `template.md`.
6. The existing registry still loads the original definitions and produces the
   same skill names as before.

Run:

```bash
pixi run pytest tests/skills/test_registry.py tests/skills/test_standard_skill_packages.py
```

If no pixi environment is available:

```bash
python -m pytest tests/skills/test_registry.py tests/skills/test_standard_skill_packages.py
```

## Phase 3: Registry Compatibility In Shadow Mode

Goal: teach the registry about standard packages without switching the planner
to them yet.

Actions:

1. Add a loader helper that can read:
   - old definitions: `bio_harness/skills/definitions/*.md`
   - new packages: `bio_harness/skills/catalog/*/contract.yaml`
2. Compare both sources in tests.
3. Generate a compatibility report:

   ```text
   workspace/reports/standard_skill_catalog_diff.json
   ```

Acceptance:

- Old and new sources expose the same skill names.
- For each skill, required fields match.
- Any differences are explicit and explainable.
- Planner still uses the old validated source by default.

## Phase 4: UI And Setup Integration

Goal: make the user-facing software show the correct concept.

Actions:

1. Rename UI labels from generic "Skills" to one of:
   - "Tool Contracts"
   - "Bioinformatics Capabilities"
   - "Agent Skills" only when showing real `SKILL.md` packages.
2. Add a view that can open the generated `SKILL.md` for a capability.
3. Keep setup assistance tied to `tools_required` and `system_requirements` from
   the contract, not from prose.
4. Make the local model/setup flow explain:
   - the model chooses a capability,
   - the harness validates the contract,
   - the wrapper executes the command,
   - outputs are checked before downstream use.

Acceptance:

- A new user can understand the difference between an agent skill and an
  executable contract.
- The UI does not imply that free-form model text is directly executing science.
- Existing UI smoke tests still pass.

## Phase 5: Optional Runtime Switch

Goal: eventually make the standard package catalog the canonical source.

This phase is optional and should not be rushed before public release.

Actions:

1. Make `contract.yaml` canonical.
2. Generate legacy `definitions/*.md` from `catalog/*/contract.yaml`, or remove
   the legacy path after all code has migrated.
3. Update `SkillRegistry` naming and docstrings:
   - `SkillRegistry` may remain if the package is now standard skill packages.
   - Otherwise rename internal variables to `tool_contracts` for clarity.
4. Update prompt construction to use either:
   - the same compact metadata as today, or
   - selected sections from `SKILL.md`.

Acceptance:

- Existing replay fixtures pass.
- Fast-signal replay and candidate-gate tests pass.
- Mini-benchmark suite passes.
- If planner-facing text changes, run the relevant model preflight before any
  full benchmark claim.

## Phase 6: Paper, README, And Presentation Cleanup

Goal: remove ambiguity from external claims.

Preferred phrasing:

> Bio-Harness exposes bioinformatics capabilities as standard agent skill
> packages backed by typed executable contracts.

Avoid:

> Bio-Harness has 82 skills.

Better:

> Bio-Harness includes 82 typed bioinformatics tool contracts. In the release
> tree, each contract is also represented as a standard `SKILL.md` package for
> agent-facing instruction and human review.

Manuscript/presentation changes:

1. Define the three-layer concept once:
   - agent skill
   - typed contract
   - executable wrapper
2. Show one real example, such as `bcftools_call`.
3. Explain why the split matters:
   - prose helps the agent decide when to use a tool,
   - contracts prevent invalid arguments and missing files,
   - wrappers make execution reproducible.

## Phase 7: Release Gate

Before calling the correction complete:

1. Run standard package validation tests.
2. Run existing registry and wrapper tests.
3. Run fast replay tests if any planner-facing skill text changed.
4. Run UI smoke tests if UI labels or capability views changed.
5. Confirm README and manuscript language no longer overclaim.

Suggested quick gate:

```bash
pixi run pytest \
  tests/skills/test_registry.py \
  tests/skills/test_standard_skill_packages.py \
  tests/core/test_harness_help_context.py \
  tests/core/test_tool_registry.py
```

Suggested broader gate:

```bash
pixi run pytest tests/skills tests/core/test_skill_retrieval.py tests/core/test_harness_help_context.py
```

Only run full benchmarks if planner-facing metadata changes in a way that could
change model behavior.

## Risks And Mitigations

Risk: generated `SKILL.md` files make the repo much larger.

Mitigation: generated files are small Markdown/YAML files. If size becomes an
issue, keep the generator and generated catalog in release builds only.

Risk: the model starts reading verbose `SKILL.md` files and behavior changes.

Mitigation: do not feed `SKILL.md` text to the planner in the first migration.
Keep the compact selected metadata path until fast-signal tests are green.

Risk: manual edits drift from the source contract.

Mitigation: generated-file headers plus CI tests that compare `contract.yaml`
against the current source definition.

Risk: terminology churn breaks existing code and tests.

Mitigation: do not rename internal code first. Fix public terminology first,
then add the catalog, then gradually rename internals only when tests cover it.

## Recommended Next Implementation Sequence

1. Make the terminology safety pass in README/UI/docs.
2. Add `scripts/generate_standard_skill_packages.py`.
3. Generate catalog packages under `bio_harness/skills/catalog/`.
4. Add validation tests for the generated packages.
5. Run the focused skill/registry test gate.
6. Update public wording to say "standard `SKILL.md` packages backed by typed
   executable contracts."
7. Defer canonical registry migration until after the public release unless a
   reviewer specifically requires it.

## Bottom Line

This is a fixable packaging and naming problem, not a failure of the core
harness. The safe path is to keep the benchmarked contract system, generate real
standard `SKILL.md` packages around it, validate that every package maps back to
the same executable contract, and only then decide whether to migrate the runtime
registry.
