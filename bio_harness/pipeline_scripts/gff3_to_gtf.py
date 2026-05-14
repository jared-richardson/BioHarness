#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _parse_attrs(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for token in str(text or "").strip().split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, value = token.split("=", 1)
        attrs[key.strip()] = value.strip()
    return attrs


def convert(in_path: Path, out_path: Path) -> int:
    gene_ids: dict[str, str] = {}
    transcript_to_gene: dict[str, str] = {}
    rows: list[str] = []

    with in_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 9:
                continue
            chrom, source, feature, start, end, score, strand, frame, attrs_text = fields
            attrs = _parse_attrs(attrs_text)
            feature_l = feature.lower()
            feature_id = attrs.get("ID", "").split(",")[0].strip()
            parent_id = attrs.get("Parent", "").split(",")[0].strip()
            gene_name = attrs.get("Name", "").split(",")[0].strip()

            if feature_l in {"gene", "pseudogene"} and feature_id:
                gene_ids[feature_id] = feature_id
                transcript_id = feature_id
                gtf_attrs = [f'gene_id "{feature_id}"', f'transcript_id "{transcript_id}"']
                if gene_name:
                    gtf_attrs.append(f'gene_name "{gene_name}"')
                rows.append(
                    "\t".join(
                        [chrom, source, "gene", start, end, score, strand, frame, "; ".join(gtf_attrs) + ";"]
                    )
                )
                continue

            if feature_l in {"mrna", "transcript"} and feature_id:
                gene_id = gene_ids.get(parent_id, parent_id or feature_id)
                transcript_to_gene[feature_id] = gene_id
                transcript_id = feature_id
                gtf_attrs = [f'gene_id "{gene_id}"', f'transcript_id "{transcript_id}"']
                if gene_name:
                    gtf_attrs.append(f'gene_name "{gene_name}"')
                rows.append(
                    "\t".join(
                        [chrom, source, "transcript", start, end, score, strand, frame, "; ".join(gtf_attrs) + ";"]
                    )
                )
                continue

            if feature_l not in {"exon", "cds"}:
                continue
            transcript_id = parent_id or feature_id
            gene_id = transcript_to_gene.get(transcript_id, transcript_id)
            gtf_attrs = [f'gene_id "{gene_id}"', f'transcript_id "{transcript_id}"']
            rows.append(
                "\t".join(
                    [chrom, source, feature, start, end, score, strand, frame, "; ".join(gtf_attrs) + ";"]
                )
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return 0 if rows else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input.gff3> <output.gtf>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(convert(Path(sys.argv[1]), Path(sys.argv[2])))
