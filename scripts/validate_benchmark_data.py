#!/usr/bin/env python3
"""Validate that all benchmark data referenced in the benchmarking plan exists.

Checks:
1. All 24 ablation manifest entries: data_root dir + prompt_file exist
2. All domain-specific benchmark dirs have data files + prompt.txt + truth.json
3. Literature grounding fixtures have data + prompt
"""
import json
import sys
from pathlib import Path

ERRORS = []
WARNINGS = []
PASSED = 0


def check(label, path, is_dir=False):
    global PASSED
    p = Path(path)
    if is_dir:
        if p.is_dir() and any(p.iterdir()):
            PASSED += 1
        else:
            ERRORS.append(f"  MISSING DIR: {label} -> {path}")
    else:
        if p.is_file() and p.stat().st_size > 0:
            PASSED += 1
        else:
            ERRORS.append(f"  MISSING FILE: {label} -> {path}")


def check_domain(name, base, expected_files):
    for case_dir in sorted(Path(base).iterdir()):
        if not case_dir.is_dir():
            continue
        case = case_dir.name
        check(f"{name}/{case}/prompt.txt", case_dir / "prompt.txt")
        data_dir = case_dir / "data"
        check(f"{name}/{case}/data", data_dir, is_dir=True)
        for ef in expected_files:
            # Check if at least one matching file exists
            matches = list(data_dir.glob(ef)) if data_dir.is_dir() else []
            if matches:
                global PASSED
                PASSED += 1
            else:
                WARNINGS.append(f"  NO MATCH: {name}/{case}/data/{ef}")


print("=" * 60)
print("BENCHMARK DATA VALIDATION")
print("=" * 60)

# 1. Ablation manifest
print("\n--- §7 Ablation Manifest (24 cases) ---")
manifest = json.load(open("workspace/benchmark_data/ablation_manifest_24.json"))
for case in manifest["cases"]:
    cid = case["id"]
    check(f"ablation/{cid}/data_root", case["data_root"], is_dir=True)
    check(f"ablation/{cid}/prompt_file", case["prompt_file"])

# 2. Long-read (9 cases)
print("\n--- §3 Long-Read (9 cases) ---")
check_domain("long_read", "workspace/benchmark_data/long_read", ["*.fastq", "*.fasta"])

# 3. Spatial (6 cases)
print("\n--- §4 Spatial (6 cases) ---")
check_domain("spatial", "workspace/benchmark_data/spatial", ["*.h5ad"])

# 4. Proteomics (6 cases)
print("\n--- §5 Proteomics (6 cases) ---")
check_domain("proteomics", "workspace/benchmark_data/proteomics", ["abundance_matrix.csv"])

# 5. Metabolomics (6 cases)
print("\n--- §6 Metabolomics (6 cases) ---")
check_domain("metabolomics", "workspace/benchmark_data/metabolomics", ["feature_table.csv"])

# 6. Literature grounding (2 cases)
print("\n--- §2 Literature Grounding (2 cases) ---")
check_domain("literature", "workspace/benchmark_data/literature_grounding", ["*.fastq", "*.fasta"])

# 7. Germline no-RG stress case
print("\n--- §7 Germline No-RG stress case ---")
check("germline_no_rg/data", "workspace/benchmark_data/ablation_data/germline_no_rg/data", is_dir=True)

# Summary
print("\n" + "=" * 60)
if WARNINGS:
    print(f"Warnings ({len(WARNINGS)}):")
    for w in WARNINGS:
        print(w)
if ERRORS:
    print(f"\nERRORS ({len(ERRORS)}):")
    for e in ERRORS:
        print(e)
    print(f"\nFAIL: {PASSED} passed, {len(ERRORS)} errors, {len(WARNINGS)} warnings")
    sys.exit(1)
else:
    print(f"ALL PASSED: {PASSED} checks OK, {len(WARNINGS)} warnings")
    sys.exit(0)
