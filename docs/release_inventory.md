# Public Release Inventory

This inventory is the working source of truth for staging a clean public
BioHarness tree. It is intentionally conservative: files are copied into
`release/public/bio-harness/` only when they are explicitly allowlisted by the
staging script.

## Staging Target

```text
release/public/bio-harness/
```

The staging tree is a candidate public repository root. It should be validated
independently before publishing.

## Keep In Public Staging

| Area | Source | Public destination | Rationale |
| --- | --- | --- | --- |
| Core package | `bio_harness/` | `bio_harness/` for the first staging pass | Product code, wrappers, planner, strict binders, UI support modules, reporting, setup helpers. |
| Public CLI scripts | selected `scripts/*.py` | `scripts/` | Bootstrap, doctor, LLM setup, execution, benchmark, replay, scorecard, preflight, reporting, and setup-assistance commands. |
| Package config | `pyproject.toml`, `pixi.toml`, `pixi.lock`, `requirements/` | repo root / `requirements/` | Needed for install, Pixi tool environment, and package metadata. |
| UI source | `ui_v2_api.py`, `ui_v2/src`, `ui_v2/public`, UI config files, `app.py` | `ui_v2_api.py`, `apps/web/`, `apps/streamlit/app.py` | Stage the React/FastAPI UI as the primary public UI and keep Streamlit as a compatibility fallback. |
| Public docs | curated docs listed in staging script | `docs/` | Installation, packaging, isolated tools, fast-signal evidence, release checklist, and cleanup plan. |
| Fast-signal fixtures | `tests/fixtures/fast_signal/` | `tests/fixtures/fast_signal/` | Durable replay regression suite. |
| Focused tests | selected test directories | `tests/` | Public regression coverage for core harness, skills, workflows, UI helpers, setup helpers, and fixtures. |
| Benchmark manifests | selected small manifests + generated mini-benchmark docs | `benchmark_data/` | Public benchmark metadata. Mini-benchmark inputs are generated on demand so staged files never carry private absolute paths. |
| Docker recipes | `docker/isolated-tools/*.Dockerfile` | `docker/isolated-tools/` | Optional isolated tool setup documentation/build recipes. |
| GitHub workflows | selected `.github/workflows` after review | `.github/workflows/` | CI and packaging automation once sanitized. |

## Archive Or Document As Evidence, Not Raw Public Files

| Area | Source | Action |
| --- | --- | --- |
| Full benchmark results | `workspace/ablation_results/`, `workspace/runs/`, `runs/` | Do not stage raw runs. Summarize in `docs/benchmark_evidence.md` or existing evidence docs. |
| Scorecard study outputs | `workspace/studies/` | Stage selected summary docs only. Keep raw JSONL local unless explicitly small and public-safe. |
| Manuscript outputs | `docs/manuscript_assets/`, generated `.docx`, `.pptx`, figure build products | Keep out of code release unless publishing a separate paper artifact. |
| Historical planning docs | many `docs/*PLAN*.md` files | Keep only current public-facing plans and evidence docs. Archive the rest privately. |
| External repositories | `external/` | Do not stage vendored external repos. Document clone instructions instead. |

## Exclude From Public Staging

| Pattern / Directory | Reason |
| --- | --- |
| `.venv*`, `.pixi/envs`, `.tool-envs*` | Local environments and installed binary stacks. |
| `node_modules`, `.vite`, `dist/`, `.playwright-cli` | Generated frontend/browser artifacts. |
| `__pycache__`, `*.pyc`, `.pytest_cache`, `.ruff_cache`, `.DS_Store` | Generated caches and platform noise. |
| `workspace/`, `runs/`, `output/`, `outputs/` | Local run products and user data. |
| `SRR*` root directories | Downloaded sequencing data. |
| `snpEff_summary.html`, `snpEff_genes.txt`, `help.log` | Local run artifacts. |
| `.claude/`, local assistant planning caches | Private/local workflow state. |
| `docs/node_modules`, `ui_v2/node_modules`, `ui_v2/dist` | Generated dependency/build output. |

## Owner Review Needed

Before final publication, assign a release decision to these items:

| Item | Default decision | Review question |
| --- | --- | --- |
| `ui_v2/` React app | `experimental_public` source only | Is it polished enough to include, or should first release be Streamlit-only? |
| Literature/research modules | `experimental_public` | Are external network expectations and cache behavior documented? |
| Tool onboarding modules | `supported_public` | Do docs and tests show the workflow clearly enough for outside users? |
| Result review modules | `experimental_public` | Are review criteria stable enough to expose as supported API? |
| Extended-suite benchmark docs | `internal_deferred` | Which extended suite claims should appear in public docs versus paper supplement? |
| GitHub workflow `networked_geo_caps.yml` | `review_before_public` | Does it rely on credentials/network assumptions unsuitable for public CI? |

## Decisions Landed In The Staging Script

These release decisions are now encoded in
`scripts/stage_public_release_tree.py` so regenerated trees stay consistent:

| Area | Decision |
| --- | --- |
| Public README | Generated by the staging script. It points at the React/Vite UI under `apps/web/` and documents Streamlit as a compatibility fallback. |
| Public Qwen setup | Recommend `qwen3-coder-next:latest` as the default one-download Qwen path. The single-model `qwen_full` deployment sweep passed 24/24; Qwen 3.6 split-local remains research/stress evidence. |
| Mini-benchmark data | Generate on demand with `scripts/prepare_fast_signal_mini_benchmarks.py --output-root workspace/benchmark_data/fast_signal_mini`. Do not copy the private workspace `manifest.json` because it contains machine-specific absolute paths. |
| Script surface | Copy a classified allowlist of setup, execution, benchmark, fast-signal, reporting, and extension scripts. Omit one-off manuscript/research helper scripts from the public tree. |
| React UI | Stage source only under `apps/web/`, stage `ui_v2_api.py` at the repo root so its `PROJECT_ROOT` logic remains valid, and generate a primary UI README. Do not stage generated `node_modules`, `.vite`, or `dist`. |
| Public docs | Generate `docs/installation.md`, `docs/setup_assistance.md`, `docs/benchmark_evidence.md`, `docs/fast_signal.md`, and `docs/ui.md` as the first public documentation set; stage `docs/QWEN_CODER_SINGLE_MODEL_PAPER_SUMMARY_20260502.md` as the evidence note for the one-model Qwen result. |
| Package data | Include runtime catalogs, skill definitions, uncommon-skill schemas, and repair advisories in the wheel via `pyproject.toml` package-data metadata. Installed-wheel checks must run from outside the repo checkout so local files cannot mask missing package data. |
| Public path hygiene | Sanitize machine-specific repository paths during staging and keep `release_manifest.json` public-safe. Targeted scans currently show no checked private-path hits in the staged tree, wheel, or sdist. |
| Public replay gate | Fast-signal replay is deterministic on a clean public machine because replay disables native executable availability checks while preserving harness gate/binding/repair logic. Staged replay is currently 38/38 green. |
| Public CI | Generate `.github/workflows/ci.yml` for focused release tests, public-tree scan, package build, artifact scan, and experimental web UI lint/build/audit; generate `.github/workflows/package-smoke.yml` for full install smoke, console-script help, package-data checks, and fixture replay. |
| UI validation | React/Vite serves, reaches FastAPI, renders in a browser, and reports no console errors. Streamlit remains a validated compatibility fallback. |
| Public metadata | Root `LICENSE`, `CITATION.cff`, `CONTRIBUTING.md`, and `SECURITY.md` exist and are copied into the staged tree; `pyproject.toml` advertises README, license, author, project URLs, classifiers, and public keywords. |

## First-Pass Staging Policy

The first staging pass keeps the current import layout to reduce risk:

- `bio_harness/` remains at the staged repo root.
- React source is copied to `apps/web/` without generated dependencies.
- The React API backend is copied to `ui_v2_api.py` at the staged root.
- `app.py` is copied to `apps/streamlit/app.py` as the compatibility UI.
- Public tests and fixtures are copied as-is, excluding generated caches.

Later packaging cleanup can move `bio_harness/` to `src/bio_harness/` after
the staged tree has a green baseline. That move should be a separate PR because
it affects imports, package data, and console script entrypoints.

## Current Next Action

The staging script, package build, full-dependency clean wheel install, package
data check, public console-script `--help` smoke pass, staged 38/38 fast-signal
replay, generated CI workflows, repeatable public release scan, and UI smoke are
green. Final public metadata is now present. The next release-cleanup action is
to validate the staged tree as the actual public repository:

- create a fresh public repository or orphan/squashed release branch from
  `release/public/bio-harness/`;
- run the generated workflows in that public repository;
- tag the first public release only after the public workflow run is green.

For a quick regeneration check, run:

```bash
python3 scripts/stage_public_release_tree.py --dry-run
python3 scripts/stage_public_release_tree.py --clean
```

Then inspect:

```bash
release/public/bio-harness/release_manifest.json
```

The manifest must show zero blocked files before packaging work continues.

Also check:

```bash
python3 scripts/setup_llm_backend.py --help
python3 -m py_compile ui_v2_api.py
python3 scripts/prepare_fast_signal_mini_benchmarks.py --output-root /tmp/bioharness-mini-smoke
```

from inside `release/public/bio-harness/` before publishing.
