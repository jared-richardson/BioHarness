#!/usr/bin/env python3
"""Validate metagenomics classification benchmark results.

Usage:
    python3 scripts/validate_metagenomics.py truth.json kraken2_report.txt [contigs.fasta]

Checks:
  1. Classification rate >= min_classification_rate (from truth.json)
  2. All expected genera detected in Kraken2 report
  3. Assembly contigs exist and are non-empty (optional)
  4. Report format is valid Kraken2 standard format
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def parse_kraken2_report(report_path: Path) -> dict:
    """Parse standard Kraken2 report format.

    Format: pct  count_rooted  count_direct  rank  taxid  name
    Rank codes: U=unclassified, R=root, D=domain, P=phylum, C=class,
                O=order, F=family, G=genus, S=species
    """
    entries = []
    total_classified = 0
    total_unclassified = 0

    with open(report_path) as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 6:
                print(f"  [WARN] Line {line_no}: expected 6+ tab-separated fields, got {len(parts)}")
                continue

            pct = float(parts[0].strip())
            count_rooted = int(parts[1].strip())
            count_direct = int(parts[2].strip())
            rank = parts[3].strip()
            taxid = int(parts[4].strip())
            name = parts[5].strip()

            entry = {
                "pct": pct,
                "count_rooted": count_rooted,
                "count_direct": count_direct,
                "rank": rank,
                "taxid": taxid,
                "name": name,
            }
            entries.append(entry)

            if rank == "U":
                total_unclassified = count_rooted
            elif rank == "R":
                total_classified = count_rooted

    return {
        "entries": entries,
        "total_classified": total_classified,
        "total_unclassified": total_unclassified,
    }


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <truth.json> <kraken2_report.txt> [contigs.fasta]")
        return 1

    truth_path = Path(sys.argv[1])
    report_path = Path(sys.argv[2])
    contigs_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    if not truth_path.exists():
        print(f"ERROR: Truth file not found: {truth_path}")
        return 1
    if not report_path.exists():
        print(f"ERROR: Kraken2 report not found: {report_path}")
        return 1

    truth = json.loads(truth_path.read_text())
    min_rate = truth.get("min_classification_rate", 0.80)
    expected_genera = truth.get("expected_top_genus", [])

    print("=" * 60)
    print("Metagenomics Classification Benchmark Validation")
    print("=" * 60)
    print(f"Truth: {truth_path}")
    print(f"Report: {report_path}")
    if contigs_path:
        print(f"Contigs: {contigs_path}")
    print()

    # Parse report
    report = parse_kraken2_report(report_path)
    total_reads = report["total_classified"] + report["total_unclassified"]
    if total_reads == 0:
        # Fallback: use paired reads count (kraken2 reports reads, not pairs)
        # Try to get total from the report entries
        for entry in report["entries"]:
            if entry["rank"] == "U":
                total_reads = entry["count_rooted"]
                for e2 in report["entries"]:
                    if e2["rank"] == "R":
                        total_reads += e2["count_rooted"]
                        break
                break

    if total_reads == 0:
        print("ERROR: Could not determine total read count from report")
        return 1

    checks_passed = 0
    checks_total = 0

    # Check 1: Classification rate
    checks_total += 1
    classification_rate = report["total_classified"] / total_reads if total_reads > 0 else 0
    print("Check 1: Classification rate")
    print(f"  Total reads/pairs in report: {total_reads:,}")
    print(f"  Classified: {report['total_classified']:,}")
    print(f"  Unclassified: {report['total_unclassified']:,}")
    print(f"  Rate: {classification_rate:.2%} (threshold: {min_rate:.0%})")
    if classification_rate >= min_rate:
        print("  ✓ PASS")
        checks_passed += 1
    else:
        print("  ✗ FAIL")
    print()

    # Check 2: Genus detection
    checks_total += 1
    found_genera = set()
    genus_entries = [e for e in report["entries"] if e["rank"] == "G"]
    for entry in genus_entries:
        for expected in expected_genera:
            if expected.lower() in entry["name"].lower():
                found_genera.add(expected)
    missing_genera = set(expected_genera) - found_genera
    print("Check 2: Genus detection")
    print(f"  Expected: {', '.join(expected_genera)}")
    print(f"  Found: {', '.join(sorted(found_genera)) if found_genera else 'none'}")
    if missing_genera:
        print(f"  Missing: {', '.join(sorted(missing_genera))}")
        print("  ✗ FAIL")
    else:
        print("  ✓ PASS")
        checks_passed += 1
    print()

    # Check 3: Species-level abundance (informational)
    print("Species abundance (from report):")
    species_entries = [e for e in report["entries"] if e["rank"] == "S"]
    for entry in sorted(species_entries, key=lambda e: e["count_rooted"], reverse=True)[:10]:
        print(f"  {entry['name']}: {entry['pct']:.2f}% ({entry['count_rooted']:,} reads)")
    print()

    # Check 4: Assembly (optional)
    if contigs_path:
        checks_total += 1
        print("Check 3: Assembly contigs")
        if contigs_path.exists() and contigs_path.stat().st_size > 0:
            # Count contigs
            n_contigs = 0
            with open(contigs_path) as fh:
                for line in fh:
                    if line.startswith(">"):
                        n_contigs += 1
            print(f"  Contigs file: {contigs_path}")
            print(f"  Size: {contigs_path.stat().st_size:,} bytes")
            print(f"  Number of contigs: {n_contigs}")
            print("  ✓ PASS")
            checks_passed += 1
        else:
            print(f"  Contigs file missing or empty: {contigs_path}")
            print("  ✗ FAIL")
        print()

    # Check 5: Report format validation
    checks_total += 1
    print(f"Check {checks_total}: Report format")
    has_unclassified = any(e["rank"] == "U" for e in report["entries"])
    has_root = any(e["rank"] == "R" for e in report["entries"])
    has_species = any(e["rank"] == "S" for e in report["entries"])
    print(f"  Has unclassified line (U): {has_unclassified}")
    print(f"  Has root line (R): {has_root}")
    print(f"  Has species-level entries (S): {has_species}")
    if has_unclassified and has_species:
        print("  ✓ PASS")
        checks_passed += 1
    else:
        print("  ✗ FAIL")
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
