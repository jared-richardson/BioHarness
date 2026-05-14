#!/usr/bin/env python3
"""Validate bacterial evolution variant calling benchmark results.

Supports two modes:
  Benchmark mode: python3 scripts/validate_evolution.py truth.csv agent_output.csv
    Compares agent-called shared variants against known truth variants.

  Sanity mode:    python3 scripts/validate_evolution.py --sanity agent_output.csv
    Checks format, plausible variant counts, and internal consistency.
    Useful for novel data where no truth is available.

Checks (benchmark mode):
  1. Output format — CSV with required columns (CHROM, POS, REF, ALT)
  2. Variant recall — fraction of truth variants found in agent output
  3. Variant precision — fraction of agent variants that are true positives
  4. Impact annotation — HIGH/MODERATE/LOW/MODIFIER labels present

Checks (sanity mode):
  1. Output format — CSV/TSV with variant-like columns
  2. Variant count — at least 1 variant reported, not suspiciously many
  3. Variant plausibility — REF/ALT are valid DNA bases, POS is positive integer
  4. Annotation completeness — GENE, IMPACT, EFFECT columns populated
"""

from __future__ import annotations

import csv
import re as _re
import sys
from pathlib import Path


def parse_variants_csv(path: Path) -> list[dict]:
    """Parse a CSV/TSV with variant columns. Auto-detects delimiter."""
    text = path.read_text()
    # Auto-detect delimiter
    first_line = text.strip().split("\n")[0]
    delimiter = "\t" if "\t" in first_line else ","

    rows = []
    reader = csv.DictReader(text.strip().split("\n"), delimiter=delimiter)
    for row in reader:
        # Normalize column names (lowercase, strip whitespace)
        normed = {k.strip().lower(): v.strip() for k, v in row.items()}
        rows.append(normed)
    return rows

def _extract_node_number(chrom: str) -> str | None:
    """Extract the NODE number from a SPAdes contig name like NODE_4_length_102125_cov_7.39."""
    m = _re.match(r"NODE_(\d+)", chrom.strip())
    return m.group(1) if m else None


def normalize_variant(chrom: str, pos: str, ref: str, alt: str) -> tuple[str, int, str, str]:
    """Normalize a variant for comparison. Strips whitespace, uppercases alleles."""
    return (chrom.strip(), int(pos.strip()), ref.strip().upper(), alt.strip().upper())


def _chroms_equivalent(chrom1: str, chrom2: str) -> bool:
    """Check if two contig names refer to the same genomic region.

    SPAdes assembly is non-deterministic: different runs produce contigs with
    the same NODE number but different length/coverage suffixes.
    E.g. NODE_4_length_102125_cov_7.399194 ≡ NODE_4_length_102007_cov_6.351643
    """
    if chrom1 == chrom2:
        return True
    node1 = _extract_node_number(chrom1)
    node2 = _extract_node_number(chrom2)
    if node1 is not None and node2 is not None and node1 == node2:
        return True
    return False


def variants_match(v1: tuple, v2: tuple, pos_tolerance: int = 5) -> bool:
    """Check if two variants match.

    Uses fuzzy contig matching (NODE number) to handle SPAdes non-determinism,
    and position tolerance for slight assembly differences.
    """
    chrom1, pos1, ref1, alt1 = v1
    chrom2, pos2, ref2, alt2 = v2

    if not _chroms_equivalent(chrom1, chrom2):
        return False

    # Exact allele match with position tolerance
    if abs(pos1 - pos2) <= pos_tolerance and ref1 == ref2 and alt1 == alt2:
        return True

    # Handle VCF normalization: different left-alignment of same indel.
    if abs(pos1 - pos2) <= max(pos_tolerance, 1):
        min_ref = min(len(ref1), len(ref2))
        min_alt = min(len(alt1), len(alt2))
        if min_ref > 0 and min_alt > 0:
            if ref1[0] == ref2[0] and alt1[0] == alt2[0]:
                if ref1[1:] == ref2[1:] and alt1[1:] == alt2[1:]:
                    return True
    return False


def run_benchmark(truth_path: Path, output_path: Path) -> int:
    """Benchmark mode: compare against truth."""
    print("=" * 60)
    print("Bacterial Evolution Variant Calling — Benchmark Validation")
    print("=" * 60)
    print(f"Truth:  {truth_path}")
    print(f"Output: {output_path}")
    print()

    truth_rows = parse_variants_csv(truth_path)
    agent_rows = parse_variants_csv(output_path)

    checks_passed = 0
    checks_total = 0

    # Check 1: Output format
    checks_total += 1
    required_cols = {"chrom", "pos", "ref", "alt"}
    agent_cols = set(agent_rows[0].keys()) if agent_rows else set()
    missing = required_cols - agent_cols
    print("Check 1: Output format")
    if missing:
        print(f"  Missing columns: {', '.join(sorted(missing))}")
        print("  \u2717 FAIL")
        print()
    else:
        print(f"  Columns found: {', '.join(sorted(agent_cols))}")
        print(f"  Variants reported: {len(agent_rows)}")
        print("  \u2713 PASS")
        checks_passed += 1
    print()

    if not agent_rows or missing:
        print("=" * 60)
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
        print("=" * 60)
        return 1

    # Parse variants for comparison
    truth_variants = []
    for r in truth_rows:
        try:
            truth_variants.append(normalize_variant(r["chrom"], r["pos"], r["ref"], r["alt"]))
        except (KeyError, ValueError):
            continue

    agent_variants = []
    for r in agent_rows:
        try:
            agent_variants.append(normalize_variant(r["chrom"], r["pos"], r["ref"], r["alt"]))
        except (KeyError, ValueError):
            continue

    # Check 2: Variant recall
    checks_total += 1
    matched_truth = set()
    matched_agent = set()
    for i, tv in enumerate(truth_variants):
        for j, av in enumerate(agent_variants):
            if variants_match(tv, av, pos_tolerance=1):
                matched_truth.add(i)
                matched_agent.add(j)
                break

    recall = len(matched_truth) / len(truth_variants) if truth_variants else 0
    recall_threshold = 0.90
    print("Check 2: Variant recall")
    print(f"  Truth variants: {len(truth_variants)}")
    print(f"  Matched: {len(matched_truth)}/{len(truth_variants)}")
    print(f"  Recall: {recall:.1%} (threshold: {recall_threshold:.0%})")
    # Show which truth variants were not matched
    if len(matched_truth) < len(truth_variants):
        for i, tv in enumerate(truth_variants):
            if i not in matched_truth:
                print(f"    MISSED: {tv[0]} pos={tv[1]} {tv[2]}>{tv[3]}")
    if recall >= recall_threshold:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 3: Variant precision
    checks_total += 1
    precision = len(matched_agent) / len(agent_variants) if agent_variants else 0
    precision_threshold = 0.50
    print("Check 3: Variant precision")
    print(f"  Agent variants: {len(agent_variants)}")
    print(f"  True positives: {len(matched_agent)}")
    print(f"  Precision: {precision:.1%} (threshold: {precision_threshold:.0%})")
    if precision >= precision_threshold:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 4: Impact annotation (informational + check)
    checks_total += 1
    has_impact = "impact" in agent_cols
    has_effect = "effect" in agent_cols
    has_gene = "gene" in agent_cols
    print("Check 4: Annotation completeness")
    print(f"  Has IMPACT column: {has_impact}")
    print(f"  Has EFFECT column: {has_effect}")
    print(f"  Has GENE column: {has_gene}")
    if has_impact:
        impacts = set(r.get("impact", "").upper() for r in agent_rows)
        print(f"  Impact values: {', '.join(sorted(impacts))}")
    if has_impact and has_effect:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Summary
    print("=" * 60)
    if checks_passed == checks_total:
        print(f"BENCHMARK PASSED ({checks_passed}/{checks_total} checks)")
    else:
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
    print("=" * 60)
    return 0 if checks_passed == checks_total else 1


def run_sanity(output_path: Path) -> int:
    """Sanity mode: check format and plausibility without truth data."""
    print("=" * 60)
    print("Bacterial Evolution Variant Calling — Sanity Check")
    print("=" * 60)
    print(f"Output: {output_path}")
    print("(No truth data — checking format and plausibility only)")
    print()

    agent_rows = parse_variants_csv(output_path)

    checks_passed = 0
    checks_total = 0

    # Check 1: Basic format
    checks_total += 1
    print("Check 1: Output format")
    if not agent_rows:
        print("  No data rows found")
        print("  \u2717 FAIL")
    else:
        cols = set(agent_rows[0].keys())
        has_pos = any(c in cols for c in ("pos", "position"))
        has_ref = "ref" in cols
        has_alt = "alt" in cols
        print(f"  Columns: {', '.join(sorted(cols))}")
        print(f"  Rows: {len(agent_rows)}")
        print(f"  Has position: {has_pos}, Has REF: {has_ref}, Has ALT: {has_alt}")
        if has_pos and has_ref and has_alt:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL — missing essential variant columns")
    print()

    # Check 2: Variant count plausibility
    checks_total += 1
    print("Check 2: Variant count plausibility")
    n = len(agent_rows)
    print(f"  Variants reported: {n}")
    if 1 <= n <= 10000:
        print("  Plausible range (1-10000)")
        print("  \u2713 PASS")
        checks_passed += 1
    elif n == 0:
        print("  Zero variants — pipeline may have failed")
        print("  \u2717 FAIL")
    else:
        print(f"  Suspiciously high count ({n}) — may include noise")
        print("  \u2717 FAIL")
    print()

    # Check 3: Allele validity
    checks_total += 1
    valid_bases = set("ACGTNacgtn")
    bad_alleles = 0
    bad_pos = 0
    for r in agent_rows:
        ref = r.get("ref", "")
        alt = r.get("alt", "")
        pos = r.get("pos", r.get("position", "0"))
        if ref and not all(c in valid_bases for c in ref):
            bad_alleles += 1
        if alt and not all(c in valid_bases for c in alt):
            bad_alleles += 1
        try:
            p = int(pos)
            if p < 1:
                bad_pos += 1
        except ValueError:
            bad_pos += 1

    print("Check 3: Allele and position validity")
    print(f"  Invalid alleles: {bad_alleles}/{len(agent_rows)*2}")
    print(f"  Invalid positions: {bad_pos}/{len(agent_rows)}")
    if bad_alleles == 0 and bad_pos == 0:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 4: Annotation completeness
    checks_total += 1
    cols = set(agent_rows[0].keys()) if agent_rows else set()
    annotation_cols = {"gene", "impact", "effect"}
    found_ann = annotation_cols & cols
    print("Check 4: Annotation completeness")
    print(f"  Annotation columns found: {', '.join(sorted(found_ann)) if found_ann else 'none'}")
    if found_ann:
        # Check that annotation values are non-empty
        empty_count = sum(1 for r in agent_rows if not r.get("impact", "").strip())
        print(f"  Empty IMPACT values: {empty_count}/{len(agent_rows)}")
        if len(found_ann) >= 2 and empty_count < len(agent_rows) * 0.5:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL — insufficient annotations")
    else:
        print("  \u2717 FAIL — no annotation columns (GENE, IMPACT, EFFECT)")
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
        print(f"  {sys.argv[0]} <truth.csv> <agent_output.csv>   # Benchmark mode")
        print(f"  {sys.argv[0]} --sanity <agent_output.csv>       # Sanity mode (no truth)")
        return 1

    if sys.argv[1] == "--sanity":
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} --sanity <agent_output.csv>")
            return 1
        return run_sanity(Path(sys.argv[2]))

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <truth.csv> <agent_output.csv>")
        return 1

    truth_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not truth_path.exists():
        print(f"ERROR: Truth file not found: {truth_path}")
        return 1
    if not output_path.exists():
        print(f"ERROR: Output file not found: {output_path}")
        return 1

    return run_benchmark(truth_path, output_path)


if __name__ == "__main__":
    sys.exit(main())
