# Bio-Harness

Bio-Harness is a local-first bioinformatics agent harness. It combines local
LLM planning, strict artifact binding, real bioinformatics tool wrappers,
setup assistance, replay fixtures, and fast-signal regression gates.

This public tree is staged from the research repository. It intentionally omits
local workspaces, full benchmark runs, generated UI dependencies, and private or
large run artifacts.

## Quick Start

Python floor: `3.10+`.

```bash
python3 scripts/first_run_setup.py
```

The first-run helper checks Python/Pixi readiness, Ollama availability, tested
local model choices, disk/RAM fit, and writes a receipt under
`workspace/setup_reports/`.

Launch the primary React UI from this staged/public layout:

```bash
.venv/bin/python ui_v2_api.py
cd apps/web
npm ci
npm run dev
```

`qwen3-coder-next:latest` is the recommended public Qwen path: it has passed
the 24-case default harness sweep as a single local model for both planning and
execution. If setup is incomplete, the React UI opens a first-run wizard that
can start Ollama, pull the selected tested model with progress, verify the
backend, and run the mini preflight. To review the wizard after setup is already
complete, open `http://localhost:5173/?setup=1`.

The API binds to `127.0.0.1:8000` by default. Set
`BIO_HARNESS_UI_HOST=0.0.0.0` only on a trusted network. To point the Vite app
at a non-default backend, set `VITE_API_BASE`, for example:

```bash
VITE_API_BASE=http://127.0.0.1:8000 npm run dev
```

If you serve the UI from a LAN hostname, set `BIO_HARNESS_UI_CORS_ORIGINS` to
the explicit comma-separated browser origins you want to allow.

Then open:

```text
http://localhost:5173
```

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

## Fast-Signal Regression Gates

Replay the curated fast-signal fixtures:

```bash
python3 scripts/replay_fast_signal_fixtures.py
```

Prepare tiny synthetic mini-benchmark inputs on demand:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \
  --output-root workspace/benchmark_data/fast_signal_mini
```

Run the advisory fast-model mini preflight:

```bash
python3 scripts/run_fast_model_preflight.py \
  --suite mini \
  --mini-root workspace/benchmark_data/fast_signal_mini \
  --output-json workspace/studies/fast_model_preflight_latest.json
```

The mini-benchmark data is generated rather than shipped as checked-in raw
FASTQ/FASTA outputs. This keeps the repository small and avoids stale absolute
paths in release artifacts.

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
- `scripts/README.md`
- `benchmark_data/README.md`

## Release Caveats

- Full historical benchmark workspaces are not included.
- Public benchmark data is limited to manifests, fixtures, and generated
  synthetic mini-benchmark inputs.
- Wheel/PyPI installation is not the primary release path yet; use source
  checkout plus bootstrap for now.
