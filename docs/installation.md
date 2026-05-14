# Installation

Bio-Harness is currently source-checkout first.

## Supported Path

```bash
python3 scripts/bootstrap_bioharness.py
```

The bootstrap helper creates `.venv`, installs Python dependencies, installs the
package in editable mode, prepares the default Pixi tool environment, and writes
a receipt under `workspace/bootstrap_reports/`.

For broader local tool coverage:

```bash
python3 scripts/bootstrap_bioharness.py --all-installable-tools
```

## Manual Python-Only Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/venv-core.txt
.venv/bin/python -m pip install -e . --no-deps
```

## Local Model Backend

The easiest local model setup path is:

```bash
python3 scripts/first_run_setup.py
```

This single-model Qwen Coder setup is the recommended default for public users.
It avoids requiring a second Qwen download. Advanced users can still configure a
separate planner model with `BIO_HARNESS_MODEL_HEAVY` for research/stress
experiments.

Other supported backend names are `ollama_openai`, `openai_compatible`, `vllm`,
and `mlx`.

## UI

The primary staged-tree UI command is:

```bash
.venv/bin/python ui_v2_api.py
cd apps/web
npm ci
npm run dev
```
