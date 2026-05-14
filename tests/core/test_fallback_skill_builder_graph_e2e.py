from __future__ import annotations

import sqlite3
from pathlib import Path

from bio_harness.core.fallback_skill_builder import FallbackBuilderRequest, run_fallback_skill_builder
from bio_harness.core.path_graph_store import default_path_graph_db_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_fastq(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@r1\nACGT\n+\n!!!!\n", encoding="utf-8")


def _write_refs(workspace: Path) -> None:
    inp = workspace / "inputs_readonly"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "mouse_fasta").write_text(">chr1\nACGT\n", encoding="utf-8")
    (inp / "mouse_gtf").write_text("chr1\tsrc\tgene\t1\t2\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")


def test_fallback_builder_updates_graph_and_rerank_is_stable(tmp_path):
    workspace = tmp_path / "workspace"
    data_root = workspace / "inputs_readonly"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_refs(workspace)
    _write_fastq(data_root / "1_S1_R1_001.fastq")
    _write_fastq(data_root / "1_S1_R2_001.fastq")
    _write_fastq(data_root / "6_S6_R1_001.fastq")
    _write_fastq(data_root / "6_S6_R2_001.fastq")

    graph_db = default_path_graph_db_path(workspace)
    constraints = {
        "path_graph_db": str(graph_db),
        "path_graph_user_key": "builder_e2e",
        "path_graph_scope": "global",
        "preference_profile": {
            "tool_blacklist": ["gatk"],
            "mode": "conservative",
        },
    }
    request = FallbackBuilderRequest.from_raw(
        target_capability_set=["alignment", "variant_calling", "group_comparison", "reference_inputs"],
        allowed_tools=["bcftools", "bwa", "samtools", "gatk"],
        data_reference_constraints=constraints,
        strictness_mode="conservative",
        request_text="Call somatic variants in tumor vs normal using Mutect2.",
        selected_dir=str(workspace),
        data_root=str(data_root),
    )

    report_one = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=request)
    report_two = run_fallback_skill_builder(project_root=PROJECT_ROOT, request=request)

    pipeline_one = str(report_one.get("selection_details", {}).get("selection", {}).get("pipeline_id", ""))
    pipeline_two = str(report_two.get("selection_details", {}).get("selection", {}).get("pipeline_id", ""))
    assert pipeline_one == "somatic_variant_bcftools_tn_degrade"
    assert pipeline_two == pipeline_one

    with sqlite3.connect(str(graph_db)) as conn:
        run_count = conn.execute(
            "SELECT COUNT(*) FROM path_runs WHERE path_id=?",
            (pipeline_one,),
        ).fetchone()[0]
        metrics = conn.execute(
            "SELECT success_rate, quality_score FROM path_metrics WHERE path_id=?",
            (pipeline_one,),
        ).fetchone()

    assert int(run_count) >= 4
    assert metrics is not None
    assert float(metrics[0]) >= 0.0
    assert float(metrics[1]) >= 0.0
