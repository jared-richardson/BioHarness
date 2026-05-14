from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_agent_e2e import AgentE2EHarness, HarnessConfig


def _write_fastq(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")


def _write_refs(workspace: Path) -> tuple[str, str]:
    inp = workspace / "inputs_readonly"
    inp.mkdir(parents=True, exist_ok=True)
    fasta = inp / "mouse_fasta"
    gtf = inp / "mouse_gtf"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf.write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    return str(fasta), str(gtf)


def _cfg(prompt: str, selected_dir: Path, data_root: Path) -> HarnessConfig:
    return HarnessConfig(
        prompt=prompt,
        selected_dir=selected_dir,
        data_root=data_root,
        workspace_root=selected_dir,
        max_repairs=0,
        heartbeat_seconds=15,
        stall_timeout_seconds=45,
        live_process_grace_seconds=900,
        model_name=None,
        host=None,
        auto_install_missing_tools=False,
        allow_replan=True,
        allow_canonicalize=True,
        plan_path=None,
        result_json=None,
        quiet=True,
        print_plan=False,
    )


@pytest.mark.parametrize(
    "prompt,setup,expected_pipeline",
    [
        ("Run bisulfite methylation analysis with Bismark.", "paired", "methylation_bismark_style"),
        ("Profile metagenomics reads with Kraken2 and Bracken.", "paired", "metagenomics_kraken2_bracken_style"),
        ("Detect fusions using STAR-Fusion.", "paired", "fusion_star_fusion_style"),
        ("Run CNVkit copy-number analysis from this BAM.", "bam", "cnv_cnvkit_style"),
        ("Profile immune repertoire with MiXCR.", "paired", "immune_repertoire_mixcr_style"),
        ("Infer a phylogenetic tree with IQ-TREE.", "protein", "phylogenetics_iqtree_style"),
    ],
)
def test_prepare_plan_covers_uncommon_prompts(tmp_path: Path, prompt: str, setup: str, expected_pipeline: str):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_refs(workspace)

    if setup == "paired":
        _write_fastq(data_root / "1_S1_R1_001.fastq")
        _write_fastq(data_root / "1_S1_R2_001.fastq")
    elif setup == "bam":
        bam = workspace / "outputs" / "sample.bam"
        bam.parent.mkdir(parents=True, exist_ok=True)
        bam.write_bytes(b"BAM")
    elif setup == "protein":
        (workspace / "query.faa").write_text(">p1\nACGTACGT\n", encoding="utf-8")

    harness = AgentE2EHarness(_cfg(prompt, selected_dir=workspace, data_root=data_root))
    harness._init_run()

    def _timeout_planner(_prompt: str, analysis_spec=None):
        raise TimeoutError("Planner request timed out while waiting for model output.")

    harness.orchestrator.think = _timeout_planner  # type: ignore[method-assign]
    harness._prepare_plan()

    selected_pipeline = str(harness.run.get("fallback_selection", {}).get("selected_pipeline_id", ""))
    assert selected_pipeline == expected_pipeline
    assert harness.run.get("plan", {}).get("plan", [])
