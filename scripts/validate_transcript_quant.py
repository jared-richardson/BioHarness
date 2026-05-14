#!/usr/bin/env python3
"""Validate transcript quantification benchmark results.

Supports two modes:
  Benchmark mode: python3 scripts/validate_transcript_quant.py truth.tsv quant.sf
    Compares estimated counts against known truth via Pearson correlation.

  Sanity mode:    python3 scripts/validate_transcript_quant.py --sanity quant.sf
    Checks format, count distribution, and internal consistency.
    Useful for novel data where no truth is available.

Checks (benchmark mode):
  1. Output format — TSV/CSV with transcript ID and count columns
  2. Transcript recovery — fraction of truth transcripts found in output
  3. Count correlation — Pearson r between truth and estimated counts
  4. Total count consistency — sum of counts within plausible range

Checks (sanity mode):
  1. Output format — recognized quantification format (Salmon/Kallisto/custom)
  2. Transcript count — plausible number of transcripts quantified
  3. Count distribution — non-degenerate (not all zeros, reasonable dynamic range)
  4. TPM normalization — TPM sums approximately to 1,000,000 (if column present)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path


def parse_quant_file(path: Path) -> dict[str, dict]:
    """Parse a quantification file (Salmon quant.sf, Kallisto abundance.tsv, or generic TSV/CSV).

    Returns {transcript_id: {"count": float, "tpm": float, "length": float}}.
    """
    text = path.read_text()
    lines = text.strip().split("\n")
    if not lines:
        return {}

    # Auto-detect delimiter
    first_line = lines[0]
    delimiter = "\t" if "\t" in first_line else ","

    header = first_line.split(delimiter)
    header_lower = [h.strip().lower() for h in header]

    # Find columns by common names
    id_col = None
    count_col = None
    tpm_col = None
    length_col = None

    for i, h in enumerate(header_lower):
        if h in ("name", "target_id", "transcript_id", "transcript"):
            id_col = i
        elif h in ("numreads", "est_counts", "count", "expected_count", "counts"):
            count_col = i
        elif h in ("tpm",):
            tpm_col = i
        elif h in ("length", "eff_length", "effectivelength"):
            if length_col is None:
                length_col = i

    # Fallback: if no recognized ID column, use first column
    if id_col is None:
        id_col = 0
    if count_col is None:
        # Try second column as count (simple truth.tsv format: id\tcount)
        if len(header) == 2:
            count_col = 1

    results = {}
    for line in lines[1:]:
        parts = line.split(delimiter)
        if len(parts) <= max(id_col, count_col or 0):
            continue
        tid = parts[id_col].strip()
        count = 0.0
        tpm = 0.0
        length = 0.0
        if count_col is not None:
            try:
                count = float(parts[count_col].strip())
            except ValueError:
                continue
        if tpm_col is not None:
            try:
                tpm = float(parts[tpm_col].strip())
            except ValueError:
                pass
        if length_col is not None:
            try:
                length = float(parts[length_col].strip())
            except ValueError:
                pass
        results[tid] = {"count": count, "tpm": tpm, "length": length}

    return results


def pearson_r(x: list[float], y: list[float]) -> float:
    """Compute Pearson correlation coefficient (pure Python, no numpy)."""
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / n)
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / n)
    if sx == 0 or sy == 0:
        return 0.0
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
    return cov / (sx * sy)


def run_benchmark(truth_path: Path, output_path: Path) -> int:
    """Benchmark mode: compare against truth."""
    print("=" * 60)
    print("Transcript Quantification — Benchmark Validation")
    print("=" * 60)
    print(f"Truth:  {truth_path}")
    print(f"Output: {output_path}")
    print()

    truth = parse_quant_file(truth_path)
    agent = parse_quant_file(output_path)

    checks_passed = 0
    checks_total = 0

    # Check 1: Output format
    checks_total += 1
    print("Check 1: Output format")
    print(f"  Transcripts in truth: {len(truth)}")
    print(f"  Transcripts in output: {len(agent)}")
    if len(agent) > 0:
        sample_key = next(iter(agent))
        sample_val = agent[sample_key]
        print(f"  Sample entry: {sample_key} -> count={sample_val['count']:.1f}")
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  No transcripts parsed from output file")
        print("  \u2717 FAIL")
    print()

    if not agent:
        print("=" * 60)
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
        print("=" * 60)
        return 1

    # Check 2: Transcript recovery
    checks_total += 1
    common = set(truth.keys()) & set(agent.keys())
    recovery = len(common) / len(truth) if truth else 0
    recovery_threshold = 0.90
    print("Check 2: Transcript recovery")
    print(f"  Common transcripts: {len(common)}/{len(truth)}")
    print(f"  Recovery: {recovery:.1%} (threshold: {recovery_threshold:.0%})")
    if recovery >= recovery_threshold:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
        if len(common) < 5:
            # Show some IDs to help debug format mismatch
            print(f"  Truth IDs (first 3): {list(truth.keys())[:3]}")
            print(f"  Agent IDs (first 3): {list(agent.keys())[:3]}")
    print()

    # Check 3: Count correlation
    checks_total += 1
    truth_counts = []
    agent_counts = []
    for tid in common:
        truth_counts.append(truth[tid]["count"])
        agent_counts.append(agent[tid]["count"])

    r = pearson_r(truth_counts, agent_counts) if common else 0
    r_threshold = 0.95
    print("Check 3: Count correlation (Pearson r)")
    print(f"  Compared on {len(common)} transcripts")
    print(f"  Pearson r: {r:.6f} (threshold: {r_threshold})")
    # Show top/bottom deviations
    if common:
        deviations = [(tid, truth[tid]["count"], agent[tid]["count"],
                       abs(truth[tid]["count"] - agent[tid]["count"]))
                      for tid in common]
        deviations.sort(key=lambda x: -x[3])
        print("  Largest deviations:")
        for tid, tc, ac, dev in deviations[:3]:
            print(f"    {tid}: truth={tc:.0f}, agent={ac:.1f}, diff={dev:.1f}")
    if r >= r_threshold:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 4: Total count consistency
    checks_total += 1
    total_truth = sum(truth[tid]["count"] for tid in common)
    total_agent = sum(agent[tid]["count"] for tid in common)
    ratio = total_agent / total_truth if total_truth > 0 else 0
    print("Check 4: Total count consistency")
    print(f"  Truth total (matched): {total_truth:,.0f}")
    print(f"  Agent total (matched): {total_agent:,.1f}")
    print(f"  Ratio: {ratio:.4f}")
    if 0.5 <= ratio <= 2.0:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL — total counts differ by more than 2x")
    print()

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
    print("Transcript Quantification — Sanity Check")
    print("=" * 60)
    print(f"Output: {output_path}")
    print("(No truth data — checking format and plausibility only)")
    print()

    agent = parse_quant_file(output_path)

    checks_passed = 0
    checks_total = 0

    # Check 1: Recognized format
    checks_total += 1
    print("Check 1: Output format")
    if not agent:
        print("  Could not parse any transcripts from file")
        print("  \u2717 FAIL")
    else:
        sample = next(iter(agent.values()))
        print(f"  Transcripts parsed: {len(agent)}")
        print(f"  Sample: count={sample['count']:.1f}, tpm={sample['tpm']:.1f}")
        print("  \u2713 PASS")
        checks_passed += 1
    print()

    if not agent:
        print("=" * 60)
        print(f"SANITY CHECK: {checks_passed}/{checks_total} checks passed")
        print("=" * 60)
        return 1

    # Check 2: Transcript count plausibility
    checks_total += 1
    n = len(agent)
    print("Check 2: Transcript count plausibility")
    print(f"  Transcripts quantified: {n}")
    if 10 <= n <= 500000:
        print("  Plausible range for a transcriptome")
        print("  \u2713 PASS")
        checks_passed += 1
    elif n < 10:
        print("  Very few transcripts — possible pipeline issue")
        print("  \u2717 FAIL")
    else:
        print("  Very high count — may include non-coding features")
        print("  \u2717 FAIL")
    print()

    # Check 3: Count distribution
    checks_total += 1
    counts = [v["count"] for v in agent.values()]
    nonzero = sum(1 for c in counts if c > 0)
    max_count = max(counts)
    min_nonzero = min((c for c in counts if c > 0), default=0)
    dynamic_range = math.log10(max_count / min_nonzero) if min_nonzero > 0 else 0
    print("Check 3: Count distribution")
    print(f"  Non-zero counts: {nonzero}/{len(counts)} ({nonzero/len(counts)*100:.0f}%)")
    print(f"  Range: {min_nonzero:.1f} to {max_count:.1f} (dynamic range: {dynamic_range:.1f} decades)")
    if nonzero > 0 and dynamic_range >= 1.0:
        print("  \u2713 PASS")
        checks_passed += 1
    elif nonzero == 0:
        print("  \u2717 FAIL — all counts are zero")
    else:
        print("  \u2717 FAIL — insufficient dynamic range")
    print()

    # Check 4: TPM normalization (if available)
    checks_total += 1
    tpms = [v["tpm"] for v in agent.values()]
    tpm_sum = sum(tpms)
    print("Check 4: TPM normalization")
    if tpm_sum > 0:
        print(f"  TPM sum: {tpm_sum:,.0f} (expected ~1,000,000)")
        if 900000 <= tpm_sum <= 1100000:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL — TPM does not sum to ~1M")
    else:
        print("  No TPM column detected — skipping (PASS by default)")
        print("  \u2713 PASS")
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
        print(f"  {sys.argv[0]} <truth.tsv> <quant.sf>   # Benchmark mode")
        print(f"  {sys.argv[0]} --sanity <quant.sf>       # Sanity mode (no truth)")
        return 1

    if sys.argv[1] == "--sanity":
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} --sanity <quant.sf>")
            return 1
        return run_sanity(Path(sys.argv[2]))

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <truth.tsv> <quant.sf>")
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
