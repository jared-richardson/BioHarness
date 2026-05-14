from __future__ import annotations

import argparse
import csv
from pathlib import Path

_PREFERRED_LABEL_ORDER = ("5xFAD", "3xTG_AD", "PS3O1S")


def _load_pathway_pvalues(path: Path) -> dict[str, float]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return {}
        fieldnames = {str(name).strip().lower(): str(name).strip() for name in reader.fieldnames if str(name).strip()}
        pathway_field = fieldnames.get("term") or fieldnames.get("pathway")
        pvalue_field = fieldnames.get("p-value") or fieldnames.get("pvalue") or fieldnames.get("adjusted p-value")
        if not pathway_field or not pvalue_field:
            return {}
        rows: dict[str, float] = {}
        for row in reader:
            pathway = str(row.get(pathway_field, "") or "").strip()
            if not pathway:
                continue
            try:
                pvalue = float(str(row.get(pvalue_field, "") or "").strip())
            except ValueError:
                continue
            rows[pathway] = pvalue
        return rows


def export_multi_model_pathway_comparison(
    *,
    label_to_csv: dict[str, Path],
    output_csv: Path,
) -> dict[str, object]:
    normalized_inputs = {
        str(label).strip(): Path(path).expanduser().resolve(strict=False)
        for label, path in label_to_csv.items()
        if str(label).strip()
    }
    loaded = {
        label: _load_pathway_pvalues(path)
        for label, path in normalized_inputs.items()
        if path.exists()
    }
    if len(loaded) < 2:
        raise ValueError("Need at least two pathway-enrichment CSV files to compare.")

    shared: set[str] | None = None
    for rows in loaded.values():
        shared = set(rows) if shared is None else shared & set(rows)
    shared = shared or set()

    ordered_labels = [label for label in _PREFERRED_LABEL_ORDER if label in loaded]
    labels = ordered_labels + [label for label in loaded if label not in ordered_labels]
    sorted_rows = sorted(
        shared,
        key=lambda pathway: min(loaded[label][pathway] for label in labels),
    )

    output_csv = Path(output_csv).expanduser().resolve(strict=False)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["Pathway"] + [f"{label}_pvalue" for label in labels]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for pathway in sorted_rows:
            row = {"Pathway": pathway}
            for label in labels:
                row[f"{label}_pvalue"] = loaded[label][pathway]
            writer.writerow(row)

    return {
        "output_csv": str(output_csv),
        "row_count": len(sorted_rows),
        "labels": labels,
        "shared_pathways": len(shared),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export shared pathway comparison CSV from per-model enrichment files.")
    parser.add_argument(
        "--kegg-file",
        action="append",
        default=[],
        help="Mapping of model label to enrichment CSV as label=path. Repeatable.",
    )
    parser.add_argument("--output-csv", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    label_to_csv: dict[str, Path] = {}
    for item in args.kegg_file:
        label, sep, path = str(item).partition("=")
        if not sep or not label.strip() or not path.strip():
            raise SystemExit(f"Invalid --kegg-file value: {item!r}")
        label_to_csv[label.strip()] = Path(path.strip())
    meta = export_multi_model_pathway_comparison(
        label_to_csv=label_to_csv,
        output_csv=Path(args.output_csv),
    )
    print(meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
