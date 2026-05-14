"""Support helpers for bash command canonicalization in workflow templates.

This module isolates shell-command rewrite rules used by
``bio_harness.workflows.templates`` so the main template module can stay focused
on plan-level canonicalization rather than low-level token rewriting.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Dict

from bio_harness.core.shell_parse import split_shell_segments
from bio_harness.workflows.template_io_support import script_command


def split_shell_command_chains(command: str) -> list[str]:
    """Split a shell command on chain operators while preserving pipelines."""

    return [segment.strip() for segment in re.split(r"\|\||&&|;", command or "") if segment.strip()]


def strip_destructive_segments(command: str) -> tuple[str, list[str]]:
    """Remove destructive cache/index cleanup chains from a shell command."""

    segments = split_shell_command_chains(command or "")
    if not segments:
        return command.strip(), []
    removed: list[str] = []
    kept: list[str] = []
    destructive = re.compile(
        r"\brm\s+-rf\b.*(star[_-]?index|__STARtmp|rmats_tmp|outputs/_cache/star_indexes)",
        flags=re.IGNORECASE,
    )
    for segment in segments:
        candidate = segment.strip()
        if not candidate:
            continue
        if destructive.search(candidate):
            removed.append(candidate)
            continue
        kept.append(candidate)
    if not removed:
        return command.strip(), []
    return " ; ".join(kept).strip(), removed


def extract_shell_flag(tokens: list[str], flag: str) -> str:
    """Return the value for one shell flag from a tokenized command."""

    for idx, token in enumerate(tokens):
        if token == flag and idx + 1 < len(tokens):
            return tokens[idx + 1]
    return ""


def extract_star_genomegenerate(command: str) -> Dict[str, str]:
    """Parse one STAR genomeGenerate invocation into structured arguments."""

    for segment in split_shell_segments(command or ""):
        try:
            tokens = shlex.split(segment, posix=True)
        except Exception:
            continue
        if not tokens:
            continue
        if Path(tokens[0]).name.lower() != "star":
            continue
        if extract_shell_flag(tokens, "--runMode").lower() != "genomegenerate":
            continue
        genome_dir = extract_shell_flag(tokens, "--genomeDir")
        fasta = extract_shell_flag(tokens, "--genomeFastaFiles")
        gtf = extract_shell_flag(tokens, "--sjdbGTFfile")
        threads = extract_shell_flag(tokens, "--runThreadN") or "2"
        sjdb_overhang = extract_shell_flag(tokens, "--sjdbOverhang") or "149"
        if genome_dir and fasta and gtf:
            return {
                "genome_dir": genome_dir,
                "fasta": fasta,
                "gtf": gtf,
                "threads": threads,
                "sjdb_overhang": sjdb_overhang,
            }
    return {}


def extract_manifest_redirect(command: str) -> str:
    """Return the manifest redirect path from a FASTQ-discovery command."""

    match = re.search(
        r">\s*([^\s]+(?:manifest[^\s]*\.txt|fastq_manifest\.txt))",
        command or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return match.group(1).strip().strip("'\"")


def rewrite_star_alignreads_command(command: str) -> tuple[str, bool]:
    """Drop unnecessary decompression helpers from STAR alignReads commands."""

    raw = (command or "").strip()
    if not raw or any(op in raw for op in ("&&", "||", ";", "|")):
        return raw, False
    try:
        tokens = shlex.split(raw, posix=True)
    except Exception:
        return raw, False
    if not tokens or Path(tokens[0]).name.lower() != "star":
        return raw, False

    run_mode = extract_shell_flag(tokens, "--runMode").lower()
    if run_mode and run_mode != "alignreads":
        return raw, False

    read_files: list[str] = []
    idx = 0
    while idx < len(tokens):
        if tokens[idx] == "--readFilesIn":
            idx += 1
            while idx < len(tokens) and not tokens[idx].startswith("--"):
                read_files.append(tokens[idx])
                idx += 1
            continue
        idx += 1

    read_files_command = ""
    idx = 0
    while idx < len(tokens):
        if tokens[idx] == "--readFilesCommand" and idx + 1 < len(tokens):
            read_files_command = tokens[idx + 1].strip().lower()
            break
        idx += 1

    if not read_files_command or not read_files:
        return raw, False
    if all(str(path).lower().endswith(".gz") for path in read_files):
        return raw, False
    if read_files_command not in {"zcat", "gzip", "gunzip", "pigz"}:
        return raw, False

    rewritten: list[str] = []
    idx = 0
    while idx < len(tokens):
        if tokens[idx] == "--readFilesCommand":
            idx += 2
            continue
        rewritten.append(tokens[idx])
        idx += 1
    return " ".join(shlex.quote(token) for token in rewritten), True


def normalize_rmats_command(command: str) -> tuple[str, bool]:
    """Normalize legacy rMATS CLI flags to the harness-safe form."""

    raw = (command or "").strip()
    if not raw or any(op in raw for op in ("&&", "||", "|", ";")):
        return raw, False
    try:
        tokens = shlex.split(raw, posix=True)
    except Exception:
        return raw, False
    if not tokens or Path(tokens[0]).name.lower() not in {"rmats.py", "rmats"}:
        return raw, False

    paired_requested = any(token.lower() == "--paired-end" for token in tokens)
    has_read_length = any(
        token == "--readLength" or token.lower().startswith("--readlength=") for token in tokens
    )
    if not paired_requested and has_read_length:
        return raw, False

    rewritten: list[str] = []
    changed = False
    has_t_flag = False
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        token_l = token.lower()
        if token_l == "--paired-end":
            changed = True
            idx += 1
            continue
        if token == "-t" and idx + 1 < len(tokens):
            has_t_flag = True
            value = tokens[idx + 1]
            if value.lower() != "paired":
                value = "paired"
                changed = True
            rewritten.extend([token, value])
            idx += 2
            continue
        if token == "--readLength" and idx + 1 < len(tokens):
            has_read_length = True
            rewritten.extend([token, tokens[idx + 1]])
            idx += 2
            continue
        if token_l.startswith("--readlength="):
            has_read_length = True
        rewritten.append(token)
        idx += 1

    if paired_requested and not has_t_flag:
        rewritten.extend(["-t", "paired"])
        changed = True
    if not has_read_length:
        rewritten.extend(["--readLength", "150"])
        changed = True

    if not changed:
        return raw, False
    return " ".join(shlex.quote(token) for token in rewritten), True


def extract_flag_value(tokens: list[str], flag: str) -> str:
    """Return a flag value from ``--flag value`` or ``--flag=value`` syntax."""

    value = extract_shell_flag(tokens, flag)
    if value:
        return value
    prefix = f"{flag}="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix) :].strip()
    return ""


def split_rmats_bam_inputs(value: str) -> list[str]:
    """Split one rMATS BAM argument into a list of concrete BAM paths."""

    raw = str(value or "").strip()
    if not raw:
        return []
    if "," in raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    try:
        tokens = [str(token).strip() for token in shlex.split(raw, posix=True) if str(token).strip()]
    except Exception:
        tokens = [part for part in raw.split() if part]
    return tokens


def looks_like_bam_list_file(path_value: str) -> bool:
    """Return whether a path looks like a BAM-list manifest rather than one BAM."""

    suffix_l = Path(str(path_value or "").strip()).suffix.lower()
    return suffix_l in {".txt", ".list", ".lst"}


def rewrite_rmats_to_wrapper(command: str) -> tuple[str, bool]:
    """Rewrite direct rMATS CLI usage to the harness wrapper script."""

    raw = (command or "").strip()
    if not raw or any(op in raw for op in ("&&", "||", "|", ";")):
        return raw, False
    try:
        tokens = shlex.split(raw, posix=True)
    except Exception:
        return raw, False
    if not tokens or Path(tokens[0]).name.lower() not in {"rmats.py", "rmats"}:
        return raw, False

    b1 = extract_flag_value(tokens, "--b1")
    b2 = extract_flag_value(tokens, "--b2")
    gtf = extract_flag_value(tokens, "--gtf")
    out_dir = extract_flag_value(tokens, "--od")
    tmp_dir = extract_flag_value(tokens, "--tmp")
    read_length = extract_flag_value(tokens, "--readLength") or "150"
    threads = extract_flag_value(tokens, "--nthread") or "2"
    if not (b1 and b2 and gtf and out_dir and tmp_dir):
        return raw, False

    prelude_parts: list[str] = []
    if looks_like_bam_list_file(b1) and looks_like_bam_list_file(b2):
        b1_list = b1
        b2_list = b2
    else:
        b1_items = split_rmats_bam_inputs(b1)
        b2_items = split_rmats_bam_inputs(b2)
        if not b1_items or not b2_items:
            return raw, False
        b1_list = str(Path(out_dir) / "control_bams.auto.txt")
        b2_list = str(Path(out_dir) / "treatment_bams.auto.txt")
        b1_items_q = " ".join(shlex.quote(item) for item in b1_items)
        b2_items_q = " ".join(shlex.quote(item) for item in b2_items)
        prelude_parts.append(f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(tmp_dir)}")
        prelude_parts.append(f"printf '%s\\n' {b1_items_q} > {shlex.quote(b1_list)}")
        prelude_parts.append(f"printf '%s\\n' {b2_items_q} > {shlex.quote(b2_list)}")

    wrapper_cmd = script_command(
        "run_rmats_if_needed.sh",
        b1_list,
        b2_list,
        gtf,
        out_dir,
        tmp_dir,
        read_length,
        threads,
    )
    if prelude_parts:
        return f"{' ; '.join(prelude_parts)} ; {wrapper_cmd}", True
    return wrapper_cmd, True
