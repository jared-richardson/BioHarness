"""Shared-variant export repair helpers for plan repair."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.shell_parse import split_shell_chain_segments
from bio_harness.harness.config import SHARED_VARIANT_EXPORTER
from bio_harness.harness.plan_helpers import (
    _extract_csv_output_from_command,
    _normalize_steps,
    _renumber_plan_steps,
)


def _shared_variant_export_settings_from_analysis_spec(
    analysis_spec: dict[str, Any] | None,
) -> dict[str, Any]:
    grounding = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("protocol_grounding", {}), dict)
        else {}
    )
    benchmark_profile = (
        grounding.get("benchmark_profile", {})
        if isinstance(grounding.get("benchmark_profile", {}), dict)
        else {}
    )
    export_profile = (
        benchmark_profile.get("export_profile", {})
        if isinstance(benchmark_profile.get("export_profile", {}), dict)
        else {}
    )
    shared_policy = (
        benchmark_profile.get("shared_variant_policy", {})
        if isinstance(benchmark_profile.get("shared_variant_policy", {}), dict)
        else {}
    )
    return {
        "min_impact": str(export_profile.get("min_impact", shared_policy.get("min_impact", "MODERATE"))).strip()
        or "MODERATE",
        "status": str(export_profile.get("status", "")).strip() or "shared",
        "header_case": str(export_profile.get("header_case", "")).strip().lower() or "lower",
        "dedupe_by_gene": bool(export_profile.get("dedupe_by_gene", shared_policy.get("dedupe_by_gene", True))),
        "normalize_before_compare": bool(shared_policy.get("normalize_before_compare", True)),
    }


def _evolution_variant_repair_settings(
    analysis_spec: dict[str, Any] | None,
) -> dict[str, Any]:
    grounding = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("protocol_grounding", {}), dict)
        else {}
    )
    benchmark_profile = (
        grounding.get("benchmark_profile", {})
        if isinstance(grounding.get("benchmark_profile", {}), dict)
        else {}
    )
    variant_filter = (
        benchmark_profile.get("variant_filter", {})
        if isinstance(benchmark_profile.get("variant_filter", {}), dict)
        else {}
    )
    post_alignment_policy = (
        benchmark_profile.get("post_alignment_policy", {})
        if isinstance(benchmark_profile.get("post_alignment_policy", {}), dict)
        else {}
    )
    return {
        # Fix #18: INFO/-qualify AO (and related fields) so bcftools does not
        # error on "ambiguous filtering expression" against FreeBayes VCFs.
        "filter_expression": str(variant_filter.get("expression", "")).strip()
        or "QUAL > 1 & QUAL / INFO/AO > 10 & INFO/SAF > 0 & INFO/SAR > 0 & INFO/RPR > 1 & INFO/RPL > 1",
        "postprocess_mode": str(post_alignment_policy.get("mode", "")).strip() or "fixmate_markdup_q20",
    }


def _extract_snpeff_bash_output_paths(command: str) -> list[str]:
    """Return annotated VCF outputs emitted by inline ``snpEff annotate`` shell segments."""

    outputs: list[str] = []
    seen: set[str] = set()
    for segment in split_shell_chain_segments(str(command or "")):
        segment_text = str(segment or "").strip()
        segment_l = segment_text.lower()
        if "snpeff annotate" not in segment_l:
            continue
        match = re.search(r">\s*(\S+)\s*$", segment_text)
        if not match:
            match = re.search(r"(?:^|\s)-o\s+(\S+)", segment_text)
        if not match:
            continue
        output_path = str(match.group(1) or "").strip().strip("'\"")
        if not output_path or output_path in seen:
            continue
        seen.add(output_path)
        outputs.append(output_path)
    return outputs


def _repair_shared_variant_csv_exports_with_analysis_spec(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}
    export_settings = _shared_variant_export_settings_from_analysis_spec(analysis_spec)

    snpeff_outputs: list[str] = []
    normalized_outputs: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if tool == "snpeff_annotate":
            output_vcf = str(args.get("output_vcf", "")).strip()
            if output_vcf:
                snpeff_outputs.append(output_vcf)
        elif tool == "bash_run":
            cmd = str(args.get("command", "")).strip()
            snpeff_outputs.extend(
                output_path
                for output_path in _extract_snpeff_bash_output_paths(cmd)
                if output_path not in snpeff_outputs
            )
            if "bcftools norm" in cmd:
                match = re.search(r"-(?:o|O\s+\w\s+-o)\s+(\S+)", cmd)
                if not match:
                    match = re.search(r"-Oz\s+-o\s+(\S+)", cmd)
                if match:
                    norm_path = match.group(1).strip("'\"").rstrip(";")
                    if norm_path:
                        normalized_outputs.append(norm_path)
    if not snpeff_outputs:
        return plan, {"changed": False, "why": "insufficient_snpeff_outputs"}
    comparison_vcfs = normalized_outputs if len(normalized_outputs) >= len(snpeff_outputs) else snpeff_outputs

    replacements: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        command_l = command.lower()
        if ".csv" not in command_l:
            continue
        if (
            "awk" not in command_l
            and "export_shared_variants_csv.py" not in command_l
            and "bcftools query" not in command_l
            and "vcf2csv" not in command_l
        ):
            continue
        output_csv = _extract_csv_output_from_command(command)
        if not output_csv:
            match = re.search(r"([A-Za-z0-9_./-]*variants[^\s'\"]*\.csv)\b", command, flags=re.IGNORECASE)
            if match:
                output_csv = str(match.group(1) or "").strip()
        if not output_csv:
            continue
        output_path = Path(output_csv)
        if "variants_shared" in output_path.stem.lower() and output_path.name != "variants_shared.csv":
            output_csv = str(output_path.with_name("variants_shared.csv"))
        elif "shared" in output_path.stem.lower() and output_path.name != "variants_shared.csv":
            output_csv = str(output_path.with_name("variants_shared.csv"))
        left_vcf = comparison_vcfs[0]
        right_vcf = comparison_vcfs[1] if len(comparison_vcfs) > 1 else comparison_vcfs[0]
        repaired_command = (
            f"python {shlex.quote(str(SHARED_VARIANT_EXPORTER))} "
            f"--input-vcf-a {shlex.quote(left_vcf)} "
            f"--input-vcf-b {shlex.quote(right_vcf)} "
            f"--output-csv {shlex.quote(output_csv)} "
            f"--min-impact {shlex.quote(export_settings['min_impact'])} "
            f"--status {shlex.quote(export_settings['status'])} "
            f"--header-case {shlex.quote(export_settings['header_case'])}"
        )
        if export_settings["dedupe_by_gene"]:
            repaired_command += " --dedupe-by-gene"
        step["arguments"] = {**args, "command": repaired_command}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bash_run",
                "output_csv": output_csv,
                "input_vcfs": [left_vcf, right_vcf],
            }
        )

    if not replacements:
        return plan, {"changed": False, "why": "no_shared_variant_export_repairs"}

    filtered_steps: list[dict[str, Any]] = []
    removed_steps: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            filtered_steps.append(step)
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        command_l = command.lower()
        is_redundant_filter = (
            tool_name == "bash_run"
            and "bcftools filter" in command_l
            and "impact=" in command_l
            and "shared_annotated.vcf" in command_l
        )
        if is_redundant_filter:
            removed_steps.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "reason": "shared_variant_exporter_handles_impact_filtering",
                }
            )
            continue
        filtered_steps.append(step)

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = filtered_steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "removed_steps": removed_steps,
        "diff_summary": {
            "replacement_count": len(replacements),
            "removed_step_count": len(removed_steps),
        },
    }
