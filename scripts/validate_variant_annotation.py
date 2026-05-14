#!/usr/bin/env python3
"""Validate variant annotation benchmark results against truth data."""

import json
import sys


def validate(truth_path: str, annotated_vcf: str, filtered_vcf: str) -> bool:
    with open(truth_path) as f:
        truth = json.load(f)

    # Parse annotated VCF
    annotated_impacts = {}
    with open(annotated_vcf) as f:
        for line in f:
            if line.startswith("#"):
                continue
            fields = line.strip().split("\t")
            vid = fields[2]
            info = fields[7]
            for part in info.split(";"):
                if part.startswith("ANN="):
                    ann = part[4:]
                    first_ann = ann.split(",")[0]
                    impact = first_ann.split("|")[2]
                    annotated_impacts[vid] = impact
                    break

    # Parse filtered VCF
    filtered_ids = set()
    with open(filtered_vcf) as f:
        for line in f:
            if line.startswith("#"):
                continue
            fields = line.strip().split("\t")
            filtered_ids.add(fields[2])

    # Validate impact annotations
    print("=== VARIANT ANNOTATION BENCHMARK RESULTS ===")
    print()
    print("Impact Annotations:")
    all_correct = True
    for t in truth["variants"]:
        vid = t["id"]
        expected = t["expected_impact"]
        actual = annotated_impacts.get(vid, "MISSING")
        ok = actual == expected
        mark = "OK" if ok else "FAIL"
        if not ok:
            all_correct = False
        print(f"  {vid:20s}  expected={expected:10s}  actual={actual:10s}  [{mark}]")

    print()
    print(f"All impacts correct: {all_correct}")
    print()

    # Check filtered set
    truth_filtered = set(truth["high_moderate_ids"])
    print(f"Filtered variants (expected {len(truth_filtered)}): {len(filtered_ids)}")
    print(f"  Expected: {sorted(truth_filtered)}")
    print(f"  Got:      {sorted(filtered_ids)}")
    filter_correct = filtered_ids == truth_filtered
    print(f"  Filter correct: {filter_correct}")

    print()
    passed = all_correct and filter_correct
    print(f"BENCHMARK PASSED: {passed}")
    print(f"Annotated: {len(annotated_impacts)}/{truth['total_variants']}")
    print(f"Filtered:  {len(filtered_ids)}/{truth['high_moderate_count']}")
    return passed


if __name__ == "__main__":
    truth = sys.argv[1] if len(sys.argv) > 1 else "benchmark_data/variant_annotation/truth.json"
    annotated = sys.argv[2] if len(sys.argv) > 2 else "workspace/variant_annotation/attempt2/output/annotated.vcf"
    filtered = sys.argv[3] if len(sys.argv) > 3 else "workspace/variant_annotation/attempt2/output/filtered_pathogenic.vcf"
    ok = validate(truth, annotated, filtered)
    sys.exit(0 if ok else 1)
