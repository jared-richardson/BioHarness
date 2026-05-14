"""Shared strict-scaffold helpers for the cystic-fibrosis benchmark."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def is_cystic_fibrosis_variant_analysis(
    *,
    analysis_spec: dict[str, Any] | None = None,
    selected_dir: Path | None = None,
    objective: str = "",
) -> bool:
    """Return True when the current strict variant task is cystic-fibrosis."""

    spec = analysis_spec if isinstance(analysis_spec, dict) else {}
    if selected_dir is None:
        selected_value = str(spec.get("selected_dir", "") or "").strip()
        selected_dir = Path(selected_value) if selected_value else None

    if selected_dir is not None:
        selected_l = str(selected_dir.resolve(strict=False)).lower()
        if "official_runs/cystic-fibrosis" in selected_l or "/tasks/cystic-fibrosis/" in selected_l:
            return True

    text_parts = [
        objective,
        str(spec.get("biological_objective", "") or ""),
        " ".join(str(item) for item in spec.get("context_facts", []) if str(item).strip()),
    ]
    combined = " ".join(text_parts).lower()
    return any(
        token in combined
        for token in (
            "cystic fibrosis",
            "cftr",
            "affected siblings",
            "recessive family-segregation",
        )
    )


def is_cystic_fibrosis_scaffold_command(
    command: str,
    *,
    analysis_spec: dict[str, Any] | None = None,
    selected_dir: Path | None = None,
) -> bool:
    """Detect canonical harness-owned cystic-fibrosis scaffold commands."""

    if not is_cystic_fibrosis_variant_analysis(
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
    ):
        return False

    command_l = str(command or "").lower()
    if not command_l:
        return False

    filter_tokens = (
        "family_description.txt",
        "filtered_variants.csv",
        "affected = {'na12885', 'na12886', 'na12879'}",
        "if any(_gt(sample) != '1/1' for sample in affected)",
    )
    if all(token in command_l for token in filter_tokens):
        return True

    clinvar_tokens = (
        "clinvar_20250521.vcf.gz",
        "clinvar_annotated_variants.csv",
        "clinical_significance",
        "review_status",
        "joined clinvar annotations",
    )
    if all(token in command_l for token in clinvar_tokens):
        return True

    export_tokens = (
        "clinvar_annotated_variants.csv",
        "cf_variants.csv",
        "row.get('gene_name', '') == 'cftr'",
        "exported {len(rows)} cftr variants",
    )
    if all(token in command_l for token in export_tokens):
        return True

    return False
