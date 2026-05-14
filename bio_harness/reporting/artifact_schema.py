"""Profile researcher-facing schema summaries for common output artifacts."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from typing import Any, Iterable


_DEFAULT_SAMPLE_ROWS = 25
_GFF_COLUMNS = [
    "seqid",
    "source",
    "type",
    "start",
    "end",
    "score",
    "strand",
    "phase",
    "attributes",
]
_VCF_COLUMNS = [
    "#CHROM",
    "POS",
    "ID",
    "REF",
    "ALT",
    "QUAL",
    "FILTER",
    "INFO",
]


def _open_text(path: Path):
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _looks_like_int(value: str) -> bool:
    try:
        int(value)
    except ValueError:
        return False
    return True


def _looks_like_float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _infer_value_type(values: Iterable[str]) -> str:
    normalized = [str(value).strip() for value in values if str(value).strip()]
    if not normalized:
        return "empty"
    lowered = {value.lower() for value in normalized}
    if lowered <= {"true", "false", "0", "1", "yes", "no"}:
        return "boolean"
    if all(_looks_like_int(value) for value in normalized):
        return "integer"
    if all(_looks_like_float(value) for value in normalized):
        return "number"
    return "string"


def _sample_examples(values: Iterable[str], *, limit: int = 3) -> list[str]:
    examples: list[str] = []
    for value in values:
        token = str(value).strip()
        if not token or token in examples:
            continue
        examples.append(token[:80])
        if len(examples) >= limit:
            break
    return examples


def _profile_tabular_rows(columns: list[str], rows: list[list[str]], *, format_name: str) -> dict[str, Any]:
    column_profiles: list[dict[str, Any]] = []
    for index, name in enumerate(columns):
        values = [row[index] if index < len(row) else "" for row in rows]
        non_empty = [value for value in values if str(value).strip()]
        column_profiles.append(
            {
                "name": str(name),
                "inferred_type": _infer_value_type(values),
                "non_empty_fraction": round(len(non_empty) / max(len(rows), 1), 3),
                "examples": _sample_examples(values),
            }
        )
    return {
        "format": format_name,
        "columns": column_profiles,
        "sample_rows_analyzed": len(rows),
    }


def _profile_delimited(path: Path, *, sample_rows: int) -> dict[str, Any]:
    with _open_text(path) as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "\t" if "\t" in sample else ","
        reader = csv.reader(handle, delimiter=delimiter)
        rows = [row for row in reader if row and any(str(cell).strip() for cell in row)]
    if not rows:
        return {"format": "delimited", "columns": [], "sample_rows_analyzed": 0, "delimiter": delimiter}
    header, data_rows = rows[0], rows[1 : sample_rows + 1]
    profile = _profile_tabular_rows(list(header), data_rows, format_name="tsv" if delimiter == "\t" else "csv")
    profile["delimiter"] = delimiter
    return profile


def _profile_vcf(path: Path, *, sample_rows: int) -> dict[str, Any]:
    header: list[str] = []
    rows: list[list[str]] = []
    info_fields: list[str] = []
    with _open_text(path) as handle:
        for line in handle:
            stripped = line.rstrip("\n")
            if stripped.startswith("##INFO=<ID="):
                field = stripped.split("ID=", 1)[1].split(",", 1)[0].strip()
                if field and field not in info_fields:
                    info_fields.append(field)
                continue
            if stripped.startswith("#CHROM"):
                header = stripped.split("\t")
                continue
            if stripped.startswith("#"):
                continue
            if stripped.strip():
                rows.append(stripped.split("\t"))
            if len(rows) >= sample_rows:
                break
    if not header:
        header = list(_VCF_COLUMNS)
    profile = _profile_tabular_rows(header, rows, format_name="vcf")
    profile["info_fields"] = info_fields
    profile["sample_columns"] = header[9:] if len(header) > 9 else []
    return profile


def _profile_gff_like(path: Path, *, sample_rows: int) -> dict[str, Any]:
    rows: list[list[str]] = []
    with _open_text(path) as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            stripped = line.rstrip("\n")
            if not stripped:
                continue
            rows.append(stripped.split("\t"))
            if len(rows) >= sample_rows:
                break
    return _profile_tabular_rows(list(_GFF_COLUMNS), rows, format_name="gff_like")


def _profile_jsonl(path: Path, *, sample_rows: int) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    with _open_text(path) as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
            if len(records) >= sample_rows:
                break
    columns = sorted({str(key) for record in records for key in record.keys()})
    rows = [[json.dumps(record.get(column, ""), sort_keys=True) if isinstance(record.get(column), (dict, list)) else str(record.get(column, "")) for column in columns] for record in records]
    return _profile_tabular_rows(columns, rows, format_name="jsonl")


def profile_artifact_schema(path: str | Path, *, sample_rows: int = _DEFAULT_SAMPLE_ROWS) -> dict[str, Any]:
    """Build a compact schema/data-dictionary profile for a completed artifact."""
    artifact_path = Path(path).expanduser().resolve()
    if not artifact_path.is_file():
        raise FileNotFoundError(f"Artifact path does not exist: {artifact_path}")

    suffixes = "".join(artifact_path.suffixes).lower()
    if suffixes.endswith(".vcf") or suffixes.endswith(".vcf.gz"):
        schema = _profile_vcf(artifact_path, sample_rows=sample_rows)
    elif suffixes.endswith(".gff") or suffixes.endswith(".gff3") or suffixes.endswith(".gtf") or suffixes.endswith(".gff.gz") or suffixes.endswith(".gtf.gz"):
        schema = _profile_gff_like(artifact_path, sample_rows=sample_rows)
    elif suffixes.endswith(".jsonl") or suffixes.endswith(".ndjson") or suffixes.endswith(".jsonl.gz"):
        schema = _profile_jsonl(artifact_path, sample_rows=sample_rows)
    elif suffixes.endswith(".csv") or suffixes.endswith(".tsv") or suffixes.endswith(".txt") or suffixes.endswith(".csv.gz") or suffixes.endswith(".tsv.gz"):
        schema = _profile_delimited(artifact_path, sample_rows=sample_rows)
    else:
        schema = {"format": artifact_path.suffix.lower().lstrip(".") or "unknown", "columns": [], "sample_rows_analyzed": 0}

    return {
        "artifact_path": str(artifact_path),
        "size_bytes": int(artifact_path.stat().st_size),
        "format": schema.get("format", "unknown"),
        "sample_rows_analyzed": int(schema.get("sample_rows_analyzed", 0) or 0),
        "columns": schema.get("columns", []),
        "delimiter": schema.get("delimiter", ""),
        "info_fields": schema.get("info_fields", []),
        "sample_columns": schema.get("sample_columns", []),
    }


def write_artifact_schema_profile(
    path: str | Path,
    output_path: str | Path | None = None,
    *,
    sample_rows: int = _DEFAULT_SAMPLE_ROWS,
) -> Path:
    """Write a JSON schema profile for a completed artifact."""
    artifact_path = Path(path).expanduser().resolve()
    target = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else artifact_path.parent / f"{artifact_path.name}.schema.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = profile_artifact_schema(artifact_path, sample_rows=sample_rows)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
