from __future__ import annotations

from bio_harness.skills.library._blast_support import (
    BLAST_OUTFMT_DEFAULT,
    build_blast_formatter_command,
)


def blast_formatter_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    archive_file = str(kwargs.get("archive_file", "")).strip()
    output_file = str(kwargs.get("output_file", "")).strip()
    if not archive_file or not output_file:
        raise ValueError("Missing required parameter(s) for template: archive_file, output_file")
    return build_blast_formatter_command(
        archive_file=archive_file,
        output_file=output_file,
        outfmt=str(kwargs.get("outfmt", BLAST_OUTFMT_DEFAULT)).strip() or BLAST_OUTFMT_DEFAULT,
        html=bool(kwargs.get("html", False)),
        max_target_seqs=int(kwargs["max_target_seqs"]) if str(kwargs.get("max_target_seqs", "")).strip() else None,
        parse_deflines=bool(kwargs.get("parse_deflines", False)),
    )
