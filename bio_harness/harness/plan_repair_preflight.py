"""Preflight execution issue helpers for plan repair."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from bio_harness.harness.contract_utils import (
    _collect_planned_output_paths,
    _extract_reference_paths_from_plan,
    _extract_sample_tags_from_plan,
)
from bio_harness.harness.path_utils import (
    _discover_fastq_files,
    _normalize_plan_path_text,
    _path_within_root,
)


def _preflight_execution_issues(
    plan: dict[str, Any],
    data_root: Path,
    contract: dict[str, Any] | None = None,
    selected_dir: Path | None = None,
    analysis_type: str = "",
    analysis_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    issues: dict[str, Any] = {
        "missing_data_root": False,
        "missing_fastq": False,
        "missing_groups": [],
        "missing_references": [],
        "fastq_count": 0,
    }
    if not data_root.exists():
        issues["missing_data_root"] = True

    plan_text = json.dumps(plan or {}, ensure_ascii=True).lower()
    requires_fastq_context = any(
        token in plan_text
        for token in (
            ".fastq",
            ".fq",
            "fastqc",
            "readfilesin",
            "rmats",
            "splicing",
            "select_sample_r1.sh",
            "fastq_manifest.sh",
        )
    )
    non_fastq_analysis_types = frozenset(
        {
            "phylogenetics",
            "comparative_genomics",
            "variant_annotation",
            "protein_analysis",
        }
    )
    spec = analysis_spec if isinstance(analysis_spec, Mapping) else {}
    execution_contract = (
        spec.get("execution_contract", {})
        if isinstance(spec.get("execution_contract", {}), Mapping)
        else {}
    )
    input_mode = str(
        execution_contract.get("input_mode", "") or spec.get("input_mode", "")
    ).strip().lower()
    has_bam_context = any(token in plan_text for token in (".bam", ".cram"))
    if analysis_type and analysis_type in non_fastq_analysis_types:
        requires_fastq_context = False
    if input_mode in {"aligned_bam", "bam", "input_bam"}:
        requires_fastq_context = False
    elif has_bam_context and not any(token in plan_text for token in (".fastq", ".fq", "readfilesin")):
        requires_fastq_context = False
    if requires_fastq_context:
        discovered = _discover_fastq_files(str(data_root), True, "", 5000)
        issues["fastq_count"] = len(discovered)
        if not discovered:
            issues["missing_fastq"] = True
        else:
            requested_caps = {
                str(x).strip().lower()
                for x in ((contract or {}).get("must_include_capabilities", []) if isinstance(contract, dict) else [])
                if str(x).strip()
            }
            requires_group_context = "group_comparison" in requested_caps
            if requires_group_context:
                control_tag, treatment_tag = _extract_sample_tags_from_plan(plan)
                lower_names = [Path(x).name.lower() for x in discovered]
                has_control = any(f"_{control_tag.lower()}_" in name and "_r1_001" in name for name in lower_names)
                has_treatment = any(f"_{treatment_tag.lower()}_" in name and "_r1_001" in name for name in lower_names)
                if not has_control:
                    issues["missing_groups"].append("control")
                if not has_treatment:
                    issues["missing_groups"].append("treatment")
    planned_outputs = _collect_planned_output_paths(plan, selected_dir or data_root)
    for reference_path in _extract_reference_paths_from_plan(plan):
        if Path(reference_path).expanduser().exists():
            continue
        normalized = _normalize_plan_path_text(reference_path, selected_dir or data_root)
        if normalized and normalized in planned_outputs:
            continue
        if selected_dir is not None and _path_within_root(reference_path, selected_dir):
            continue
        if not Path(reference_path).expanduser().exists():
            issues["missing_references"].append(reference_path)
    return issues
