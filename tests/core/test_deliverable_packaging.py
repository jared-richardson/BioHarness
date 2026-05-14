from __future__ import annotations

from pathlib import Path

from bio_harness.harness.deliverable_packaging import package_deliverables


def test_package_deliverables_mirrors_single_cell_sidecars(tmp_path: Path) -> None:
    selected_dir = tmp_path / "run_sc"
    output_dir = selected_dir / "sc_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cluster_assignments.json").write_text(
        '{"BC1":"0","BC2":"0","BC3":"1","BC4":"1"}',
        encoding="utf-8",
    )
    (output_dir / "marker_genes.json").write_text(
        '{"0":["Gene0000","Gene0001"],"1":["Gene0015","Gene0016"]}',
        encoding="utf-8",
    )
    (output_dir / "raw_counts.json").write_text(
        '{"BC1":{"Gene0000":9},"BC2":{"Gene0001":8},"BC3":{"Gene0015":10},"BC4":{"Gene0016":7}}',
        encoding="utf-8",
    )

    result = package_deliverables(
        selected_dir=selected_dir,
        analysis_spec={"analysis_type": "single_cell_rna_seq", "protocol_grounding": {}},
        plan={
            "plan": [
                {
                    "tool_name": "sc_count_and_cluster",
                    "arguments": {"output_dir": str(output_dir)},
                }
            ]
        },
    )

    exported = result["exported"]
    assert len(exported) == 4
    assert (selected_dir / "final" / "single_cell_results.csv").exists()
    assert (selected_dir / "cluster_assignments.json").exists()
    assert (selected_dir / "marker_genes.json").exists()
    assert (selected_dir / "raw_counts.json").exists()
    assert result["failures"] == []


def test_package_deliverables_respects_requested_deseq_final_csv(tmp_path: Path) -> None:
    selected_dir = tmp_path / "run_de"
    output_dir = selected_dir / "my_analysis" / "de_intermediate"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "deseq2_results.tsv").write_text(
        "baseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj\tgene_id\n"
        "10\t2.5\t0.1\t3.0\t0.001\t0.005\tCPAR2_600150\n",
        encoding="utf-8",
    )

    result = package_deliverables(
        selected_dir=selected_dir,
        analysis_spec={
            "analysis_type": "rna_seq_differential_expression",
            "required_deliverables": [str(selected_dir / "my_analysis" / "final_result.csv")],
        },
        plan={
            "plan": [
                {
                    "tool_name": "deseq2_run",
                    "arguments": {"output_dir": str(output_dir)},
                }
            ]
        },
    )

    assert (selected_dir / "my_analysis" / "final_result.csv").exists()
    assert result["failures"] == []
    assert result["exported"][0]["output_path"].endswith("my_analysis/final_result.csv")
