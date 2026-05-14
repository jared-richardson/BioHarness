from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_harness.core.path_graph_store import PathGraphStore, default_path_graph_db_path
from bio_harness.workflows.fallback_catalog import build_ranked_fallback_catalog, select_ranked_fallback_plan


FIXTURE_ROWS = json.loads(
    (Path(__file__).resolve().parents[1] / "core" / "fixtures" / "path_graph_preference_profiles.json").read_text(
        encoding="utf-8"
    )
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


def _tool_override_all_true() -> dict[str, bool]:
    return {
        "star": True,
        "hisat2": True,
        "bwa": True,
        "bowtie2": True,
        "minimap2": True,
        "samtools": True,
        "gatk": True,
        "bcftools": True,
        "freebayes": True,
        "Rscript": True,
        "rmats": True,
        "majiq": True,
        "blastp": True,
        "hmmscan": True,
        "prokka": True,
        "vep": True,
        "snpEff": True,
        "featureCounts": True,
        "bismark": True,
        "kraken2": True,
        "bracken": True,
        "star-fusion": True,
        "cnvkit.py": True,
        "mixcr": True,
        "iqtree2": True,
    }


@pytest.mark.parametrize("row", FIXTURE_ROWS, ids=[str(item.get("name", "case")) for item in FIXTURE_ROWS])
def test_preference_profile_fixtures_are_stable_and_deterministic(tmp_path, row: dict):
    workspace = tmp_path / str(row.get("name", "case")) / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    fasta, gtf = _write_refs(workspace)

    setup = str(row.get("setup", "")).strip()
    if setup == "somatic_fastq":
        _write_fastq(data_root / "1_S1_R1_001.fastq")
        _write_fastq(data_root / "1_S1_R2_001.fastq")
        _write_fastq(data_root / "6_S6_R1_001.fastq")
        _write_fastq(data_root / "6_S6_R2_001.fastq")
    elif setup == "protein_query":
        (workspace / "query.faa").write_text(">p1\nMTEYKLVVVG\n", encoding="utf-8")
    elif setup == "counts_and_metadata":
        out = workspace / "outputs"
        out.mkdir(parents=True, exist_ok=True)
        (out / "counts.tsv").write_text(
            "Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1\tS6\ng1\tchr1\t1\t2\t+\t2\t10\t11\n",
            encoding="utf-8",
        )
        (out / "metadata.tsv").write_text("sample\tcondition\nS1\tcontrol\nS6\ttreatment\n", encoding="utf-8")

    graph_store = PathGraphStore(default_path_graph_db_path(workspace))
    graph_store.ensure_catalog_paths(build_ranked_fallback_catalog())

    kwargs = {
        "contract": dict(row.get("contract", {})),
        "prompt": str(row.get("prompt", "")),
        "data_root": str(data_root),
        "selected_dir": str(workspace),
        "reference_fasta": fasta,
        "annotation_gtf": gtf,
        "tool_availability_override": _tool_override_all_true(),
        "graph_store": graph_store,
        "preference_profile": dict(row.get("preferences", {})),
    }

    plan_a, details_a = select_ranked_fallback_plan(**kwargs)
    plan_b, details_b = select_ranked_fallback_plan(**kwargs)

    assert plan_a is not None
    assert plan_b is not None

    selected_a = str(details_a.get("selection", {}).get("pipeline_id", ""))
    selected_b = str(details_b.get("selection", {}).get("pipeline_id", ""))
    assert selected_a == str(row.get("expected_pipeline_id", ""))
    assert selected_b == selected_a

    top_a = [str(x.get("pipeline_id", "")) for x in details_a.get("candidates", [])[:5] if isinstance(x, dict)]
    top_b = [str(x.get("pipeline_id", "")) for x in details_b.get("candidates", [])[:5] if isinstance(x, dict)]
    assert top_a == top_b
