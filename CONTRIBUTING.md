# Contributing To Bio-Harness

Thank you for helping improve Bio-Harness. The project aims to make local-first
bioinformatics agents reliable, auditable, and practical for small open-source
models.

## Development Setup

Start with the public setup helpers:

```bash
python3 scripts/bootstrap_bioharness.py
python3 scripts/doctor_bioharness.py
```

For model setup, the recommended one-download local path is:

```bash
ollama pull qwen3-coder-next:latest
```

The supported user interface is the Streamlit app:

```bash
python3 -m streamlit run apps/streamlit/app.py
```

The React/Vite app in `apps/web/` is experimental.

## Coding Standards

All code changes must follow `CODING_STANDARDS.md`.

Important defaults:

- Keep strict benchmark mode benchmark-blind. Deterministic scaffolding is
  allowed; deterministic scientific-plan replacement is not.
- Reject placeholder, fabricated, guessed, or helper-bypassed science before
  execution.
- Keep public functions and classes documented with Google-style Python
  docstrings.
- Prefer small focused helpers over monolithic functions.
- Add targeted regression tests for every behavior change.

When a repair failure repeats, update the repo-versioned repair advisory catalog
with `scripts/upsert_repair_advisory.py` rather than adding ad hoc prompt text.

## Test Checklist

Run the smallest relevant tests first, then broaden only when needed.

Useful release-facing checks:

```bash
pytest -q tests/core/test_stage_public_release_tree.py tests/core/test_scan_public_release_tree.py
python3 scripts/stage_public_release_tree.py --clean
python3 scripts/scan_public_release_tree.py --root release/public/bio-harness
python3 scripts/replay_fast_signal_fixtures.py tests/fixtures/fast_signal
```

For the experimental React/Vite UI:

```bash
cd apps/web
npm ci
npm run lint
npm run build
npm audit --audit-level=moderate
```

For packaging changes, build and install the package from a clean staged tree so
local checkout files cannot hide missing package data.

## Pull Request Expectations

Please include:

- a concise description of the behavior change;
- the test command and result;
- any benchmark or fixture evidence used to validate the change;
- any limitations or follow-up work that remain.

Do not include local run outputs, downloaded sequencing data, environment
directories, private paths, API keys, or generated dependency directories.
