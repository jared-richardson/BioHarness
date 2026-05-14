#!/usr/bin/env python3
"""Create synthetic proteomics benchmark data — 6 cases.

Cases:
  clean             — canonical happy path
  nested_output     — same data, prompt requests nested output dir
  noisy_prompt      — same data, vague prompt
  metadata_ambiguity — extra metadata columns
  high_missingness  — 25% missing values
  malformed         — corrupted rows appended
"""
import csv
import json
import random
import shutil
from pathlib import Path

BASE = Path("workspace/benchmark_data/proteomics")
SEED = 42
N_PROTEINS = 500
N_SAMPLES = 12  # 6 control + 6 treatment
N_DE = 50


def _make_data(data_dir: Path, *, missingness=0.0, malformed=False, ambiguous_meta=False):
    data_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    proteins = [f"PROT_{i:04d}" for i in range(N_PROTEINS)]
    samples = [f"sample_{i}" for i in range(N_SAMPLES)]
    conditions = ["control"] * 6 + ["treatment"] * 6

    de_indices = set(rng.sample(range(N_PROTEINS), N_DE))
    de_direction = {}
    for idx in de_indices:
        de_direction[idx] = rng.choice([-1, 1])

    # Abundance matrix (log2 scale)
    with open(data_dir / "abundance_matrix.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["protein"] + samples)
        for p in range(N_PROTEINS):
            row = [proteins[p]]
            for s in range(N_SAMPLES):
                base = rng.gauss(20, 3)
                if p in de_indices and conditions[s] == "treatment":
                    base += de_direction[p] * rng.uniform(1.5, 3.0)
                if rng.random() < missingness:
                    row.append("")
                else:
                    row.append(f"{base:.4f}")
            w.writerow(row)

    if malformed:
        with open(data_dir / "abundance_matrix.csv", "a") as f:
            f.write("CORRUPTED_ROW\n")
            f.write(",".join(["X"] * 5) + "\n")

    # Metadata
    with open(data_dir / "metadata.csv", "w", newline="") as f:
        w = csv.writer(f)
        if ambiguous_meta:
            w.writerow(["id", "group", "batch", "age"])
            for i in range(N_SAMPLES):
                w.writerow([samples[i], conditions[i], f"batch{i % 3}", rng.randint(20, 60)])
        else:
            w.writerow(["sample", "condition"])
            for i in range(N_SAMPLES):
                w.writerow([samples[i], conditions[i]])

    # Truth
    de_names = [proteins[i] for i in sorted(de_indices)]
    with open(data_dir / "truth.json", "w") as f:
        json.dump(
            {
                "n_proteins": N_PROTEINS,
                "n_samples": N_SAMPLES,
                "n_de": N_DE,
                "de_proteins": de_names,
                "conditions": ["control", "treatment"],
            },
            f,
            indent=2,
        )


def write_prompts():
    prompts = {
        "clean": (
            "Perform differential protein abundance analysis comparing control "
            "vs treatment conditions. Use the abundance matrix and metadata provided."
        ),
        "nested_output": (
            "Run differential abundance on these proteomics data. "
            "Save results to results/diff_abundance/"
        ),
        "noisy_prompt": (
            "I have some protein expression data. Can you tell me which proteins "
            "are different between my two groups?"
        ),
        "metadata_ambiguity": (
            "Analyze differential abundance. The metadata has multiple columns — "
            "use the condition column for grouping."
        ),
        "high_missingness": (
            "Run differential abundance analysis on this proteomics dataset. "
            "Note: there may be missing values that need handling."
        ),
        "malformed": (
            "Perform proteomics differential abundance analysis on the provided data."
        ),
    }
    for name, text in prompts.items():
        p = BASE / name / "prompt.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")


def main():
    print("Creating proteomics benchmark data...")
    _make_data(BASE / "clean" / "data")
    _make_data(BASE / "nested_output" / "data")
    _make_data(BASE / "noisy_prompt" / "data")
    _make_data(BASE / "metadata_ambiguity" / "data", ambiguous_meta=True)
    _make_data(BASE / "high_missingness" / "data", missingness=0.25)
    _make_data(BASE / "malformed" / "data", malformed=True)
    write_prompts()
    print("Done: 6 proteomics cases")


if __name__ == "__main__":
    main()
