from __future__ import annotations

from pathlib import Path

from bio_harness.pipeline_scripts.infer_phylogeny_biopython import main


def test_infer_phylogeny_biopython_writes_newick_tree(tmp_path: Path) -> None:
    input_fasta = tmp_path / "sequences.fasta"
    input_fasta.write_text(
        "\n".join(
            [
                ">taxon_a",
                "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAN",
                ">taxon_b",
                "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEAQAN",
                ">taxon_c",
                "MKLAYIAKQRQISFVKSHFSRQLEERLGMIEAQAN",
                ">taxon_d",
                "GATAYIAKQRQISFVKSHFSRQLEERLGMIEAQAN",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output_tree = tmp_path / "final" / "phylogeny.treefile"

    rc = main(["--input-fasta", str(input_fasta), "--output-tree", str(output_tree)])

    assert rc == 0
    text = output_tree.read_text(encoding="utf-8").strip()
    assert text.endswith(";")
    for taxon in ("taxon_a", "taxon_b", "taxon_c", "taxon_d"):
        assert taxon in text
