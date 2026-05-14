from __future__ import annotations

import argparse
import csv
import gzip
from dataclasses import dataclass
from pathlib import Path

IMPACT_RANK = {
    "HIGH": 3,
    "MODERATE": 2,
    "LOW": 1,
    "MODIFIER": 0,
}


@dataclass(frozen=True)
class VariantRow:
    chrom: str
    pos: str
    ref: str
    alt: str
    gene: str
    impact: str
    effect: str


def _open_text(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _parse_ann_first(info_field: str) -> tuple[str, str, str]:
    ann_value = ""
    for item in str(info_field or "").split(";"):
        if item.startswith("ANN="):
            ann_value = item[4:]
            break
    if not ann_value:
        return "", "", ""
    first = ann_value.split(",", 1)[0]
    fields = first.split("|")
    effect = fields[1].strip() if len(fields) > 1 else ""
    impact = fields[2].strip() if len(fields) > 2 else ""
    gene = fields[3].strip() if len(fields) > 3 else ""
    return gene, impact, effect


def _load_variants(path: Path, min_impact: str) -> tuple[list[tuple[str, str, str, str]], dict[tuple[str, str, str, str], VariantRow]]:
    order: list[tuple[str, str, str, str]] = []
    rows: dict[tuple[str, str, str, str], VariantRow] = {}
    threshold = IMPACT_RANK.get(str(min_impact).upper(), IMPACT_RANK["MODERATE"])
    with _open_text(path) as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            chrom, pos, _id, ref, alt, _qual, _flt, info = parts[:8]
            gene, impact, effect = _parse_ann_first(info)
            if IMPACT_RANK.get(impact.upper(), -1) < threshold:
                continue
            key = (chrom, pos, ref, alt)
            if key in rows:
                continue
            order.append(key)
            rows[key] = VariantRow(
                chrom=chrom,
                pos=pos,
                ref=ref,
                alt=alt,
                gene=gene,
                impact=impact,
                effect=effect,
            )
    return order, rows


def export_shared_variants_csv(
    *,
    input_vcf_a: Path,
    input_vcf_b: Path,
    output_csv: Path,
    min_impact: str = "MODERATE",
    status: str = "shared",
    dedupe_by_gene: bool = False,
    header_case: str = "lower",
) -> None:
    order, left = _load_variants(input_vcf_a, min_impact)
    _, right = _load_variants(input_vcf_b, min_impact)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    headers = ["chrom", "pos", "ref", "alt", "gene", "impact", "effect", "status"]
    if str(header_case).lower() == "upper":
        headers = [item.upper() for item in headers]
    seen_genes: set[str] = set()
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for key in order:
            if key not in right:
                continue
            row = left[key]
            if dedupe_by_gene and row.gene:
                if row.gene in seen_genes:
                    continue
                seen_genes.add(row.gene)
            writer.writerow([row.chrom, row.pos, row.ref, row.alt, row.gene, row.impact, row.effect, status])


def main() -> int:
    parser = argparse.ArgumentParser(description="Export shared annotated variants from two VCFs to CSV.")
    parser.add_argument("--input-vcf-a", required=True)
    parser.add_argument("--input-vcf-b", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--min-impact", default="MODERATE")
    parser.add_argument("--status", default="shared")
    parser.add_argument("--header-case", choices=("lower", "upper"), default="lower")
    parser.add_argument("--dedupe-by-gene", action="store_true")
    args = parser.parse_args()
    export_shared_variants_csv(
        input_vcf_a=Path(args.input_vcf_a),
        input_vcf_b=Path(args.input_vcf_b),
        output_csv=Path(args.output_csv),
        min_impact=args.min_impact,
        status=args.status,
        dedupe_by_gene=args.dedupe_by_gene,
        header_case=args.header_case,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
