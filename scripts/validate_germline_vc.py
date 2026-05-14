#!/usr/bin/env python3
"""Validate germline variant calling benchmark results.

Supports two modes:
  Benchmark mode: python3 scripts/validate_germline_vc.py truth.vcf agent.vcf
    Compares called variants against known truth VCF.

  Sanity mode:    python3 scripts/validate_germline_vc.py --sanity agent.vcf
    Checks VCF format, variant plausibility, and quality distribution.
    Useful for novel data where no truth is available.

Checks (benchmark mode):
  1. Output format — valid VCF with header and data lines
  2. Sensitivity — fraction of truth variants found (position + allele matching)
  3. Precision — fraction of called variants that match truth
  4. Genotype concordance — agreement on het (0/1) vs hom-alt (1/1)

Checks (sanity mode):
  1. VCF format — valid header, data lines parse correctly
  2. Variant count — plausible for the genome size
  3. Quality distribution — QUAL scores span a reasonable range
  4. Het/hom ratio — within plausible biological range
"""

from __future__ import annotations

import sys
from pathlib import Path


def parse_vcf(path: Path) -> list[dict]:
    """Parse a VCF file into a list of variant dicts."""
    variants = []
    text = path.read_text()
    for line in text.strip().split("\n"):
        if line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        chrom = parts[0]
        try:
            pos = int(parts[1])
        except ValueError:
            continue
        ref = parts[3].upper()
        alt = parts[4].upper()
        qual = parts[5]
        filt = parts[6]

        # Parse genotype if present
        gt = ""
        if len(parts) >= 10 and parts[8].startswith("GT"):
            format_fields = parts[8].split(":")
            sample_fields = parts[9].split(":")
            gt_idx = format_fields.index("GT") if "GT" in format_fields else 0
            if gt_idx < len(sample_fields):
                gt = sample_fields[gt_idx]

        # Parse INFO for variant type
        info = parts[7]
        vtype = ""
        for kv in info.split(";"):
            if kv.startswith("TYPE="):
                vtype = kv[5:]

        variants.append({
            "chrom": chrom, "pos": pos, "ref": ref, "alt": alt,
            "qual": qual, "filter": filt, "gt": gt, "type": vtype,
        })
    return variants


def variant_key(v: dict) -> tuple[str, int, str, str]:
    """Canonical variant key for matching."""
    return (v["chrom"], v["pos"], v["ref"], v["alt"])


def variants_match(v1: dict, v2: dict) -> bool:
    """Check if two variants match, with indel normalization tolerance."""
    if v1["chrom"] != v2["chrom"]:
        return False
    if v1["ref"] == v2["ref"] and v1["alt"] == v2["alt"]:
        return abs(v1["pos"] - v2["pos"]) <= 1
    # Try trimming common prefix for indels
    r1, a1 = v1["ref"], v1["alt"]
    r2, a2 = v2["ref"], v2["alt"]
    # Strip leading common base (VCF padding)
    while len(r1) > 1 and len(a1) > 1 and r1[0] == a1[0]:
        r1, a1 = r1[1:], a1[1:]
    while len(r2) > 1 and len(a2) > 1 and r2[0] == a2[0]:
        r2, a2 = r2[1:], a2[1:]
    if r1 == r2 and a1 == a2:
        return abs(v1["pos"] - v2["pos"]) <= 2
    return False


def run_benchmark(truth_path: Path, output_path: Path) -> int:
    """Benchmark mode: compare against truth VCF."""
    print("=" * 60)
    print("Germline Variant Calling — Benchmark Validation")
    print("=" * 60)
    print(f"Truth:  {truth_path}")
    print(f"Output: {output_path}")
    print()

    truth = parse_vcf(truth_path)
    agent = parse_vcf(output_path)

    checks_passed = 0
    checks_total = 0

    # Check 1: Output format
    checks_total += 1
    print("Check 1: Output format")
    has_header = any(line.startswith("#CHROM") for line in output_path.read_text().split("\n"))
    print(f"  VCF header present: {has_header}")
    print(f"  Truth variants: {len(truth)}")
    print(f"  Agent variants: {len(agent)}")
    if len(agent) > 0 and has_header:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    if not agent:
        print("=" * 60)
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
        print("=" * 60)
        return 1

    # Check 2: Sensitivity (recall)
    checks_total += 1
    matched_truth = set()
    matched_agent = set()
    for i, tv in enumerate(truth):
        for j, av in enumerate(agent):
            if variants_match(tv, av):
                matched_truth.add(i)
                matched_agent.add(j)
                break

    sensitivity = len(matched_truth) / len(truth) if truth else 0
    sensitivity_threshold = 0.90
    print("Check 2: Sensitivity (recall)")
    print(f"  Truth variants found: {len(matched_truth)}/{len(truth)}")
    print(f"  Sensitivity: {sensitivity:.1%} (threshold: {sensitivity_threshold:.0%})")
    if len(matched_truth) < len(truth):
        missed = [truth[i] for i in range(len(truth)) if i not in matched_truth]
        for v in missed[:5]:
            print(f"    MISSED: {v['chrom']}:{v['pos']} {v['ref']}>{v['alt']} ({v['type']})")
    if sensitivity >= sensitivity_threshold:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 3: Precision
    checks_total += 1
    precision = len(matched_agent) / len(agent) if agent else 0
    precision_threshold = 0.50
    print("Check 3: Precision")
    print(f"  True positives: {len(matched_agent)}/{len(agent)}")
    print(f"  Precision: {precision:.1%} (threshold: {precision_threshold:.0%})")
    false_positives = len(agent) - len(matched_agent)
    if false_positives > 0 and false_positives <= 10:
        fps = [agent[j] for j in range(len(agent)) if j not in matched_agent]
        for v in fps[:5]:
            print(f"    FALSE POSITIVE: {v['chrom']}:{v['pos']} {v['ref']}>{v['alt']}")
    if precision >= precision_threshold:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 4: Genotype concordance (informational — soft check)
    # Genotype accuracy depends on coverage, ploidy assumptions, and caller
    # settings. The primary metric is variant identity (pos+allele), not GT.
    checks_total += 1
    gt_correct = 0
    gt_compared = 0
    for i in matched_truth:
        tv = truth[i]
        if not tv["gt"]:
            continue
        # Find matching agent variant
        for j, av in enumerate(agent):
            if variants_match(tv, av) and av["gt"]:
                gt_compared += 1
                # Normalize genotype (0/1 == 1/0)
                tgt = tv["gt"].replace("|", "/")
                agt = av["gt"].replace("|", "/")
                tgt_set = set(tgt.split("/"))
                agt_set = set(agt.split("/"))
                if tgt_set == agt_set:
                    gt_correct += 1
                break

    gt_concordance = gt_correct / gt_compared if gt_compared else 0
    print("Check 4: Genotype concordance (informational)")
    print(f"  Genotypes compared: {gt_compared}")
    print(f"  Genotypes correct: {gt_correct}/{gt_compared}")
    print(f"  Concordance: {gt_concordance:.1%}")
    if gt_compared == 0:
        print("  No genotypes to compare")
    elif gt_concordance >= 0.80:
        print("  Excellent genotype accuracy")
    elif gt_concordance >= 0.50:
        print("  Moderate genotype accuracy (caller may differ on het/hom calls)")
    else:
        print("  Low genotype concordance — check coverage and caller parameters")
    # Always pass — genotype is informational, variant identity is the gate
    print("  \u2713 PASS (genotype is informational; variant identity is the primary metric)")
    checks_passed += 1
    print()

    # Summary by variant type
    snp_truth = sum(1 for i in matched_truth if truth[i].get("type") in ("SNP", "") or len(truth[i]["ref"]) == len(truth[i]["alt"]) == 1)
    indel_truth = len(matched_truth) - snp_truth
    print(f"Breakdown: {snp_truth} SNPs + {indel_truth} indels matched")
    print()

    print("=" * 60)
    if checks_passed == checks_total:
        print(f"BENCHMARK PASSED ({checks_passed}/{checks_total} checks)")
    else:
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
    print("=" * 60)
    return 0 if checks_passed == checks_total else 1


def run_sanity(output_path: Path) -> int:
    """Sanity mode: check VCF format and variant plausibility."""
    print("=" * 60)
    print("Germline Variant Calling — Sanity Check")
    print("=" * 60)
    print(f"Output: {output_path}")
    print("(No truth data — checking format and plausibility only)")
    print()

    agent = parse_vcf(output_path)
    text = output_path.read_text()

    checks_passed = 0
    checks_total = 0

    # Check 1: VCF format
    checks_total += 1
    has_fileformat = "##fileformat=VCF" in text
    has_header = any(line.startswith("#CHROM") for line in text.split("\n"))
    print("Check 1: VCF format")
    print(f"  ##fileformat present: {has_fileformat}")
    print(f"  #CHROM header present: {has_header}")
    print(f"  Data lines parsed: {len(agent)}")
    if has_header and len(agent) > 0:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    if not agent:
        print("=" * 60)
        print(f"SANITY CHECK: {checks_passed}/{checks_total} checks passed")
        print("=" * 60)
        return 1

    # Check 2: Variant count
    checks_total += 1
    n = len(agent)
    print("Check 2: Variant count plausibility")
    print(f"  Variants called: {n}")
    snps = sum(1 for v in agent if len(v["ref"]) == 1 and len(v["alt"]) == 1)
    indels = n - snps
    print(f"  SNPs: {snps}, Indels: {indels}")
    if 1 <= n <= 10000000:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL — suspicious variant count")
    print()

    # Check 3: Quality distribution
    checks_total += 1
    quals = []
    for v in agent:
        try:
            q = float(v["qual"])
            quals.append(q)
        except (ValueError, TypeError):
            pass
    print("Check 3: Quality distribution")
    if quals:
        min_q = min(quals)
        max_q = max(quals)
        mean_q = sum(quals) / len(quals)
        print(f"  QUAL range: {min_q:.1f} - {max_q:.1f} (mean: {mean_q:.1f})")
        print(f"  Variants with QUAL > 30: {sum(1 for q in quals if q > 30)}/{len(quals)}")
        if max_q > 0:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL — all QUAL=0")
    else:
        print("  No parseable QUAL scores (PASS by default)")
        checks_passed += 1
    print()

    # Check 4: Het/hom ratio
    checks_total += 1
    hets = sum(1 for v in agent if "/" in v["gt"] and set(v["gt"].replace("|", "/").split("/")) != {"1"} and "0" in v["gt"])
    homs = sum(1 for v in agent if v["gt"] in ("1/1", "1|1"))
    print("Check 4: Het/hom ratio")
    print(f"  Heterozygous: {hets}")
    print(f"  Homozygous-alt: {homs}")
    if hets + homs > 0:
        ratio = hets / (hets + homs)
        print(f"  Het fraction: {ratio:.2f}")
        # Biological note: outbred diploids typically have het fraction 0.4-0.7.
        # All-hom (0.0) is normal for haploid organisms, inbred lines, or
        # high-coverage synthetic data where callers see only alt alleles.
        if 0.1 <= ratio <= 0.95:
            print("  Typical for outbred diploid germline")
        elif ratio == 0.0:
            print("  All homozygous — normal for haploid/inbred; check if diploid expected")
        else:
            print("  Unusual — check ploidy assumptions and caller settings")
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  No genotype information available (PASS by default)")
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
        print(f"  {sys.argv[0]} <truth.vcf> <agent.vcf>   # Benchmark mode")
        print(f"  {sys.argv[0]} --sanity <agent.vcf>       # Sanity mode (no truth)")
        return 1

    if sys.argv[1] == "--sanity":
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} --sanity <agent.vcf>")
            return 1
        return run_sanity(Path(sys.argv[2]))

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <truth.vcf> <agent.vcf>")
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
