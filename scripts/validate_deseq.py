#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path


REQUIRED_COLUMNS = ["gene_id", "log2FoldChange", "pvalue", "padj"]
LOG2FC_THRESHOLD = 2.0
PADJ_THRESHOLD = 0.01
MIN_RECALL = 0.95
MIN_PRECISION = 0.95
MAX_MEAN_ABS_LOG2FC_ERROR = 0.35


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        first_line = handle.readline()
        handle.seek(0)
        delimiter = "," if first_line.count(",") > first_line.count("\t") else "\t"
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            return []
        return [{str(key): str(value or "").strip() for key, value in row.items()} for row in reader]


def _safe_float(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _significant_upregulated(rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    selected: dict[str, dict[str, float]] = {}
    for row in rows:
        gene_id = str(row.get("gene_id", "") or "").strip()
        log2fc = _safe_float(row.get("log2FoldChange", ""))
        pvalue = _safe_float(row.get("pvalue", ""))
        padj = _safe_float(row.get("padj", ""))
        if not gene_id or log2fc is None or pvalue is None or padj is None:
            continue
        if log2fc > LOG2FC_THRESHOLD and padj < PADJ_THRESHOLD:
            selected[gene_id] = {
                "log2FoldChange": log2fc,
                "pvalue": pvalue,
                "padj": padj,
            }
    return selected


def validate(truth_path: Path, output_path: Path) -> bool:
    truth_rows = _read_rows(truth_path)
    output_rows = _read_rows(output_path)

    print("=== DESEQ BENCHMARK RESULTS ===")
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

    truth_sig = _significant_upregulated(truth_rows)
    output_sig = _significant_upregulated(output_rows)
    if not truth_sig:
        print("FAIL: truth CSV has no significant upregulated genes after filtering")
        return False

    truth_ids = set(truth_sig)
    output_ids = set(output_sig)
    overlap = truth_ids & output_ids

    recall = len(overlap) / len(truth_ids) if truth_ids else 0.0
    precision = len(overlap) / len(output_ids) if output_ids else 0.0
    print(f"Truth significant upregulated genes: {len(truth_ids)}")
    print(f"Output significant upregulated genes: {len(output_ids)}")
    print(f"Gene recall:    {len(overlap)}/{len(truth_ids)} = {recall:.1%}")
    print(f"Gene precision: {len(overlap)}/{len(output_ids)} = {precision:.1%}")

    abs_errors: list[float] = []
    for gene_id in sorted(overlap):
        abs_errors.append(abs(output_sig[gene_id]["log2FoldChange"] - truth_sig[gene_id]["log2FoldChange"]))
    mean_abs_log2fc_error = sum(abs_errors) / len(abs_errors) if abs_errors else math.inf
    print(f"Mean |log2FoldChange error| on overlap: {mean_abs_log2fc_error:.3f}")

    recall_ok = recall >= MIN_RECALL
    precision_ok = precision >= MIN_PRECISION
    log2fc_ok = mean_abs_log2fc_error <= MAX_MEAN_ABS_LOG2FC_ERROR
    passed = recall_ok and precision_ok and log2fc_ok

    print()
    print(f"Recall OK: {recall_ok}")
    print(f"Precision OK: {precision_ok}")
    print(f"Log2FC agreement OK: {log2fc_ok}")
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
    significant = _significant_upregulated(rows)
    print(f"Significant upregulated genes found: {len(significant)}")
    print(f"SANITY PASSED: {bool(significant)}")
    return bool(significant)


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
