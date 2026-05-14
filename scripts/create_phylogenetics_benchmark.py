#!/usr/bin/env python3
"""Create synthetic benchmark data for phylogenetic inference.

Generates:
  - Protein sequences evolved along a known tree topology
  - Unaligned FASTA (to test alignment + tree inference pipeline)
  - Truth Newick tree

The known tree topology:
  ((species_A:0.1,species_B:0.1):0.05,(species_C:0.15,species_D:0.15):0.05,species_E:0.2)

Branch lengths represent expected substitutions per site.
"""

import random
from pathlib import Path

SEED = 42
SEQ_LEN = 300  # amino acids
AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"

# WAG-like rough exchangeability: some AAs are more likely to substitute to
# biochemically similar ones.  For simplicity we use a uniform model but
# weight transitions toward similar residues slightly.
SIMILAR_GROUPS = [
    set("ILMV"),   # hydrophobic
    set("FYW"),    # aromatic
    set("KRH"),    # positive
    set("DE"),     # negative
    set("STNQ"),   # polar uncharged
    set("AG"),     # small
]


def _similar(aa: str) -> list[str]:
    """Return AAs in the same biochemical group (excluding self)."""
    for group in SIMILAR_GROUPS:
        if aa in group:
            return [a for a in group if a != aa]
    return []


def generate_root_sequence(length: int, rng: random.Random) -> str:
    return "".join(rng.choice(AMINO_ACIDS) for _ in range(length))


def evolve_sequence(seq: str, branch_length: float, rng: random.Random) -> str:
    """Mutate each site with probability = branch_length, biased toward similar AAs."""
    result = list(seq)
    for i in range(len(result)):
        if rng.random() < branch_length:
            similar = _similar(result[i])
            if similar and rng.random() < 0.6:
                result[i] = rng.choice(similar)
            else:
                alternatives = [a for a in AMINO_ACIDS if a != result[i]]
                result[i] = rng.choice(alternatives)
    return "".join(result)


def add_indels(seq: str, rate: float, rng: random.Random) -> str:
    """Add small insertions/deletions to make alignment non-trivial."""
    result = list(seq)
    i = 0
    while i < len(result):
        if rng.random() < rate:
            if rng.random() < 0.5 and len(result) > 50:
                # deletion (1-3 aa)
                del_len = rng.randint(1, 3)
                del result[i : i + del_len]
            else:
                # insertion (1-3 aa)
                ins = "".join(rng.choice(AMINO_ACIDS) for _ in range(rng.randint(1, 3)))
                for j, aa in enumerate(ins):
                    result.insert(i + j, aa)
                i += len(ins)
        i += 1
    return "".join(result)


def write_fasta(path: Path, sequences: dict[str, str]):
    with open(path, "w") as f:
        for name, seq in sequences.items():
            f.write(f">{name}\n")
            for i in range(0, len(seq), 80):
                f.write(seq[i : i + 80] + "\n")


def main():
    rng = random.Random(SEED)

    base_dir = Path("workspace/benchmarks/bioagent-bench/tasks/phylogenetics")
    data_dir = base_dir / "data"
    results_dir = base_dir / "results"
    data_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    # Generate root sequence
    root_seq = generate_root_sequence(SEQ_LEN, rng)
    print(f"Root sequence: {len(root_seq)} aa")

    # Tree: ((A:0.1,B:0.1):0.05,(C:0.15,D:0.15):0.05,E:0.2)
    # Simulate evolution
    # Internal node 1 (ancestor of A,B): 0.05 from root
    internal1 = evolve_sequence(root_seq, 0.05, rng)
    # Internal node 2 (ancestor of C,D): 0.05 from root
    internal2 = evolve_sequence(root_seq, 0.05, rng)

    # Leaf sequences
    seq_a = evolve_sequence(internal1, 0.1, rng)
    seq_b = evolve_sequence(internal1, 0.1, rng)
    seq_c = evolve_sequence(internal2, 0.15, rng)
    seq_d = evolve_sequence(internal2, 0.15, rng)
    seq_e = evolve_sequence(root_seq, 0.2, rng)  # outgroup

    # Add small indels to make alignment interesting (low rate)
    indel_rate = 0.005
    sequences = {
        "species_A": add_indels(seq_a, indel_rate, rng),
        "species_B": add_indels(seq_b, indel_rate, rng),
        "species_C": add_indels(seq_c, indel_rate, rng),
        "species_D": add_indels(seq_d, indel_rate, rng),
        "species_E": add_indels(seq_e, indel_rate, rng),
    }

    # Write unaligned FASTA
    fasta_path = data_dir / "sequences.fasta"
    write_fasta(fasta_path, sequences)
    print(f"Wrote {len(sequences)} sequences to {fasta_path}")
    for name, seq in sequences.items():
        print(f"  {name}: {len(seq)} aa")

    # Write truth tree (unrooted topology)
    truth_tree = "((species_A:0.1,species_B:0.1):0.05,(species_C:0.15,species_D:0.15):0.05,species_E:0.2);"
    truth_path = results_dir / "truth_tree.nwk"
    with open(truth_path, "w") as f:
        f.write(truth_tree + "\n")
    print(f"Truth tree: {truth_tree}")
    print(f"Wrote truth tree to {truth_path}")

    # Also write a brief description of expected topology
    desc_path = results_dir / "topology_description.txt"
    with open(desc_path, "w") as f:
        f.write("Expected topology (unrooted):\n")
        f.write("  - species_A and species_B are sister taxa (closest relatives)\n")
        f.write("  - species_C and species_D are sister taxa\n")
        f.write("  - (A,B) clade and (C,D) clade are more closely related to each other than to E\n")
        f.write("  - species_E is the outgroup\n")
        f.write("\nNewick: ((species_A,species_B),(species_C,species_D),species_E)\n")


if __name__ == "__main__":
    main()
