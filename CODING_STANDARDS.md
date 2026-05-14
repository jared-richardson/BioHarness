# BioHarness Coding Standards

## Scope

These rules apply to all new files and all edited regions of existing files.
When legacy code violates these standards, improve the touched area rather than
spreading the old pattern further.

## Core principles

- Keep strict-mode benchmark runs benchmark-blind. Deterministic artifact
  paths and process scaffolding are allowed; deterministic scientific-plan
  replacement is not.
- In strict benchmark mode, add or maintain semantic guards that reject mock,
  demo, placeholder, guessed, or fabricated scientific commands before
  execution. Helper-backed scaffolds must validate helper usage rather than
  silently allowing inline scientific rewrites.
- Prefer small targeted fixes over broad rewrites. New helpers should have one
  job and explicit names.
- Optimize for maintainability first: predictable interfaces, readable control
  flow, and regression tests for every behavior change.
- User-facing failures must explain the operational impact and likely remedy in
  plain language.

## Python style

- Follow PEP 8 plus Google Python Style for naming, spacing, imports, and
  docstring structure.
- Use type hints on all new public functions, methods, and module-level
  constants whose types are not obvious from assignment.
- Add Google-style docstrings to all new public modules, classes, and
  functions. Include `Args:`, `Returns:`, and `Raises:` when they apply.
- Prefer `pathlib.Path` for filesystem logic. Convert to strings only at shell
  or serialization boundaries.
- Keep functions focused. If a function needs multiple unrelated branches or
  more than roughly 40 to 60 lines of business logic, extract helpers.
- Avoid boolean-flag explosions. If a function needs many mode switches,
  introduce a small value object or split responsibilities.

## Comments and documentation

- Comments should explain intent, invariants, or benchmark constraints. Do not
  narrate obvious line-by-line behavior.
- Document benchmark-specific constraints close to the code that enforces them.
- Update nearby docs when behavior changes affect operator expectations,
  strict-mode guarantees, or benchmark coverage assumptions.
- When a planner or repair failure mode becomes recurring knowledge, store it
  in the repo-versioned repair advisory catalog instead of scattering ad hoc
  prompt fragments across the harness.

## Error handling and logging

- Never use bare `except` clauses.
- Catch the narrowest practical exception type and preserve context in the
  raised error message.
- Log enough context to reconstruct failures without dumping unnecessary noise
  into normal execution paths.
- Prefer stable, actionable error text over raw stack traces in user-facing
  surfaces.

## Skills and wrappers

- Every executable tool path exposed to the planner should remain wrapped in a
  skill or harness helper rather than open-coded in multiple places.
- Skill wrappers should centralize environment setup, quoting, output
  directory creation, and deterministic path conventions.
- Wrapper functions must have unit tests that validate command rendering for
  normal and edge-case inputs.

## Testing expectations

- Every logic change requires a focused regression test in the closest relevant
  test module.
- Prefer unit tests for normalization, binding, and plan-repair rules; use
  integration or benchmark reruns only when runtime behavior must be verified.
- When fixing benchmark behavior, verify both the generated executable plan and
  the validator outcome whenever feasible.
- Do not merge code that changes plan semantics without adding or updating
  tests that pin the intended artifact handoff.

## File and module organization

- Avoid adding new responsibilities to already-large scripts when a shared
  module or helper file is more appropriate.
- Keep CLI entrypoints thin; push reusable logic into importable modules under
  `bio_harness/`.
- If a file becomes hard to navigate because of repeated benchmark-specific
  branches, split the logic into named helpers grouped by analysis type or
  concern.

## Dependency policy

- Prefer the standard library and existing project dependencies first.
- Add new dependencies only when the functionality cannot be implemented
  cleanly with the current stack.
- All runtime dependencies must be declared in `pixi.toml` or the existing
  project packaging config before use.

## Path and filesystem safety

- Write outputs only to task-selected run directories or other approved
  workspace locations.
- Keep deterministic artifact naming centralized in harness helpers and strict
  binders.
- Avoid hard-coding ad hoc temporary paths in planner-facing logic.
