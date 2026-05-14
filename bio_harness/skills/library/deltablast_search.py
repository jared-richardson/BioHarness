from __future__ import annotations

from bio_harness.skills.library._blast_support import (
    BLAST_OUTFMT_DEFAULT,
    build_blast_search_command,
)


def deltablast_search(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    query_fasta = str(kwargs.get("query_fasta", "")).strip()
    database = str(kwargs.get("database", "")).strip()
    output_tsv = str(kwargs.get("output_tsv", "")).strip()
    if not query_fasta or not database or not output_tsv:
        raise ValueError("Missing required parameter(s) for template: database, output_tsv, query_fasta")
    flags: list[str] = []
    domain_database = str(kwargs.get("domain_database", "")).strip()
    if domain_database:
        flags.append(f"-rpsdb {domain_database}")
    return build_blast_search_command(
        program="deltablast",
        query_fasta=query_fasta,
        database=database,
        output_tsv=output_tsv,
        dbtype="prot",
        threads=int(kwargs.get("threads", 2) or 2),
        evalue=str(kwargs.get("evalue", "1e-5")).strip() or "1e-5",
        outfmt=str(kwargs.get("outfmt", BLAST_OUTFMT_DEFAULT)).strip() or BLAST_OUTFMT_DEFAULT,
        extra_flags=" ".join(flags),
    )
