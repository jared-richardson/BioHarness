from __future__ import annotations

from pathlib import Path

import pytest

import bio_harness.workflows.fallback_catalog as fallback_catalog_mod
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


def _make_cfg(prompt: str, selected_dir: Path, data_root: Path) -> HarnessConfig:
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
    "prompt,setup_kind,expected_ids",
    [
        (
            "Run alternative splicing analysis with rMATS for control vs treatment.",
            "splicing",
            {"sr_rna_splicing_rmats_star"},
        ),
        (
            "Call germline variants from DNA reads with GATK HaplotypeCaller.",
            "germline",
            {
                "germline_variant_gatk_haplotypecaller",
                "germline_variant_bcftools",
                "germline_variant_varscan",
                "germline_variant_freebayes",
            },
        ),
        (
            "Call somatic variants in tumor vs normal using Mutect2.",
            "somatic",
            {"somatic_variant_mutect2_tn", "somatic_variant_bcftools_tn_degrade"},
        ),
        (
            "Perform long-read DNA alignment for nanopore data.",
            "longread",
            {"lr_dna_align_minimap2"},
        ),
        (
            "Run differential expression from an existing count matrix and metadata.",
            "counts",
            {"differential_expression_deseq2_from_counts", "differential_expression_deseq2"},
        ),
        (
            "Run a protein homology search with BLASTP for these proteins.",
            "protein",
            {"protein_blastp_homology"},
        ),
    ],
)
def test_prepare_plan_recovers_with_ranked_fallback_templates(tmp_path, prompt: str, setup_kind: str, expected_ids: set[str]):
    workspace = tmp_path / setup_kind / "workspace"
    selected_dir = workspace
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_refs(workspace)

    if setup_kind in {"splicing", "somatic"}:
        _write_fastq(data_root / "1_S1_R1_001.fastq")
        _write_fastq(data_root / "1_S1_R2_001.fastq")
        _write_fastq(data_root / "6_S6_R1_001.fastq")
        _write_fastq(data_root / "6_S6_R2_001.fastq")
    elif setup_kind == "germline":
        _write_fastq(data_root / "1_S1_R1_001.fastq")
        _write_fastq(data_root / "1_S1_R2_001.fastq")
    elif setup_kind == "longread":
        _write_fastq(data_root / "nanopore_reads.fastq")
    elif setup_kind == "counts":
        out = selected_dir / "outputs"
        out.mkdir(parents=True, exist_ok=True)
        (out / "counts.tsv").write_text(
            "Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1\tS6\ng1\tchr1\t1\t2\t+\t2\t10\t11\n",
            encoding="utf-8",
        )
        (out / "metadata.tsv").write_text("sample\tcondition\nS1\tcontrol\nS6\ttreatment\n", encoding="utf-8")
    elif setup_kind == "protein":
        (selected_dir / "query.faa").write_text(">p1\nMTEYKLVVVG\n", encoding="utf-8")

    cfg = _make_cfg(prompt, selected_dir=selected_dir, data_root=data_root)
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    def _timeout_planner(_prompt: str, analysis_spec=None):
        raise TimeoutError("Planner request timed out while waiting for model output.")

    harness.orchestrator.think = _timeout_planner  # type: ignore[method-assign]
    harness._prepare_plan()

    selected_pipeline = str(harness.run.get("fallback_selection", {}).get("selected_pipeline_id", ""))
    if not selected_pipeline:
        selected_pipeline = (
            harness.run.get("fallback_selection", {})
            .get("selection", {})
            .get("selection", {})
            .get("pipeline_id", "")
        )
    assert selected_pipeline in expected_ids
    assert harness.run.get("plan", {}).get("plan", [])


def test_prepare_plan_somatic_degrades_when_gatk_missing(tmp_path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "somatic_no_gatk" / "workspace"
    selected_dir = workspace
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_refs(workspace)
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")

    cfg = _make_cfg("Call somatic variants in tumor vs normal using Mutect2.", selected_dir=selected_dir, data_root=data_root)
    harness = AgentE2EHarness(cfg)
    harness._init_run()

    def _timeout_planner(_prompt: str, analysis_spec=None):
        raise TimeoutError("Planner request timed out while waiting for model output.")

    def _fake_requirement(tool_name: str) -> bool:
        normalized = Path(str(tool_name)).name.lower()
        if normalized == "gatk":
            return False
        return normalized in {"bcftools", "bwa", "samtools", "star", "rscript", "fastqc", "rmats", "rmats.py"}

    def _fake_which(binary: str):
        name = Path(str(binary)).name.lower()
        if name == "gatk":
            return None
        return f"/mock/{name}"

    harness.orchestrator.think = _timeout_planner  # type: ignore[method-assign]
    monkeypatch.setattr(fallback_catalog_mod, "requirement_available", _fake_requirement)
    monkeypatch.setattr("bio_harness.harness.contract_utils.requirement_available", _fake_requirement)
    monkeypatch.setattr("bio_harness.harness.contract_utils._which_with_pixi", _fake_which)

    harness._prepare_plan()
    selected_pipeline = str(harness.run.get("fallback_selection", {}).get("selected_pipeline_id", ""))
    assert selected_pipeline == "somatic_variant_bcftools_tn_degrade"
    step_tools = [str(s.get("tool_name", "")) for s in harness.run.get("plan", {}).get("plan", [])]
    assert "gatk_mutect2_call" not in step_tools
    assert "bcftools_call" in step_tools
