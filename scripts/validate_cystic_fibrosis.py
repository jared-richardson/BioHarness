#!/usr/bin/env python3
from __future__ import annotations

import csv
import sys
from pathlib import Path


REQUIRED_COLUMNS = [
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
CORE_FIELDS = [
    "chromosome",
    "position",
    "reference",
    "alternate",
    "gene_name",
    "gene_id",
    "annotation",
    "impact",
    "transcript_id",
    "hgvs_c",
    "hgvs_p",
]
OPTIONAL_FIELDS = ["variant_id", "clinical_significance", "diseases", "review_status", "rs_id"]


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [{str(key): str(value or "").strip() for key, value in row.items()} for row in reader]


def _normalize_diseases(value: str) -> set[str]:
    tokens: set[str] = set()
    for sep in ("|", ";"):
        value = value.replace(sep, ";")
    for token in value.split(";"):
        normalized = str(token).strip()
        if normalized:
            tokens.add(normalized)
    return tokens


def validate(truth_path: Path, output_path: Path) -> bool:
    truth_rows = _read_rows(truth_path)
    output_rows = _read_rows(output_path)
    if not truth_rows:
        print("FAIL: truth CSV is empty")
        return False
    if not output_rows:
        print("FAIL: output CSV is empty")
        return False

    truth = truth_rows[0]
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in output_rows[0]]
    if missing_columns:
        print(f"FAIL: output CSV is missing required columns: {missing_columns}")
        return False

    winner: dict[str, str] | None = None
    for row in output_rows:
        if all(str(row.get(field, "")).strip() == str(truth.get(field, "")).strip() for field in CORE_FIELDS):
            winner = row
            break

    print("=== CYSTIC FIBROSIS BENCHMARK RESULTS ===")
    print()
    if winner is None:
        print("Core causal-variant match: FAIL")
        print("Expected:")
        print({field: truth.get(field, "") for field in CORE_FIELDS})
        print("Observed rows:")
        for row in output_rows[:5]:
            print({field: row.get(field, "") for field in CORE_FIELDS})
        return False

    print("Core causal-variant match: PASS")
    optional_failures: list[str] = []
    informational_missing: list[str] = []
    for field in OPTIONAL_FIELDS:
        actual = str(winner.get(field, "")).strip()
        expected = str(truth.get(field, "")).strip()
        if not actual:
            informational_missing.append(field)
            continue
        if field == "diseases":
            truth_terms = _normalize_diseases(expected)
            actual_terms = _normalize_diseases(actual)
            if actual_terms != truth_terms:
                optional_failures.append(field)
        elif actual != expected:
            optional_failures.append(field)

    print(f"Optional exact fields present: {len(OPTIONAL_FIELDS) - len(informational_missing)}/{len(OPTIONAL_FIELDS)}")
    if informational_missing:
        print(f"Optional fields omitted: {informational_missing}")
    if optional_failures:
        print(f"FAIL: contradictory optional fields: {optional_failures}")
        return False

    print("BENCHMARK PASSED: True")
    return True


def sanity(output_path: Path) -> bool:
    rows = _read_rows(output_path)
    if not rows:
        print("FAIL: output CSV is empty")
        return False
    missing = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
    if missing:
        print(f"FAIL: missing columns {missing}")
        return False
    if not any(str(row.get("gene_name", "")).strip() for row in rows):
        print("FAIL: no gene_name values")
        return False
    print("SANITY PASSED: True")
    return True


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--sanity":
        ok = sanity(Path(sys.argv[2]))
        sys.exit(0 if ok else 1)
    if len(sys.argv) == 3:
        ok = validate(Path(sys.argv[1]), Path(sys.argv[2]))
        sys.exit(0 if ok else 1)
    print(f"Usage: {sys.argv[0]} <truth.csv> <agent_output.csv>")
    print(f"   or: {sys.argv[0]} --sanity <agent_output.csv>")
    sys.exit(2)
