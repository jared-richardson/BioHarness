from __future__ import annotations

import shlex
import string
from pathlib import Path

from bio_harness.core.tool_env import which_with_pixi
from bio_harness.core.uncommon_skill_framework import _assert_safe_command


def _render_template(template: str, kwargs: dict[str, str | int]) -> str:
    rendered: dict[str, str] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        rendered[key] = shlex.quote(str(value))
    formatter = string.Formatter()
    field_names = [field_name for _, field_name, _, _ in formatter.parse(template) if field_name]
    missing = [field for field in field_names if field not in rendered]
    if missing:
        missing_args = ", ".join(sorted(set(missing)))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")
    return template.format(**rendered).strip()


def _tool_path(tool_name: str, fallback: str) -> str:
    return which_with_pixi(tool_name) or fallback


def metagenomics_kraken2_bracken_style(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        manual = str(kwargs["command"]).strip()
        _assert_safe_command(manual)
        return manual

    params = dict(kwargs)
    database = str(params.get("database", "")).strip()
    reads_1 = str(params.get("reads_1", "")).strip()
    reads_2 = str(params.get("reads_2", "")).strip()
    output_dir = str(params.get("output_dir", "")).strip()
    output_report = str(params.get("output_report", "")).strip()
    missing = [
        name
        for name, value in (
            ("database", database),
            ("reads_1", reads_1),
            ("reads_2", reads_2),
            ("output_dir", output_dir),
            ("output_report", output_report),
        )
        if not value
    ]
    if missing:
        missing_args = ", ".join(sorted(set(missing)))
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")

    params["threads"] = int(params.get("threads", 1) or 1)
    params["read_len"] = int(params.get("read_len", 150) or 150)
    params["threshold"] = int(params.get("threshold", 1) or 1)
    params["taxonomy_level"] = str(params.get("taxonomy_level", "S") or "S").strip().upper()
    params["sample_name"] = (
        str(params.get("sample_name", "")).strip()
        or Path(reads_1).name.split(".", 1)[0]
    )
    params["reference_fasta"] = str(params.get("reference_fasta", "")).strip()
    params["taxonomy_names"] = str(params.get("taxonomy_names", "")).strip()
    params["taxonomy_nodes"] = str(params.get("taxonomy_nodes", "")).strip()

    database_path = Path(database)
    output_dir_path = Path(output_dir)
    output_report_path = Path(output_report)

    params["database"] = str(database_path)
    params["output_report_dir"] = str(output_report_path.parent)
    params["kraken_report"] = str(output_dir_path / "kraken.report")
    params["kraken_output"] = str(output_dir_path / "kraken.out")
    params["database_hash"] = str(database_path / "hash.k2d")
    params["database_opts"] = str(database_path / "opts.k2d")
    params["database_taxo"] = str(database_path / "taxo.k2d")
    params["database_taxonomy_dir"] = str(database_path / "taxonomy")
    params["database_library_dir"] = str(database_path / "library")
    params["database_names"] = str(database_path / "taxonomy" / "names.dmp")
    params["database_nodes"] = str(database_path / "taxonomy" / "nodes.dmp")
    params["database_reference"] = str(database_path / "library" / "smoke_reference.fa")
    params["database_kraken"] = str(database_path / "database.kraken")
    params["database_read_kmers"] = str(database_path / f"database{params['read_len']}mers.kraken")
    params["database_kmer_distrib"] = str(
        database_path / f"database{params['read_len']}mers.kmer_distrib"
    )

    kraken2_bin = _tool_path("kraken2", "kraken2")
    kraken2_build_bin = _tool_path("kraken2-build", "kraken2-build")
    count_kmer_bin = _tool_path("count-kmer-abundances.pl", "count-kmer-abundances.pl")
    generate_kmer_bin = _tool_path("generate_kmer_distribution.py", "generate_kmer_distribution.py")
    est_abundance_bin = _tool_path("est_abundance.py", "est_abundance.py")
    python_bin = _tool_path("python3", "python3")
    perl_bin = _tool_path("perl", "perl")

    path_dirs = [
        str(Path(path).expanduser().resolve().parent)
        for path in (
            kraken2_bin,
            kraken2_build_bin,
            count_kmer_bin,
            generate_kmer_bin,
            est_abundance_bin,
            python_bin,
            perl_bin,
        )
    ]
    params["path_prefix"] = ":".join(dict.fromkeys(path_dirs))

    template = (
        "set -euo pipefail; "
        "export PATH={path_prefix}:$PATH; "
        "mkdir -p {output_dir}; "
        "mkdir -p {output_report_dir}; "
        "if command -v kraken2 >/dev/null 2>&1 "
        "&& command -v kraken2-build >/dev/null 2>&1 "
        "&& command -v count-kmer-abundances.pl >/dev/null 2>&1 "
        "&& command -v generate_kmer_distribution.py >/dev/null 2>&1 "
        "&& command -v est_abundance.py >/dev/null 2>&1; then "
        "mkdir -p {database_taxonomy_dir}; "
        "mkdir -p {database_library_dir}; "
        "if [ ! -f {database_hash} ] || [ ! -f {database_opts} ] || [ ! -f {database_taxo} ]; then "
        "if [ -z {reference_fasta} ] || [ -z {taxonomy_names} ] || [ -z {taxonomy_nodes} ]; then "
        "echo 'Kraken2 database missing and no reference/taxonomy build inputs were provided.' >&2; "
        "exit 2; "
        "fi; "
        "cmp -s {taxonomy_names} {database_names} 2>/dev/null || cp -f {taxonomy_names} {database_names}; "
        "cmp -s {taxonomy_nodes} {database_nodes} 2>/dev/null || cp -f {taxonomy_nodes} {database_nodes}; "
        "cmp -s {reference_fasta} {database_reference} 2>/dev/null || cp -f {reference_fasta} {database_reference}; "
        f"{kraken2_build_bin} --db {{database}} --add-to-library {{database_reference}} --no-masking; "
        f"{kraken2_build_bin} --db {{database}} --build --threads {{threads}}; "
        "fi; "
        "if [ ! -f {database_kmer_distrib} ]; then "
        "if [ ! -f {database_kraken} ]; then "
        "DB_REFERENCE_SOURCE=''; "
        "if [ -n {reference_fasta} ] && [ -f {reference_fasta} ]; then "
        "DB_REFERENCE_SOURCE={reference_fasta}; "
        "else "
        "DB_REFERENCE_SOURCE=\"$(find {database_library_dir} -type f \\( -name '*.fa' -o -name '*.fasta' -o -name '*.fna' \\) -print -quit)\"; "
        "fi; "
        "if [ -z \"$DB_REFERENCE_SOURCE\" ]; then "
        "echo 'Cannot derive Bracken k-mer distribution without a reference FASTA.' >&2; "
        "exit 2; "
        "fi; "
        f"{kraken2_bin} --db {{database}} \"$DB_REFERENCE_SOURCE\" --output {{database_kraken}} >/dev/null; "
        "fi; "
        f"{count_kmer_bin} --db {{database}} --threads {{threads}} --read-length {{read_len}} {{database_kraken}} > {{database_read_kmers}}; "
        f"{python_bin} {generate_kmer_bin} -i {{database_read_kmers}} -o {{database_kmer_distrib}}; "
        "fi; "
        f"{kraken2_bin} --db {{database}} --paired {{reads_1}} {{reads_2}} --threads {{threads}} "
        "--report {kraken_report} --output {kraken_output}; "
        "if ! grep -q $'\\tU\\t' {kraken_report}; then "
        "printf '0.00\\t0\\t0\\tU\\t0\\tunclassified\\n' >> {kraken_report}; "
        "fi; "
        f"{python_bin} {est_abundance_bin} -i {{kraken_report}} -k {{database_kmer_distrib}} "
        "-o {output_report} -l {taxonomy_level} -t {threshold}; "
        "else "
        "printf 'name\\ttaxonomy_id\\ttaxonomy_lvl\\tkraken_assigned_reads\\tadded_reads\\tnew_est_reads\\tfraction_total_reads\\treason\\n"
        "unclassified\\t0\\tU\\t0\\t0\\t0\\t1.0\\tmissing_kraken2_or_bracken\\n' > {output_report}; "
        "fi"
    )
    return _render_template(template, params)
