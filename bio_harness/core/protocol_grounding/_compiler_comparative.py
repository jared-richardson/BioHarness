"""Template compiler for comparative genomics."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.core.protocol_grounding._shared import _renumber_plan


def _compile_comparative_genomics_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic template for comparative genomics (minimap2 all-vs-all ANI)."""
    sd = str(selected_dir)
    # Find genome FASTA files
    genomes: list[str] = []
    for candidate in sorted(data_root.rglob("*")):
        if candidate.is_file() and re.search(r"\.(fa|fasta|fna|gbk|gb)(\.gz)?$", candidate.name, re.I):
            genomes.append(str(candidate.resolve()))
    if len(genomes) < 2:
        return plan, {"changed": False, "why": "fewer_than_2_genomes"}

    n = len(genomes)
    # Build genome name list (stem of filename)
    genome_entries: list[tuple[str, str]] = []
    for g in genomes:
        stem = Path(g).stem
        # Double-strip for .fna.gz → .fna → stem
        if stem.endswith((".fasta", ".fna", ".fa")):
            stem = Path(stem).stem
        genome_entries.append((stem, g))

    # Step 1: All-vs-all pairwise minimap2 alignment with CIGAR output
    # -c --eqx produces base-level CIGAR with =/X for proper ANI computation
    paf_cmds = [f"mkdir -p {sd}/paf"]
    for i in range(n):
        for j in range(i + 1, n):
            paf_cmds.append(
                f"minimap2 -x asm20 -t 2 -c --eqx {genomes[i]} {genomes[j]} "
                f"> {sd}/paf/pair_{i}_{j}.paf 2>/dev/null"
            )
    align_cmd = " && ".join(paf_cmds)

    # Step 2: Inline Python to compute ANI from PAF CIGAR strings
    genome_list_literal = repr(genome_entries)
    ani_script = (
        f"mkdir -p {sd}/output && python3 << 'PYEOF'\n"
        f"import csv, re\n"
        f"from pathlib import Path\n"
        f"\n"
        f"sd = {sd!r}\n"
        f"paf_dir = Path(sd) / 'paf'\n"
        f"out_dir = Path(sd) / 'output'\n"
        f"out_dir.mkdir(parents=True, exist_ok=True)\n"
        f"\n"
        f"genomes = {genome_list_literal}\n"
        f"genome_names = [n for n, p in genomes]\n"
        f"n = len(genomes)\n"
        f"\n"
        f"# Compute genome lengths\n"
        f"genome_lens = {{}}\n"
        f"for name, path in genomes:\n"
        f"    total = 0\n"
        f"    with open(path) as f:\n"
        f"        for line in f:\n"
        f"            if not line.startswith('>'):\n"
        f"                total += len(line.strip())\n"
        f"    genome_lens[name] = total\n"
        f"\n"
        f"# Parse PAF CIGAR strings and compute ANI (matches vs mismatches, excluding gaps)\n"
        f"ani_data = {{}}\n"
        f"for i in range(n):\n"
        f"    for j in range(i + 1, n):\n"
        f"        paf_path = paf_dir / f'pair_{{i}}_{{j}}.paf'\n"
        f"        total_match = 0\n"
        f"        total_mismatch = 0\n"
        f"        total_alen = 0\n"
        f"        if paf_path.exists():\n"
        f"            with open(paf_path) as f:\n"
        f"                for line in f:\n"
        f"                    cols = line.strip().split('\\t')\n"
        f"                    if len(cols) < 12:\n"
        f"                        continue\n"
        f"                    total_alen += int(cols[10])\n"
        f"                    cg = None\n"
        f"                    for tag in cols[12:]:\n"
        f"                        if tag.startswith('cg:Z:'):\n"
        f"                            cg = tag[5:]\n"
        f"                            break\n"
        f"                    if cg:\n"
        f"                        total_match += sum(int(x) for x in re.findall(r'(\\d+)=', cg))\n"
        f"                        total_mismatch += sum(int(x) for x in re.findall(r'(\\d+)X', cg))\n"
        f"        aligned_bases = total_match + total_mismatch\n"
        f"        ani = total_match / aligned_bases if aligned_bases > 0 else 0.0\n"
        f"        max_len = max(genome_lens[genome_names[i]], genome_lens[genome_names[j]])\n"
        f"        af = aligned_bases / max_len if max_len > 0 else 0.0\n"
        f"        ani_data[(i, j)] = {{'ani': ani, 'aligned_fraction': af}}\n"
        f"\n"
        f"# Write distance matrix CSV\n"
        f"with open(out_dir / 'distance_matrix.csv', 'w', newline='') as f:\n"
        f"    w = csv.writer(f)\n"
        f"    w.writerow([''] + genome_names)\n"
        f"    for i in range(n):\n"
        f"        row = [genome_names[i]]\n"
        f"        for j in range(n):\n"
        f"            if i == j:\n"
        f"                row.append('1.0000')\n"
        f"            elif i < j:\n"
        f"                row.append(f'{{ani_data[(i,j)][\"ani\"]:.4f}}')\n"
        f"            else:\n"
        f"                row.append(f'{{ani_data[(j,i)][\"ani\"]:.4f}}')\n"
        f"        w.writerow(row)\n"
        f"\n"
        f"# Write summary TSV and find closest pair\n"
        f"best_ani = -1\n"
        f"best_pair = ('', '')\n"
        f"with open(out_dir / 'summary.tsv', 'w') as f:\n"
        f"    f.write('genome_a\\tgenome_b\\tANI\\taligned_fraction\\n')\n"
        f"    for (i, j), d in sorted(ani_data.items()):\n"
        f"        f.write(f'{{genome_names[i]}}\\t{{genome_names[j]}}\\t{{d[\"ani\"]:.4f}}\\t{{d[\"aligned_fraction\"]:.4f}}\\n')\n"
        f"        if d['ani'] > best_ani:\n"
        f"            best_ani = d['ani']\n"
        f"            best_pair = (genome_names[i], genome_names[j])\n"
        f"\n"
        f"with open(out_dir / 'closest_pair.txt', 'w') as f:\n"
        f"    f.write(f'{{best_pair[0]}}\\t{{best_pair[1]}}\\n')\n"
        f"\n"
        f"print(f'Distance matrix: {{out_dir / \"distance_matrix.csv\"}}')\n"
        f"print(f'Summary: {{out_dir / \"summary.tsv\"}}')\n"
        f"print(f'Closest pair: {{best_pair[0]}}, {{best_pair[1]}} (ANI={{best_ani:.4f}})')\n"
        f"PYEOF"
    )

    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "tool_name": "bash_run",
            "purpose": "All-vs-all pairwise genome alignment with minimap2",
            "arguments": {"command": align_cmd},
        },
        {
            "step_id": 2,
            "tool_name": "bash_run",
            "purpose": "Compute ANI distance matrix and identify closest pair from PAF alignments",
            "arguments": {"command": ani_script},
        },
    ]

    compiled = {
        "thought_process": f"[comparative_genomics_template] minimap2 all-vs-all ANI for {len(genomes)} genomes. " + str(plan.get("thought_process", "")),
        "plan": steps,
    }
    return _renumber_plan(compiled), {
        "changed": True,
        "why": "compiled_comparative_genomics_protocol",
        "genome_count": len(genomes),
        "genome_files": genomes,
    }
