from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi

BLAST_OUTFMT_DEFAULT = "6 qseqid sseqid pident length evalue bitscore"

_DB_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "prot": (".pin", ".pal", ".psq", ".phr", ".pdb", ".pot", ".ptf", ".pto", ".pjs"),
    "nucl": (".nin", ".nal", ".nsq", ".nhr", ".ndb", ".not", ".ntf", ".nto", ".njs"),
}
_DBTYPE_ALIASES: dict[str, str] = {
    "prot": "prot",
    "protein": "prot",
    "proteins": "prot",
    "aa": "prot",
    "amino": "prot",
    "amino_acid": "prot",
    "amino-acid": "prot",
    "nucl": "nucl",
    "nucleotide": "nucl",
    "nucleotides": "nucl",
    "dna": "nucl",
    "rna": "nucl",
}


def _validated_dbtype(dbtype: str) -> str:
    token = str(dbtype or "").strip().lower()
    normalized = _DBTYPE_ALIASES.get(token, token)
    if normalized not in {"prot", "nucl"}:
        raise ValueError("dbtype must be one of: prot, nucl")
    return normalized


def build_makeblastdb_run_command(
    *,
    input_fasta: str,
    output_prefix: str,
    dbtype: str,
    title: str | None = None,
    parse_seqids: bool = False,
    input_type: str = "fasta",
) -> str:
    input_path = str(input_fasta or "").strip()
    output_path = str(output_prefix or "").strip()
    if not input_path or not output_path:
        raise ValueError("input_fasta and output_prefix are required")
    dbtype_value = _validated_dbtype(dbtype)
    makeblastdb_cmd = which_with_pixi("makeblastdb") or "makeblastdb"
    path_prefix = shell_path_prefix("makeblastdb")
    out_dir = str(Path(output_path).expanduser().parent)
    title_flag = f" -title {shlex.quote(str(title).strip())}" if str(title or "").strip() else ""
    parse_flag = " -parse_seqids" if parse_seqids else ""
    command = (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"mkdir -p {shlex.quote(out_dir)}; "
        f"{makeblastdb_cmd} -in {shlex.quote(input_path)} -input_type {shlex.quote(str(input_type).strip() or 'fasta')} "
        f"-dbtype {dbtype_value} -out {shlex.quote(output_path)}{title_flag}{parse_flag}"
    )
    return f"bash -c {shlex.quote(command)}"


def build_blast_search_command(
    *,
    program: str,
    query_fasta: str,
    database: str,
    output_tsv: str,
    dbtype: str,
    threads: int = 2,
    evalue: str = "1e-5",
    outfmt: str = BLAST_OUTFMT_DEFAULT,
    extra_flags: str = "",
    database_strategy: str = "auto",
) -> str:
    query_path = str(query_fasta or "").strip()
    database_value = str(database or "").strip()
    output_path = str(output_tsv or "").strip()
    if not query_path or not database_value or not output_path:
        raise ValueError("query_fasta, database, and output_tsv are required")
    dbtype_value = _validated_dbtype(dbtype)
    strategy = str(database_strategy or "auto").strip().lower()
    if strategy not in {"auto", "db_only"}:
        raise ValueError("database_strategy must be one of: auto, db_only")
    blast_cmd = which_with_pixi(program) or program
    makeblastdb_cmd = which_with_pixi("makeblastdb") or "makeblastdb"
    makeblastdb_available = which_with_pixi("makeblastdb") is not None
    path_prefix = shell_path_prefix(program, "makeblastdb")
    out_dir = str(Path(output_path).expanduser().parent)
    local_db = str(Path(out_dir) / "query_self_db")
    extension_checks = " || ".join(
        f"[ -f \"${{db}}{ext}\" ]" for ext in _DB_EXTENSIONS[dbtype_value]
    )
    makeblastdb_flags = f" -in \"$db\" -input_type fasta -dbtype {dbtype_value} -out \"$db\""
    make_local_flags = (
        f" -in {shlex.quote(query_path)} -input_type fasta -dbtype {dbtype_value} -out \"$db\""
    )
    makeblastdb_guard = "true" if makeblastdb_available else "false"
    extra = str(extra_flags or "").strip()
    extra_fragment = f" {extra}" if extra else ""
    if strategy == "db_only":
        command = (
            "set -euo pipefail; "
            f"export PATH={shlex.quote(path_prefix)}:$PATH; "
            f"mkdir -p {shlex.quote(out_dir)}; "
            f"{blast_cmd} -query {shlex.quote(query_path)} -db {shlex.quote(database_value)} "
            f"-out {shlex.quote(output_path)} -outfmt {shlex.quote(outfmt)} -num_threads {int(threads)} "
            f"-evalue {shlex.quote(str(evalue).strip() or '1e-5')}{extra_fragment}"
        )
        return f"bash -c {shlex.quote(command)}"
    command = (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"mkdir -p {shlex.quote(out_dir)}; "
        "mode=db; "
        f"db={shlex.quote(database_value)}; "
        f"if {extension_checks}; then "
        "  :; "
        f"elif [ -f \"$db\" ] && {makeblastdb_guard}; then "
        f"  {shlex.quote(makeblastdb_cmd)}{makeblastdb_flags}; "
        f"elif {makeblastdb_guard}; then "
        f"  db={shlex.quote(local_db)}; "
        f"  {shlex.quote(makeblastdb_cmd)}{make_local_flags}; "
        "else "
        "  mode=subject; "
        "fi; "
        "if [ \"$mode\" = \"subject\" ]; then "
        f"  {blast_cmd} -query {shlex.quote(query_path)} -subject {shlex.quote(database_value)} "
        f"-out {shlex.quote(output_path)} -outfmt {shlex.quote(outfmt)} -num_threads {int(threads)} "
        f"-evalue {shlex.quote(str(evalue).strip() or '1e-5')}{extra_fragment}; "
        "else "
        f"  {blast_cmd} -query {shlex.quote(query_path)} -db \"$db\" "
        f"-out {shlex.quote(output_path)} -outfmt {shlex.quote(outfmt)} -num_threads {int(threads)} "
        f"-evalue {shlex.quote(str(evalue).strip() or '1e-5')}{extra_fragment}; "
        "fi"
    )
    return f"bash -c {shlex.quote(command)}"


def build_blast_formatter_command(
    *,
    archive_file: str,
    output_file: str,
    outfmt: str = BLAST_OUTFMT_DEFAULT,
    html: bool = False,
    max_target_seqs: int | None = None,
    parse_deflines: bool = False,
) -> str:
    archive_path = str(archive_file or "").strip()
    output_path = str(output_file or "").strip()
    if not archive_path or not output_path:
        raise ValueError("archive_file and output_file are required")
    blast_formatter_cmd = which_with_pixi("blast_formatter") or "blast_formatter"
    path_prefix = shell_path_prefix("blast_formatter")
    out_dir = str(Path(output_path).expanduser().parent)
    html_flag = " -html" if html else ""
    max_target_flag = f" -max_target_seqs {int(max_target_seqs)}" if max_target_seqs else ""
    parse_flag = " -parse_deflines" if parse_deflines else ""
    command = (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"mkdir -p {shlex.quote(out_dir)}; "
        f"{blast_formatter_cmd} -archive {shlex.quote(archive_path)} "
        f"-out {shlex.quote(output_path)} -outfmt {shlex.quote(outfmt)}"
        f"{html_flag}{max_target_flag}{parse_flag}"
    )
    return f"bash -c {shlex.quote(command)}"


def build_blastdbcmd_command(
    *,
    database: str,
    output_file: str | None = None,
    dbtype: str = "guess",
    entry: str | None = None,
    entry_batch: str | None = None,
    outfmt: str | None = None,
    info: bool = False,
    metadata: bool = False,
    tax_info: bool = False,
    show_search_path: bool = False,
    range_spec: str | None = None,
    strand: str | None = None,
) -> str:
    db_value = str(database or "").strip()
    if not db_value:
        raise ValueError("database is required")
    if not any([str(entry or "").strip(), str(entry_batch or "").strip(), info, metadata, tax_info, show_search_path]):
        raise ValueError("Provide one of: entry, entry_batch, info, metadata, tax_info, show_search_path")
    blastdbcmd_cmd = which_with_pixi("blastdbcmd") or "blastdbcmd"
    path_prefix = shell_path_prefix("blastdbcmd")
    output_path = str(output_file or "").strip()
    out_dir = str(Path(output_path).expanduser().parent) if output_path else "."
    flags: list[str] = [f"-db {shlex.quote(db_value)}", f"-dbtype {shlex.quote(str(dbtype or 'guess').strip() or 'guess')}"]
    if str(entry or "").strip():
        flags.append(f"-entry {shlex.quote(str(entry).strip())}")
    if str(entry_batch or "").strip():
        flags.append(f"-entry_batch {shlex.quote(str(entry_batch).strip())}")
    if output_path:
        flags.append(f"-out {shlex.quote(output_path)}")
    if str(outfmt or "").strip():
        flags.append(f"-outfmt {shlex.quote(str(outfmt).strip())}")
    if info:
        flags.append("-info")
    if metadata:
        flags.append("-metadata")
    if tax_info:
        flags.append("-tax_info")
    if show_search_path:
        flags.append("-show_blastdb_search_path")
    if str(range_spec or "").strip():
        flags.append(f"-range {shlex.quote(str(range_spec).strip())}")
    if str(strand or "").strip():
        flags.append(f"-strand {shlex.quote(str(strand).strip())}")
    command = (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"mkdir -p {shlex.quote(out_dir)}; "
        f"{blastdbcmd_cmd} {' '.join(flags)}"
    )
    return f"bash -c {shlex.quote(command)}"


def build_blastdbcheck_command(
    *,
    database: str | None = None,
    directory: str | None = None,
    dbtype: str = "guess",
    verbosity: int = 2,
    full: bool = False,
    recursive: bool = False,
) -> str:
    db_value = str(database or "").strip()
    dir_value = str(directory or "").strip()
    if bool(db_value) == bool(dir_value):
        raise ValueError("Provide exactly one of: database or directory")
    blastdbcheck_cmd = which_with_pixi("blastdbcheck") or "blastdbcheck"
    path_prefix = shell_path_prefix("blastdbcheck")
    flags = [f"-dbtype {shlex.quote(str(dbtype or 'guess').strip() or 'guess')}", f"-verbosity {int(verbosity)}"]
    if db_value:
        flags.append(f"-db {shlex.quote(db_value)}")
    if dir_value:
        flags.append(f"-dir {shlex.quote(dir_value)}")
    if full:
        flags.append("-full")
    if recursive:
        flags.append("-recursive")
    command = (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"{blastdbcheck_cmd} {' '.join(flags)}"
    )
    return f"bash -c {shlex.quote(command)}"


def build_blastdb_aliastool_command(
    *,
    dblist: list[str] | str,
    dbtype: str,
    output_alias: str,
    title: str | None = None,
    num_volumes: int | None = None,
) -> str:
    db_tokens = [str(x).strip() for x in (dblist if isinstance(dblist, list) else str(dblist or "").split()) if str(x).strip()]
    if not db_tokens:
        raise ValueError("dblist is required")
    dbtype_value = _validated_dbtype(dbtype)
    output_value = str(output_alias or "").strip()
    if not output_value:
        raise ValueError("output_alias is required")
    blastdb_aliastool_cmd = which_with_pixi("blastdb_aliastool") or "blastdb_aliastool"
    path_prefix = shell_path_prefix("blastdb_aliastool")
    out_dir = str(Path(output_value).expanduser().parent)
    title_flag = f" -title {shlex.quote(str(title).strip())}" if str(title or "").strip() else ""
    volumes_flag = f" -num_volumes {int(num_volumes)}" if num_volumes else ""
    command = (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"mkdir -p {shlex.quote(out_dir)}; "
        f"{blastdb_aliastool_cmd} -dblist {shlex.quote(' '.join(db_tokens))} -dbtype {dbtype_value} "
        f"-out {shlex.quote(output_value)}{title_flag}{volumes_flag}"
    )
    return f"bash -c {shlex.quote(command)}"


def build_makeprofiledb_command(
    *,
    input_list: str,
    output_prefix: str,
    dbtype: str = "rps",
    title: str | None = None,
    binary: bool = False,
    index: bool = True,
    threshold: float | None = None,
    matrix: str | None = None,
) -> str:
    input_value = str(input_list or "").strip()
    output_value = str(output_prefix or "").strip()
    if not input_value or not output_value:
        raise ValueError("input_list and output_prefix are required")
    dbtype_value = str(dbtype or "rps").strip().lower()
    dbtype_aliases = {
        "profile": "rps",
        "profiles": "rps",
        "pssm": "rps",
        "checkpoint": "rps",
    }
    dbtype_value = dbtype_aliases.get(dbtype_value, dbtype_value)
    if dbtype_value not in {"rps", "delta", "cobalt"}:
        raise ValueError("dbtype must be one of: rps, delta, cobalt")
    makeprofiledb_cmd = which_with_pixi("makeprofiledb") or "makeprofiledb"
    path_prefix = shell_path_prefix("makeprofiledb")
    out_dir = str(Path(output_value).expanduser().parent)
    flags = [
        f"-in {shlex.quote(input_value)}",
        f"-out {shlex.quote(output_value)}",
        f"-dbtype {shlex.quote(dbtype_value)}",
        f"-index {'true' if index else 'false'}",
    ]
    if str(title or "").strip():
        flags.append(f"-title {shlex.quote(str(title).strip())}")
    if binary:
        flags.append("-binary")
    if threshold is not None:
        flags.append(f"-threshold {float(threshold)}")
    if str(matrix or "").strip():
        flags.append(f"-matrix {shlex.quote(str(matrix).strip())}")
    command = (
        "set -euo pipefail; "
        f"export PATH={shlex.quote(path_prefix)}:$PATH; "
        f"mkdir -p {shlex.quote(out_dir)}; "
        f"{makeprofiledb_cmd} {' '.join(flags)}"
    )
    return f"bash -c {shlex.quote(command)}"
