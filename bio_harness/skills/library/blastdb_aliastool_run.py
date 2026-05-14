from __future__ import annotations

from bio_harness.skills.library._blast_support import build_blastdb_aliastool_command


def blastdb_aliastool_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    dblist_raw = kwargs.get("dblist", [])
    if isinstance(dblist_raw, str):
        dblist = [token for token in dblist_raw.split() if token.strip()]
    else:
        dblist = [str(token).strip() for token in dblist_raw if str(token).strip()]
    output_alias = str(kwargs.get("output_alias", "")).strip()
    dbtype = str(kwargs.get("dbtype", "")).strip()
    if not dblist or not output_alias or not dbtype:
        raise ValueError("Missing required parameter(s) for template: dblist, dbtype, output_alias")
    return build_blastdb_aliastool_command(
        dblist=dblist,
        dbtype=dbtype,
        output_alias=output_alias,
        title=str(kwargs.get("title", "")).strip() or None,
        num_volumes=int(kwargs["num_volumes"]) if str(kwargs.get("num_volumes", "")).strip() else None,
    )
