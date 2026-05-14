#!/usr/bin/env python3
"""Validate comparative genomics benchmark results.

Usage:
    python3 scripts/validate_comparative_genomics.py truth.json output_dir

Checks:
  1. Distance matrix CSV exists with correct dimensions
  2. ANI values within expected ranges for each pair
  3. Closest pair correctly identified
  4. Summary TSV has expected number of pairs with non-zero aligned fraction
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


def _find_name(short: str, names: list[str]) -> str | None:
    """Fuzzy match a short name like 'ecoli_k12' against matrix genome names."""
    short_norm = short.replace("-", "_").replace(".", "_").lower()
    for n in names:
        n_norm = n.replace("-", "_").replace(".", "_").lower()
        if short_norm in n_norm or n_norm in short_norm:
            return n
    return None


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <truth.json> <output_dir>")
        return 1

    truth_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])

    if not truth_path.exists():
        print(f"ERROR: Truth file not found: {truth_path}")
        return 1
    if not output_dir.exists():
        print(f"ERROR: Output directory not found: {output_dir}")
        return 1

    truth = json.loads(truth_path.read_text())

    print("=" * 60)
    print("Comparative Genomics Benchmark Validation")
    print("=" * 60)
    print(f"Truth: {truth_path}")
    print(f"Output: {output_dir}")
    print()

    checks_passed = 0
    checks_total = 0

    # Check 1: Distance matrix exists with correct dimensions
    checks_total += 1
    dm_path = output_dir / "distance_matrix.csv"
    print("Check 1: Distance matrix dimensions")
    if not dm_path.exists():
        print(f"  Distance matrix not found: {dm_path}")
        print("  ✗ FAIL")
    else:
        with open(dm_path, newline="") as f:
            rows = list(csv.reader(f))
        n_genomes = len(truth["genomes"])
        expected_rows = n_genomes + 1  # header + data
        if len(rows) == expected_rows and len(rows[0]) == n_genomes + 1:
            genome_names = rows[0][1:]
            print(f"  Dimensions: {n_genomes}×{n_genomes}")
            print(f"  Genome names: {genome_names}")
            print("  ✓ PASS")
            checks_passed += 1
        else:
            print(f"  Got {len(rows)} rows, {len(rows[0]) if rows else 0} cols (expected {expected_rows}×{n_genomes+1})")
            print("  ✗ FAIL")
            genome_names = rows[0][1:] if rows else []
    print()

    # Parse matrix for later checks
    ani_matrix: dict[tuple[str, str], float] = {}
    if dm_path.exists():
        with open(dm_path, newline="") as f:
            rows = list(csv.reader(f))
        if len(rows) > 1:
            header = rows[0][1:]
            for row in rows[1:]:
                name = row[0]
                for j, val in enumerate(row[1:]):
                    try:
                        ani_matrix[(name, header[j])] = float(val)
                    except (ValueError, IndexError):
                        pass

    # Check 2: ANI values within expected ranges
    checks_total += 1
    print("Check 2: ANI values within expected ranges")
    all_ranges_ok = True
    for pair in truth["expected_pairs"]:
        ga = pair["genome_a"]
        gb = pair["genome_b"]
        ani_min = pair["ani_min"]
        ani_max = pair["ani_max"]

        ma = _find_name(ga, list({k[0] for k in ani_matrix} | {k[1] for k in ani_matrix}))
        mb = _find_name(gb, list({k[0] for k in ani_matrix} | {k[1] for k in ani_matrix}))

        if ma is None or mb is None:
            print(f"  {ga} <-> {gb}: could not find in matrix")
            all_ranges_ok = False
            continue

        ani = ani_matrix.get((ma, mb), ani_matrix.get((mb, ma), -1.0))
        in_range = ani_min <= ani <= ani_max
        mark = "✓" if in_range else "✗"
        print(f"  {ga} <-> {gb}: ANI={ani:.4f}  range=[{ani_min:.2f}, {ani_max:.2f}]  {mark}")
        if not in_range:
            all_ranges_ok = False

    if all_ranges_ok:
        print("  ✓ PASS")
        checks_passed += 1
    else:
        print("  ✗ FAIL")
    print()

    # Check 3: Closest pair
    checks_total += 1
    closest_path = output_dir / "closest_pair.txt"
    print("Check 3: Closest pair identification")
    if not closest_path.exists():
        print(f"  closest_pair.txt not found: {closest_path}")
        print("  ✗ FAIL")
    else:
        line = closest_path.read_text().strip()
        parts = line.split("\t")
        if len(parts) >= 2:
            expected = set(truth["closest_pair"])
            found = set()
            for p in parts[:2]:
                for exp in expected:
                    if _find_name(exp, [p]) is not None:
                        found.add(exp)
            if found == expected:
                print(f"  Found: {parts[0]}, {parts[1]}")
                print("  ✓ PASS")
                checks_passed += 1
            else:
                print(f"  Found: {parts[0]}, {parts[1]}")
                print(f"  Expected: {truth['closest_pair']}")
                print("  ✗ FAIL")
        else:
            print(f"  Unexpected format: {line!r}")
            print("  ✗ FAIL")
    print()

    # Check 4: Summary completeness
    checks_total += 1
    summary_path = output_dir / "summary.tsv"
    print("Check 4: Summary completeness")
    if not summary_path.exists():
        print(f"  summary.tsv not found: {summary_path}")
        print("  ✗ FAIL")
    else:
        with open(summary_path) as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("genome_a")]
        n_pairs = len(lines)
        expected_min = truth["min_pairs_with_alignment"]
        zero_af = 0
        for line in lines:
            cols = line.split("\t")
            if len(cols) >= 4:
                try:
                    af = float(cols[3])
                    if af == 0.0:
                        zero_af += 1
                except ValueError:
                    pass
        ok = n_pairs >= expected_min and zero_af == 0
        print(f"  Pairs found: {n_pairs} (expected >= {expected_min})")
        if zero_af > 0:
            print(f"  Pairs with zero aligned fraction: {zero_af}")
        if ok:
            print("  ✓ PASS")
            checks_passed += 1
        else:
            print("  ✗ FAIL")
    print()

    # Print the full distance matrix for visibility
    if dm_path.exists():
        print("Distance Matrix:")
        with open(dm_path) as f:
            for line in f:
                print(f"  {line.strip()}")
        print()

    # Summary
    print("=" * 60)
    if checks_passed == checks_total:
        print(f"BENCHMARK PASSED ({checks_passed}/{checks_total} checks)")
    else:
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
    print("=" * 60)

    return 0 if checks_passed == checks_total else 1


if __name__ == "__main__":
    sys.exit(main())
