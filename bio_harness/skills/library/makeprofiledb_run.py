from __future__ import annotations

from bio_harness.skills.library._blast_support import build_makeprofiledb_command


def makeprofiledb_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    input_list = str(kwargs.get("input_list", "")).strip()
    output_prefix = str(kwargs.get("output_prefix", "")).strip()
    if not input_list or not output_prefix:
        raise ValueError("Missing required parameter(s) for template: input_list, output_prefix")
    return build_makeprofiledb_command(
        input_list=input_list,
        output_prefix=output_prefix,
        dbtype=str(kwargs.get("dbtype", "rps")).strip() or "rps",
        title=str(kwargs.get("title", "")).strip() or None,
        binary=bool(kwargs.get("binary", False)),
        index=bool(kwargs.get("index", True)),
        threshold=float(kwargs["threshold"]) if str(kwargs.get("threshold", "")).strip() else None,
        matrix=str(kwargs.get("matrix", "")).strip() or None,
    )
