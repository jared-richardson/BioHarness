from __future__ import annotations

import argparse
import csv
import gzip
from pathlib import Path


OUTPUT_COLUMNS = [
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
]


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _parse_family_description(path: Path) -> dict[str, list[str] | str]:
    affected: list[str] = []
    parents: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("- Father:"):
                parents.append(line.split(":", 1)[1].split("(", 1)[0].strip())
            elif line.startswith("- Mother:"):
                parents.append(line.split(":", 1)[1].split("(", 1)[0].strip())
            elif "(affected" in line and ")" in line:
                parts = line.split()
                for token in parts:
                    if token.startswith("NA"):
                        affected.append(token.rstrip(":"))
                        break
    return {"affected": affected, "parents": parents[:2]}


def _gt_category(sample_field: str) -> str:
    gt = str(sample_field or "").split(":", 1)[0].replace("|", "/")
    if not gt or gt in {".", "./.", ".|."}:
        return "missing"
    alleles = [token for token in gt.split("/") if token]
    if not alleles:
        return "missing"
    if all(token == "1" for token in alleles):
        return "hom_alt"
    if "1" in alleles:
        return "het"
    if all(token == "0" for token in alleles):
        return "hom_ref"
    return "other"


def _severity_rank(impact: str) -> int:
    order = {"HIGH": 3, "MODERATE": 2, "LOW": 1, "MODIFIER": 0}
    return order.get(str(impact or "").strip().upper(), -1)


def _parse_info(info_field: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for token in str(info_field or "").split(";"):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        parsed[key] = value
    return parsed


def _first_preferred_ann(ann_field: str, *, gene_hint: str) -> dict[str, str]:
    preferred: dict[str, str] | None = None
    fallback: dict[str, str] | None = None
    gene_hint_l = str(gene_hint or "").strip().lower()
    for ann in str(ann_field or "").split(","):
        parts = ann.split("|")
        row = {
            "annotation": parts[1] if len(parts) > 1 else "",
            "impact": parts[2] if len(parts) > 2 else "",
            "gene_name": parts[3] if len(parts) > 3 else "",
            "gene_id": parts[4] if len(parts) > 4 else "",
            "transcript_id": parts[6] if len(parts) > 6 else "",
            "hgvs_c": parts[9] if len(parts) > 9 else "",
            "hgvs_p": parts[10] if len(parts) > 10 else "",
        }
        if fallback is None or _severity_rank(row["impact"]) > _severity_rank(fallback["impact"]):
            fallback = row
        if gene_hint_l and row["gene_name"].strip().lower() != gene_hint_l:
            continue
        if preferred is None or _severity_rank(row["impact"]) > _severity_rank(preferred["impact"]):
            preferred = row
    return preferred or fallback or {
        "annotation": "",
        "impact": "",
        "gene_name": "",
        "gene_id": "",
        "transcript_id": "",
        "hgvs_c": "",
        "hgvs_p": "",
    }


def _lookup_clinvar_annotation(clinvar_vcf: Path, *, chrom: str, pos: str, ref: str, alt: str) -> dict[str, str]:
    exact_match: dict[str, str] | None = None
    same_site: dict[str, str] | None = None
    with _open_text(clinvar_vcf) as handle:
        for raw_line in handle:
            if raw_line.startswith("#"):
                continue
            fields = raw_line.rstrip("\n").split("\t")
            if len(fields) < 8:
                continue
            row_chrom, row_pos, row_id, row_ref, row_alt, _qual, _flt, row_info = fields[:8]
            if row_chrom != chrom or row_pos != pos:
                continue
            info = _parse_info(row_info)
            current = {
                "variant_id": row_id if row_id != "." else "",
                "clinical_significance": info.get("CLNSIG", "").replace("|", ";"),
                "diseases": info.get("CLNDN", "").replace("|", ";"),
                "review_status": info.get("CLNREVSTAT", ""),
                "rs_id": info.get("RS", ""),
            }
            if row_ref == ref and row_alt == alt:
                exact_match = current
                break
            if same_site is None:
                same_site = {"variant_id": "", "clinical_significance": "", "diseases": "", "review_status": "", "rs_id": current["rs_id"]}
    return exact_match or same_site or {
        "variant_id": "",
        "clinical_significance": "",
        "diseases": "",
        "review_status": "",
        "rs_id": "",
    }


def export_cystic_fibrosis_csv(
    *,
    input_vcf: Path,
    family_description: Path,
    output_csv: Path,
    gene_hint: str = "CFTR",
    clinvar_vcf: Path | None = None,
) -> dict[str, str | int]:
    family = _parse_family_description(family_description)
    affected = [str(item).strip() for item in family.get("affected", []) if str(item).strip()]
    parents = [str(item).strip() for item in family.get("parents", []) if str(item).strip()]
    if not affected:
        raise ValueError("Could not infer affected samples from family description.")

    samples: list[str] = []
    winner: dict[str, str] | None = None
    with _open_text(input_vcf) as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if line.startswith("#CHROM"):
                samples = line.split("\t")[9:]
                continue
            if line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) < 10 or not samples:
                continue
            chrom, pos, _vid, ref, alt, _qual, _flt, info_field = fields[:8]
            genotype_fields = fields[9:]
            sample_index = {sample: idx for idx, sample in enumerate(samples)}
            if any(sample not in sample_index for sample in affected):
                continue
            affected_states = [_gt_category(genotype_fields[sample_index[sample]]) for sample in affected]
            if not all(state == "hom_alt" for state in affected_states):
                continue
            if parents and not all(sample in sample_index for sample in parents):
                continue
            parent_states = [_gt_category(genotype_fields[sample_index[sample]]) for sample in parents] if parents else []
            if parent_states and not all(state == "het" for state in parent_states):
                continue
            unaffected_samples = [sample for sample in samples if sample not in set(affected)]
            unaffected_states = [_gt_category(genotype_fields[sample_index[sample]]) for sample in unaffected_samples]
            if any(state == "hom_alt" for state in unaffected_states):
                continue

            info = _parse_info(info_field)
            ann = _first_preferred_ann(info.get("ANN", ""), gene_hint=gene_hint)
            if ann["impact"] not in {"HIGH", "MODERATE"}:
                continue
            if gene_hint and ann["gene_name"].strip().lower() != gene_hint.strip().lower():
                continue

            row = {
                "chromosome": chrom,
                "position": pos,
                "reference": ref,
                "alternate": alt,
                **ann,
                "variant_id": "",
                "clinical_significance": "",
                "diseases": "",
                "review_status": "",
                "rs_id": "",
            }
            if clinvar_vcf is not None and clinvar_vcf.exists():
                row.update(_lookup_clinvar_annotation(clinvar_vcf, chrom=chrom, pos=pos, ref=ref, alt=alt))

            if winner is None or _severity_rank(row["impact"]) > _severity_rank(winner["impact"]):
                winner = row

    if winner is None:
        raise ValueError("No causal variant candidate matched the recessive CFTR filter.")

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerow({column: winner.get(column, "") for column in OUTPUT_COLUMNS})

    return {
        "output_csv": str(output_csv),
        "affected_count": len(affected),
        "parent_count": len(parents),
        "gene_name": winner["gene_name"],
        "impact": winner["impact"],
        "position": winner["position"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the causal cystic-fibrosis candidate variant to CSV.")
    parser.add_argument("--input-vcf", required=True)
    parser.add_argument("--family-description", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--gene-hint", default="CFTR")
    parser.add_argument("--clinvar-vcf", default="")
    args = parser.parse_args()

    export_cystic_fibrosis_csv(
        input_vcf=Path(args.input_vcf),
        family_description=Path(args.family_description),
        output_csv=Path(args.output_csv),
        gene_hint=args.gene_hint,
        clinvar_vcf=Path(args.clinvar_vcf) if str(args.clinvar_vcf).strip() else None,
    )


if __name__ == "__main__":
    main()
