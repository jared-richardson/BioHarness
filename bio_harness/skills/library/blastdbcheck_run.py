from __future__ import annotations

from bio_harness.skills.library._blast_support import build_blastdbcheck_command


def blastdbcheck_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    database = str(kwargs.get("database", "")).strip() or None
    directory = str(kwargs.get("directory", "")).strip() or None
    return build_blastdbcheck_command(
        database=database,
        directory=directory,
        dbtype=str(kwargs.get("dbtype", "guess")).strip() or "guess",
        verbosity=int(kwargs.get("verbosity", 2) or 2),
        full=bool(kwargs.get("full", False)),
        recursive=bool(kwargs.get("recursive", False)),
    )
