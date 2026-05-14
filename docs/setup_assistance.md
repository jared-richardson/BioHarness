# Setup Assistance

Bio-Harness keeps setup checks in explicit helpers so failed environment setup
does not silently become an agent failure.

## Helpers

- `scripts/bootstrap_bioharness.py`: source checkout bootstrap.
- `scripts/first_run_setup.py`: consolidated first-run setup assistant.
- `scripts/doctor_bioharness.py`: local dependency and workspace checks.
- `scripts/setup_llm_backend.py`: backend/model diagnostics and next steps.
- `scripts/show_harness_help.py`: deterministic harness capability summary.
- `scripts/stage_inputs.py`: symlink or copy user inputs into the workspace.
- `scripts/trusted_download.py`: audited downloads into `workspace/`.
- `scripts/configure_isolated_tools.py`: optional isolated tool recipes.

## Smoke Commands

```bash
python3 scripts/doctor_bioharness.py --help
python3 scripts/first_run_setup.py --dry-run --json
python3 scripts/setup_llm_backend.py --help
python3 scripts/show_harness_help.py --compact
```

## React Setup Wizard

The React UI uses the same deterministic setup APIs. If setup is incomplete,
the app opens a first-run wizard that:

- checks environment, model, and resource readiness,
- recommends tested local Ollama models,
- shows disk and memory guidance before model pulls,
- starts Ollama when available,
- pulls the selected model only after an explicit user action,
- streams setup job progress,
- verifies model readiness, and
- offers the mini preflight as the final setup check.

Force the wizard for QA with:

```text
http://localhost:5173/?setup=1
```

Use `workspace/` for generated receipts and run artifacts. The public staging
tree intentionally ignores `workspace/` in `.gitignore`.
