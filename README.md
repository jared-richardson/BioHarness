# Bio-Harness

Bio-Harness is a local-first bioinformatics agent harness for running
multi-step scientific workflows with open models and real command-line tools.
It combines local LLM planning, deterministic protocol scaffolding, strict
artifact binding, setup assistance, replay fixtures, and fast-signal regression
gates.

![Bio-Harness UI preview](apps/web/src/assets/hero.png)

The public release is designed for researchers who want an agentic workflow
assistant without sending genomic or clinical data to a cloud API. Bio-Harness
keeps model execution local through Ollama, stages inputs into a controlled
workspace, checks tool and model readiness before a run, and fails closed when
the plan cannot be bound to real artifacts.

Paper: arXiv link coming soon.

## What It Does

- Plans bioinformatics workflows from natural-language analysis goals.
- Runs real local tools through typed wrappers and audited shell boundaries.
- Guides first-run setup for Python, Pixi, Ollama, local models, and tool
  bundles.
- Lets users choose tested local model paths with disk and RAM guidance before
  downloading.
- Tracks execution, logs, artifacts, and terminal output in the React/FastAPI
  UI.
- Converts failures into replay fixtures and fast-signal regression checks so
  fixes do not disappear into one-off benchmark runs.

## Validated Local Model Paths

| Setup | Model(s) | Status | Notes |
| --- | --- | --- | --- |
| Recommended public path | `qwen3-coder-next:latest` | 24/24 public-mode sweep | One local model for planning and execution. |
| Tested Gemma alternative | `gemma4:26b` | 24/24 strict and public sweeps | Single-model Gemma validation path. |
| Advanced stress path | `qwen3.6:35b-a3b` planner + `qwen3-coder-next:latest` executor | 24/24 strict and public sweeps | Research/stress setup; not required for the default public path. |

Smaller models can be used for setup smoke tests or experimentation, but the
validated release evidence above is tied to the listed model tags and recorded
digests.

## Quick Start

Python floor: `3.10+`.

1. Clone the repository and enter it.

   ```bash
   git clone https://github.com/jared-richardson/BioHarness.git
   cd BioHarness
   ```

2. Run the first-run setup assistant.

   ```bash
   python3 scripts/first_run_setup.py
   ```

   The assistant checks Python/Pixi readiness, Ollama availability, installed
   local models, disk/RAM fit, and writes a setup receipt under
   `workspace/setup_reports/`.

3. For the full source-checkout setup, run the bootstrap helper.

   ```bash
   python3 scripts/bootstrap_bioharness.py
   ```

   For broader optional scientific tool coverage:

   ```bash
   python3 scripts/bootstrap_bioharness.py --all-installable-tools
   ```

4. Start Ollama if it is not already running.

   ```bash
   ollama serve
   ```

5. Pull the recommended public model if needed.

   ```bash
   python3 scripts/setup_llm_backend.py \
     --llm-backend ollama \
     --model-name qwen3-coder-next:latest \
     --pull-if-missing
   ```

   The React setup wizard can also start Ollama, pull the selected model with
   progress, verify readiness, and offer the mini preflight.

## Launch The UI

```bash
.venv/bin/python ui_v2_api.py
```

In another terminal:

```bash
cd apps/web
npm ci
npm run dev
```

Open:

```text
http://localhost:5173
```

If setup is incomplete, the UI opens the first-run wizard automatically. To
force the wizard for QA after setup is already complete:

```text
http://localhost:5173/?setup=1
```

The API binds to `127.0.0.1:8000` by default. Set
`BIO_HARNESS_UI_HOST=0.0.0.0` only on a trusted network because the local API
includes a terminal endpoint. To point the Vite app at a non-default backend,
set `VITE_API_BASE`:

```bash
VITE_API_BASE=http://127.0.0.1:8000 npm run dev
```

If you serve the UI from a LAN hostname, set `BIO_HARNESS_UI_CORS_ORIGINS` to
the explicit comma-separated browser origins you want to allow.

## CLI Harness

Run a prompt directly:

```bash
pixi run python scripts/run_agent_e2e.py \
  --prompt "Run RNA-seq differential expression on the staged inputs" \
  --data-root workspace/inputs_readonly \
  --selected-dir workspace \
  --print-plan
```

Stage user inputs into the workspace:

```bash
python3 scripts/stage_inputs.py /absolute/path/to/sample.fastq.gz
```

Inspect deterministic setup/capability guidance:

```bash
python3 scripts/show_harness_help.py --compact
```

## Mini Preflight

Mini preflight uses generated tiny real inputs and real tool wrappers. It is a
fast setup confidence check, not a replacement for the full benchmark sweeps.

Generate the mini-benchmark inputs:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \
  --output-root workspace/benchmark_data/fast_signal_mini
```

Run the mini suite:

```bash
python3 scripts/run_fast_model_preflight.py \
  --suite mini \
  --mini-root workspace/benchmark_data/fast_signal_mini \
  --model qwen3-coder-next:latest \
  --output-json workspace/studies/fast_model_preflight_latest.json
```

## Fast-Signal Regression Gates

Replay the curated fast-signal fixtures:

```bash
python3 scripts/replay_fast_signal_fixtures.py
```

The public tree includes 38 curated planner-shape and candidate-gate fixtures.
These tests are designed to catch harness regressions before launching long
local-model benchmarks.

## Repository Contents

- `apps/web/`: primary React/Vite UI source.
- `ui_v2_api.py`: FastAPI backend for the React UI.
- `apps/streamlit/app.py`: compatibility UI.
- `bio_harness/`: core harness, wrappers, binders, setup helpers, and runtime
  logic.
- `scripts/`: setup, execution, benchmark, replay, and reporting commands.
- `tests/fixtures/fast_signal/`: curated replay fixtures.
- `benchmark_data/`: public-safe manifests and tiny-data generator entry
  points.
- `docs/`: installation, setup, benchmark evidence, and release-methodology
  notes.

## Development Checks

```bash
python3 scripts/scan_public_release_tree.py --root .
python3 -m build --sdist --wheel --outdir dist
python3 scripts/scan_public_release_tree.py --root dist

python3 scripts/replay_fast_signal_fixtures.py tests/fixtures/fast_signal \
  --jsonl workspace/studies/replay_latest.jsonl

cd apps/web
npm ci
npm run lint
npm run build
npm audit --audit-level=moderate
```

## UI Modes

- `apps/web/` is the primary React/Vite UI source. It is staged without
  `node_modules`, `.vite`, or `dist`.
- `ui_v2_api.py` is staged at the repository root as the FastAPI backend for
  `apps/web/`, preserving its source-tree `PROJECT_ROOT` behavior.
- `apps/streamlit/app.py` remains available as a compatibility fallback.

See:

- `docs/installation.md`
- `docs/setup_assistance.md`
- `docs/benchmark_evidence.md`
- `docs/fast_signal.md`
- `docs/ui.md`
- `docs/packaging.md`
- `scripts/README.md`
- `benchmark_data/README.md`

## Release Caveats

- Full historical benchmark workspaces are not included.
- Public benchmark data is limited to manifests, fixtures, and generated
  synthetic mini-benchmark inputs.
- Model weights, Ollama caches, Pixi environments, and run outputs are installed
  or generated locally and are not committed to this repository.
- Wheel/PyPI installation is not the primary release path yet; use source
  checkout plus bootstrap for now.
