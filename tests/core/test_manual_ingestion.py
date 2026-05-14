from __future__ import annotations

from bio_harness.core.manual_ingestion import (
    collect_documentation_sources,
    ingest_tool_documentation,
    render_manual_ingestion_result,
)


def test_ingest_tool_documentation_extracts_structured_guidance() -> None:
    help_text = """
Usage: stringtie [options] -G genes.gtf -o assembled.gtf reads.bam

Output files:
  assembled.gtf
  gene_abundances.tsv

Examples:
  stringtie -G genes.gtf -o assembled.gtf reads.bam

Warnings:
  Do not use this mode for gene-level counting only.
  --force will overwrite an existing output.
  Error: missing reference annotation GTF. Recheck the -G path.
"""
    result = ingest_tool_documentation(
        "stringtie",
        help_text=help_text,
        readme_text="StringTie assembles and quantifies transcripts from aligned RNA-seq reads.",
    )

    assert result.when_to_use.startswith("StringTie assembles and quantifies")
    assert result.when_not_to_use == "Do not use this mode for gene-level counting only."
    assert list(result.canonical_outputs) == ["assembled.gtf", "gene_abundances.tsv"]
    assert list(result.dangerous_flags) == ["--force"]
    assert result.example_invocations == (
        "stringtie -G genes.gtf -o assembled.gtf reads.bam",
    )
    assert result.common_errors[0]["pattern"].startswith("Error: missing reference annotation GTF")


def test_collect_documentation_sources_uses_normalized_hits_and_librarian_fallback() -> None:
    class _FakeLibrarian:
        def web_search(self, query: str, max_results: int = 3, allowed_domains=None):
            assert "samtools" in query
            assert allowed_domains == ["www.htslib.org"]
            return [
                {
                    "title": "samtools docs",
                    "href": "https://www.htslib.org/doc/samtools.html",
                    "body": "samtools view writes output.bam and supports --output-fmt.",
                }
            ]

    sources = collect_documentation_sources(
        "samtools",
        help_text="samtools --help",
        source_meta={"source": "https://www.htslib.org/doc/samtools.html", "mode": "official_docs"},
        librarian=_FakeLibrarian(),
    )

    assert [source.kind for source in sources] == ["help_text", "web"]
    assert sources[1].source == "https://www.htslib.org/doc/samtools.html"
    assert "output.bam" in sources[1].text


def test_render_manual_ingestion_result_returns_json_ready_mapping() -> None:
    result = ingest_tool_documentation(
        "bcftools",
        help_text="""
Usage: bcftools stats --output summary.txt input.vcf
Example: bcftools stats --output summary.txt input.vcf
""",
        web_hits=[{"title": "bcftools", "href": "https://samtools.github.io/bcftools", "body": "Produces summary.txt"}],
    )

    rendered = render_manual_ingestion_result(result)

    assert rendered["canonical_outputs"] == ["summary.txt"]
    assert rendered["source_documents"] == [
        "help_text:bcftools",
        "https://samtools.github.io/bcftools",
    ]
