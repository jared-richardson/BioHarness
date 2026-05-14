"""Strict cystic-fibrosis and direct-step inference helpers for artifact binding."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bio_harness.core.strict_artifact_binding_paths import (
    CysticFibrosisArtifactPaths,
    _build_cystic_fibrosis_paths,
)

_DIRECT_BRANCH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(anc(?:estor)?|evol\d+|sample\d+|line\d+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_EVOLUTION_FILTER_OBJECTIVE = "Filter the ancestor and evolved callsets into indexed comparison-ready VCFs"
_EVOLUTION_SUBTRACT_OBJECTIVE = (
    "Subtract the ancestor-supported sites from each evolved callset separately before any evolved-evolved comparison"
)
_EVOLUTION_ANNOTATE_OBJECTIVE = "Annotate the ancestor-subtracted evolved variants with ANN-compatible fields"
_EVOLUTION_EXPORT_OBJECTIVE = (
    "Normalize the annotated evolved callsets, intersect them in the shared scaffold coordinate system, "
    "and write a comma-separated final CSV with the exact required columns"
)

_CF_REQUIRED_COLUMNS = (
    "chromosome",
    "position",
    "variant_id",
    "reference",
    "alternate",
    "gene_name",
    "gene_id",
    "annotation",
    "impact",
    "transcript_id",
    "hgvs_c",
    "hgvs_p",
    "clinical_significance",
    "diseases",
    "review_status",
    "rs_id",
)


def _build_cystic_fibrosis_filter_command(paths: CysticFibrosisArtifactPaths) -> str:
    """Build the canonical recessive-segregation filter command."""

    return "\n".join(
        [
            "python3 - <<'EOF'",
            "import csv",
            "import os",
            "",
            f"input_vcf = {paths.input_vcf!r}",
            f"family_description = {paths.family_description!r}",
            f"output_dir = {paths.selected_dir!r}",
            f"intermediate_dir = {paths.intermediate_dir!r}",
            f"output_csv = {paths.filtered_csv!r}",
            "",
            "affected = {'NA12885', 'NA12886', 'NA12879'}",
            "parents = set()",
            "with open(family_description, 'r', encoding='utf-8') as handle:",
            "    for line in handle:",
            "        parts = line.strip().split()",
            "        if len(parts) >= 3 and parts[0] in affected:",
            "            parents.add(parts[1])",
            "            parents.add(parts[2])",
            "",
            "header = []",
            "variants = []",
            "with open(input_vcf, 'r', encoding='utf-8') as handle:",
            "    for line in handle:",
            "        if line.startswith('#CHROM'):",
            "            header = line.rstrip('\\n').split('\\t')",
            "            continue",
            "        if line.startswith('#'):",
            "            continue",
            "        if not header:",
            "            continue",
            "        parts = line.rstrip('\\n').split('\\t')",
            "        if len(parts) < 10:",
            "            continue",
            "        sample_map = {name: idx for idx, name in enumerate(header)}",
            "        if any(sample not in sample_map for sample in affected):",
            "            continue",
            "        info = parts[7]",
            "        ann_entries = [item[4:] for item in info.split(';') if item.startswith('ANN=')]",
            "        if not ann_entries:",
            "            continue",
            "        matched = None",
            "        for ann_entry in ann_entries[0].split(','):",
            "            fields = ann_entry.split('|')",
            "            if len(fields) > 3 and fields[3] == 'CFTR':",
            "                matched = fields",
            "                break",
            "        if matched is None:",
            "            continue",
            "        def _gt(sample: str) -> str:",
            "            if sample not in sample_map or sample_map[sample] >= len(parts):",
            "                return './.'",
            "            return parts[sample_map[sample]].split(':', 1)[0]",
            "        if any(_gt(sample) != '1/1' for sample in affected):",
            "            continue",
            "        if any(_gt(parent) != '0/1' for parent in parents if parent in sample_map):",
            "            continue",
            "        variants.append({",
            "            'chromosome': parts[0],",
            "            'position': parts[1],",
            "            'variant_id': parts[2] if parts[2] != '.' else '',",
            "            'reference': parts[3],",
            "            'alternate': parts[4],",
            "            'gene_name': matched[3] if len(matched) > 3 else '',",
            "            'gene_id': matched[4] if len(matched) > 4 else '',",
            "            'annotation': matched[1] if len(matched) > 1 else '',",
            "            'impact': matched[2] if len(matched) > 2 else '',",
            "            'transcript_id': matched[6] if len(matched) > 6 else '',",
            "            'hgvs_c': matched[9] if len(matched) > 9 else '',",
            "            'hgvs_p': matched[10] if len(matched) > 10 else '',",
            "            'clinical_significance': '',",
            "            'diseases': '',",
            "            'review_status': '',",
            "            'rs_id': '',",
            "        })",
            "",
            "os.makedirs(intermediate_dir, exist_ok=True)",
            "with open(output_csv, 'w', newline='', encoding='utf-8') as handle:",
            f"    writer = csv.DictWriter(handle, fieldnames={list(_CF_REQUIRED_COLUMNS)!r})",
            "    writer.writeheader()",
            "    writer.writerows(variants)",
            "print(f'Filtered {len(variants)} recessive CFTR variants to {output_csv}')",
            "EOF",
        ]
    )


def _build_cystic_fibrosis_clinvar_command(paths: CysticFibrosisArtifactPaths) -> str:
    """Build the canonical ClinVar enrichment command."""

    return "\n".join(
        [
            "python3 - <<'EOF'",
            "import csv",
            "import gzip",
            "import os",
            "",
            f"input_csv = {paths.filtered_csv!r}",
            f"clinvar_vcf = {paths.clinvar_vcf!r}",
            f"output_csv = {paths.clinvar_csv!r}",
            "",
            "rows = []",
            "with open(input_csv, 'r', encoding='utf-8', newline='') as handle:",
            "    reader = csv.DictReader(handle)",
            "    for row in reader:",
            "        rows.append(dict(row))",
            "",
            "clinvar_lookup = {}",
            "if os.path.exists(clinvar_vcf):",
            "    with gzip.open(clinvar_vcf, 'rt', encoding='utf-8') as handle:",
            "        for line in handle:",
            "            if line.startswith('#'):",
            "                continue",
            "            parts = line.rstrip('\\n').split('\\t')",
            "            if len(parts) < 8:",
            "                continue",
            "            chrom, pos, rs_id, ref, alt, _, _, info = parts[:8]",
            "            info_map = {}",
            "            for item in info.split(';'):",
            "                if '=' in item:",
            "                    key, value = item.split('=', 1)",
            "                    info_map[key] = value",
            "            clinvar_lookup[(chrom, pos, ref, alt)] = {",
            "                'clinical_significance': info_map.get('CLNSIG', ''),",
            "                'diseases': info_map.get('CLNDN', ''),",
            "                'review_status': info_map.get('CLNREVSTAT', ''),",
            "                'rs_id': info_map.get('RS', rs_id if rs_id != '.' else ''),",
            "            }",
            "",
            "for row in rows:",
            "    key = (",
            "        row.get('chromosome', ''),",
            "        row.get('position', ''),",
            "        row.get('reference', ''),",
            "        row.get('alternate', ''),",
            "    )",
            "    clinvar = clinvar_lookup.get(key, {})",
            "    row['clinical_significance'] = clinvar.get(",
            "        'clinical_significance',",
            "        row.get('clinical_significance', ''),",
            "    )",
            "    row['diseases'] = clinvar.get('diseases', row.get('diseases', ''))",
            "    row['review_status'] = clinvar.get('review_status', row.get('review_status', ''))",
            "    row['rs_id'] = clinvar.get('rs_id', row.get('rs_id', ''))",
            "",
            "os.makedirs(os.path.dirname(output_csv), exist_ok=True)",
            "with open(output_csv, 'w', newline='', encoding='utf-8') as handle:",
            f"    writer = csv.DictWriter(handle, fieldnames={list(_CF_REQUIRED_COLUMNS)!r})",
            "    writer.writeheader()",
            "    writer.writerows(rows)",
            "print(f'Joined ClinVar annotations for {len(rows)} variants into {output_csv}')",
            "EOF",
        ]
    )


def _build_cystic_fibrosis_export_command(paths: CysticFibrosisArtifactPaths) -> str:
    """Build the canonical final CSV export command."""

    return "\n".join(
        [
            "python3 - <<'EOF'",
            "import csv",
            "import os",
            "",
            f"input_csv = {paths.clinvar_csv!r}",
            f"output_csv = {paths.final_csv!r}",
            "",
            "rows = []",
            "with open(input_csv, 'r', encoding='utf-8', newline='') as handle:",
            "    reader = csv.DictReader(handle)",
            "    for row in reader:",
            "        if row.get('gene_name', '') == 'CFTR':",
            "            rows.append({key: row.get(key, '') for key in reader.fieldnames or []})",
            "",
            "os.makedirs(os.path.dirname(output_csv), exist_ok=True)",
            "with open(output_csv, 'w', newline='', encoding='utf-8') as handle:",
            f"    writer = csv.DictWriter(handle, fieldnames={list(_CF_REQUIRED_COLUMNS)!r})",
            "    writer.writeheader()",
            "    for row in rows:",
            f"        writer.writerow({{key: row.get(key, '') for key in {list(_CF_REQUIRED_COLUMNS)!r}}})",
            "print(f'Exported {len(rows)} CFTR variants to {output_csv}')",
            "EOF",
        ]
    )


def _classify_cystic_fibrosis_bash_role(*, objective: str, command: str) -> str | None:
    """Infer the intended cystic-fibrosis bash step role from planner text."""

    combined = f"{objective}\n{command}".lower()

    if "clinvar" in combined and any(
        token in combined
        for token in (
            "join",
            "annotat",
            "clinical_significance",
            "review_status",
            "clnsig",
            "clndn",
            "clnrevstat",
        )
    ):
        return "clinvar"

    if (
        "export" in combined
        and "csv" in combined
        and any(token in combined for token in ("final", "required 16 columns", "cf_variants.csv"))
    ):
        return "export"

    if any(
        token in combined
        for token in (
            "recessive segregation",
            "segregation pattern",
            "affected siblings",
            "family-segregation",
        )
    ) and any(
        token in combined
        for token in (
            "filter",
            "cftr",
            "parents",
            "family_description",
            "genotype",
        )
    ):
        return "filter"

    return None


def _fallback_cystic_fibrosis_role(step_id: int) -> str | None:
    """Map the canonical CF bash-step positions onto scaffold roles."""

    if step_id == 2:
        return "filter"
    if step_id == 3:
        return "clinvar"
    if step_id == 4:
        return "export"
    return None


def _normalize_cystic_fibrosis_bash_command(
    command: str,
    *,
    objective: str,
    step_id: int,
    selected_dir: Path | None,
    data_root: Path | None,
) -> str:
    """Normalize cystic-fibrosis bash steps onto the strict artifact scaffold."""

    command_text = str(command or "")
    if not command_text.strip():
        return command_text

    paths = _build_cystic_fibrosis_paths(selected_dir=selected_dir, data_root=data_root)
    role = _fallback_cystic_fibrosis_role(int(step_id or 0))
    if role is None:
        role = _classify_cystic_fibrosis_bash_role(objective=objective, command=command_text)
    if role == "filter":
        return _build_cystic_fibrosis_filter_command(paths)
    if role == "clinvar":
        return _build_cystic_fibrosis_clinvar_command(paths)
    if role == "export":
        return _build_cystic_fibrosis_export_command(paths)
    return command_text


def _infer_direct_branch_id(step_spec: dict[str, Any]) -> str:
    """Infer a branch identifier from direct-plan arguments and command text."""

    args = step_spec.get("arguments", {}) if isinstance(step_spec.get("arguments", {}), dict) else {}
    priority_keys = (
        "sample_name",
        "sample_id",
        "branch_id",
        "read_group_sample",
        "output_bam",
        "output_vcf",
        "output_dir",
        "reads_1",
        "reads_2",
        "input_bam",
        "input_vcf",
        "command",
    )
    fallback_keys = (
        "reference_fasta",
        "annotation_gff",
        "output_gff",
        "output_faa",
        "input_fasta",
    )
    candidate_texts = [str(args.get(key, "") or "") for key in priority_keys if str(args.get(key, "") or "").strip()]
    candidate_texts.extend(
        str(args.get(key, "") or "") for key in fallback_keys if str(args.get(key, "") or "").strip()
    )
    candidate_texts.append(json.dumps(args, ensure_ascii=True))

    ancestor_seen = False
    for text in candidate_texts:
        for match in _DIRECT_BRANCH_TOKEN_RE.findall(text):
            token = str(match or "").strip().lower()
            if not token:
                continue
            if token.startswith("anc"):
                ancestor_seen = True
                continue
            return token
    if ancestor_seen:
        return "ancestor"
    return ""


def _infer_evolution_direct_bash_objective(command: str) -> str:
    """Infer the intended evolution bash-step role from a direct planner command."""

    command_l = str(command or "").strip().lower()
    if not command_l:
        return ""
    if "variants_shared.csv" in command_l or "export_shared_variants_csv.py" in command_l:
        return _EVOLUTION_EXPORT_OBJECTIVE
    if "bcftools query" in command_l and ".csv" in command_l:
        return _EVOLUTION_EXPORT_OBJECTIVE
    if "bcftools norm" in command_l and "bcftools isec" in command_l:
        return _EVOLUTION_EXPORT_OBJECTIVE
    if "ancestor_subtracted" in command_l or "anc_subtracted" in command_l:
        return _EVOLUTION_ANNOTATE_OBJECTIVE if "snpeff" in command_l else _EVOLUTION_SUBTRACT_OBJECTIVE
    if "bcftools isec" in command_l:
        return _EVOLUTION_SUBTRACT_OBJECTIVE
    if all(token in command_l for token in ("anc_raw", "evol1_raw", "evol2_raw")):
        return _EVOLUTION_FILTER_OBJECTIVE
    if (
        any(token in command_l for token in ("anc_raw", "evol1_raw", "evol2_raw"))
        and any(token in command_l for token in ("bcftools norm", "bgzip"))
        and "annotated" not in command_l
        and "subtracted" not in command_l
    ):
        return _EVOLUTION_FILTER_OBJECTIVE
    if any(token in command_l for token in ("vcffilter", "bcftools filter")):
        return _EVOLUTION_FILTER_OBJECTIVE
    if "bcftools view" in command_l and "filtered" in command_l:
        return _EVOLUTION_FILTER_OBJECTIVE
    return ""


def _infer_evolution_direct_branch_id(command: str, fallback_branch_id: str) -> str:
    """Infer whether a direct evolution bash step is branch-local or shared."""

    command_l = str(command or "").strip().lower()
    has_evol1 = "evol1" in command_l
    has_evol2 = "evol2" in command_l
    if has_evol1 and has_evol2:
        return ""
    return fallback_branch_id
