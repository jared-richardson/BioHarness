from __future__ import annotations

from bio_harness.skills.library._blast_support import build_blastdbcmd_command


def blastdbcmd_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    database = str(kwargs.get("database", "")).strip()
    if not database:
        raise ValueError("Missing required parameter(s) for template: database")
    return build_blastdbcmd_command(
        database=database,
        output_file=str(kwargs.get("output_file", "")).strip() or None,
        dbtype=str(kwargs.get("dbtype", "guess")).strip() or "guess",
        entry=str(kwargs.get("entry", "")).strip() or None,
        entry_batch=str(kwargs.get("entry_batch", "")).strip() or None,
        outfmt=str(kwargs.get("outfmt", "")).strip() or None,
        info=bool(kwargs.get("info", False)),
        metadata=bool(kwargs.get("metadata", False)),
        tax_info=bool(kwargs.get("tax_info", False)),
        show_search_path=bool(kwargs.get("show_search_path", False)),
        range_spec=str(kwargs.get("range_spec", "")).strip() or None,
        strand=str(kwargs.get("strand", "")).strip() or None,
    )
