# Agent Instructions

Apply the repository coding standard in
[CODING_STANDARDS.md](CODING_STANDARDS.md)
to all new and modified code.

Required defaults for agents working in this repository:
- Follow Google-style Python docstrings for all new public modules, functions, and classes.
- Prefer small focused helpers over large monolithic functions; if a file
  keeps growing, split logic before adding more branches.
- Keep comments sparse and explanatory; explain intent or non-obvious
  constraints, not line-by-line behavior.
- Preserve benchmark-blind strict-mode behavior: deterministic scaffolding is
  allowed, deterministic scientific-plan replacement is not.
- Strengthen strict semantic guards when needed: reject placeholder or
  fabricated science, guessed data splits, and helper-backed workflow bypasses
  before execution.
- When repair failures repeat, add or update repo-versioned repair guidance in
  [repair_advisories.json](bio_harness/harness/repair_advisories.json)
  via
  [upsert_repair_advisory.py](scripts/upsert_repair_advisory.py)
  instead of hard-coding more prompt text in one place.
- Add or update targeted tests for every behavior change, including regression
  coverage for benchmark-specific fixes.
