from __future__ import annotations

from pathlib import Path

from bio_harness.ui.deliverables import (
    capture_ui_run_final_outputs,
    materialize_ui_run_deliverables,
)


def test_materialize_ui_run_deliverables_exports_transcript_counts_into_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_001"
    quant_dir = tmp_path / "salmon_quant_out"
    quant_dir.mkdir(parents=True, exist_ok=True)
    (quant_dir / "quant.sf").write_text(
        "Name\tLength\tEffectiveLength\tTPM\tNumReads\n"
        "tx1\t1000\t900\t10.0\t42\n",
        encoding="utf-8",
    )
    run = {
        "run_dir": str(run_dir),
        "plan": {
            "plan": [
                {
                    "tool_name": "salmon_quant",
                    "arguments": {"output_dir": str(quant_dir)},
                }
            ]
        },
        "analysis_spec": {"analysis_type": "transcript_quantification", "protocol_grounding": {}},
    }

    result = materialize_ui_run_deliverables(run)

    exported = result["exported"]
    assert len(exported) == 1
    output_path = Path(exported[0]["output_path"])
    assert output_path == run_dir / "final" / "transcript_counts.tsv"
    assert output_path.exists()
    assert "tx1\t42" in output_path.read_text(encoding="utf-8")
    assert result["failures"] == []


def test_materialize_ui_run_deliverables_treats_stringtie_abundance_as_nonfatal(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_stringtie"
    run_dir.mkdir(parents=True, exist_ok=True)
    abundance_tsv = run_dir / "gene_abundances.tsv"
    abundance_tsv.write_text(
        "Gene ID\tGene Name\tReference\tStrand\tStart\tEnd\tCoverage\tFPKM\tTPM\n"
        "GENE1\tGeneOne\tchr14\t+\t1\t100\t12.0\t3.0\t5.0\n",
        encoding="utf-8",
    )
    run = {
        "run_dir": str(run_dir),
        "plan": {
            "plan": [
                {
                    "tool_name": "stringtie_quant",
                    "arguments": {},
                }
            ]
        },
        "analysis_spec": {"analysis_type": "transcript_quantification", "protocol_grounding": {}},
    }

    result = materialize_ui_run_deliverables(run)

    assert result["exported"] == []
    assert result["failures"] == []
    assert not (run_dir / "final" / "transcript_counts.tsv").exists()


def test_materialize_ui_run_deliverables_exports_single_cell_results_from_wrapper_output_dir(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_sc"
    output_dir = run_dir / "sc_output"
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
    run = {
        "run_dir": str(run_dir),
        "plan": {
            "plan": [
                {
                    "tool_name": "sc_count_and_cluster",
                    "arguments": {"output_dir": str(output_dir)},
                }
            ]
        },
        "analysis_spec": {"analysis_type": "single_cell_rna_seq", "protocol_grounding": {}},
    }

    result = materialize_ui_run_deliverables(run)

    exported = result["exported"]
    assert len(exported) == 4
    output_path = Path(exported[0]["output_path"])
    assert output_path == run_dir / "final" / "single_cell_results.csv"
    assert output_path.exists()
    assert (run_dir / "cluster_assignments.json").exists()
    assert (run_dir / "marker_genes.json").exists()
    assert (run_dir / "raw_counts.json").exists()
    assert result["failures"] == []


def test_materialize_ui_run_deliverables_exports_single_cell_results_from_scanpy_csv_outputs(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_scanpy"
    output_dir = run_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cluster_assignments.csv").write_text(
        "cell_id,cluster_id\nBC1,0\nBC2,0\nBC3,1\n",
        encoding="utf-8",
    )
    (output_dir / "marker_genes.csv").write_text(
        "cluster_id,rank,gene_name,score,logfoldchanges,pvals_adj\n"
        "0,1,CCL5,12.0,3.2,0.001\n"
        "1,1,MS4A1,10.5,2.8,0.002\n",
        encoding="utf-8",
    )
    run = {
        "run_dir": str(run_dir),
        "plan": {
            "plan": [
                {
                    "tool_name": "scanpy_workflow",
                    "arguments": {"output_dir": str(output_dir)},
                }
            ]
        },
        "analysis_spec": {"analysis_type": "single_cell_rna_seq", "protocol_grounding": {}},
    }

    result = materialize_ui_run_deliverables(run)

    exported = result["exported"]
    assert len(exported) == 1
    output_path = Path(exported[0]["output_path"])
    assert output_path == run_dir / "final" / "single_cell_results.csv"
    assert output_path.exists()
    contents = output_path.read_text(encoding="utf-8")
    assert "CCL5" in contents
    assert "MS4A1" in contents
    assert result["failures"] == []


def test_capture_ui_run_final_outputs_copies_explicit_phylogeny_tree_into_run_bundle(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_002"
    workspace_final = tmp_path / "workspace_final"
    workspace_final.mkdir(parents=True, exist_ok=True)
    output_tree = workspace_final / "phylogeny.treefile"
    output_tree.write_text("(a:0.1,b:0.2,c:0.3);\n", encoding="utf-8")
    run = {
        "run_dir": str(run_dir),
        "plan": {
            "plan": [
                {
                    "tool_name": "phylogenetics_iqtree_style",
                    "arguments": {"output_tree": str(output_tree)},
                }
            ]
        },
    }

    result = capture_ui_run_final_outputs(run)

    exported = result["exported"]
    assert len(exported) == 1
    copied_path = Path(exported[0]["output_path"])
    assert copied_path == run_dir / "final" / "phylogeny.treefile"
    assert copied_path.exists()
    assert copied_path.read_text(encoding="utf-8") == "(a:0.1,b:0.2,c:0.3);\n"
