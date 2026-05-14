---
name: pipeline-run-scripts
description: Use reusable bash pipeline scripts and run-specific script exports for traceable, rerunnable bioinformatics workflows (for example FastQC, STAR, and rMATS).
---

# Pipeline Run Scripts

## Use This Skill When
- You need to replace hardcoded one-off commands with reusable script modules.
- You need each execution run to produce concrete rerunnable scripts with fixed arguments.
- You need better traceability for what was executed in a run.

## Canonical Locations
- Reusable script library: `bio_harness/pipeline_scripts/*.sh`
- Plan templates: `bio_harness/workflows/templates.py`
- Script export helper: `bio_harness/workflows/templates.py` (`export_plan_run_scripts`)
- Per-run exported scripts: `workspace/runs/<run_id>/scripts/<script_set>/`

## Required Pattern
1. Put reusable command logic in `bio_harness/pipeline_scripts/*.sh`.
2. Build plans in `bio_harness/workflows/templates.py` by calling those scripts.
3. Before execution starts, export concrete run scripts with `export_plan_run_scripts(...)`.
4. Save export metadata on run state (`script_exports`) and show paths in the UI.

## Output Contract Per Run
- `plan.json`: run-local snapshot of the executable plan.
- `steps/step_XX_<tool>.sh`: one script per step.
- `run_all.sh`: executes all step scripts in order.
- `scripts_manifest.json`: index of generated script files.

## Fast Troubleshooting Mode
- Use `create_test_subset_from_r1_lists.sh` to build test FASTQs from the first N reads (default: 1,000,000 reads per FASTQ).
- In troubleshooting mode, align/test on subset FASTQs first to shorten feedback loops.
- Keep full-data execution available for final biological results.

## Bounded Auto-Repair Policy
- Classify failures (for example: tool missing, stale tmp artifacts, format errors).
- Apply a mapped repair action (for example: clean tmp, adjust retry inputs).
- Retry once (max 1-2 attempts per failure class).
- Verify outputs after retry (required deliverables exist and are non-empty).
- Record an audit trail with run id, failure class, action, and change summary.
- If a class repeats across runs, emit a promotion suggestion to update shared templates/skills.

## Script Design Rules
- Use `set -euo pipefail`.
- Accept explicit positional arguments.
- Emit machine-readable markers for known failures (for example `__MISSING_TOOL__`, `__MISSING_REFERENCE__`).
- Prefer idempotent behavior when outputs already exist.
- Avoid destructive operations.
