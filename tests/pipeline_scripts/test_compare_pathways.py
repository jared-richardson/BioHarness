from __future__ import annotations

import sys
import types
from pathlib import Path

import pandas as pd

import bio_harness.pipeline_scripts.compare_pathways as compare_pathways
from bio_harness.pipeline_scripts.compare_pathways import (
    _differential_expression_from_counts,
    _differential_expression_with_background_from_counts,
    _genes_with_background_from_precomputed_de,
)
from bio_harness.pipeline_scripts.kegg_reference import KeggHumanReference


def test_differential_expression_from_counts_uses_filtered_index(tmp_path: Path, monkeypatch):
    counts_path = tmp_path / "GSE168137_countList.txt"
    counts_path.write_text(
        "\n".join(
            [
                "gene\t5xFAD;sample1\t5xFAD;sample2\tBL6_sample1\tBL6_sample2",
                "ENSMUSG0001\t10\t11\t1\t1",
                "ENSMUSG0002\t0\t0\t0\t0",
                "ENSMUSG0003\t8\t9\t1\t2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        compare_pathways,
        "_annotate_ensembl_ids",
        lambda index, *, gene_name_map=None: pd.Series(["APP" for _ in index], index=index),
    )

    output_dir = tmp_path / "out"
    gene_names, deg_path = _differential_expression_from_counts(counts_path, label="5xFAD", output_dir=output_dir)

    assert deg_path.exists()
    text = deg_path.read_text(encoding="utf-8")
    assert "gene_id" in text
    assert isinstance(gene_names, list)


def test_differential_expression_from_counts_uses_relaxed_min_samples_for_3xtg(tmp_path: Path, monkeypatch):
    counts_path = tmp_path / "GSE161904_Raw_gene_counts_cortex.txt"
    counts_path.write_text(
        "\n".join(
            [
                "gene\tG3R1_Cortex_3xTgAD\tG3R3_Cortex_3xTgAD\tG3R4_Cortex_3xTgAD\tG3R10_Cortex_WT\tG3R7_Cortex_WT\tG3R9_Cortex_WT",
                "ENSMUSG0001\t10\t11\t9\t1\t1\t1",
                "ENSMUSG0002\t1\t0\t0\t0\t0\t0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        compare_pathways,
        "_annotate_ensembl_ids",
        lambda index, *, gene_name_map=None: pd.Series(["APP" for _ in index], index=index),
    )

    output_dir = tmp_path / "out"
    gene_names, deg_path = _differential_expression_from_counts(counts_path, label="3xTG_AD", output_dir=output_dir)

    assert deg_path.exists()
    assert isinstance(gene_names, list)


def test_differential_expression_from_counts_uses_legacy_ttest_by_default(
    tmp_path: Path,
    monkeypatch,
):
    counts_path = tmp_path / "GSE161904_Raw_gene_counts_cortex.txt"
    counts_path.write_text(
        "\n".join(
            [
                "gene\tG3R1_Cortex_3xTgAD\tG3R3_Cortex_3xTgAD\tG3R4_Cortex_3xTgAD\tG3R10_Cortex_WT\tG3R7_Cortex_WT\tG3R9_Cortex_WT",
                "ENSMUSG0001\t10\t11\t9\t1\t1\t1",
                "ENSMUSG0002\t1\t0\t0\t0\t0\t0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        compare_pathways,
        "_annotate_ensembl_ids",
        lambda index, *, gene_name_map=None: pd.Series(["APP" for _ in index], index=index),
    )

    def _unexpected_pydeseq2(*_args, **_kwargs):
        raise AssertionError("PyDESeq2 should not run unless explicitly enabled.")

    monkeypatch.setattr(compare_pathways, "_try_run_pydeseq2", _unexpected_pydeseq2)

    output_dir = tmp_path / "out"
    gene_names, _background_gene_names, deg_path = _differential_expression_with_background_from_counts(
        counts_path,
        label="3xTG_AD",
        output_dir=output_dir,
    )

    assert deg_path.exists()
    assert isinstance(gene_names, list)


def test_differential_expression_from_counts_supports_opt_in_pydeseq2(
    tmp_path: Path,
    monkeypatch,
):
    counts_path = tmp_path / "GSE161904_Raw_gene_counts_cortex.txt"
    counts_path.write_text(
        "\n".join(
            [
                "gene\tG3R1_Cortex_3xTgAD\tG3R3_Cortex_3xTgAD\tG3R4_Cortex_WT\tG3R10_Cortex_WT",
                "ENSMUSG0001\t10\t11\t1\t1",
                "ENSMUSG0002\t8\t9\t1\t1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        compare_pathways,
        "_annotate_ensembl_ids",
        lambda index, *, gene_name_map=None: pd.Series(["APP" for _ in index], index=index),
    )

    fake_result = pd.DataFrame(
        {
            "gene_id": ["ENSMUSG0001"],
            "log2fc": [1.2],
            "pval": [0.001],
            "p_adj": [0.01],
            "gene_name": ["APP"],
        }
    ).set_index(pd.Index(["ENSMUSG0001"]))

    monkeypatch.setattr(compare_pathways, "_try_run_pydeseq2", lambda *_args, **_kwargs: fake_result)

    output_dir = tmp_path / "out"
    gene_names, _background_gene_names, deg_path = _differential_expression_with_background_from_counts(
        counts_path,
        label="3xTG_AD",
        output_dir=output_dir,
        use_pydeseq2=True,
    )

    written = pd.read_csv(deg_path)
    assert gene_names == ["APP"]
    assert list(written["gene_name"]) == ["APP"]


def test_genes_with_background_from_precomputed_de_uses_legacy_cutoff_by_default(
    tmp_path: Path,
):
    precomputed_path = tmp_path / "DEA_PS3O1S.csv"
    precomputed_path.write_text(
        "\n".join(
            [
                "gene_id,gene_name,pval,log2fc",
                "ENSMUSG0001,APP,0.01,0.60",
                "ENSMUSG0002,PSEN1,0.01,0.80",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    default_genes, _background_genes, deg_path = _genes_with_background_from_precomputed_de(
        precomputed_path,
        label="PS3O1S",
        output_dir=tmp_path / "out_default",
    )
    relaxed_genes, _background_genes_relaxed, _ = _genes_with_background_from_precomputed_de(
        precomputed_path,
        label="PS3O1S",
        output_dir=tmp_path / "out_relaxed",
        log2fc_cutoff=0.5,
    )

    written = pd.read_csv(deg_path)
    assert default_genes == ["PSEN1"]
    assert relaxed_genes == ["APP", "PSEN1"]
    assert list(written["gene_name"]) == ["PSEN1"]


def test_run_kegg_enrichment_prefers_enrichr_with_local_cache(
    tmp_path: Path,
    monkeypatch,
):
    fake_df = pd.DataFrame(
        [
            {"Term": "Phagosome Homo sapiens hsa04145", "P-value": 1.0e-9},
            {"Term": "Glycosaminoglycan degradation Homo sapiens hsa00531", "P-value": 4.0e-5},
        ]
    )

    class _FakeEnrichrResult:
        def __init__(self, frame: pd.DataFrame) -> None:
            self.results = frame

    fake_gseapy = types.SimpleNamespace(
        enrichr=lambda **_kwargs: _FakeEnrichrResult(fake_df),
    )
    monkeypatch.setitem(sys.modules, "gseapy", fake_gseapy)
    monkeypatch.setattr(compare_pathways, "DEFAULT_ENRICHR_CACHE_DIR", tmp_path / "cache")

    out_path = compare_pathways._run_kegg_enrichment(
        ["APP", "TREM2"],
        background_gene_names=["APP", "TREM2", "PSEN1"],
        label="5xFAD",
        output_dir=tmp_path / "out",
        reference=KeggHumanReference(symbol_to_gids={}, pathway_names={}, pathway_gids={}),
    )

    written = pd.read_csv(out_path)
    assert list(written["Term"]) == list(fake_df["Term"])
    assert any((tmp_path / "cache").glob("*.csv"))


def test_run_kegg_enrichment_falls_back_to_reference_when_enrichr_fails(
    tmp_path: Path,
    monkeypatch,
):
    fake_gseapy = types.SimpleNamespace(
        enrichr=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("rate limited")),
    )
    monkeypatch.setitem(sys.modules, "gseapy", fake_gseapy)
    monkeypatch.setattr(compare_pathways, "ENRICHR_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(compare_pathways, "DEFAULT_ENRICHR_CACHE_DIR", tmp_path / "cache")

    reference = KeggHumanReference(
        symbol_to_gids={"APP": ("hsa:351",), "TREM2": ("hsa:54209",)},
        pathway_names={"hsa04145": "Phagosome Homo sapiens hsa04145"},
        pathway_gids={"hsa04145": ("hsa:351", "hsa:54209")},
    )

    out_path = compare_pathways._run_kegg_enrichment(
        ["APP", "TREM2"],
        background_gene_names=["APP", "TREM2", "PSEN1"],
        label="5xFAD",
        output_dir=tmp_path / "out",
        reference=reference,
    )

    written = pd.read_csv(out_path)
    assert list(written.columns) == ["Pathway", "P-value"]
    assert written.iloc[0]["Pathway"] == "Phagosome Homo sapiens hsa04145"
