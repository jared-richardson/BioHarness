#!/usr/bin/env python3
"""Stage a clean BioHarness public-release tree from an allowlist."""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

EXCLUDED_PARTS = frozenset(
    {
        ".DS_Store",
        ".claude",
        ".git",
        ".mypy_cache",
        ".pixi",
        ".playwright-cli",
        ".pytest_cache",
        ".ruff_cache",
        ".tool-envs",
        ".tool-envs-docker",
        ".tool-envs-generic",
        ".venv",
        ".venv-bootstrap-smoke",
        ".venv-bootstrap-smoke2",
        ".venv-bootstrap-smoke3",
        ".venv_mlx_server",
        ".vite",
        "__pycache__",
        "dist",
        "node_modules",
        "output",
        "outputs",
        "runs",
        "workspace",
    }
)
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".log")
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = DEFAULT_REPO_ROOT / "release" / "public" / "bio-harness"
PUBLIC_ROOT_PLACEHOLDER = "<BIO_HARNESS_ROOT>"
TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".Dockerfile",
        ".html",
        ".js",
        ".json",
        ".lock",
        ".md",
        ".py",
        ".R",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yml",
        ".yaml",
    }
)

PUBLIC_GITIGNORE = """# Python
__pycache__/
*.py[cod]
*.so
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage*
htmlcov/
*.egg-info/
build/
dist/

# Local environments
.venv/
venv/
.pixi/
.tool-envs/
.tool-envs-docker/
.tool-envs-generic/

# Frontend
node_modules/
.vite/
apps/web/dist/

# Runtime outputs and local data
workspace/
runs/
outputs/
output/
downloads/

# Platform/editor noise
.DS_Store
.vscode/
.idea/
*.log
"""

PUBLIC_CI_WORKFLOW = """name: Public CI

on:
  push:
  pull_request:
  workflow_dispatch:

jobs:
  release-gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install test tooling
        run: |
          python -m pip install --upgrade pip
          python -m pip install build pytest
      - name: Run focused release tests
        run: |
          python -m pytest -q \\
            tests/core/test_stage_public_release_tree.py \\
            tests/core/test_scan_public_release_tree.py
      - name: Scan public tree
        run: python scripts/scan_public_release_tree.py --root .
      - name: Build wheel and sdist
        run: python -m build --sdist --wheel --outdir dist
      - name: Scan package artifacts
        run: python scripts/scan_public_release_tree.py --root dist
      - name: Check experimental web UI source
        working-directory: apps/web
        run: |
          npm ci
          npm run lint
          npm run build
          npm audit --audit-level=moderate
"""

PUBLIC_PACKAGE_SMOKE_WORKFLOW = """name: Package Smoke

on:
  workflow_dispatch:
  schedule:
    - cron: "0 9 * * 1"

jobs:
  full-install-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Build package
        run: |
          python -m pip install --upgrade pip
          python -m pip install build
          python -m build --sdist --wheel --outdir dist
      - name: Install wheel with dependencies
        run: python -m pip install dist/*.whl
      - name: Check installed requirements
        run: python -m pip check
      - name: Smoke installed console scripts
        run: |
          for cmd in \\
            bio-harness-bootstrap \\
            bio-harness-first-run \\
            bio-harness-doctor \\
            bio-harness-run \\
            bio-harness-preflight \\
            bio-harness-qwen-smoke \\
            bio-harness-configure-isolated-tools \\
            bio-harness-benchmark \\
            bio-harness-compare \\
            bio-harness-report; do
            "$cmd" --help >/dev/null
          done
      - name: Verify installed package data
        run: |
          python - <<'PY'
          from pathlib import Path
          import bio_harness

          pkg = Path(bio_harness.__file__).parent
          required = [
              pkg / "skills/definitions/index.json",
              pkg / "skills/definitions/retrieval_index.json",
              pkg / "harness/repair_advisories.json",
              pkg / "capabilities/catalog.json",
              pkg / "capabilities/scientific_tools.json",
              pkg / "skills/uncommon/spec_schema.json",
          ]
          missing = [str(path) for path in required if not path.exists()]
          if missing:
              raise SystemExit("Missing package data: " + ", ".join(missing))
          PY
      - name: Replay fast-signal fixtures
        run: >-
          python scripts/replay_fast_signal_fixtures.py tests/fixtures/fast_signal
          --jsonl /tmp/fast_signal_replay.jsonl
"""

PUBLIC_README = """# Bio-Harness

Bio-Harness is a local-first bioinformatics agent harness. It combines local
LLM planning, strict artifact binding, real bioinformatics tool wrappers,
setup assistance, replay fixtures, and fast-signal regression gates.

This public tree is staged from the research repository. It intentionally omits
local workspaces, full benchmark runs, generated UI dependencies, and private or
large run artifacts.

## Quick Start

Python floor: `3.10+`.

On macOS, double-click `Launch Bio-Harness.command` for the click-first setup
path. It prepares the source checkout, starts the local API and React UI, and
opens the first-run setup wizard in your browser. The wizard can start Ollama,
show tested model choices, report disk/RAM requirements, pull the selected
model with progress, and run the mini preflight.

Command-line setup remains available:

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
pixi run python scripts/run_agent_e2e.py \\
  --prompt "Run RNA-seq differential expression on the staged inputs" \\
  --data-root workspace/inputs_readonly \\
  --selected-dir workspace \\
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
python3 scripts/prepare_fast_signal_mini_benchmarks.py \\
  --output-root workspace/benchmark_data/fast_signal_mini
```

Run the advisory fast-model mini preflight:

```bash
python3 scripts/run_fast_model_preflight.py \\
  --suite mini \\
  --mini-root workspace/benchmark_data/fast_signal_mini \\
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
"""


PUBLIC_INSTALLATION_DOC = """# Installation

Bio-Harness is currently source-checkout first.

## Click-First macOS Setup

Double-click the root-level launcher:

```text
Launch Bio-Harness.command
```

The launcher prepares the Python/Pixi environment, installs web-interface
packages when needed, starts the local API and React UI, and opens the first-run
setup wizard. Keep the launcher window open while using Bio-Harness; closing it
stops the local UI servers.

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
"""


PUBLIC_SETUP_ASSISTANCE_DOC = """# Setup Assistance

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
"""


PUBLIC_BENCHMARK_EVIDENCE_DOC = """# Benchmark Evidence

This public tree includes compact evidence and fixtures, not full historical
run workspaces.

## Headline Evaluation

The balanced Qwen/Gemma campaign completed six 24-case variant sweeps:

- four Qwen split-local sweeps,
- two Gemma single-model sweeps,
- 144/144 case-runs passed,
- zero automatic repairs,
- zero generic fallbacks,
- zero protocol fallbacks,
- zero planner fail-open events.

An additional deployment-readiness sweep validates the recommended one-model
public Qwen path:

- `qwen_coder_single_model_full_20260501_r1`,
- variant `qwen_full`,
- `qwen3-coder-next:latest` used for both planner and executor,
- 24/24 cases passed,
- zero automatic repairs,
- zero generic fallbacks,
- zero protocol fallbacks,
- zero planner fail-open events.

Counted together, the balanced campaign plus this labeled deployment sweep
represent 168/168 passing case-runs. Keep the seventh sweep labeled: it tests
the supported/default `qwen_full` path, not single-model `qwen_true_no_templates`.

Primary evidence docs staged here include:

- `docs/EVALUATION_COMPLETED_CHECKLIST_20260501.md`
- `docs/QWEN36_TESTING_CONTEXT_20260428.md`
- `docs/QWEN36_POST_MINI_GATE_RESULTS_20260430.md`
- `docs/QWEN_CODER_SINGLE_MODEL_PAPER_SUMMARY_20260502.md`
- `docs/FAST_SIGNAL_FIXTURE_COVERAGE_20260430.md`

## Public Reproduction Surface

Included:

- `benchmark_data/manifests/ablation_manifest_24.json`
- `tests/fixtures/fast_signal/`
- focused tests under `tests/`
- mini-benchmark generator and validator scripts

Not included:

- full `workspace/ablation_results/`
- full `workspace/runs/`
- generated FASTQ/FASTA mini-benchmark data
- local model caches or tool environments

Generate mini-benchmark inputs with:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \\
  --output-root workspace/benchmark_data/fast_signal_mini
```
"""


PUBLIC_FAST_SIGNAL_DOC = """# Fast-Signal Methodology

The fast-signal ladder is the cheap regression layer used before long local LLM
benchmark sweeps.

Publicly staged pieces:

- 38 curated replay fixtures under `tests/fixtures/fast_signal/`
- fixture replay runner: `scripts/replay_fast_signal_fixtures.py`
- scorecard CLI: `scripts/fast_signal_scorecard.py`
- mini-benchmark generator: `scripts/prepare_fast_signal_mini_benchmarks.py`
- mini-benchmark validator: `scripts/validate_fast_signal_mini_benchmarks.py`
- fast-model preflight: `scripts/run_fast_model_preflight.py`

Fixture kinds:

- `planner_shape`: raw planner emissions through parse/normalize/compile/repair.
- `candidate_gate`: saved stepwise prefix plus candidate through duplicate,
  binding, masking, and contract checks.

The mini-benchmark suite uses generated tiny real inputs and contract-level
assertions. It checks existence, schema, sidecars, and non-empty outputs rather
than exact scientific coordinates or p-values.
"""


PUBLIC_UI_DOC = """# UI

## Primary UI

The primary public UI is the React/Vite app staged at:

```text
apps/web/
```

Start the API backend from the repository root:

```bash
.venv/bin/python ui_v2_api.py
```

The backend is local-only by default (`127.0.0.1:8000`). Use
`BIO_HARNESS_UI_HOST`, `BIO_HARNESS_UI_PORT`, and `VITE_API_BASE` for custom
local setups. Set `BIO_HARNESS_UI_HOST=0.0.0.0` only on a trusted network
because the API includes a local terminal endpoint. If you serve the frontend
from a LAN hostname, set `BIO_HARNESS_UI_CORS_ORIGINS` to the explicit
comma-separated browser origins you want to allow.

Then start the Vite frontend:

```bash
cd apps/web
npm ci
npm run dev
```

Open:

```text
http://localhost:5173
```

If setup is incomplete, the UI opens the first-run setup wizard. To force the
wizard for QA after setup is already complete, open:

```text
http://localhost:5173/?setup=1
```

Release checks:

```bash
cd apps/web
npm ci
npm run lint
npm run build
npm audit --audit-level=moderate
```

`apps/web/` is staged as source only: no `node_modules/`, no `.vite/`, and no
`dist/`.

## Compatibility UI

The Streamlit UI remains available at:

```text
apps/streamlit/app.py
```

Launch it with:

```bash
.venv/bin/streamlit run apps/streamlit/app.py
```
"""


PUBLIC_WEB_README = """# Bio-Harness Web UI

This is the primary React/Vite UI source for Bio-Harness. The Streamlit app at
`apps/streamlit/app.py` remains available as a compatibility fallback.

## Development

Start the API backend from the repository root:

```bash
.venv/bin/python ui_v2_api.py
```

The backend binds to `127.0.0.1:8000` by default. Use these environment
variables only when you need a custom local setup:

```bash
BIO_HARNESS_UI_HOST=127.0.0.1 BIO_HARNESS_UI_PORT=8000 .venv/bin/python ui_v2_api.py
```

Set `BIO_HARNESS_UI_HOST=0.0.0.0` only on a trusted network; the API exposes a
local terminal endpoint intended for single-user local development. If you also
serve the Vite frontend from a LAN hostname, set
`BIO_HARNESS_UI_CORS_ORIGINS` to a comma-separated list of allowed origins.

Then start the Vite UI:

```bash
cd apps/web
npm ci
npm run lint
npm run build
npm audit --audit-level=moderate
npm run dev
```

To point the frontend at a non-default backend URL, set `VITE_API_BASE` or put
it in `.env.local`:

```bash
VITE_API_BASE=http://127.0.0.1:8000 npm run dev
```

Generated folders such as `node_modules/`, `.vite/`, and `dist/` are not part of
the public source tree.
"""


PUBLIC_BENCHMARK_DATA_README = """# Benchmark Data

This directory contains public-safe benchmark metadata and generated-data entry
points.

## Included

- `manifests/ablation_manifest_24.json`: the 24-case manifest metadata.
- `fast_signal_mini/README.md`: instructions for generating tiny synthetic
  mini-benchmark inputs.

## Generated On Demand

Mini-benchmark FASTQ/FASTA inputs are generated locally:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \\
  --output-root workspace/benchmark_data/fast_signal_mini
```

The generated `manifest.json` contains absolute paths for the machine that
created it, so it is intentionally not staged from the private workspace.
"""


PUBLIC_MINI_BENCHMARK_README = """# Fast-Signal Mini-Benchmarks

Generate the three tiny real-input mini-benchmark cases here:

```bash
python3 scripts/prepare_fast_signal_mini_benchmarks.py \\
  --output-root workspace/benchmark_data/fast_signal_mini
```

Cases:

- `control_evolution_mini`
- `germline_vc_mini`
- `de_mini`

Run the advisory fast-model preflight:

```bash
python3 scripts/run_fast_model_preflight.py \\
  --suite mini \\
  --mini-root workspace/benchmark_data/fast_signal_mini \\
  --output-json workspace/studies/fast_model_preflight_latest.json
```

The mini suite uses generated synthetic inputs and contract-level checks. It is
not a replacement for the full 24-case benchmark sweeps.
"""


def _generated_public_docs() -> dict[str, str]:
    """Return generated docs that make the staged tree self-contained."""
    return {
        ".github/workflows/ci.yml": PUBLIC_CI_WORKFLOW,
        ".github/workflows/package-smoke.yml": PUBLIC_PACKAGE_SMOKE_WORKFLOW,
        "apps/web/README.md": PUBLIC_WEB_README,
        "benchmark_data/README.md": PUBLIC_BENCHMARK_DATA_README,
        "benchmark_data/fast_signal_mini/README.md": PUBLIC_MINI_BENCHMARK_README,
        "docs/benchmark_evidence.md": PUBLIC_BENCHMARK_EVIDENCE_DOC,
        "docs/fast_signal.md": PUBLIC_FAST_SIGNAL_DOC,
        "docs/installation.md": PUBLIC_INSTALLATION_DOC,
        "docs/setup_assistance.md": PUBLIC_SETUP_ASSISTANCE_DOC,
        "docs/ui.md": PUBLIC_UI_DOC,
        "scripts/README.md": _public_scripts_readme(),
    }


def _public_scripts_readme() -> str:
    lines = [
        "# Public Scripts",
        "",
        "The public staging script copies a classified allowlist of scripts.",
        "Generated manuscript helpers and local one-off analysis scripts are omitted.",
        "",
    ]
    for category, file_names in PUBLIC_SCRIPT_CATEGORIES.items():
        lines.append(f"## {category.replace('_', ' ').title()}")
        lines.append("")
        for file_name in file_names:
            lines.append(f"- `{file_name}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


ROOT_FILES = (
    "AGENTS.md",
    "CODING_STANDARDS.md",
    "Launch Bio-Harness.command",
    "install.sh",
    "pyproject.toml",
    "pixi.toml",
    "pixi.lock",
    "ui_v2_api.py",
)
OPTIONAL_ROOT_FILES = (
    "LICENSE",
    "LICENSE.md",
    "CITATION.cff",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
)
DOC_FILES = (
    "docs/EVALUATION_COMPLETED_CHECKLIST_20260501.md",
    "docs/FAST_SIGNAL_COMPLETION_PLAN_20260425.md",
    "docs/FAST_SIGNAL_FIXTURE_COVERAGE_20260430.md",
    "docs/FAST_SIGNAL_IMPLEMENTATION.md",
    "docs/FAST_SIGNAL_PLAN_SHAPE_CORPUS_DECISION_20260501.md",
    "docs/FAST_SIGNAL_SCORECARD_CUTOVER_POLICY_20260501.md",
    "docs/FAST_SIGNAL_SPEED_OPTIMIZATIONS.md",
    "docs/FAST_SIGNAL_TEST_LADDER_REFINED.md",
    "docs/PUBLIC_RELEASE_PACKAGING_CLEANUP_PLAN_20260501.md",
    "docs/QWEN_CODER_SINGLE_MODEL_PAPER_SUMMARY_20260502.md",
    "docs/QWEN36_POST_MINI_GATE_RESULTS_20260430.md",
    "docs/QWEN36_TESTING_CONTEXT_20260428.md",
    "docs/bioagentbench_plan.md",
    "docs/isolated_tools.md",
    "docs/packaging.md",
    "docs/release_inventory.md",
)
WORKSPACE_BENCHMARK_FILES = {
    "workspace/benchmark_data/ablation_manifest_24.json": (
        "benchmark_data/manifests/ablation_manifest_24.json"
    ),
}
STREAMLIT_APP_DESTINATION = "apps/streamlit/app.py"
WEB_FILES = (
    "ui_v2/eslint.config.js",
    "ui_v2/index.html",
    "ui_v2/package-lock.json",
    "ui_v2/package.json",
    "ui_v2/tsconfig.app.json",
    "ui_v2/tsconfig.json",
    "ui_v2/tsconfig.node.json",
    "ui_v2/vite.config.ts",
)
WEB_DIRS = (
    ("ui_v2/src", "apps/web/src"),
    ("ui_v2/public", "apps/web/public"),
)
PUBLIC_SCRIPT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "setup_assistance": (
        "bootstrap_bioharness.py",
        "first_run_setup.py",
        "doctor_bioharness.py",
        "setup_llm_backend.py",
        "show_harness_help.py",
        "stage_inputs.py",
        "trusted_download.py",
        "configure_isolated_tools.py",
        "check_llm_backend.py",
        "check_resource_preflight.py",
        "check_star_setup.py",
    ),
    "agent_execution": (
        "run_agent_e2e.py",
        "run_agent_e2e_batch.py",
        "run_agent_e2e_execution.py",
        "run_agent_e2e_execution_marker_support.py",
        "run_agent_e2e_harness.py",
        "run_agent_e2e_plan_application_support.py",
        "run_agent_e2e_plan_bootstrap_support.py",
        "run_agent_e2e_plan_context.py",
        "run_agent_e2e_plan_normalization_support.py",
        "run_agent_e2e_plan_validation.py",
        "run_agent_e2e_planner_settings.py",
        "run_agent_e2e_planner_supervision.py",
        "run_agent_e2e_postplan_validation_support.py",
        "run_agent_e2e_preexecution_repair_support.py",
        "run_agent_e2e_preexecution_repairs.py",
        "run_agent_e2e_research_support.py",
        "run_agent_e2e_runtime_cycle_support.py",
        "run_agent_e2e_runtime_repair_actions.py",
        "run_agent_e2e_runtime_repair_branch_support.py",
        "run_agent_e2e_runtime_repair_policy_support.py",
        "run_agent_e2e_runtime_repair_support.py",
        "run_agent_e2e_runtime_repair_templates.py",
        "run_agent_e2e_runtime_replan_support.py",
        "run_agent_e2e_state.py",
        "run_agent_e2e_stepwise_loop.py",
        "run_agent_e2e_support.py",
        "run_agent_e2e_validation_phase_support.py",
    ),
    "benchmarks_and_fast_signal": (
        "build_bioagentbench_scoreboard.py",
        "export_bioagentbench_runs_for_official_eval.py",
        "extract_fast_signal_fixtures.py",
        "fast_signal_scorecard.py",
        "measure_fast_signal_prompt_sensitivity.py",
        "prepare_bioagentbench_deseq.py",
        "prepare_fast_signal_mini_benchmarks.py",
        "replay_fast_signal_fixtures.py",
        "run_bioagentbench_invocation_support.py",
        "run_bioagentbench_official.py",
        "run_bioagentbench_reliability.py",
        "run_bioagentbench_ui_reliability.py",
        "run_domain_expansion_ablation.py",
        "run_fast_model_preflight.py",
        "run_fast_signal_plan_shape_corpus.py",
        "run_fast_signal_prompt_probe.py",
        "run_fast_signal_reproduction_baseline.py",
        "run_qwen_skill_smoke_matrix.py",
        "run_variant_benchmark.py",
        "summarize_fast_signal_plan_corpus.py",
        "validate_benchmark_data.py",
        "validate_fast_signal_mini_benchmarks.py",
    ),
    "benchmark_generators_and_validators": (
        "create_comparative_genomics_benchmark.py",
        "create_dge_pathway_benchmark.py",
        "create_feature_benchmarks.py",
        "create_germline_benchmark.py",
        "create_long_read_benchmark.py",
        "create_metabolomics_benchmark.py",
        "create_metagenomics_benchmark.py",
        "create_phylogenetics_benchmark.py",
        "create_proteomics_benchmark.py",
        "create_single_cell_benchmark.py",
        "create_spatial_benchmark.py",
        "create_variant_annotation_benchmark.py",
        "create_viral_metagenomics_benchmark.py",
        "run_feature_benchmarks.py",
        "run_long_read_benchmark.py",
        "run_metabolomics_benchmark.py",
        "run_proteomics_benchmark.py",
        "run_spatial_benchmark.py",
        "validate_alzheimer_mouse.py",
        "validate_comparative_genomics.py",
        "validate_cystic_fibrosis.py",
        "validate_deseq.py",
        "validate_dge_pathway.py",
        "validate_evolution.py",
        "validate_germline_vc.py",
        "validate_metagenomics.py",
        "validate_phylogenetics.py",
        "validate_single_cell.py",
        "validate_transcript_quant.py",
        "validate_variant_annotation.py",
        "validate_viral_metagenomics.py",
    ),
    "reporting_and_extension": (
        "audit_reference_bundle.py",
        "build_run_report_bundle.py",
        "compare_runs.py",
        "export_manuscript_docx.py",
        "export_run_ro_crate.py",
        "export_workflow_exchange.py",
        "fallback_skill_builder.py",
        "materialize_reference_bundle.py",
        "onboard_novel_tool.py",
        "profile_artifact_schema.py",
        "render_figure_spec.py",
        "scan_public_release_tree.py",
        "stage_public_release_tree.py",
        "trace_driven_improvement.py",
        "upsert_repair_advisory.py",
        "upsert_scientific_tool.py",
    ),
}
PUBLIC_SCRIPT_FILES = frozenset(
    {"__init__.py"}.union(
        file_name
        for category_files in PUBLIC_SCRIPT_CATEGORIES.values()
        for file_name in category_files
    )
)
DIRECTORY_RULES = (
    ("bio_harness", "bio_harness"),
    ("requirements", "requirements"),
    ("docker", "docker"),
    ("tests/agents", "tests/agents"),
    ("tests/core", "tests/core"),
    ("tests/core_cases", "tests/core_cases"),
    ("tests/fixtures/fast_signal", "tests/fixtures/fast_signal"),
    ("tests/pipeline_scripts", "tests/pipeline_scripts"),
    ("tests/skills", "tests/skills"),
    ("tests/support", "tests/support"),
    ("tests/ui", "tests/ui"),
    ("tests/workflows", "tests/workflows"),
)


@dataclass(frozen=True)
class FileSelection:
    """A source file selected for public-release staging.

    Attributes:
        source: Absolute source path in the current repository.
        destination: Relative destination path inside the staging tree.
    """

    source: Path
    destination: Path


@dataclass
class StagingResult:
    """Summary of a staging operation.

    Attributes:
        copied_files: Relative destination paths copied or selected.
        skipped_missing: Allowlisted source paths that were absent.
        blocked_files: Files in the staged tree matching forbidden patterns.
        total_bytes: Total bytes copied or selected.
        dry_run: Whether the run avoided filesystem writes.
        output_dir: Destination root for the staged tree.
    """

    copied_files: list[str] = field(default_factory=list)
    skipped_missing: list[str] = field(default_factory=list)
    blocked_files: list[str] = field(default_factory=list)
    total_bytes: int = 0
    dry_run: bool = False
    output_dir: Path = Path()

    def to_payload(self) -> dict[str, object]:
        """Return a JSON-serializable staging summary."""
        return {
            "blocked_files": sorted(self.blocked_files),
            "copied_count": len(self.copied_files),
            "copied_files": sorted(self.copied_files),
            "dry_run": self.dry_run,
            "output_dir": self.output_dir.name if self.output_dir else "",
            "skipped_missing": sorted(self.skipped_missing),
            "total_bytes": self.total_bytes,
        }


def stage_public_release_tree(
    *,
    repo_root: Path = DEFAULT_REPO_ROOT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    dry_run: bool = False,
    clean: bool = False,
    manifest_json: Path | None = None,
) -> StagingResult:
    """Stage the allowlisted public release tree.

    Args:
        repo_root: Repository root to copy from.
        output_dir: Directory to populate with public release files.
        dry_run: If true, collect the manifest without writing files.
        clean: If true, remove a prior staged tree before copying.
        manifest_json: Optional path for the manifest. Defaults to
            `output_dir/release_manifest.json` when not a dry run.

    Returns:
        A staging summary.

    Raises:
        ValueError: If `repo_root` or `output_dir` is unsafe.
    """
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    _validate_repo_root(repo_root)
    if clean and not dry_run:
        _clean_output_dir(repo_root, output_dir)

    result = StagingResult(dry_run=dry_run, output_dir=output_dir)
    selections = list(_collect_file_selections(repo_root))
    for selection in selections:
        _copy_selection(selection, output_dir, result, dry_run=dry_run, repo_root=repo_root)
    _write_generated_text_file(
        output_dir=output_dir,
        relative_path=Path("README.md"),
        text=PUBLIC_README,
        result=result,
        dry_run=dry_run,
    )
    for relative_path, text in _generated_public_docs().items():
        _write_generated_text_file(
            output_dir=output_dir,
            relative_path=Path(relative_path),
            text=text,
            result=result,
            dry_run=dry_run,
        )
    _write_generated_text_file(
        output_dir=output_dir,
        relative_path=Path(".gitignore"),
        text=PUBLIC_GITIGNORE,
        result=result,
        dry_run=dry_run,
    )

    if not dry_run:
        result.blocked_files = _find_blocked_files(output_dir)
        manifest_path = manifest_json or output_dir / "release_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(result.to_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return result


def main() -> int:
    """Run the public release staging command."""
    args = _parse_args()
    result = stage_public_release_tree(
        repo_root=args.repo_root,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        clean=args.clean,
        manifest_json=args.manifest_json,
    )
    print(json.dumps(result.to_payload(), indent=2, sort_keys=True))
    return 1 if result.blocked_files else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest-json", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def _validate_repo_root(repo_root: Path) -> None:
    if not (repo_root / "bio_harness").is_dir():
        raise ValueError(f"Repository root does not contain bio_harness/: {repo_root}")
    if not (repo_root / "pyproject.toml").is_file():
        raise ValueError(f"Repository root does not contain pyproject.toml: {repo_root}")


def _clean_output_dir(repo_root: Path, output_dir: Path) -> None:
    release_root = (repo_root / "release" / "public").resolve()
    try:
        output_dir.relative_to(release_root)
    except ValueError as exc:
        raise ValueError(f"Refusing to clean output outside {release_root}: {output_dir}") from exc
    if output_dir == release_root:
        raise ValueError(f"Refusing to clean release root directly: {output_dir}")
    if output_dir.exists():
        shutil.rmtree(output_dir)


def _collect_file_selections(repo_root: Path) -> Iterable[FileSelection]:
    yield from _select_existing_files(repo_root, ROOT_FILES)
    yield from _select_existing_files(repo_root, OPTIONAL_ROOT_FILES)
    yield from _select_existing_files(repo_root, DOC_FILES)
    yield from _select_existing_files(
        repo_root,
        WEB_FILES,
        prefix_to_strip="ui_v2",
        destination_prefix="apps/web",
    )
    yield from _select_workspace_benchmark_files(repo_root)
    yield from _select_benchmark_metadata(repo_root)
    yield from _select_streamlit_app(repo_root)
    yield from _select_scripts(repo_root)
    for source_dir, destination_dir in DIRECTORY_RULES:
        yield from _select_directory(repo_root / source_dir, Path(destination_dir))
    for source_dir, destination_dir in WEB_DIRS:
        yield from _select_directory(repo_root / source_dir, Path(destination_dir))


def _select_existing_files(
    repo_root: Path,
    relative_paths: Sequence[str],
    *,
    prefix_to_strip: str = "",
    destination_prefix: str = "",
) -> Iterable[FileSelection]:
    for relative in relative_paths:
        source = repo_root / relative
        if not source.exists():
            continue
        destination = Path(relative)
        if prefix_to_strip:
            destination = destination.relative_to(prefix_to_strip)
        if destination_prefix:
            destination = Path(destination_prefix) / destination
        if source.is_file() and not _is_excluded(source):
            yield FileSelection(source=source, destination=destination)


def _select_workspace_benchmark_files(repo_root: Path) -> Iterable[FileSelection]:
    for source_relative, destination_relative in WORKSPACE_BENCHMARK_FILES.items():
        source = repo_root / source_relative
        if source.is_file() and not _is_excluded(Path(destination_relative)):
            yield FileSelection(source=source, destination=Path(destination_relative))


def _select_streamlit_app(repo_root: Path) -> Iterable[FileSelection]:
    source = repo_root / "app.py"
    if source.is_file() and not _is_excluded(source):
        yield FileSelection(source=source, destination=Path(STREAMLIT_APP_DESTINATION))


def _select_scripts(repo_root: Path) -> Iterable[FileSelection]:
    scripts_dir = repo_root / "scripts"
    if not scripts_dir.is_dir():
        return
    for file_name in sorted(PUBLIC_SCRIPT_FILES):
        source = scripts_dir / file_name
        if source.is_file() and not _is_excluded(source):
            yield FileSelection(source=source, destination=Path("scripts") / source.name)


def _select_benchmark_metadata(repo_root: Path) -> Iterable[FileSelection]:
    source_root = repo_root / "benchmark_data"
    if not source_root.is_dir():
        return
    for source in sorted(source_root.rglob("*")):
        if not source.is_file() or _is_excluded(source):
            continue
        if source.suffix == ".json" or source.name.upper().startswith("README"):
            yield FileSelection(
                source=source,
                destination=Path("benchmark_data") / source.relative_to(source_root),
            )


def _select_directory(source_root: Path, destination_root: Path) -> Iterable[FileSelection]:
    if not source_root.is_dir():
        return
    for source in sorted(source_root.rglob("*")):
        if source.is_file() and not _is_excluded(source):
            yield FileSelection(
                source=source,
                destination=destination_root / source.relative_to(source_root),
            )


def _copy_selection(
    selection: FileSelection,
    output_dir: Path,
    result: StagingResult,
    *,
    dry_run: bool,
    repo_root: Path,
) -> None:
    result.copied_files.append(selection.destination.as_posix())
    result.total_bytes += selection.source.stat().st_size
    if dry_run:
        return
    destination = output_dir / selection.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    public_text = _read_public_text(selection.source, repo_root)
    if public_text is None:
        shutil.copy2(selection.source, destination)
        return
    destination.write_text(public_text, encoding="utf-8")


def _write_generated_text_file(
    *,
    output_dir: Path,
    relative_path: Path,
    text: str,
    result: StagingResult,
    dry_run: bool,
) -> None:
    result.copied_files.append(relative_path.as_posix())
    result.total_bytes += len(text.encode("utf-8"))
    if dry_run:
        return
    destination = output_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def _read_public_text(source: Path, repo_root: Path) -> str | None:
    """Return sanitized text for public staging, or None for binary files."""
    if source.suffix not in TEXT_SUFFIXES and source.name not in {"AGENTS.md", ".gitignore"}:
        return None
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    return _sanitize_public_text(text, repo_root)


def _sanitize_public_text(text: str, repo_root: Path) -> str:
    """Remove machine-specific repository paths from staged text files."""
    return text.replace(repo_root.as_posix(), PUBLIC_ROOT_PLACEHOLDER)


def _is_excluded(path: Path) -> bool:
    if path.name in EXCLUDED_PARTS:
        return True
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    return any(part in EXCLUDED_PARTS for part in path.parts)


def _find_blocked_files(output_dir: Path) -> list[str]:
    if not output_dir.exists():
        return []
    blocked: list[str] = []
    for path in output_dir.rglob("*"):
        if path.is_file() and _is_excluded(path.relative_to(output_dir)):
            blocked.append(path.relative_to(output_dir).as_posix())
    return blocked


if __name__ == "__main__":
    raise SystemExit(main())
