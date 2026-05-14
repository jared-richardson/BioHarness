#!/usr/bin/env python3
"""Create synthetic metabolomics benchmark data — 6 cases.

Cases mirror the proteomics layout: clean, nested_output, noisy_prompt,
metadata_ambiguity, high_missingness, malformed.
"""
import csv
import json
import math
import random
from pathlib import Path

BASE = Path("workspace/benchmark_data/metabolomics")
SEED = 45
N_FEATURES = 300
N_SAMPLES = 10  # 5 control + 5 treatment
N_DE = 30


def _make_data(data_dir: Path, *, missingness=0.0, malformed=False, ambiguous_meta=False):
    data_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    features = [
        f"mz{rng.uniform(100, 1000):.4f}_rt{rng.uniform(0.5, 15):.2f}"
        for _ in range(N_FEATURES)
    ]
    samples = [f"sample_{i}" for i in range(N_SAMPLES)]
    conditions = ["control"] * 5 + ["treatment"] * 5

    de_indices = set(rng.sample(range(N_FEATURES), N_DE))
    de_fold = {}
    for idx in de_indices:
        de_fold[idx] = rng.choice([0.3, 3.0])

    with open(data_dir / "feature_table.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["feature"] + samples)
        for fi in range(N_FEATURES):
            row = [features[fi]]
            for si in range(N_SAMPLES):
                base = math.exp(rng.gauss(12, 1.5))
                if fi in de_indices and conditions[si] == "treatment":
                    base *= de_fold[fi]
                if rng.random() < missingness:
                    row.append("")
                else:
                    row.append(f"{base:.2f}")
            w.writerow(row)

    if malformed:
        with open(data_dir / "feature_table.csv", "a") as f:
            f.write("BAD_FEATURE,not,a,number\n")

    with open(data_dir / "metadata.csv", "w", newline="") as f:
        w = csv.writer(f)
        if ambiguous_meta:
            w.writerow(["id", "treatment_group", "batch", "instrument"])
            for i in range(N_SAMPLES):
                w.writerow([samples[i], conditions[i], f"B{i % 2}", "QTOF"])
        else:
            w.writerow(["sample", "condition"])
            for i in range(N_SAMPLES):
                w.writerow([samples[i], conditions[i]])

    de_names = [features[i] for i in sorted(de_indices)]
    with open(data_dir / "truth.json", "w") as f:
        json.dump(
            {
                "n_features": N_FEATURES,
                "n_samples": N_SAMPLES,
                "n_de": N_DE,
                "de_features": de_names,
            },
            f,
            indent=2,
        )


def write_prompts():
    prompts = {
        "clean": (
            "Perform differential metabolite analysis on the feature intensity "
            "table comparing control vs treatment."
        ),
        "nested_output": (
            "Run differential metabolite analysis. Save results to "
            "results/metabolomics_output/"
        ),
        "noisy_prompt": (
            "I ran a mass spec experiment and got this feature table. Which "
            "metabolites are changing between conditions?"
        ),
        "metadata_ambiguity": (
            "Analyze differential abundance from this metabolomics feature table. "
            "The metadata has several columns; the treatment group column indicates "
            "the experimental condition."
        ),
        "high_missingness": (
            "Analyze this metabolomics feature table. Warning: instrument dropout "
            "caused missing values in about 30% of measurements."
        ),
        "malformed": (
            "Run metabolomics differential analysis on the provided data."
        ),
    }
    for name, text in prompts.items():
        p = BASE / name / "prompt.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")


def main():
    print("Creating metabolomics benchmark data...")
    _make_data(BASE / "clean" / "data")
    _make_data(BASE / "nested_output" / "data")
    _make_data(BASE / "noisy_prompt" / "data")
    _make_data(BASE / "metadata_ambiguity" / "data", ambiguous_meta=True)
    _make_data(BASE / "high_missingness" / "data", missingness=0.30)
    _make_data(BASE / "malformed" / "data", malformed=True)
    write_prompts()
    print("Done: 6 metabolomics cases")


if __name__ == "__main__":
    main()
