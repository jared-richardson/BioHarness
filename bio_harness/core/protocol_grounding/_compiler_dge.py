"""Template compiler for multi-model DGE plus pathway comparison.

This compiler keeps the grounded Alzheimer-style pathway workflow aligned with
the repo-local ``compare_pathways.py`` helper instead of inlining analysis code
into a large shell heredoc. That preserves deterministic helper ownership and
keeps strict semantic guards meaningful in benchmark mode.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.analysis_spec_support import (
    COMPARE_PATHWAYS_HELPER_SCRIPT,
    preferred_helper_python_executable,
)
from bio_harness.core.protocol_grounding._shared import _renumber_plan


def _classify_data_files(
    data_root: Path,
) -> dict[str, dict[str, Any]]:
    """Discover and classify input files for multi-model DGE.

    Args:
        data_root: Root directory containing benchmark/task input files.

    Returns:
        A dict keyed by model label with ``path`` and ``kind`` fields, where
        ``kind`` is either ``counts`` or ``de_table``.
    """
    models: dict[str, dict[str, Any]] = {}
    for candidate in sorted(data_root.rglob("*")):
        if not candidate.is_file():
            continue
        name_l = candidate.name.lower()
        if candidate.suffix not in (".csv", ".tsv", ".txt"):
            continue
        path = str(candidate.resolve())
        try:
            with open(path, "r", encoding="utf-8") as fh:
                header = fh.readline().lower()
        except Exception:
            continue
        is_de_table = ("pval" in header or "p_val" in header) and ("qval" in header or "padj" in header or "fdr" in header)
        if "5xfad" in name_l or "gse168137" in name_l:
            models["5xFAD"] = {"path": path, "kind": "de_table" if is_de_table else "counts"}
        elif "3xtg" in name_l or "gse161904" in name_l:
            models["3xTG_AD"] = {"path": path, "kind": "de_table" if is_de_table else "counts"}
        elif "ps3o1s" in name_l or "dea_ps" in name_l:
            models["PS3O1S"] = {"path": path, "kind": "de_table" if is_de_table else "counts"}
        elif re.search(r"(count|expression|matrix)", name_l, re.I) and "model_unknown" not in models:
            models["model_unknown"] = {"path": path, "kind": "de_table" if is_de_table else "counts"}
    return models


def _quoted_arg(value: str | Path) -> str:
    """Return one shell-safe argument token."""
    return shlex.quote(str(value))


def _build_compare_pathways_command(models: dict[str, dict[str, Any]], *, selected_dir: Path) -> str:
    """Build the helper-backed compare-pathways shell command.

    Args:
        models: Classified input files keyed by model label.
        selected_dir: Current run/output directory.

    Returns:
        One ``python3 compare_pathways.py ...`` shell command.
    """
    helper_path = COMPARE_PATHWAYS_HELPER_SCRIPT.resolve(strict=False)
    python_bin = preferred_helper_python_executable()
    project_root = helper_path.parents[2]
    output_dir = (selected_dir / "output").resolve(strict=False)
    output_csv = (selected_dir / "final" / "pathway_comparison.csv").resolve(strict=False)

    parts: list[str] = ["env", _quoted_arg(f"PYTHONPATH={project_root}"), _quoted_arg(python_bin), _quoted_arg(helper_path)]
    for label in ("PS3O1S", "3xTG_AD", "5xFAD", "model_unknown"):
        info = models.get(label)
        if not isinstance(info, dict):
            continue
        path = str(info.get("path", "") or "").strip()
        if not path:
            continue
        if str(info.get("kind", "")).strip().lower() == "de_table":
            parts.extend(["--precomputed-de-table", _quoted_arg(f"{label}={path}")])
        else:
            parts.extend(["--count-table", _quoted_arg(f"{label}={path}")])
    parts.extend(
        [
            "--output_dir",
            _quoted_arg(output_dir),
            "--output-csv",
            _quoted_arg(output_csv),
            "--run-differential-analysis",
        ]
    )
    return " ".join(parts)


def _compile_multi_model_dge_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile a deterministic helper-backed multi-model DGE plan.

    Args:
        plan: Original planner-produced plan.
        analysis_spec: Grounded analysis specification for the current run.
        selected_dir: Current run directory.
        data_root: Input-data root for the current run.

    Returns:
        A compiled helper-backed plan and metadata describing the rewrite.
    """
    del analysis_spec  # The compiler currently relies on discovered data files.

    models = _classify_data_files(data_root)
    if not models:
        return plan, {"changed": False, "why": "no_data_files"}

    output_csv = (selected_dir / "final" / "pathway_comparison.csv").resolve(strict=False)
    command = _build_compare_pathways_command(models, selected_dir=selected_dir)
    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "tool_name": "bash_run",
            "purpose": (
                "Run the repo-local compare_pathways.py helper to perform multi-model differential "
                "expression, KEGG pathway enrichment, and final pathway comparison export."
            ),
            "arguments": {"command": command},
        },
        {
            "step_id": 2,
            "tool_name": "artifact_schema_profile",
            "purpose": "Profile the final pathway comparison CSV schema for deterministic export checks.",
            "arguments": {"input_path": str(output_csv)},
        },
    ]

    thought = (
        "[multi_model_dge_template] "
        + ", ".join(f"{label}({info['kind']})" for label, info in models.items())
        + " -> compare_pathways.py helper -> artifact_schema_profile. "
        + str(plan.get("thought_process", ""))
    )

    compiled = {
        "thought_process": thought,
        "plan": steps,
        "_self_contained": True,
    }
    return _renumber_plan(compiled), {
        "changed": True,
        "why": "compiled_multi_model_dge_protocol",
        "models": {label: info["kind"] for label, info in models.items()},
        "helper_script": str(COMPARE_PATHWAYS_HELPER_SCRIPT.resolve(strict=False)),
        "output_csv": str(output_csv),
    }
