#!/usr/bin/env python3
"""Validate phylogenetics benchmark results.

Supports two modes:
  Benchmark mode: python3 scripts/validate_phylogenetics.py truth.nwk inferred.treefile
    Compares inferred tree topology against known truth via Robinson-Foulds distance.

  Sanity mode:    python3 scripts/validate_phylogenetics.py --sanity inferred.treefile
    Checks Newick format validity, taxon count, and tree structure.
    Useful for novel data where no truth is available.

Checks (benchmark mode):
  1. Output format — valid Newick tree with all expected taxa
  2. Topology match — Robinson-Foulds distance (0 = perfect)
  3. Branch length plausibility — all branch lengths positive and reasonable
  4. Taxon set match — inferred tree contains same taxa as truth

Checks (sanity mode):
  1. Newick format — parseable Newick string
  2. Taxon count — at least 3 taxa
  3. Branch lengths — all positive, not degenerate
  4. Tree structure — binary (fully resolved) or has polytomies
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def parse_newick_taxa(newick: str) -> list[str]:
    """Extract taxon names from a Newick string."""
    # Remove branch lengths, supports, and special characters
    cleaned = re.sub(r":[0-9eE.+-]+", "", newick)
    cleaned = cleaned.replace("(", " ").replace(")", " ").replace(",", " ").replace(";", " ")
    taxa = [t.strip() for t in cleaned.split() if t.strip()]
    return taxa


def parse_newick_branch_lengths(newick: str) -> list[float]:
    """Extract all branch lengths from a Newick string."""
    lengths = []
    for match in re.finditer(r":([0-9eE.+-]+)", newick):
        try:
            lengths.append(float(match.group(1)))
        except ValueError:
            pass
    return lengths


def get_bipartitions(newick: str, all_taxa: set[str]) -> set[frozenset[str]]:
    """Extract non-trivial bipartitions (splits) from a Newick tree.

    A bipartition splits the taxa into two groups. Trivial splits (single taxon
    vs rest) are excluded. Returns the smaller side of each split as a frozenset.

    Pure-Python recursive parser — no external dependencies needed.
    """
    # Tokenize
    tokens = []
    i = 0
    s = newick.strip().rstrip(";")
    while i < len(s):
        if s[i] in "(),":
            tokens.append(s[i])
            i += 1
        elif s[i] == ":":
            # Skip branch length
            j = i + 1
            while j < len(s) and s[j] not in "(),;":
                j += 1
            i = j
        elif s[i] in " \t\n":
            i += 1
        else:
            # Taxon name
            j = i
            while j < len(s) and s[j] not in ":(),; \t\n":
                j += 1
            tokens.append(s[i:j])
            i = j

    # Recursive descent parser
    splits: list[set[str]] = []

    def parse_subtree(pos: int) -> tuple[set[str], int]:
        """Parse a subtree starting at pos, return (taxa_set, new_pos)."""
        if pos >= len(tokens):
            return set(), pos
        if tokens[pos] == "(":
            pos += 1  # skip '('
            children_taxa: list[set[str]] = []
            while pos < len(tokens) and tokens[pos] != ")":
                child_taxa, pos = parse_subtree(pos)
                children_taxa.append(child_taxa)
                if pos < len(tokens) and tokens[pos] == ",":
                    pos += 1
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1  # skip ')'
            # Skip any internal label
            if pos < len(tokens) and tokens[pos] not in "(),":
                pos += 1
            # Union of all children
            node_taxa: set[str] = set()
            for ct in children_taxa:
                node_taxa |= ct
            # Record non-trivial splits
            if 1 < len(node_taxa) < len(all_taxa):
                splits.append(node_taxa)
            return node_taxa, pos
        else:
            # Leaf
            taxon = tokens[pos]
            pos += 1
            return {taxon}, pos

    parse_subtree(0)

    # Normalize: use smaller side of each split
    result = set()
    for sp in splits:
        complement = all_taxa - sp
        smaller = frozenset(sp) if len(sp) <= len(complement) else frozenset(complement)
        if len(smaller) > 0 and len(smaller) < len(all_taxa):
            result.add(smaller)
    return result


def robinson_foulds(tree1: str, tree2: str) -> tuple[int, int, int]:
    """Compute Robinson-Foulds distance between two Newick trees.

    Returns (rf_distance, splits_tree1, splits_tree2).
    """
    taxa1 = set(parse_newick_taxa(tree1))
    taxa2 = set(parse_newick_taxa(tree2))
    common = taxa1 & taxa2
    if len(common) < 3:
        return -1, 0, 0

    bp1 = get_bipartitions(tree1, common)
    bp2 = get_bipartitions(tree2, common)
    # Filter to only splits involving common taxa
    bp1 = {frozenset(s & common) for s in bp1 if 1 < len(s & common) < len(common)}
    bp2 = {frozenset(s & common) for s in bp2 if 1 < len(s & common) < len(common)}
    rf = len(bp1 ^ bp2)  # symmetric difference
    return rf, len(bp1), len(bp2)


def run_benchmark(truth_path: Path, output_path: Path) -> int:
    """Benchmark mode: compare against truth tree."""
    print("=" * 60)
    print("Phylogenetics — Benchmark Validation")
    print("=" * 60)
    print(f"Truth:  {truth_path}")
    print(f"Output: {output_path}")
    print()

    truth_nwk = truth_path.read_text().strip()
    agent_nwk = output_path.read_text().strip()
    # Some tools write multiple lines; use the last non-empty line
    agent_lines = [line.strip() for line in agent_nwk.split("\n") if line.strip()]
    if agent_lines:
        agent_nwk = agent_lines[-1]

    truth_taxa = set(parse_newick_taxa(truth_nwk))
    agent_taxa = set(parse_newick_taxa(agent_nwk))

    checks_passed = 0
    checks_total = 0

    # Check 1: Output format
    checks_total += 1
    print("Check 1: Output format")
    print(f"  Truth taxa ({len(truth_taxa)}): {', '.join(sorted(truth_taxa))}")
    print(f"  Agent taxa ({len(agent_taxa)}): {', '.join(sorted(agent_taxa))}")
    has_parens = "(" in agent_nwk and ")" in agent_nwk
    print(f"  Valid Newick structure: {has_parens}")
    if has_parens and len(agent_taxa) >= 3:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 2: Taxon set match
    checks_total += 1
    missing = truth_taxa - agent_taxa
    extra = agent_taxa - truth_taxa
    print("Check 2: Taxon set match")
    if missing:
        print(f"  Missing taxa: {', '.join(sorted(missing))}")
    if extra:
        print(f"  Extra taxa: {', '.join(sorted(extra))}")
    if not missing and not extra:
        print("  Perfect taxon set match")
        print("  \u2713 PASS")
        checks_passed += 1
    elif not missing:
        print("  All truth taxa present (extra taxa are acceptable)")
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL — missing taxa from truth")
    print()

    # Check 3: Topology match (Robinson-Foulds)
    checks_total += 1
    rf, n1, n2 = robinson_foulds(truth_nwk, agent_nwk)
    print("Check 3: Topology (Robinson-Foulds distance)")
    print(f"  Truth splits: {n1}")
    print(f"  Agent splits: {n2}")
    print(f"  RF distance: {rf}")
    # Show the actual splits for debugging
    common = truth_taxa & agent_taxa
    truth_bp = get_bipartitions(truth_nwk, common)
    agent_bp = get_bipartitions(agent_nwk, common)
    truth_bp = {frozenset(s & common) for s in truth_bp if 1 < len(s & common) < len(common)}
    agent_bp = {frozenset(s & common) for s in agent_bp if 1 < len(s & common) < len(common)}
    print(f"  Truth splits: {[set(s) for s in sorted(truth_bp, key=lambda x: sorted(x))]}")
    print(f"  Agent splits: {[set(s) for s in sorted(agent_bp, key=lambda x: sorted(x))]}")
    if rf == 0:
        print("  \u2713 PASS — perfect topology match")
        checks_passed += 1
    else:
        print(f"  \u2717 FAIL — topologies differ (RF={rf})")
    print()

    # Check 4: Branch length plausibility
    checks_total += 1
    bls = parse_newick_branch_lengths(agent_nwk)
    print("Check 4: Branch length plausibility")
    if bls:
        print(f"  Branch lengths found: {len(bls)}")
        print(f"  Range: {min(bls):.6f} to {max(bls):.6f}")
        negative = sum(1 for b in bls if b < 0)
        zero = sum(1 for b in bls if b == 0)
        print(f"  Negative: {negative}, Zero: {zero}")
        if negative == 0 and max(bls) < 100:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print("  \u2717 FAIL")
    else:
        print("  No branch lengths in tree (acceptable for topology-only)")
        print("  \u2713 PASS")
        checks_passed += 1
    print()

    print("=" * 60)
    if checks_passed == checks_total:
        print(f"BENCHMARK PASSED ({checks_passed}/{checks_total} checks)")
    else:
        print(f"BENCHMARK FAILED ({checks_passed}/{checks_total} checks)")
    print("=" * 60)
    return 0 if checks_passed == checks_total else 1


def run_sanity(output_path: Path) -> int:
    """Sanity mode: check tree format and plausibility."""
    print("=" * 60)
    print("Phylogenetics — Sanity Check")
    print("=" * 60)
    print(f"Output: {output_path}")
    print("(No truth data — checking format and plausibility only)")
    print()

    agent_nwk = output_path.read_text().strip()
    agent_lines = [line.strip() for line in agent_nwk.split("\n") if line.strip()]
    if agent_lines:
        agent_nwk = agent_lines[-1]

    agent_taxa = set(parse_newick_taxa(agent_nwk))

    checks_passed = 0
    checks_total = 0

    # Check 1: Newick format
    checks_total += 1
    has_parens = "(" in agent_nwk and ")" in agent_nwk
    balanced = agent_nwk.count("(") == agent_nwk.count(")")
    print("Check 1: Newick format")
    print(f"  Has parentheses: {has_parens}")
    print(f"  Balanced: {balanced}")
    print(f"  Ends with semicolon: {agent_nwk.endswith(';')}")
    if has_parens and balanced:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL")
    print()

    # Check 2: Taxon count
    checks_total += 1
    print("Check 2: Taxon count")
    print(f"  Taxa found: {len(agent_taxa)}")
    if agent_taxa:
        print(f"  Names: {', '.join(sorted(agent_taxa))}")
    if len(agent_taxa) >= 3:
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL — need at least 3 taxa for meaningful phylogeny")
    print()

    # Check 3: Branch lengths
    checks_total += 1
    bls = parse_newick_branch_lengths(agent_nwk)
    print("Check 3: Branch lengths")
    if bls:
        print(f"  Found: {len(bls)}")
        print(f"  Range: {min(bls):.6f} to {max(bls):.6f}")
        negative = sum(1 for b in bls if b < 0)
        if negative == 0:
            print("  \u2713 PASS")
            checks_passed += 1
        else:
            print(f"  \u2717 FAIL — {negative} negative branch lengths")
    else:
        print("  No branch lengths found (topology-only tree)")
        print("  \u2713 PASS")
        checks_passed += 1
    print()

    # Check 4: Tree structure
    checks_total += 1
    n_taxa = len(agent_taxa)
    all_taxa_set = agent_taxa
    splits = get_bipartitions(agent_nwk, all_taxa_set)
    splits = {s for s in splits if 1 < len(s) < len(all_taxa_set)}
    max_internal = n_taxa - 3 if n_taxa > 3 else 0  # unrooted binary tree
    print("Check 4: Tree structure")
    print(f"  Non-trivial splits: {len(splits)}")
    print(f"  Maximum for fully resolved (unrooted): {max_internal}")
    if len(splits) > 0:
        print(f"  Resolution: {'fully resolved' if len(splits) >= max_internal else 'has polytomies'}")
        print("  \u2713 PASS")
        checks_passed += 1
    else:
        print("  \u2717 FAIL — star topology (no internal structure)")
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
        print(f"  {sys.argv[0]} <truth.nwk> <inferred.treefile>   # Benchmark mode")
        print(f"  {sys.argv[0]} --sanity <inferred.treefile>       # Sanity mode (no truth)")
        return 1

    if sys.argv[1] == "--sanity":
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} --sanity <inferred.treefile>")
            return 1
        return run_sanity(Path(sys.argv[2]))

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <truth.nwk> <inferred.treefile>")
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
