# Standard Skill Format Migration Evaluation

## What Changed

Bio-Harness now has a generated standard agent-skill catalog layered on top of
the existing validated tool-contract system.

New implementation artifacts:

- `scripts/generate_standard_skill_packages.py`
- `bio_harness/skills/catalog/<capability>/SKILL.md`
- `bio_harness/skills/catalog/<capability>/contract.yaml`
- `tests/skills/test_standard_skill_packages.py`

Documentation/planning artifacts:

- `docs/STANDARD_SKILL_FORMAT_MIGRATION_PLAN_20260707.md`
- README terminology now distinguishes:
  - tool contracts
  - tool wrappers
  - standard agent-skill packages

Packaging change:

- `pyproject.toml` package data now includes:
  - `skills/catalog/*/SKILL.md`
  - `skills/catalog/*/contract.yaml`

## Catalog Result

Generated catalog count:

- 82 standard skill package directories
- 82 `SKILL.md` files
- 82 `contract.yaml` files

Each generated package is derived from one source contract in:

```text
bio_harness/skills/definitions/*.md
```

The generated `SKILL.md` frontmatter starts at the first line so standard
agent-skill parsers can read it. The generated `contract.yaml` preserves the
machine-readable execution metadata used by the harness.

## Runtime Behavior

This migration does **not** change planner/runtime behavior yet.

The current validated execution path remains:

```text
bio_harness/skills/definitions/*.md
bio_harness/skills/library/*.py
bio_harness/pipeline_scripts/*.py
```

The new catalog is a standard-format facade and packaging/readability layer.
The planner is not switched to verbose `SKILL.md` text in this step.

## Tests Run

### New Standard Skill Catalog Tests

```bash
python3 -m pytest tests/skills/test_standard_skill_packages.py -q
```

Result:

```text
5 passed
```

### Focused Registry And Help Gate

```bash
python3 -m pytest \
  tests/skills/test_registry.py \
  tests/skills/test_standard_skill_packages.py \
  tests/core/test_harness_help_context.py \
  tests/core/test_tool_registry.py \
  -q
```

Result:

```text
51 passed
```

### Broader Skill-Related Gate

```bash
python3 -m pytest \
  tests/skills \
  tests/core/test_skill_retrieval.py \
  tests/core/test_harness_help_context.py \
  -q
```

Result:

```text
232 passed
```

### Legacy Skill-Surface Compatibility Slice

```bash
python3 -m pytest \
  tests/core/test_qwen_skill_coverage.py \
  tests/core/test_skill_generator.py \
  tests/core/test_skill_generator_comprehensive.py \
  tests/core/test_fallback_skill_builder.py \
  tests/ui/test_direct_skill_requests.py \
  -q
```

Result:

```text
108 passed
```

### Fast-Signal And Release-Gate Slice

```bash
python3 -m pytest \
  tests/core/test_fast_signal.py \
  tests/core/test_fast_signal_fixture_metadata.py \
  tests/core/test_fast_signal_preflight.py \
  tests/core/test_release_gate.py \
  -q
```

Result:

```text
63 passed, 5 existing SWIG deprecation warnings
```

### Style Check

```bash
python3 -m ruff check \
  scripts/generate_standard_skill_packages.py \
  tests/skills/test_standard_skill_packages.py
```

Result:

```text
All checks passed
```

### Wheel Build

Command:

```bash
python3 -m build --wheel
```

Result:

```text
Successfully built bio_harness-0.1.0-py3-none-any.whl
```

The isolated wheel build output showed generated catalog files being added to
the wheel, including paths such as:

```text
bio_harness/skills/catalog/bcftools_call/SKILL.md
bio_harness/skills/catalog/bcftools_call/contract.yaml
```

### Full Contract Round Trip

Additional validation compared every generated `contract.yaml` against its
source `bio_harness/skills/definitions/*.md` contract after removing only the
generated bookkeeping fields:

- `source_definition`
- `generated_by`

Result:

```text
full_contract_round_trip_ok 82
```

This verifies that the generated executable contract copy preserves the full
source metadata, not only the required fields used by the pytest smoke test.

### Idempotent Generation

The catalog was hashed before and after rerunning:

```bash
python3 scripts/generate_standard_skill_packages.py
```

Result:

```text
164 generated files
catalog hash before: 1fb0689226c28e137eee08547ad3044c8d3fc901a92f034145e1b79862dfaf30
catalog hash after:  1fb0689226c28e137eee08547ad3044c8d3fc901a92f034145e1b79862dfaf30
```

This verifies deterministic regeneration.

### Clean Wheel Install Check

The built wheel was installed into a throwaway virtual environment from outside
the source checkout, using `--no-deps` so the check focused only on package
contents.

Result:

```text
module_file <venv>/site-packages/bio_harness/__init__.py
installed_skill_md 82
installed_contract_yaml 82
installed_sample_ok <venv>/site-packages/bio_harness/skills/catalog/bcftools_call/SKILL.md
```

This verifies that the standard skill catalog is present in the installed wheel,
not only in the source tree.

## What This Proves

This evaluation supports these claims:

1. The repository now contains real standard `SKILL.md` packages for the
   bioinformatics capability layer.
2. Those packages are generated from the existing validated tool contracts.
3. The generated contracts preserve required executable metadata.
4. Existing registry, wrapper, help, fast-signal, and release-gate tests still
   pass.
5. The standard skill catalog is included in the package wheel.
6. Regeneration is deterministic.
7. The installed wheel exposes the generated `SKILL.md` and `contract.yaml`
   files from `site-packages`.

## What This Does Not Prove

This does not prove that planner behavior is unchanged under a prompt that
feeds the full generated `SKILL.md` text to the model, because that has not been
enabled.

This does not replace the need for fast-signal replay or mini-benchmark checks
if a future change makes the planner consume the generated `SKILL.md` body.

## Recommended Next Step

Keep the generated catalog as a facade for the public release. Defer making
`bio_harness/skills/catalog/*/contract.yaml` canonical until after the release,
unless a reviewer specifically requests a runtime registry migration.
