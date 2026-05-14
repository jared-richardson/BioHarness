from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_harness.workflows.fallback_catalog import build_ranked_fallback_catalog, select_ranked_fallback_plan


FIXTURES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "uncommon_prompt_intents.json").read_text(encoding="utf-8")
)


def _write_fastq(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")


def _write_refs(workspace: Path) -> tuple[str, str]:
    inputs = workspace / "inputs_readonly"
    inputs.mkdir(parents=True, exist_ok=True)
    fasta = inputs / "mouse_fasta"
    gtf = inputs / "mouse_gtf"
    fasta.write_text(">chr1\nACGTACGT\n", encoding="utf-8")
    gtf.write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    return str(fasta), str(gtf)


def _all_tools_available_override() -> dict[str, bool]:
    tools: set[str] = set()
    for row in build_ranked_fallback_catalog():
        for tool in row.get("required_tools", []):
            tools.add(str(tool))
            tools.add(str(tool).lower())
    return {tool: True for tool in tools}


@pytest.mark.parametrize("row", FIXTURES, ids=[str(item.get("id", "case")) for item in FIXTURES])
def test_uncommon_prompt_fixture_selection(row: dict, tmp_path: Path):
    workspace = tmp_path / str(row.get("id", "case")) / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    fasta, gtf = _write_refs(workspace)

    setup = str(row.get("setup", "")).strip()
    if setup in {"paired_fastq", "paired_with_ref"}:
        _write_fastq(data_root / "1_S1_R1_001.fastq")
        _write_fastq(data_root / "1_S1_R2_001.fastq")
    if setup == "bam_with_ref":
        bam = workspace / "outputs" / "sample.bam"
        bam.parent.mkdir(parents=True, exist_ok=True)
        bam.write_bytes(b"BAM")
    if setup == "protein_fasta":
        (workspace / "query.faa").write_text(">p1\nACGTACGT\n", encoding="utf-8")

    contract = dict(row.get("contract", {}))
    plan, details = select_ranked_fallback_plan(
        contract=contract,
        prompt=str(row.get("prompt", "")),
        data_root=str(data_root),
        selected_dir=str(workspace),
        reference_fasta=fasta,
        annotation_gtf=gtf,
        tool_availability_override=_all_tools_available_override(),
    )

    assert plan is not None
    selected = str(details.get("selection", {}).get("pipeline_id", ""))
    assert selected == str(row.get("expected_pipeline_id", ""))
