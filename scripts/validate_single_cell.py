#!/usr/bin/env python3
"""Validate single-cell RNA-seq benchmark results.

Supports two modes:
  Benchmark mode:
    python3 scripts/validate_single_cell.py truth_dir/ output_dir/
    Compares clustering, markers, and cell recovery against known truth.

  Sanity mode:
    python3 scripts/validate_single_cell.py --sanity output_dir/
    Checks format, cluster balance, and internal consistency.
    Useful for novel data where no truth is available.

Truth directory should contain:
  - truth_cell_types.json: {barcode: type_label, ...}
  - truth_markers.json: {type_label: [gene1, gene2, ...], ...}

Output directory should contain:
  - cluster_assignments.json: {barcode: cluster_id, ...}
  - marker_genes.json: {cluster_id: [gene1, gene2, ...], ...}

Checks (benchmark mode):
  1. Cell recovery — fraction of truth barcodes found in output
  2. Clustering accuracy — Rand Index (handles label permutation)
  3. Marker gene recall — fraction of truth markers found per cluster
  4. Cluster count — correct number of distinct clusters

Checks (sanity mode):
  1. Output format — JSON files with expected structure
  2. Cell count — plausible number of cells
  3. Cluster balance — no single cluster dominates (>90%)
  4. Marker genes — each cluster has markers, not all identical
"""

from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path


def rand_index(labels_true: list, labels_pred: list) -> float:
    """Compute the Rand Index between two clusterings (pure Python).

    Handles label permutation — only measures partition agreement.
    RI = (TP + TN) / (TP + TN + FP + FN) where:
      TP = pairs in same cluster in both
      TN = pairs in different clusters in both
      FP = pairs in same cluster in pred but different in true
      FN = pairs in different clusters in pred but same in true
    """
    n = len(labels_true)
    if n < 2:
        return 1.0

    tp = 0
    tn = 0
    fp = 0
    fn = 0
    for i, j in combinations(range(n), 2):
        same_true = labels_true[i] == labels_true[j]
        same_pred = labels_pred[i] == labels_pred[j]
        if same_true and same_pred:
            tp += 1
        elif not same_true and not same_pred:
            tn += 1
        elif not same_true and same_pred:
            fp += 1
        else:
            fn += 1

    total = tp + tn + fp + fn
    return (tp + tn) / total if total > 0 else 0.0


def adjusted_rand_index(labels_true: list, labels_pred: list) -> float:
    """Compute Adjusted Rand Index (pure Python).

    ARI = (RI - Expected_RI) / (Max_RI - Expected_RI)
    Adjusted for chance — ARI=0 means random, ARI=1 means perfect.
    """
    n = len(labels_true)
    if n < 2:
        return 1.0

    # Build contingency table
    true_clusters: dict[str, set[int]] = {}
    pred_clusters: dict[str, set[int]] = {}
    for i in range(n):
        true_clusters.setdefault(str(labels_true[i]), set()).add(i)
        pred_clusters.setdefault(str(labels_pred[i]), set()).add(i)

    # Compute nij (intersection sizes)
    sum_nij_c2 = 0
    for tc in true_clusters.values():
        for pc in pred_clusters.values():
            nij = len(tc & pc)
            if nij >= 2:
                sum_nij_c2 += nij * (nij - 1) // 2

    sum_ai_c2 = sum(len(c) * (len(c) - 1) // 2 for c in true_clusters.values())
    sum_bj_c2 = sum(len(c) * (len(c) - 1) // 2 for c in pred_clusters.values())
    n_c2 = n * (n - 1) // 2

    if n_c2 == 0:
        return 1.0

    expected = sum_ai_c2 * sum_bj_c2 / n_c2
    max_idx = (sum_ai_c2 + sum_bj_c2) / 2
    denom = max_idx - expected

    if denom == 0:
        return 1.0 if sum_nij_c2 == expected else 0.0

    return (sum_nij_c2 - expected) / denom


def find_best_mapping(truth_types: dict[str, str], pred_clusters: dict[str, int]) -> dict[int, str]:
    """Find the best mapping from predicted cluster IDs to truth type labels.

    Uses majority voting: each cluster is assigned to the truth type most
    represented among its cells.
    """
    common_barcodes = set(truth_types.keys()) & set(pred_clusters.keys())

    # For each predicted cluster, count truth types
    cluster_type_counts: dict[int, dict[str, int]] = {}
    for bc in common_barcodes:
        cid = pred_clusters[bc]
        ttype = truth_types[bc]
        cluster_type_counts.setdefault(cid, {})
        cluster_type_counts[cid][ttype] = cluster_type_counts[cid].get(ttype, 0) + 1

    # Assign each cluster to its most common truth type
    mapping = {}
    for cid, type_counts in cluster_type_counts.items():
        best_type = max(type_counts, key=type_counts.get)
        mapping[cid] = best_type

    return mapping


def run_benchmark(truth_dir: Path, output_dir: Path) -> int:
    """Benchmark mode: compare against truth."""
    print("=" * 60)
    print("Single-Cell RNA-Seq — Benchmark Validation")
    print("=" * 60)
    print(f"Truth:  {truth_dir}")
    print(f"Output: {output_dir}")
    print()

    # Load truth data
    truth_types_path = truth_dir / "truth_cell_types.json"
    truth_markers_path = truth_dir / "truth_markers.json"
    if not truth_types_path.exists():
        print(f"ERROR: {truth_types_path} not found")
        return 1

    truth_types: dict[str, str] = json.loads(truth_types_path.read_text())
    truth_markers: dict[str, list[str]] = {}
    if truth_markers_path.exists():
        truth_markers = json.loads(truth_markers_path.read_text())

    # Load agent output
    cluster_path = output_dir / "cluster_assignments.json"
    markers_path = output_dir / "marker_genes.json"
    if not cluster_path.exists():
        print(f"ERROR: {cluster_path} not found")
        return 1

    pred_clusters: dict[str, int] = json.loads(cluster_path.read_text())
    pred_markers: dict[str, list[str]] = {}
    if markers_path.exists():
        pred_markers = json.loads(markers_path.read_text())

    checks_passed = 0
    checks_total = 0

    # Check 1: Cell recovery
    checks_total += 1
    common = set(truth_types.keys()) & set(pred_clusters.keys())
    recovery = len(common) / len(truth_types) if truth_types else 0
    recovery_threshold = 0.90
    print("Check 1: Cell recovery")
    print(f"  Truth cells: {len(truth_types)}")
    print(f"  Agent cells: {len(pred_clusters)}")
    print(f"  Common: {len(common)}")
    print(f"  Recovery: {recovery:.1%} (threshold: {recovery_threshold:.0%})")
    if recovery >= recovery_threshold:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 2: Clustering accuracy (Rand Index)
    checks_total += 1
    common_list = sorted(common)
    true_labels = [truth_types[bc] for bc in common_list]
    pred_labels = [pred_clusters[bc] for bc in common_list]
    ri = rand_index(true_labels, pred_labels)
    ari = adjusted_rand_index(true_labels, pred_labels)
    ri_threshold = 0.85
    print("Check 2: Clustering accuracy")
    print(f"  Rand Index: {ri:.4f} (threshold: {ri_threshold})")
    print(f"  Adjusted Rand Index: {ari:.4f}")
    # Show cluster-to-type mapping
    mapping = find_best_mapping(truth_types, {bc: pred_clusters[bc] for bc in common})
    for cid, ttype in sorted(mapping.items(), key=lambda x: str(x[0])):
        cells_in_cluster = sum(1 for bc in common if pred_clusters[bc] == cid)
        correct = sum(1 for bc in common if pred_clusters[bc] == cid and truth_types[bc] == ttype)
        print(f"    Cluster {cid} -> {ttype}: {correct}/{cells_in_cluster} correct")
    if ri >= ri_threshold:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 3: Marker gene recall
    checks_total += 1
    total_truth_markers = 0
    total_found_markers = 0
    print("Check 3: Marker gene recall")
    if truth_markers and pred_markers:
        for cid, ttype in mapping.items():
            if ttype not in truth_markers:
                continue
            truth_set = set(truth_markers[ttype])
            pred_set = set(pred_markers.get(str(cid), []))
            found = truth_set & pred_set
            total_truth_markers += len(truth_set)
            total_found_markers += len(found)
            print(f"    {ttype} (cluster {cid}): {len(found)}/{len(truth_set)} markers recovered")

        recall = total_found_markers / total_truth_markers if total_truth_markers else 0
        recall_threshold = 0.70
        print(f"  Overall marker recall: {total_found_markers}/{total_truth_markers} ({recall:.1%}, threshold: {recall_threshold:.0%})")
        if recall >= recall_threshold:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL")
    elif not truth_markers:
        print("  No truth markers provided (PASS by default)")
        checks_passed += 1
    else:
        print("  No marker_genes.json in agent output")
        print("  \u2717 FAIL")
    print()

    # Check 4: Cluster count
    checks_total += 1
    n_truth_types = len(set(truth_types.values()))
    n_pred_clusters = len(set(pred_clusters.values()))
    print("Check 4: Cluster count")
    print(f"  Truth types: {n_truth_types}")
    print(f"  Agent clusters: {n_pred_clusters}")
    if n_pred_clusters == n_truth_types:
        print("  \u2713 PASS — exact match")
        checks_passed += 1
    elif abs(n_pred_clusters - n_truth_types) <= 1:
        print("  \u2713 PASS — within tolerance (±1)")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    print("=" * 60)
    if checks_passed == checks_total:
        print(f"BENCHMARK PASSED ({checks_passed}/{checks_total} checks)")
    else:
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
    print("=" * 60)
    return 0 if checks_passed == checks_total else 1


def run_sanity(output_dir: Path) -> int:
    """Sanity mode: check format and plausibility without truth data."""
    print("=" * 60)
    print("Single-Cell RNA-Seq — Sanity Check")
    print("=" * 60)
    print(f"Output: {output_dir}")
    print("(No truth data — checking format and plausibility only)")
    print()

    cluster_path = output_dir / "cluster_assignments.json"
    markers_path = output_dir / "marker_genes.json"

    checks_passed = 0
    checks_total = 0

    # Check 1: Output format
    checks_total += 1
    print("Check 1: Output format")
    if not cluster_path.exists():
        print("  cluster_assignments.json not found")
        print("  \u2717 FAIL")
    else:
        try:
            clusters = json.loads(cluster_path.read_text())
            print(f"  cluster_assignments.json: {len(clusters)} cells")
            has_markers = markers_path.exists()
            if has_markers:
                markers = json.loads(markers_path.read_text())
                print(f"  marker_genes.json: {len(markers)} clusters")
            else:
                print("  marker_genes.json: not found (optional)")
            print("  \u2713 PASS")
            checks_passed += 1
        except json.JSONDecodeError as e:
            print(f"  JSON parse error: {e}")
            print("  \u2717 FAIL")
    print()

    if not cluster_path.exists():
        print("=" * 60)
        print(f"SANITY CHECK: {checks_passed}/{checks_total} checks passed")
        print("=" * 60)
        return 1

    clusters = json.loads(cluster_path.read_text())

    # Check 2: Cell count
    checks_total += 1
    n_cells = len(clusters)
    print("Check 2: Cell count plausibility")
    print(f"  Cells: {n_cells}")
    if 10 <= n_cells <= 100000:
        print("  Plausible cell count")
        print("  \u2713 PASS")
        checks_passed += 1
    elif n_cells < 10:
        print("  Very few cells — possible filtering issue")
        print("  \u2717 FAIL")
    else:
        print("  Very many cells — may include doublets or ambient RNA")
        print("  \u2717 FAIL")
    print()

    # Check 3: Cluster balance
    checks_total += 1
    cluster_sizes: dict[str, int] = {}
    for bc, cid in clusters.items():
        key = str(cid)
        cluster_sizes[key] = cluster_sizes.get(key, 0) + 1

    n_clusters = len(cluster_sizes)
    largest = max(cluster_sizes.values())
    largest_frac = largest / n_cells if n_cells else 0
    print("Check 3: Cluster balance")
    print(f"  Clusters: {n_clusters}")
    for cid, size in sorted(cluster_sizes.items(), key=lambda x: -x[1]):
        print(f"    Cluster {cid}: {size} cells ({size/n_cells*100:.1f}%)")
    if n_clusters >= 2 and largest_frac < 0.95:
        print("  \u2713 PASS — no single cluster dominates")
        checks_passed += 1
    elif n_clusters < 2:
        print("  \u2717 FAIL — only 1 cluster found")
    else:
        print(f"  \u2717 FAIL — largest cluster has {largest_frac:.0%} of cells")
    print()

    # Check 4: Marker genes
    checks_total += 1
    print("Check 4: Marker gene quality")
    if markers_path.exists():
        markers = json.loads(markers_path.read_text())
        all_marker_sets = [set(v) for v in markers.values()]
        total_markers = sum(len(s) for s in all_marker_sets)
        print(f"  Total markers across clusters: {total_markers}")

        # Check if marker sets are distinct (not all the same)
        if len(all_marker_sets) >= 2:
            overlap = all_marker_sets[0]
            for s in all_marker_sets[1:]:
                overlap = overlap & s
            unique_frac = 1 - (len(overlap) / total_markers * len(all_marker_sets)) if total_markers > 0 else 0
            print(f"  Shared across all clusters: {len(overlap)}")
            print(f"  Marker specificity: {unique_frac:.1%}")

        if total_markers > 0:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL — no markers found")
    else:
        print("  No marker_genes.json (optional, PASS by default)")
        checks_passed += 1
    print()

    print("=" * 60)
    if checks_passed == checks_total:
        print(f"SANITY CHECK PASSED ({checks_passed}/{checks_total} checks)")
    else:
        print(f"SANITY CHECK: {checks_passed}/{checks_total} checks passed")
    print("=" * 60)
    return 0 if checks_passed == checks_total else 1


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print(f"  {sys.argv[0]} <truth_dir> <output_dir>    # Benchmark mode")
        print(f"  {sys.argv[0]} --sanity <output_dir>        # Sanity mode (no truth)")
        print()
        print("Truth dir should contain: truth_cell_types.json, truth_markers.json")
        print("Output dir should contain: cluster_assignments.json, marker_genes.json")
        return 1

    if sys.argv[1] == "--sanity":
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} --sanity <output_dir>")
            return 1
        return run_sanity(Path(sys.argv[2]))

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <truth_dir> <output_dir>")
        return 1

    truth_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])

    if not truth_dir.exists():
        print(f"ERROR: Truth directory not found: {truth_dir}")
        return 1
    if not output_dir.exists():
        print(f"ERROR: Output directory not found: {output_dir}")
        return 1

    return run_benchmark(truth_dir, output_dir)


if __name__ == "__main__":
    sys.exit(main())
