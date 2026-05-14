"""Cystic-fibrosis-specific repair helpers extracted from plan_repair."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bio_harness.harness.config import CF_CAUSAL_VARIANT_EXPORTER
from bio_harness.harness.plan_helpers import _normalize_steps, _renumber_plan_steps


def _is_cystic_fibrosis_task(analysis_spec: dict[str, Any], request_text: str) -> bool:
    analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip().lower()
    request_l = str(request_text or "").strip().lower()
    if analysis_type != "variant_annotation":
        return False
    if any(token in request_l for token in ("cystic fibrosis", "cftr", "recessive variant")):
        return True
    context = [str(item).strip().lower() for item in (analysis_spec.get("context_facts", []) or []) if str(item).strip()]
    return any(token in " ".join(context) for token in ("cystic", "cftr"))


def _discover_cystic_fibrosis_inputs(
    *,
    plan: dict[str, Any],
    selected_dir: Path,
    data_root: Path,
) -> dict[str, str]:
    input_vcf = ""
    family_description = ""
    clinvar_vcf = ""

    for candidate in sorted(data_root.rglob("*")):
        if not candidate.is_file():
            continue
        name_l = candidate.name.lower()
        if not input_vcf and name_l.endswith((".eff.vcf", ".eff.vcf.gz")):
            input_vcf = str(candidate.resolve())
        elif not family_description and "family" in name_l and candidate.suffix.lower() in {".txt", ".tsv", ".csv"}:
            family_description = str(candidate.resolve())
    if not input_vcf:
        for step in (plan.get("plan", []) if isinstance(plan, dict) else []):
            if not isinstance(step, dict):
                continue
            if str(step.get("tool_name", "")).strip().lower() != "snpeff_annotate":
                continue
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            output_vcf = str(args.get("output_vcf", "") or "").strip()
            if output_vcf:
                input_vcf = output_vcf
                break
    if not input_vcf:
        for candidate in sorted(data_root.rglob("*")):
            if candidate.is_file() and candidate.name.lower().endswith((".vcf", ".vcf.gz")):
                input_vcf = str(candidate.resolve())
                break

    search_roots = [
        data_root.parent / "references",
        data_root.parent,
        selected_dir,
    ]
    for root in search_roots:
        root = root.resolve(strict=False)
        if not root.exists() or not root.is_dir():
            continue
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            name_l = candidate.name.lower()
            if not clinvar_vcf and "clinvar" in name_l and name_l.endswith((".vcf", ".vcf.gz")):
                clinvar_vcf = str(candidate.resolve())
                break
        if clinvar_vcf:
            break

    return {
        "input_vcf": input_vcf,
        "family_description": family_description,
        "clinvar_vcf": clinvar_vcf,
    }


def _repair_cystic_fibrosis_csv_exports_with_analysis_spec(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
    request_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(analysis_spec, dict) or not _is_cystic_fibrosis_task(analysis_spec, request_text):
        return plan, {"changed": False, "why": "not_cystic_fibrosis_task"}
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    discovered = _discover_cystic_fibrosis_inputs(plan=plan, selected_dir=selected_dir, data_root=data_root)
    input_vcf = str(discovered.get("input_vcf", "") or "").strip()
    family_description = str(discovered.get("family_description", "") or "").strip()
    clinvar_vcf = str(discovered.get("clinvar_vcf", "") or "").strip()
    if not input_vcf or not family_description:
        return plan, {
            "changed": False,
            "why": "missing_cystic_fibrosis_inputs",
            "input_vcf": input_vcf,
            "family_description": family_description,
            "clinvar_vcf": clinvar_vcf,
        }

    output_csv = str((selected_dir / "final" / "cf_variants.csv").resolve(strict=False))
    repaired_command = (
        f"python {shlex.quote(str(CF_CAUSAL_VARIANT_EXPORTER))} "
        f"--input-vcf {shlex.quote(input_vcf)} "
        f"--family-description {shlex.quote(family_description)} "
        f"--output-csv {shlex.quote(output_csv)} "
        f"--gene-hint CFTR"
    )
    if clinvar_vcf:
        repaired_command += f" --clinvar-vcf {shlex.quote(clinvar_vcf)}"

    replacements: list[dict[str, Any]] = []
    appended = False
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "").strip()
        command_l = command.lower()
        if ".csv" not in command_l:
            continue
        if not any(token in command_l for token in ("cftr", "cystic", "clinvar", "casecontrol", "cf_variants", "output_cf_variant")):
            continue
        step["arguments"] = {**args, "command": repaired_command}
        replacements.append({"step_id": int(step.get("step_id", idx)), "mode": "replace"})

    if not replacements:
        steps.append(
            {
                "step_id": len(steps) + 1,
                "tool_name": "bash_run",
                "purpose": "Export the causal cystic-fibrosis candidate variant as CSV",
                "arguments": {"command": repaired_command},
            }
        )
        appended = True

    patched = dict(plan)
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "repaired_cystic_fibrosis_csv_export",
        "input_vcf": input_vcf,
        "family_description": family_description,
        "clinvar_vcf": clinvar_vcf,
        "output_csv": output_csv,
        "replacements": replacements,
        "appended": appended,
    }
