"""Pathway-comparison plan repairs for the E2E harness."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.harness.config import (
    COMPARE_PATHWAYS_SCRIPT,
)
from bio_harness.harness.plan_helpers import (
    _normalize_steps,
    _renumber_plan_steps,
)


def _looks_like_inline_multi_model_compare_pathways_command(command: str) -> bool:
    """Return whether a bash command inlines the Alzheimer compare-pathways logic."""

    command_l = str(command or "").lower()
    if "compare_pathways.py" in command_l:
        return False
    input_markers = ("dea_ps3o1s", "gse161904", "gse168137")
    scientific_markers = (
        "ttest_ind",
        "fisher_exact",
        "import pandas",
        "import scipy",
        "5xfad_pvalue",
        "3xtg_ad_pvalue",
        "ps3o1s_pvalue",
        "pathway_comparison.csv",
    )
    matched_inputs = sum(marker in command_l for marker in input_markers)
    matched_science = sum(marker in command_l for marker in scientific_markers)
    return matched_inputs >= 2 and matched_science >= 1


def _repair_multi_model_compare_pathways_commands(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rewrite inline Alzheimer pathway comparisons to the repo helper script."""

    if not isinstance(analysis_spec, dict):
        return plan, {"changed": False, "why": "analysis_spec_missing"}
    if str(analysis_spec.get("analysis_type", "") or "").strip().lower() != "multi_model_dge_pathway":
        return plan, {"changed": False, "why": "analysis_type_not_multi_model_dge_pathway"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    ps3o1s_csv = ""
    tg_counts = ""
    fad_counts = ""
    for candidate in sorted(data_root.rglob("*")):
        if not candidate.is_file():
            continue
        name_l = candidate.name.lower()
        resolved = str(candidate.resolve())
        if not ps3o1s_csv and "ps3o1s" in name_l and name_l.endswith(".csv"):
            ps3o1s_csv = resolved
        elif not tg_counts and "161904" in name_l and candidate.suffix.lower() in {".txt", ".tsv", ".csv"}:
            tg_counts = resolved
        elif not fad_counts and "168137" in name_l and candidate.suffix.lower() in {".txt", ".tsv", ".csv"}:
            fad_counts = resolved
    if not (ps3o1s_csv and tg_counts and fad_counts):
        return plan, {
            "changed": False,
            "why": "missing_multi_model_pathway_inputs",
            "ps3o1s_csv": ps3o1s_csv,
            "tg_counts": tg_counts,
            "fad_counts": fad_counts,
        }

    output_dir = str((selected_dir / "outputs" / "alzheimer_mouse").resolve(strict=False))
    output_csv = str((selected_dir / "final" / "pathway_comparison.csv").resolve(strict=False))
    python_bin = str(preferred_helper_python_executable())
    project_root = str(COMPARE_PATHWAYS_SCRIPT.resolve(strict=False).parents[2])
    repaired_command = (
        f"env {shlex.quote(f'PYTHONPATH={project_root}')} {shlex.quote(python_bin)} "
        f"{shlex.quote(str(COMPARE_PATHWAYS_SCRIPT))} "
        f"--precomputed-de-table PS3O1S={shlex.quote(ps3o1s_csv)} "
        f"--count-table 3xTG_AD={shlex.quote(tg_counts)} "
        f"--count-table 5xFAD={shlex.quote(fad_counts)} "
        f"--output_dir {shlex.quote(output_dir)} "
        f"--output-csv {shlex.quote(output_csv)} "
        "--run-differential-analysis"
    )

    replacements: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "").strip()
        command_l = command.lower()
        repair_needed = False
        if "compare_pathways.py" in command_l:
            repair_needed = True
        elif "featurecounts_run" in command_l and "pathway_comparison.csv" in command_l:
            repair_needed = True
        elif _looks_like_inline_multi_model_compare_pathways_command(command):
            repair_needed = True
        if not repair_needed:
            continue
        step["arguments"] = {**args, "command": repaired_command}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "mode": "replace",
                "from": command,
                "to": repaired_command,
            }
        )

    if not replacements:
        return plan, {"changed": False, "why": "no_multi_model_compare_pathways_repairs"}

    patched = dict(plan)
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "repaired_multi_model_compare_pathways_command",
        "output_dir": output_dir,
        "output_csv": output_csv,
        "replacements": replacements,
    }


__all__ = [
    "_looks_like_inline_multi_model_compare_pathways_command",
    "_repair_multi_model_compare_pathways_commands",
]
