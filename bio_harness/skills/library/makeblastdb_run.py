from __future__ import annotations

from bio_harness.skills.library._blast_support import build_makeblastdb_run_command


def makeblastdb_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    input_fasta = str(kwargs.get("input_fasta", "")).strip()
    output_prefix = str(kwargs.get("output_prefix", "")).strip()
    dbtype = str(kwargs.get("dbtype", "")).strip()
    if not input_fasta or not output_prefix or not dbtype:
        raise ValueError("Missing required parameter(s) for template: dbtype, input_fasta, output_prefix")
    return build_makeblastdb_run_command(
        input_fasta=input_fasta,
        output_prefix=output_prefix,
        dbtype=dbtype,
        title=str(kwargs.get("title", "")).strip() or None,
        parse_seqids=bool(kwargs.get("parse_seqids", False)),
        input_type=str(kwargs.get("input_type", "fasta")).strip() or "fasta",
    )
