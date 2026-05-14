from __future__ import annotations

from bio_harness.core.request_output_intent import (
    extract_requested_deliverable_paths,
    extract_requested_output_paths,
    extract_requested_output_root,
)


def test_extract_requested_output_paths_captures_results_under_directory() -> None:
    paths = extract_requested_output_paths(
        "Write results under /tmp/edger_results and do not use bash_run.",
    )

    assert paths == ["/tmp/edger_results"]


def test_extract_requested_output_root_accepts_output_to_directory() -> None:
    root = extract_requested_output_root(
        "Use limma_voom_run and output to /tmp/limma_results/.",
    )

    assert root == "/tmp/limma_results/"


def test_extract_requested_output_root_ignores_file_like_output_path() -> None:
    root = extract_requested_output_root(
        "Run DESeq2 and output to /tmp/final_result.csv.",
    )

    assert root == ""


def test_extract_requested_output_paths_captures_multiple_stringtie_files() -> None:
    paths = extract_requested_output_paths(
        (
            "Keep this on stringtie_quant and write outputs to "
            "/tmp/run/stringtie/assembled.gtf and /tmp/run/stringtie/gene_abundances.tsv."
        ),
    )

    assert paths == [
        "/tmp/run/stringtie/assembled.gtf",
        "/tmp/run/stringtie/gene_abundances.tsv",
    ]


def test_extract_requested_output_paths_captures_labeled_stringtie_outputs() -> None:
    paths = extract_requested_output_paths(
        (
            "Use only stringtie_quant on /tmp/sample.bam with /tmp/genes.gtf. "
            "Write the assembled transcript GTF to /tmp/run/stringtie/assembled.gtf "
            "and the gene abundance table to /tmp/run/stringtie/gene_abundances.tsv."
        ),
    )

    assert paths == [
        "/tmp/run/stringtie/assembled.gtf",
        "/tmp/run/stringtie/gene_abundances.tsv",
    ]


def test_extract_requested_output_paths_captures_output_only_directory() -> None:
    paths = extract_requested_output_paths(
        "scanpy_workflow on /tmp/pbmc3k_processed.h5ad output /tmp/run/scanpy_output only",
    )

    assert paths == ["/tmp/run/scanpy_output"]


def test_extract_requested_output_paths_captures_put_outputs_in_directory() -> None:
    paths = extract_requested_output_paths(
        "Use stringtie_quant on /tmp/sample.bam with /tmp/genes.gtf. Put outputs in /tmp/run/custom_stringtie/output_set.",
    )

    assert paths == ["/tmp/run/custom_stringtie/output_set"]


def test_extract_requested_deliverable_paths_captures_explicit_final_csv() -> None:
    paths = extract_requested_deliverable_paths(
        (
            "Use deseq2_run directly, write intermediate outputs under /tmp/run/deseq_results, "
            "and write the final CSV to /tmp/run/final/deseq_results.csv."
        ),
    )

    assert paths == ["/tmp/run/final/deseq_results.csv"]
