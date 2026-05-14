# Packaging and Installation

Bio-Harness is a Python-first project. The core harness, reporting exporters,
manuscript tooling, and Streamlit interface do not require `npm`, `pnpm`, or
`yarn`.

Supported Python floor: `3.10+`

Supported release path today:

- source checkout from the repository root
- bootstrap with `python3 scripts/bootstrap_bioharness.py`
- or editable install with `pip install -e . --no-deps`

Wheel/PyPI artifact support is not the supported distribution target yet.

## Recommended Installation Paths

### 1. Bootstrap the supported local environment

Use the bootstrap script when you want a working Python virtual environment plus
the repo-managed Pixi toolchain.

```bash
python3 scripts/bootstrap_bioharness.py
.venv/bin/streamlit run app.py
```

If you prefer a shell entry point, [install.sh](../install.sh) is supported as a thin wrapper around the same bootstrap path:

```bash
./install.sh
```

This path:

- creates `.venv`
- installs the Python package and console entry points
- installs the default Pixi environment
- installs isolated-tool recipes for requested tools such as `cnvkit.py`,
  `prokka`, and `STAR-Fusion`
- lets the harness discover Pixi-managed tools automatically at runtime
- writes a bootstrap receipt to `workspace/bootstrap_reports/`

For the broader optional tool surface used by the non-benchmark Qwen skill
smoke matrix:

```bash
python3 scripts/bootstrap_bioharness.py --all-installable-tools
```

That additionally installs the optional Pixi environments:

- `reports`
- `alignment-extra`
- `variant-extra`
- `r-bulk`
- `r-splicing`
- `r-singlecell`
- `specialty-general`
- `specialty-assembly`
- `specialty-annotation`

### 2. Python package installation

Use editable install when you want the Python package and console commands,
while managing bioinformatics binaries outside the repo.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/venv-core.txt
.venv/bin/python -m pip install -e . --no-deps
bio-harness-run --help
bio-harness-report --help
bio-harness-export-ro-crate --help
```

This path is useful for:

- figure generation
- manuscript exports
- RO-Crate and workflow-exchange exports
- report generation
- integration with external package managers or site-specific tool modules

## Platform Status

The current `pixi` workspace explicitly targets:

- `osx-arm64`
- `linux-64`

These are the tested binary-tool platforms in the repository today.

The Python-only layers of the project are substantially more portable, but the
full bioinformatics stack depends on external native tools. For additional CPU
targets such as `osx-64` or `linux-aarch64`, the safe current recommendation is:

- install the Python package with `pip`
- provision native tools separately
- or use a containerized execution environment once multi-architecture images
  are added

## Console Commands

The package now exposes installable Python entry points:

- `bio-harness-bootstrap`
- `bio-harness-doctor`
- `bio-harness-configure-isolated-tools`
- `bio-harness-run`
- `bio-harness-benchmark`
- `bio-harness-figure`
- `bio-harness-schema`
- `bio-harness-preflight`
- `bio-harness-compare`
- `bio-harness-reference-audit`
- `bio-harness-reference-build`
- `bio-harness-export-docx`
- `bio-harness-export-ro-crate`
- `bio-harness-export-workflow`
- `bio-harness-report`

These commands are additive convenience wrappers around the existing repository
scripts. They do not change default execution behavior.

## Do You Need NPM?

No for the main harness.

The current UI is Streamlit-based and runs from Python. There is no required
Node or browser-build step for:

- the harness core
- benchmark execution
- report/export generation
- manuscript/figure generation

An `npm` toolchain would only be relevant if the project later grows a separate
JavaScript frontend. That is not required for the current system.

## What the Harness Can Install Deterministically

The repo-managed Pixi environments can provision the default benchmark toolchain
plus these additional bundles:

- `reports`: `multiqc`, `quarto`
- `alignment-extra`: `bowtie2`, `hisat2`, `kallisto`
- `variant-extra`: `freebayes`
- `r-bulk`: `bioconductor-edger`, `bioconductor-limma`
- `r-splicing`: `bioconductor-dexseq`
- `r-singlecell`: `r-seurat`
- `specialty-general`: `hmmer`, `macs2`, `bismark`
- `specialty-assembly`: `trinity`
- `specialty-annotation`: `ensembl-vep`

The harness bootstrap and runtime auto-install path can deterministically
install those Pixi-managed tools when they are requested. The harness searches
across all installed Pixi environments at runtime, so these optional bundles do
not need to be merged into one large environment.

## Manual-Only Tools

Some tools are still outside the configured Pixi channels and therefore remain
manual installs today:

- `cellranger`
- `mixcr`
- `majiq`

The bootstrap report calls these out explicitly instead of pretending they were
installed.

## Isolated Tool Paths

For selected hard-to-package tools, Bio-Harness can use isolated tool-specific
paths without changing the default environment:

- `cnvkit.py`
- `prokka`
- `STAR-Fusion`

See [docs/isolated_tools.md](isolated_tools.md).

At runtime, the harness can also try these isolated recipes automatically with:

```bash
bio-harness-run --auto-setup-isolated-tools
```

In normal `scientific_harness` mode, `bio-harness-run` now defaults to
automatic Pixi install and isolated-tool setup during missing-tool recovery.
Use `--no-auto-install-missing-tools` or `--no-auto-setup-isolated-tools` to
disable those deterministic self-healing paths explicitly.
