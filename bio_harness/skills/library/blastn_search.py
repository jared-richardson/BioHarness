from __future__ import annotations

from bio_harness.skills.library._blast_support import (
    BLAST_OUTFMT_DEFAULT,
    build_blast_search_command,
)


def blastn_search(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    query_fasta = str(kwargs.get("query_fasta", "")).strip()
    database = str(kwargs.get("database", "")).strip()
    output_tsv = str(kwargs.get("output_tsv", "")).strip()
    if not query_fasta or not database or not output_tsv:
        raise ValueError("Missing required parameter(s) for template: database, output_tsv, query_fasta")
    task = str(kwargs.get("task", "")).strip()
    extra_flags = f"-task {task}" if task else ""
    return build_blast_search_command(
        program="blastn",
        query_fasta=query_fasta,
        database=database,
        output_tsv=output_tsv,
        dbtype="nucl",
        threads=int(kwargs.get("threads", 2) or 2),
        evalue=str(kwargs.get("evalue", "1e-10")).strip() or "1e-10",
        outfmt=str(kwargs.get("outfmt", BLAST_OUTFMT_DEFAULT)).strip() or BLAST_OUTFMT_DEFAULT,
        extra_flags=extra_flags,
    )
