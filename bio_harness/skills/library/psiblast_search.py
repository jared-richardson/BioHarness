from __future__ import annotations

from bio_harness.skills.library._blast_support import (
    BLAST_OUTFMT_DEFAULT,
    build_blast_search_command,
)


def psiblast_search(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    query_fasta = str(kwargs.get("query_fasta", "")).strip()
    database = str(kwargs.get("database", "")).strip()
    output_tsv = str(kwargs.get("output_tsv", "")).strip()
    if not query_fasta or not database or not output_tsv:
        raise ValueError("Missing required parameter(s) for template: database, output_tsv, query_fasta")
    flags: list[str] = []
    num_iterations = kwargs.get("num_iterations")
    if str(num_iterations or "").strip():
        flags.append(f"-num_iterations {int(num_iterations)}")
    inclusion_ethresh = str(kwargs.get("inclusion_ethresh", "")).strip()
    if inclusion_ethresh:
        flags.append(f"-inclusion_ethresh {inclusion_ethresh}")
    return build_blast_search_command(
        program="psiblast",
        query_fasta=query_fasta,
        database=database,
        output_tsv=output_tsv,
        dbtype="prot",
        threads=int(kwargs.get("threads", 2) or 2),
        evalue=str(kwargs.get("evalue", "1e-5")).strip() or "1e-5",
        outfmt=str(kwargs.get("outfmt", BLAST_OUTFMT_DEFAULT)).strip() or BLAST_OUTFMT_DEFAULT,
        extra_flags=" ".join(flags),
    )
