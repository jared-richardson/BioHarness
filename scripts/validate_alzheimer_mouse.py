#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path


REQUIRED_COLUMNS = ["Pathway", "5xFAD_pvalue", "3xTG_AD_pvalue", "PS3O1S_pvalue"]
PVALUE_COLUMNS = REQUIRED_COLUMNS[1:]
SIGNIFICANCE_THRESHOLD = 0.05


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [{str(key): str(value or "").strip() for key, value in row.items()} for row in reader]


def _safe_float(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def validate(truth_path: Path, output_path: Path) -> bool:
    truth_rows = _read_rows(truth_path)
    output_rows = _read_rows(output_path)

    print("=== ALZHEIMER MOUSE BENCHMARK RESULTS ===")
    print()
    if not truth_rows:
        print("FAIL: truth CSV is empty")
        return False
    if not output_rows:
        print("FAIL: output CSV is empty")
        return False

    missing = [column for column in REQUIRED_COLUMNS if column not in output_rows[0]]
    if missing:
        print(f"FAIL: missing required columns: {missing}")
        return False

    truth_by_pathway = {row["Pathway"]: row for row in truth_rows if str(row.get("Pathway", "")).strip()}
    output_by_pathway = {row["Pathway"]: row for row in output_rows if str(row.get("Pathway", "")).strip()}
    truth_pathways = set(truth_by_pathway)
    output_pathways = set(output_by_pathway)
    overlap = truth_pathways & output_pathways

    recall = len(overlap) / len(truth_pathways) if truth_pathways else 0.0
    precision = len(overlap) / len(output_pathways) if output_pathways else 0.0
    print(f"Pathway recall:    {len(overlap)}/{len(truth_pathways)} = {recall:.1%}")
    print(f"Pathway precision: {len(overlap)}/{len(output_pathways)} = {precision:.1%}")

    significance_scores: dict[str, float] = {}
    log_p_errors: dict[str, float] = {}
    for column in PVALUE_COLUMNS:
        matches = 0
        comparable = 0
        abs_errors: list[float] = []
        for pathway in sorted(overlap):
            truth_p = _safe_float(truth_by_pathway[pathway].get(column, ""))
            output_p = _safe_float(output_by_pathway[pathway].get(column, ""))
            if truth_p is None or output_p is None:
                continue
            comparable += 1
            truth_sig = truth_p < SIGNIFICANCE_THRESHOLD
            output_sig = output_p < SIGNIFICANCE_THRESHOLD
            if truth_sig == output_sig:
                matches += 1
            abs_errors.append(abs(math.log10(max(output_p, 1e-300))) - abs(math.log10(max(truth_p, 1e-300))))
        significance_scores[column] = (matches / comparable) if comparable else 0.0
        log_p_errors[column] = (sum(abs_errors) / len(abs_errors)) if abs_errors else float("inf")
        print(
            f"{column}: significance agreement={significance_scores[column]:.1%} "
            f"mean |log10 p| error={log_p_errors[column]:.3f}"
        )

    recall_ok = recall >= 0.80
    precision_ok = precision >= 0.70
    significance_ok = all(score >= 0.80 for score in significance_scores.values())
    log_p_ok = all(error <= 1.5 for error in log_p_errors.values())
    passed = recall_ok and precision_ok and significance_ok and log_p_ok

    print()
    print(f"Recall OK: {recall_ok}")
    print(f"Precision OK: {precision_ok}")
    print(f"Significance OK: {significance_ok}")
    print(f"Log-p agreement OK: {log_p_ok}")
    print(f"BENCHMARK PASSED: {passed}")
    return passed


def sanity(output_path: Path) -> bool:
    rows = _read_rows(output_path)
    if not rows:
        print("FAIL: output CSV is empty")
        return False
    missing = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
    if missing:
        print(f"FAIL: missing required columns: {missing}")
        return False
    if not any(str(row.get("Pathway", "")).strip() for row in rows):
        print("FAIL: no pathways reported")
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
