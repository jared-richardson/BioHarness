#!/usr/bin/env python3
"""Validate viral metagenomics benchmark results.

Usage:
    python3 scripts/validate_viral_metagenomics.py truth.json output_dir/

Checks:
  1. Classification report format — classification_report.tsv exists with correct columns
  2. Virus detection — All expected viruses in detected_viruses.txt
  3. Abundance proportions — Each virus's relative_abundance within truth range
  4. Coverage thresholds — Each virus's coverage_pct >= min_coverage_pct
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def parse_classification_report(report_path: Path) -> list[dict]:
    """Parse classification_report.tsv into list of dicts."""
    entries = []
    with open(report_path) as fh:
        header = fh.readline().strip().split("\t")
        for line in fh:
            vals = line.strip().split("\t")
            if len(vals) < len(header):
                continue
            entry = {}
            for h, v in zip(header, vals):
                try:
                    entry[h] = float(v) if "." in v else int(v)
                except ValueError:
                    entry[h] = v
            entries.append(entry)
    return entries


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
    expected_viruses = set(truth.get("expected_viruses", []))
    expected_abundances = truth.get("expected_abundances", {})
    min_coverage = truth.get("min_coverage_pct", 50.0)
    min_detected = truth.get("min_detected_viruses", 3)

    print("=" * 60)
    print("Viral Metagenomics Benchmark Validation")
    print("=" * 60)
    print(f"Truth: {truth_path}")
    print(f"Output: {output_dir}")
    print()

    checks_passed = 0
    checks_total = 0

    # ── Check 1: Classification report format ──────────────────────────
    checks_total += 1
    report_path = output_dir / "classification_report.tsv"
    print("Check 1: Classification report format")
    required_cols = {"virus_name", "ref_length", "mapped_reads", "covered_bases", "coverage_pct", "relative_abundance"}
    if not report_path.exists():
        print(f"  classification_report.tsv not found in {output_dir}")
        print("  \u2717 FAIL")
    else:
        with open(report_path) as f:
            header_line = f.readline().strip()
        cols = set(header_line.split("\t"))
        missing = required_cols - cols
        if missing:
            print(f"  Missing columns: {', '.join(sorted(missing))}")
            print("  \u2717 FAIL")
        else:
            entries = parse_classification_report(report_path)
            print(f"  Columns: {', '.join(sorted(cols))}")
            print(f"  Entries: {len(entries)}")
            print("  \u2713 PASS")
            checks_passed += 1
    print()

    # ── Check 2: Virus detection ───────────────────────────────────────
    checks_total += 1
    detected_path = output_dir / "detected_viruses.txt"
    print("Check 2: Virus detection")
    if not detected_path.exists():
        print(f"  detected_viruses.txt not found in {output_dir}")
        print("  \u2717 FAIL")
    else:
        detected = set()
        for line in detected_path.read_text().strip().splitlines():
            detected.add(line.strip())
        print(f"  Expected: {', '.join(sorted(expected_viruses))}")
        print(f"  Detected: {', '.join(sorted(detected)) if detected else 'none'}")
        missing_viruses = expected_viruses - detected
        if missing_viruses:
            print(f"  Missing: {', '.join(sorted(missing_viruses))}")
            print("  \u2717 FAIL")
        elif len(detected) < min_detected:
            print(f"  Only {len(detected)} detected (need {min_detected})")
            print("  \u2717 FAIL")
        else:
            print("  \u2713 PASS")
            checks_passed += 1
    print()

    # Parse report for checks 3+4
    if report_path.exists():
        entries = parse_classification_report(report_path)
        report_by_virus = {e["virus_name"]: e for e in entries}
    else:
        report_by_virus = {}

    # ── Check 3: Abundance proportions ─────────────────────────────────
    checks_total += 1
    print("Check 3: Abundance proportions")
    if not report_by_virus:
        print("  No classification report to check")
        print("  \u2717 FAIL")
    else:
        abundance_ok = True
        for virus_acc, bounds in expected_abundances.items():
            entry = report_by_virus.get(virus_acc, {})
            abundance = entry.get("relative_abundance", 0.0)
            lo, hi = bounds["min"], bounds["max"]
            in_range = lo <= abundance <= hi
            status = "\u2713" if in_range else "\u2717"
            print(f"  {virus_acc}: {abundance:.4f} (expected {lo:.2f}-{hi:.2f}) {status}")
            if not in_range:
                abundance_ok = False
        if abundance_ok:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL")
    print()

    # ── Check 4: Coverage thresholds ───────────────────────────────────
    checks_total += 1
    print(f"Check 4: Coverage thresholds (>= {min_coverage:.0f}%)")
    if not report_by_virus:
        print("  No classification report to check")
        print("  \u2717 FAIL")
    else:
        coverage_ok = True
        for virus_acc in sorted(expected_viruses):
            entry = report_by_virus.get(virus_acc, {})
            cov = entry.get("coverage_pct", 0.0)
            ok = cov >= min_coverage
            status = "\u2713" if ok else "\u2717"
            print(f"  {virus_acc}: {cov:.1f}% {status}")
            if not ok:
                coverage_ok = False
        if coverage_ok:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL")
    print()

    # Show full report for reference
    if report_by_virus:
        print("Full classification report:")
        for entry in entries:
            print(f"  {entry['virus_name']}: {entry.get('mapped_reads', 0)} reads, "
                  f"{entry.get('coverage_pct', 0):.1f}% coverage, "
                  f"{entry.get('relative_abundance', 0):.4f} abundance")
        print()

    # ── Summary ────────────────────────────────────────────────────────
    print("=" * 60)
    if checks_passed == checks_total:
        print(f"BENCHMARK PASSED ({checks_passed}/{checks_total} checks)")
    else:
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
    print("=" * 60)

    return 0 if checks_passed == checks_total else 1


if __name__ == "__main__":
    sys.exit(main())
