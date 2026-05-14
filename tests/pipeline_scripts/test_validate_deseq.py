from __future__ import annotations

from pathlib import Path

from scripts.validate_deseq import validate


def test_validate_deseq_accepts_matching_significant_gene_set(tmp_path: Path) -> None:
    truth = tmp_path / "truth.csv"
    output = tmp_path / "output.csv"
    truth.write_text(
        "gene_id,log2FoldChange,pvalue,padj\n"
        "CPAR2_600150,2.50,1e-40,1e-20\n"
        "CPAR2_600160,3.10,1e-30,1e-15\n",
        encoding="utf-8",
    )
    output.write_text(
        "gene_id,log2FoldChange,pvalue,padj\n"
        "CPAR2_600150,2.55,1e-39,2e-20\n"
        "CPAR2_600160,3.00,1e-29,2e-15\n"
        "CPAR2_600999,1.50,1e-02,2e-02\n",
        encoding="utf-8",
    )

    assert validate(truth, output) is True


def test_validate_deseq_rejects_wrong_gene_set(tmp_path: Path) -> None:
    truth = tmp_path / "truth.csv"
    output = tmp_path / "output.csv"
    truth.write_text(
        "gene_id,log2FoldChange,pvalue,padj\n"
        "CPAR2_600150,2.50,1e-40,1e-20\n"
        "CPAR2_600160,3.10,1e-30,1e-15\n",
        encoding="utf-8",
    )
    output.write_text(
        "gene_id,log2FoldChange,pvalue,padj\n"
        "CPAR2_123456,5.00,1e-40,1e-20\n"
        "CPAR2_654321,3.50,1e-30,1e-15\n",
        encoding="utf-8",
    )

    assert validate(truth, output) is False
