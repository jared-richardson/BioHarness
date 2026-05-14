# Public Release Packaging And Repo Cleanup Plan - 2026-05-01

## Purpose

Prepare BioHarness for a clean open-source release by staging only the product
code, public tests, setup assistance, UI, and reproducible benchmark assets into
a professional release tree. The current working repository contains valuable
research history, generated artifacts, local environments, manuscript build
outputs, benchmark run products, and experimental branches of functionality.
Those should not be published as-is.

The plan favors a clean staged release directory first, then a new public Git
repository or squashed release branch. Deleting old directories from this repo
alone is not enough if the existing Git history will be made public.

## Core Decision

Create a clean staging tree at:

```text
release/public/bio-harness/
```

Treat that directory as the candidate public repository root. Populate it with
only the files we intend to publish. Validate it independently. Once green,
publish from that staged tree into a new GitHub repository or an orphan/squashed
release branch.

Do not make the current messy repo public without either:

- creating a fresh public repository from the staged tree, or
- rewriting history with a deliberate `git filter-repo`/orphan-branch process.

## Current Implementation Status - 2026-05-03

The first non-destructive staging pass is implemented and verified:

- `scripts/stage_public_release_tree.py` builds
  `release/public/bio-harness/` from an explicit allowlist.
- Public `README.md`, setup docs, UI docs, benchmark evidence docs,
  fast-signal docs, benchmark-data docs, and `scripts/README.md` are generated
  for the staged layout.
- The primary UI path is the React/Vite app under `apps/web/`, backed by
  `ui_v2_api.py` at the public repository root.
- The Streamlit UI is still staged at `apps/streamlit/app.py` as a
  compatibility fallback.
- Mini-benchmark raw inputs are not copied from private workspaces; they are
  generated on demand with
  `scripts/prepare_fast_signal_mini_benchmarks.py --output-root workspace/benchmark_data/fast_signal_mini`.
- The public script surface is classified and allowlisted. One-off manuscript
  and local analysis helpers are omitted.
- The staged tree was regenerated clean after smoke testing and had zero
  blocked files in `release_manifest.json`.
- Public setup docs now recommend the one-download Qwen path,
  `qwen3-coder-next:latest`, based on
  `qwen_coder_single_model_full_20260501_r1` passing the 24-case `qwen_full`
  sweep with the same model used for planning and execution.
- Package-build validation passed from the staged tree: wheel and sdist build
  successfully, wheel metadata carries the `requirements/venv-core.txt`
  dependency set, every declared console-script module is present in the staged
  tree, and a fresh full-dependency wheel install completed in a clean virtual
  environment.
- Installed-wheel smoke checks passed from outside the repository checkout:
  public console-script `--help` paths work, isolated imports resolve from
  `site-packages`, `pip check` reports no broken requirements, and runtime
  package data is present in the wheel for capability catalogs, skill
  definitions, uncommon-skill schemas, and repair advisories.
- Public staging now sanitizes machine-specific repository paths in copied
  text files. The staged tree, rebuilt wheel, and rebuilt sdist have no hits for
  the checked private-path patterns, no files larger than 10 MB, and no obvious
  private-key/API-key assignment hits in the targeted release scan.
- Fast-signal replay is public-machine deterministic: staged-tree replay no
  longer depends on optional native tool installations for candidate-gate
  fixtures. The staged tree replays 38/38 curated fast-signal fixtures.
- Public CI scaffolding is generated into the staged tree:
  `.github/workflows/ci.yml` runs focused release tests, public-tree scans, and
  package artifact scans, plus experimental web UI `npm ci`, lint, build, and
  audit; `.github/workflows/package-smoke.yml` runs the heavier full-dependency
  wheel install, console-script smoke, package-data check, and fast-signal replay
  on demand or weekly.
- `scripts/scan_public_release_tree.py` is now the repeatable local/CI scan for
  large files, machine-specific path leaks, obvious private keys, API-key
  assignments, and archive contents.
- UI release validation passed for both surfaces: the React/Vite UI serves from
  the expected Vite port, reaches the FastAPI backend, renders the Bio-Harness
  workspace shell in a real browser, and reports no browser console errors; the
  Streamlit compatibility UI also serves and renders cleanly. React/Vite lint,
  production build, and `npm audit --audit-level=moderate` pass.
- Final public metadata has been added at the repository root and is copied by
  the staging script: `LICENSE`, `CITATION.cff`, `CONTRIBUTING.md`, and
  `SECURITY.md`. Package metadata now exposes the README, MIT license file,
  author, project URLs, classifiers, and public keywords in `pyproject.toml`.
- A final pre-git public-tree validation pass is complete. The staged tree was
  regenerated, scanned, and confirmed free of generated runtime directories
  such as `workspace/`, `.pixi/`, `node_modules/`, `dist/`, `build/`,
  `*.egg-info`, `__pycache__`, and `.git`.
- The public tree now includes the May 3 `bcftools_filter_run` hardening for
  FreeBayes/GATK filter-expression mismatches: missing `QD` is rewritten to
  `QUAL / INFO/DP` only when the VCF header declares `INFO/DP`; unsupported
  missing tags remain strict failures. The corresponding repair advisory and
  regression tests are staged.
- Final public-tree fast-model mini preflight is green at `3/3` under
  `qwen3-coder-next:latest`:
  - `control_evolution_mini` -> `final/variants_shared.csv`
  - `germline_vc_mini` -> `final/variants.vcf`
  - `de_mini` -> `final/deseq_results.csv`
- Final quick pre-git release-quality receipt is green:
  `workspace/studies/release_quality_receipt_pre_git_20260503.{json,md}`.
  It covers public-tree staging/scanning, release-critical Ruff check/format,
  broad Ruff advisory, compileall, scoped mypy, focused pytest, and frontend
  lint/build/audit.
- Full validation details are recorded in
  `docs/PUBLIC_TREE_VALIDATION_REPORT_20260503.md`.

Deferred cleanup remains:

- Move to a `src/bio_harness/` layout only after the staged tree has a stable
  package/test baseline.
- Continue product-level UI polish after the first public packaging pass.

## Release Scope

The public repository should include:

- The BioHarness Python package.
- Thin CLI entrypoints for setup, doctor, execution, benchmark, replay, and
  reporting.
- Public setup assistance and environment diagnostics.
- The supported UI path.
- Small synthetic fixtures and fast-signal regression fixtures.
- Benchmark manifests, validators, and summary evidence.
- Documentation needed to install, run, extend, test, and cite the project.
- CI workflows and contributor guardrails.

The public repository should exclude:

- Local virtual environments and Pixi environments.
- Node modules, Vite caches, Playwright traces, pytest caches, `__pycache__`,
  `.DS_Store`, and generated local output.
- Raw benchmark run directories, full workspace artifacts, temporary SRA/output
  files, and large data copies.
- Private local paths, local API keys, personal machine state, and accidental
  downloaded references.
- Manuscript scratch notes and bulky generated manuscript assets unless they
  are intentionally part of a separate paper artifact.

## Target Directory Layout

The staged public tree should look like this:

```text
release/public/bio-harness/
  README.md
  LICENSE
  CITATION.cff
  CODE_OF_CONDUCT.md
  CONTRIBUTING.md
  SECURITY.md
  pyproject.toml
  pixi.toml
  pixi.lock
  requirements/
  src/
    bio_harness/
      agents/
      capabilities/
      core/
      harness/
      pipeline_scripts/
      reporting/
      skills/
      ui/
      workflows/
  apps/
    streamlit/
      app.py
    web/
      package.json
      package-lock.json
      src/
      public/
  scripts/
    bootstrap_bioharness.py
    doctor_bioharness.py
    setup_llm_backend.py
    stage_inputs.py
    trusted_download.py
    configure_isolated_tools.py
    run_agent_e2e.py
    run_domain_expansion_ablation.py
    replay_fast_signal_fixtures.py
    fast_signal_scorecard.py
  tests/
    core/
    skills/
    pipeline_scripts/
    fixtures/
      fast_signal/
      mini_bench/
  benchmark_data/
    manifests/
    fast_signal_mini/
    synthetic_public/
  docs/
    architecture.md
    installation.md
    setup_assistance.md
    ui.md
    benchmark_evidence.md
    fast_signal.md
    extending_tools.md
    troubleshooting.md
    release_checklist.md
  docker/
  .github/
    workflows/
      ci.yml
      package.yml
  .gitignore
```

The final public repo root can either keep this layout directly or move the
contents of `release/public/bio-harness/` to repository root after validation.

## Keep Inventory

### Core Harness

Keep and package these modules, after standards cleanup:

- `bio_harness/agents`
- `bio_harness/core`
- `bio_harness/harness`
- `bio_harness/skills`
- `bio_harness/workflows`
- `bio_harness/pipeline_scripts`
- `bio_harness/reporting`
- `bio_harness/capabilities`
- `bio_harness/tools`

Key behavior to preserve:

- Local-first LLM planning and execution.
- Ollama, Ollama OpenAI-compatible, vLLM, MLX, and generic OpenAI-compatible
  backend support.
- Stepwise execution.
- Strict artifact binding.
- Branch-stage frontier control.
- Repair advisories through `bio_harness/harness/repair_advisories.json`.
- Skill registry and wrapper execution.
- Deterministic setup and preflight assistance.
- Fast-signal fixtures, replay, scorecard, and mini-benchmark utilities.
- Reporting/export helpers.

### Setup Assistance

These are user-facing features and must be preserved:

- `scripts/bootstrap_bioharness.py`
- `scripts/doctor_bioharness.py`
- `scripts/setup_llm_backend.py`
- `scripts/check_llm_backend.py`
- `scripts/check_resource_preflight.py`
- `scripts/configure_isolated_tools.py`
- `scripts/stage_inputs.py`
- `scripts/trusted_download.py`
- `scripts/audit_reference_bundle.py`
- `scripts/materialize_reference_bundle.py`
- `bio_harness/core/harness_doctor.py`
- `bio_harness/core/env_bootstrap.py`
- `bio_harness/core/environment_bootstrap.py`
- `bio_harness/core/llm_setup_support.py`
- `bio_harness/core/resource_preflight.py`
- `bio_harness/core/input_staging.py`
- `bio_harness/core/trusted_downloads.py`
- `bio_harness/core/reference_manager.py`

Each setup helper needs a public smoke test that runs without large external
data and without requiring a live LLM unless marked.

### Features Built Earlier But Easy To Lose

Explicitly inventory and decide support level for these before cleanup:

- Tool onboarding:
  `bio_harness/core/tool_onboarding.py`,
  `bio_harness/core/onboarding_*`,
  `scripts/onboard_novel_tool.py`.
- Literature/research support:
  `bio_harness/core/literature_*`,
  `bio_harness/core/research_engine.py`.
- Result review and output quality:
  `bio_harness/core/result_review.py`,
  `bio_harness/core/result_decision_policy.py`,
  `bio_harness/core/output_quality.py`,
  `bio_harness/core/in_run_quality_monitor.py`.
- Manual ingestion:
  `bio_harness/core/manual_ingestion.py`.
- UI followups and model-switch guidance:
  `bio_harness/ui/completed_run_followups.py`,
  `bio_harness/ui/model_switch_help.py`.
- Fast-signal infrastructure:
  `bio_harness/core/fast_signal*.py`,
  `scripts/replay_fast_signal_fixtures.py`,
  `scripts/fast_signal_scorecard.py`,
  `tests/fixtures/fast_signal`.

For each item, assign one of:

- `supported_public`
- `experimental_public`
- `internal_deferred`
- `remove_from_release`

Do not silently drop features that users may need for setup, tool onboarding,
or troubleshooting.

## Remove / Archive Inventory

These should not be copied into the public staging tree:

- `.venv*`, `.pixi/envs`, `.tool-envs*`, `.vite`, `.ruff_cache`,
  `.pytest_cache`, `.playwright-cli`, `__pycache__`, `*.pyc`.
- `workspace/`, `runs/`, `output/`, `outputs/`, ad hoc root files such as
  `snpEff_summary.html` and `snpEff_genes.txt`.
- `ui_v2/node_modules`, `ui_v2/dist`, `ui_v2/.vite`.
- `docs/node_modules`, generated slide/docx scratch artifacts, bulky
  manuscript build outputs unless intentionally moved to a paper artifact.
- `dist/` review bundles and generated local packages.
- Raw downloaded data directories such as `SRR1553606` unless a tiny synthetic
  fixture is intentionally kept.
- Private local benchmark outputs and full run workspaces.

If something is scientifically important evidence, preserve it as a small
summary artifact in `docs/benchmark_evidence.md` with paths to archived local
storage, not as raw run directories in GitHub.

## Implementation Phases

### Phase 0 - Freeze And Audit

1. Create a cleanup branch.
2. Freeze feature work except critical bug fixes.
3. Generate an inventory:
   - tracked files by directory,
   - untracked files by directory,
   - large files,
   - generated caches,
   - potential secrets,
   - public package data.
4. Run a secret scan before staging anything.
5. Produce `docs/release_inventory.md` with four tables:
   - keep,
   - stage as docs/evidence,
   - archive outside public repo,
   - delete/ignore.

Gate:

- No public staging until the inventory has owner decisions for each top-level
  directory.

### Phase 1 - Create The Public Staging Tree

1. Create `release/public/bio-harness/`.
2. Copy only approved keep files into the staging tree.
3. Start with the current package, then progressively tighten:
   - `bio_harness/` -> `src/bio_harness/`
   - supported `scripts/`
   - `requirements/`
   - `pixi.toml` and `pixi.lock`
   - small public benchmark fixtures
   - curated docs
   - CI workflows
4. Exclude all generated directories with explicit copy rules.
5. Add a staging script, for example:
   `scripts/stage_public_release_tree.py`.

Gate:

- `find release/public/bio-harness -name node_modules -o -name __pycache__`
  returns nothing.
- No file in the staged tree is larger than the agreed limit unless explicitly
  allowlisted.
- Secret scan passes.

### Phase 2 - Package Layout And Entry Points

Move to a public Python package layout:

- Use `src/bio_harness` as the canonical package location.
- Keep `scripts/` as thin public CLIs, not as an importable Python package.
- Move reusable CLI logic into `bio_harness/cli/` or existing importable
  modules.
- Update `pyproject.toml` so `setuptools` packages only `bio_harness*`, not
  arbitrary `scripts*`.
- Declare package data explicitly:
  - skill definitions,
  - capability catalogs,
  - isolated tool recipes,
  - repair advisories,
  - benchmark fixture metadata.
- Keep console scripts stable:
  - `bio-harness-bootstrap`
  - `bio-harness-doctor`
  - `bio-harness-run`
  - `bio-harness-benchmark`
  - `bio-harness-preflight`
  - `bio-harness-configure-isolated-tools`
  - `bio-harness-reference-audit`
  - `bio-harness-reference-build`
  - `bio-harness-report`
  - `bio-harness-replay-fixtures`
  - `bio-harness-scorecard`

Gate:

- `python -m build` succeeds from the staged tree.
- `pip install dist/*.whl` succeeds in a clean virtualenv.
- Console scripts import and show help.

### Phase 3 - Standards Cleanup

Apply `CODING_STANDARDS.md` to the staged code:

- Add Google-style docstrings to new or public modules/functions/classes that
  lack them.
- Add type hints to public interfaces.
- Split oversized modules before adding more branches.
- Convert shell/file path logic to `pathlib.Path` where practical.
- Remove bare `except` patterns.
- Ensure user-facing errors explain impact and likely remedy.
- Keep strict-mode benchmark behavior benchmark-blind.
- Move recurring repair knowledge into `repair_advisories.json` through
  `scripts/upsert_repair_advisory.py`.

High-priority cleanup targets:

- Root `app.py` is too large. Move the supported Streamlit entrypoint to
  `apps/streamlit/app.py` and keep app logic in `bio_harness/ui/`.
- Keep CLI entrypoints thin; move reusable logic out of large scripts.
- Split any large strict binder or runner modules by concern only when tests
  are already in place.
- Remove stale README references to missing scripts or unsupported flows.

Gate:

- Ruff or equivalent lint passes on staged code.
- Focused test suite passes.
- Import smoke test passes for every public package module category.

### Phase 4 - Preserve Setup Assistance

Create a setup-assistance acceptance matrix:

| Feature | Command | Expected Public Behavior |
| --- | --- | --- |
| Bootstrap | `bio-harness-bootstrap --help` and dry-run/smoke mode | Explains/install checks without large data. |
| Doctor | `bio-harness-doctor --selected-dir <tmp>` | Reports environment, tools, backend, and actionable next steps. |
| LLM setup | `scripts/setup_llm_backend.py --help` | Explains Ollama/OpenAI-compatible/vLLM/MLX setup. |
| Input staging | `scripts/stage_inputs.py --help` | Stages by symlink/copy with receipt. |
| Trusted download | `scripts/trusted_download.py --help` | Enforces policy and writes receipt. |
| Isolated tools | `bio-harness-configure-isolated-tools --help` | Lists supported sidecar tool setups. |
| Preflight | `bio-harness-preflight --help` | Validates resources and gives remedies. |

Add or keep tests for each command.

Gate:

- A fresh user can go from clone -> bootstrap -> doctor -> backend setup
  guidance without reading internal benchmark docs.

### Phase 5 - UI Product Work

Support two UI tracks, with clear public labels.

#### React UI - Primary Release Surface

The React/Vite UI is now the intended product UI for public release. Keep it in:

```text
apps/web/
ui_v2_api.py
```

Required UI features to preserve:

- Interactive orchestrator chat.
- Model/backend selection and setup diagnostics.
- Project directory selection.
- Input staging/upload/linking.
- Plan generation and review.
- Stepwise execution controls.
- Run status, logs, artifacts, and terminal state.
- Skill/tool capability browsing.
- Completed-run followups.
- Model-switch help.
- Safe command execution under workspace constraints.

Needed UI improvements:

- Move `ui_v2_api.py` into a proper backend module such as
  `bio_harness/api/server.py` after the public tree is stable.
- Remove stale demo labels and duplicated UI flows.
- Improve first-run onboarding and backend setup guidance.
- Add visible health/status strip for model server, Pixi tools, selected dir,
  and active run.
- Add clearer artifact previews and final deliverable export actions.
- Add API contract tests between the web app and backend.
- Add regression tests for state persistence, model selection, and
  plan/execution controls.
- Add screenshots to `docs/ui.md`.

#### Streamlit UI - Compatibility Fallback

The Streamlit UI remains useful as a Python-only compatibility fallback. Keep it
staged at:

```text
apps/streamlit/app.py
```

Do not publish generated UI dependencies or build output:

- remove `node_modules`, `.vite`, and `dist`;
- keep `package.json`, `package-lock.json`, `src/`, `public/`, and config;
- keep frontend lint/build/audit CI green.

Gate:

- React UI launches cleanly, reaches the FastAPI backend, and has no browser
  console errors.
- Streamlit compatibility UI launches cleanly from the staged tree.

### Phase 6 - Benchmark And Evidence Hygiene

Public release should include enough benchmark material to reproduce claims
without publishing the entire local workspace.

Keep:

- `workspace/benchmark_data/ablation_manifest_24.json` copied to
  `benchmark_data/manifests/ablation_manifest_24.json`.
- Small synthetic mini-benchmark cases.
- Fast-signal fixtures.
- Summary evidence docs:
  - `docs/EVALUATION_COMPLETED_CHECKLIST_20260501.md`
  - Qwen/Gemma summary tables
  - benchmark command examples.

Do not keep:

- Full `workspace/ablation_results`.
- Full `workspace/runs`.
- Large raw input data unless license and size are release-safe.

Create:

- `docs/benchmark_evidence.md`: concise result table plus artifact checks.
- `docs/reproduce_benchmarks.md`: commands to regenerate public/synthetic
  tests and instructions for private/full datasets.
- `benchmark_data/README.md`: data licensing and expected external downloads.

Gate:

- Public benchmark tests run without private data.
- Full benchmark reproduction docs clearly explain which data must be obtained
  externally.

### Phase 7 - Documentation Set

Create or rewrite the public docs:

- `README.md`: concise value proposition, install, quick run, UI, CLI.
- `docs/installation.md`: Python, Pixi, Ollama, optional tool stacks.
- `docs/setup_assistance.md`: bootstrap, doctor, backend setup, staging,
  trusted download, isolated tools.
- `docs/architecture.md`: planner, executor, skill registry, strict binding,
  repair, scorecard.
- `docs/extending_tools.md`: adding wrappers, skills, repair advisories, tests.
- `docs/benchmark_evidence.md`: Qwen/Gemma results and limits.
- `docs/fast_signal.md`: replay, mini-bench, reproduction baseline, scorecard.
- `docs/ui.md`: supported UI flow and screenshots.
- `docs/security.md` or `SECURITY.md`: local execution threat model.
- `CONTRIBUTING.md`: coding standards, tests, PR checklist.
- `CITATION.cff`: citation metadata.
- `LICENSE`: chosen open-source license.

Gate:

- README commands work from a fresh clone of the staged tree.
- No doc points at deleted local-only artifacts as required public inputs.

### Phase 8 - CI And Release Gates

Add GitHub Actions:

1. `ci.yml`
   - install Python dependencies,
   - run lint,
   - run focused unit tests,
   - run fast-signal replay,
   - run setup helper help/import smoke tests.
2. `package.yml`
   - build wheel/sdist,
   - install wheel into clean environment,
   - run console-script help smoke tests.
3. `frontend.yml` if React UI is included:
   - npm ci,
   - npm run lint,
   - npm run build.
4. Optional nightly/local workflow:
   - mini-benchmark suite,
   - selected integration tests,
   - no live LLM required unless explicitly marked.

Minimum public-release gate:

- Python package build passes.
- Clean install smoke passes.
- Focused pytest gate passes.
- Fast-signal replay passes.
- Setup helpers show help and import successfully.
- Streamlit UI import/start smoke passes.
- Secret scan passes.
- No generated/cache/large artifacts in staged tree.

### Phase 9 - New Public Repository Cutover

Recommended path:

1. Finish and validate `release/public/bio-harness/`.
2. Create a brand-new GitHub repository.
3. Copy the staged tree into the new repository root.
4. Commit with clean history.
5. Tag `v0.1.0-alpha` or similar.
6. Keep the current research repo private as the development/archive repo.

Alternative path:

1. Create an orphan branch from the staged tree.
2. Commit only staged public files.
3. Force-push that orphan branch to a public repository.

Avoid publishing the current repo history unless secret/data scans and history
rewrites have been completed.

## Detailed Work Queue

1. Create `docs/release_inventory.md`.
2. Add or update `.gitignore` to cover all generated directories.
3. Write `scripts/stage_public_release_tree.py`.
4. Populate `release/public/bio-harness/` with approved files.
5. Move package to `src/bio_harness` in the staged tree. Deferred until after
   the public package baseline is stable.
6. Convert console scripts to importable CLI modules or verified thin wrappers.
   The current wrappers are verified by installed-wheel `--help` smoke tests.
7. Declare package data in `pyproject.toml`. Complete for runtime catalogs,
   skill definitions, uncommon-skill schemas, and repair advisories.
8. Add release docs and metadata files.
9. Decide UI release mode:
   - React/Vite primary,
   - Streamlit compatibility fallback.
10. Remove generated UI artifacts from staging.
11. Add setup-assistance smoke tests.
12. Add public benchmark/evidence docs.
13. Add CI workflows.
14. Run clean install and package build. Complete for wheel/sdist build plus
   full-dependency clean wheel install.
15. Run focused tests and replay suite from staged tree.
16. Run UI smoke.
17. Run secret and large-file scans.
18. Publish to new clean repository or orphan branch.

## Definition Of Done

The cleanup is done when:

- The staged public tree contains no local environments, run workspaces,
  generated caches, node modules, or private data.
- A fresh clone can bootstrap, run doctor, configure a local model backend,
  stage inputs, and launch the supported UI.
- The package builds as wheel and sdist.
- Public console scripts work after wheel install.
- Focused test gate and fast-signal replay pass from the staged tree.
- The three required mini-benchmark families remain runnable.
- Setup assistance features are documented and tested.
- UI support level is explicit and documented.
- Benchmark evidence docs state what passed, what is reproducible publicly, and
  what remains private/external-data-dependent.
- The public repo has clean metadata: license, citation, contributing,
  security, README, and CI.
- The current research repo remains private or is not published with old
  history.

## Immediate Next Step

The staging, install-quality, public replay, public mini preflight,
release-quality, scan, CI-scaffolding, UI launch, and metadata gates are green.
Continue with public-repo publication polish:

- create the fresh public Git repository or orphan/squashed release branch from
  `release/public/bio-harness/`;
- run the generated CI and package-smoke workflows in that actual public
  repository;
- decide whether to add a first-release tag after the public workflow run is
  green.
