#!/usr/bin/env python3
"""Validate DGE + pathway enrichment benchmark results against truth data.

Checks:
  1. DE gene recall >= 80%
  2. DE gene precision >= 70%
  3. Enrichment recall: expected pathways have p < 0.05
  4. Enrichment specificity: non-enriched pathways have p >= 0.05
  5. Direction check: APOP genes up, CCYC genes down
"""

import json
import sys
from pathlib import Path

import pandas as pd


def validate(truth_path: str, dge_results_path: str, enrichment_path: str) -> bool:
    with open(truth_path) as f:
        truth = json.load(f)

    truth_de = set(truth["de_genes"])
    enriched_pws = set(truth["enriched_pathways"])
    non_enriched_pws = set(truth["non_enriched_pathways"])

    print("=== DGE + PATHWAY ENRICHMENT BENCHMARK RESULTS ===")
    print()

    # -- Check 1 & 2: DE gene recall and precision --
    dge = pd.read_csv(dge_results_path, index_col=0)
    reported_de = set(dge.index)

    tp = reported_de & truth_de
    fp = reported_de - truth_de
    fn = truth_de - reported_de

    recall = len(tp) / len(truth_de) if truth_de else 0
    precision = len(tp) / len(reported_de) if reported_de else 0

    print("DE Gene Detection:")
    print(f"  Truth DE genes:    {len(truth_de)}")
    print(f"  Reported DE genes: {len(reported_de)}")
    print(f"  True positives:    {len(tp)}")
    print(f"  False positives:   {len(fp)}")
    if fp:
        print(f"    FP genes: {sorted(fp)}")
    print(f"  False negatives:   {len(fn)}")
    if fn:
        print(f"    FN genes: {sorted(fn)}")
    print(f"  Recall:    {recall:.2%} (threshold >= 80%)")
    print(f"  Precision: {precision:.2%} (threshold >= 70%)")

    recall_ok = recall >= 0.80
    precision_ok = precision >= 0.70
    print(f"  Recall OK:    {recall_ok}")
    print(f"  Precision OK: {precision_ok}")
    print()

    # -- Check 3 & 4: Pathway enrichment --
    enr = pd.read_csv(enrichment_path)
    pw_pvals = dict(zip(enr["pathway"], enr["pvalue"]))

    print("Pathway Enrichment:")
    enrichment_recall_ok = True
    for pw in enriched_pws:
        pv = pw_pvals.get(pw, 1.0)
        ok = pv < 0.05
        if not ok:
            enrichment_recall_ok = False
        print(f"  {pw:30s}  p={pv:.2e}  expected=enriched    [{'OK' if ok else 'FAIL'}]")

    enrichment_specificity_ok = True
    for pw in non_enriched_pws:
        pv = pw_pvals.get(pw, 1.0)
        ok = pv >= 0.05
        if not ok:
            enrichment_specificity_ok = False
        print(f"  {pw:30s}  p={pv:.2e}  expected=NOT enriched [{'OK' if ok else 'FAIL'}]")

    print(f"  Enrichment recall OK:      {enrichment_recall_ok}")
    print(f"  Enrichment specificity OK: {enrichment_specificity_ok}")
    print()

    # -- Check 5: Direction --
    # Read the full DGE results to check direction of detected DE genes
    dge_all_path = str(Path(dge_results_path).parent / "dge_all.csv")
    try:
        dge_all = pd.read_csv(dge_all_path, index_col=0)
    except FileNotFoundError:
        dge_all = dge  # fallback to significant-only

    direction_ok = True
    directions = truth.get("expected_de_direction", {})
    print("Direction Check:")
    for pattern, expected_dir in directions.items():
        prefix = pattern.replace("_*", "_")
        matching_genes = [g for g in tp if g.startswith(prefix)]
        if not matching_genes:
            continue
        correct = 0
        for g in matching_genes:
            lfc = dge_all.loc[g, "log2FC"] if g in dge_all.index else 0
            if expected_dir == "up" and lfc > 0:
                correct += 1
            elif expected_dir == "down" and lfc < 0:
                correct += 1
        pct = correct / len(matching_genes) if matching_genes else 0
        ok = pct >= 0.90
        if not ok:
            direction_ok = False
        print(f"  {pattern:10s} expected={expected_dir:5s}  correct={correct}/{len(matching_genes)} ({pct:.0%})  [{'OK' if ok else 'FAIL'}]")

    print(f"  Direction OK: {direction_ok}")
    print()

    # -- Summary --
    passed = all([recall_ok, precision_ok, enrichment_recall_ok, enrichment_specificity_ok, direction_ok])
    print(f"BENCHMARK PASSED: {passed}")
    return passed


if __name__ == "__main__":
    truth = sys.argv[1] if len(sys.argv) > 1 else "benchmark_data/dge_pathway/truth.json"
    dge_results = sys.argv[2] if len(sys.argv) > 2 else "workspace/dge_pathway/attempt1/output/dge_results.csv"
    enrichment = sys.argv[3] if len(sys.argv) > 3 else "workspace/dge_pathway/attempt1/output/enrichment_results.csv"
    ok = validate(truth, dge_results, enrichment)
    sys.exit(0 if ok else 1)
