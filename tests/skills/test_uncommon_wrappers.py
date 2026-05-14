from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_harness.core.uncommon_skill_framework import (
    build_uncommon_wrapper_command,
    uncommon_skill_specs,
)
from bio_harness.skills.library.cnv_cnvkit_style import cnv_cnvkit_style
from bio_harness.skills.library.fusion_star_fusion_style import fusion_star_fusion_style
from bio_harness.skills.library.immune_repertoire_mixcr_style import immune_repertoire_mixcr_style
from bio_harness.skills.library.metagenomics_kraken2_bracken_style import metagenomics_kraken2_bracken_style
from bio_harness.skills.library.methylation_bismark_style import methylation_bismark_style
from bio_harness.skills.library.phylogenetics_iqtree_style import phylogenetics_iqtree_style


WRAPPER_MAP = {
    "methylation_bismark_style": methylation_bismark_style,
    "metagenomics_kraken2_bracken_style": metagenomics_kraken2_bracken_style,
    "fusion_star_fusion_style": fusion_star_fusion_style,
    "cnv_cnvkit_style": cnv_cnvkit_style,
    "immune_repertoire_mixcr_style": immune_repertoire_mixcr_style,
    "phylogenetics_iqtree_style": phylogenetics_iqtree_style,
}


@pytest.mark.parametrize("spec", uncommon_skill_specs(), ids=[row["name"] for row in uncommon_skill_specs()])
def test_uncommon_wrappers_render_deterministic_commands(spec: dict):
    func = WRAPPER_MAP[spec["name"]]
    kwargs = dict(spec.get("sample_args", {}))
    cmd_a = func(**kwargs)
    cmd_b = func(**kwargs)
    assert cmd_a == cmd_b
    assert "set -euo pipefail" in cmd_a


def test_wrapper_rejects_destructive_manual_command_override():
    with pytest.raises(ValueError):
        methylation_bismark_style(command="rm -rf /")


def test_framework_fallback_branch_writes_artifact_when_tool_missing(tmp_path: Path):
    spec_path = tmp_path / "catalog.json"
    spec_path.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": [
                    {
                        "name": "dummy_uncommon",
                        "description": "dummy",
                        "risk_level": "low",
                        "capabilities": ["annotation"],
                        "tools_required": ["tool_that_does_not_exist_12345"],
                        "parameters": {
                            "output_report": {
                                "type": "path",
                                "description": "out",
                                "required": True,
                            }
                        },
                        "required_args": ["output_report"],
                        "optional_flags": {},
                        "command_template": "tool_that_does_not_exist_12345 > {output_report}",
                        "tool_groups": [["tool_that_does_not_exist_12345"]],
                        "fallback_outputs": [
                            {
                                "path_arg": "output_report",
                                "content": "status\\tdegraded\\n",
                            }
                        ],
                        "fallback_note": "missing tool fallback",
                        "docs_anchor": "dummy",
                        "test_files": ["tests/skills/test_uncommon_wrappers.py"],
                        "fixtures": ["tests/workflows/fixtures/uncommon_prompt_intents.json#dummy"],
                        "sample_args": {"output_report": str(tmp_path / "out.tsv")},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    out_path = tmp_path / "out.tsv"
    cmd = build_uncommon_wrapper_command(
        "dummy_uncommon",
        {"output_report": str(out_path)},
        spec_path=spec_path,
    )
    assert "__MISSING_TOOL__" in cmd
    assert "out.tsv" in cmd


def test_phylogenetics_wrapper_creates_output_tree_parent_dir():
    cmd = phylogenetics_iqtree_style(
        alignment_fasta="/tmp/aligned.fasta",
        output_dir="/tmp/phylo",
        output_prefix="/tmp/phylo/iqtree",
        output_tree="/tmp/phylo/final/phylogeny.treefile",
        model="MFP",
        threads=2,
        seed=42,
    )

    assert 'mkdir -p "$(dirname /tmp/phylo/final/phylogeny.treefile)"' in cmd
    assert ' -redo -pre "$OUT_PREFIX"' in cmd
    assert 'cp "$OUT_PREFIX.treefile" /tmp/phylo/final/phylogeny.treefile' in cmd


def test_cnvkit_wrapper_uses_flat_reference_no_r_path() -> None:
    cmd = cnv_cnvkit_style(
        input_bam="/tmp/sample.bam",
        reference_fasta="/tmp/ref.fa",
        output_dir="/tmp/cnv",
        output_report="/tmp/cnv/cnv.tsv",
        threads=2,
    )

    assert "--method wgs --normal" in cmd
    assert "--segment-method none" in cmd
    assert "cnvkit_summary.py" in cmd
    assert "missing_cnvkit" in cmd


def test_bismark_wrapper_prepares_genome_folder_when_reference_provided() -> None:
    cmd = methylation_bismark_style(
        genome_folder="/tmp/bismark_genome",
        reference_fasta="/tmp/ref.fa",
        reads_1="/tmp/reads_R1.fastq",
        reads_2="/tmp/reads_R2.fastq",
        output_dir="/tmp/methylation",
        output_report="/tmp/methylation/report.tsv",
        sample_name="methylation_smoke",
        threads=1,
    )

    assert "bismark_genome_preparation" in cmd
    assert "--genome_folder /tmp/bismark_genome" in cmd
    assert "--basename methylation_smoke" in cmd
    assert "bismark_summary.py" in cmd


def test_metagenomics_wrapper_builds_tiny_db_and_bracken_profile() -> None:
    cmd = metagenomics_kraken2_bracken_style(
        database="/tmp/kraken_db",
        reference_fasta="/tmp/reference.fa",
        taxonomy_names="/tmp/names.dmp",
        taxonomy_nodes="/tmp/nodes.dmp",
        reads_1="/tmp/reads_R1.fastq",
        reads_2="/tmp/reads_R2.fastq",
        output_dir="/tmp/meta",
        output_report="/tmp/meta/bracken.tsv",
        threads=1,
        read_len=40,
        taxonomy_level="S",
        threshold=1,
    )

    assert "kraken2-build" in cmd
    assert "count-kmer-abundances.pl" in cmd
    assert "generate_kmer_distribution.py" in cmd
    assert "est_abundance.py" in cmd
    assert "database40mers.kmer_distrib" in cmd
    assert "unclassified" in cmd


def test_fusion_wrapper_degrades_when_ctat_genome_lib_is_missing() -> None:
    cmd = fusion_star_fusion_style(
        genome_lib_dir="/tmp/missing_ctat_lib",
        reads_1="/tmp/reads_R1.fastq",
        reads_2="/tmp/reads_R2.fastq",
        output_dir="/tmp/fusion/out",
        output_report="/tmp/fusion/fusions.tsv",
    )

    assert "missing_ctat_genome_lib" in cmd
    assert "[ -d /tmp/missing_ctat_lib ]" in cmd
    assert "STAR-Fusion --genome_lib_dir /tmp/missing_ctat_lib" in cmd


def test_fusion_wrapper_still_respects_manual_command_override() -> None:
    cmd = fusion_star_fusion_style(command="echo test")
    assert cmd == "echo test"
