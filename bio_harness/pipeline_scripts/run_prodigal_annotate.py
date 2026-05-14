"""Helper CLI for one atomic ``prodigal`` annotation invocation.

The wrapper keeps Prodigal execution deterministic while handling a common
short-contig failure mode: Prodigal's single-genome training mode requires at
least 20 kb of sequence. In ``auto`` mode this helper uses metagenomic mode for
short assemblies and single-genome mode for longer assemblies.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from bio_harness.core.tool_env import which_with_pixi

EXIT_USAGE = 64
EXIT_EMPTY_PREDICTIONS = 65
SHORT_CONTIG_SINGLE_MODE_MIN_BP = 20_000


def fasta_sequence_bases(path: Path) -> int:
    """Count non-header FASTA bases.

    Args:
        path: FASTA file path.

    Returns:
        Total non-whitespace bases across all sequence records.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """

    if not path.exists():
        raise FileNotFoundError(f"Input FASTA not found: {path}")
    total = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith(">"):
                continue
            total += len(stripped)
    return total


def resolve_prodigal_mode(mode: str, *, sequence_bases: int) -> str:
    """Resolve Prodigal mode from user input and input size.

    Args:
        mode: Requested mode, one of ``auto``, ``single``, or ``meta``.
        sequence_bases: Total FASTA bases.

    Returns:
        The concrete Prodigal mode, ``single`` or ``meta``.

    Raises:
        ValueError: If ``mode`` is unsupported.
    """

    token = str(mode or "auto").strip().lower()
    if token in {"single", "normal"}:
        return "single"
    if token in {"meta", "metagenomic"}:
        return "meta"
    if token != "auto":
        raise ValueError(f"Unsupported Prodigal mode: {mode}")
    if sequence_bases < SHORT_CONTIG_SINGLE_MODE_MIN_BP:
        return "meta"
    return "single"


def has_predicted_cds(gff_path: Path, faa_path: Path) -> bool:
    """Return whether Prodigal emitted at least one coding prediction.

    Args:
        gff_path: Prodigal GFF output path.
        faa_path: Prodigal protein FASTA output path.

    Returns:
        ``True`` when a CDS feature or protein record is present.
    """

    try:
        with gff_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3 and parts[2] == "CDS":
                    return True
    except OSError:
        return False
    try:
        with faa_path.open("r", encoding="utf-8", errors="replace") as handle:
            return any(line.startswith(">") for line in handle)
    except OSError:
        return False


def build_prodigal_command(
    *,
    prodigal: str,
    input_fasta: Path,
    output_gff: Path,
    output_faa: Path,
    mode: str,
) -> list[str]:
    """Build the ``prodigal`` argv.

    Args:
        prodigal: Resolved Prodigal executable.
        input_fasta: Input assembled FASTA.
        output_gff: Output GFF path.
        output_faa: Output translated protein FASTA path.
        mode: Concrete Prodigal mode, ``single`` or ``meta``.

    Returns:
        Command argv for ``subprocess.run``.
    """

    command = [
        prodigal,
        "-i",
        str(input_fasta),
        "-f",
        "gff",
        "-o",
        str(output_gff),
        "-a",
        str(output_faa),
    ]
    if mode == "meta":
        command.extend(["-p", "meta"])
    elif mode == "single":
        command.extend(["-p", "single"])
    return command


def run_prodigal_annotate(
    *,
    input_fasta: Path,
    output_gff: Path,
    output_faa: Path,
    mode: str = "auto",
    require_cds: bool = True,
) -> int:
    """Run Prodigal with deterministic short-contig mode selection.

    Args:
        input_fasta: Input assembled FASTA.
        output_gff: Output GFF path.
        output_faa: Output translated protein FASTA path.
        mode: Requested mode, ``auto``, ``single``, or ``meta``.
        require_cds: Whether to fail when Prodigal emits no CDS/protein.

    Returns:
        Process exit code.
    """

    try:
        sequence_bases = fasta_sequence_bases(input_fasta)
        concrete_mode = resolve_prodigal_mode(mode, sequence_bases=sequence_bases)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: prodigal_annotate: {exc}", file=sys.stderr)
        return EXIT_USAGE

    output_gff.parent.mkdir(parents=True, exist_ok=True)
    output_faa.parent.mkdir(parents=True, exist_ok=True)
    prodigal = which_with_pixi("prodigal") or shutil.which("prodigal") or "prodigal"
    command = build_prodigal_command(
        prodigal=str(prodigal),
        input_fasta=input_fasta,
        output_gff=output_gff,
        output_faa=output_faa,
        mode=concrete_mode,
    )
    print(
        "INFO: prodigal_annotate: "
        f"sequence_bases={sequence_bases} mode={concrete_mode}",
        file=sys.stderr,
    )
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        return int(completed.returncode)
    if require_cds and not has_predicted_cds(output_gff, output_faa):
        print(
            "ERROR: prodigal_annotate: no CDS predictions were emitted; "
            "downstream variant annotation would be empty.",
            file=sys.stderr,
        )
        return EXIT_EMPTY_PREDICTIONS
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        Configured argument parser.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-fasta", required=True, type=Path)
    parser.add_argument("--output-gff", required=True, type=Path)
    parser.add_argument("--output-faa", required=True, type=Path)
    parser.add_argument(
        "--mode",
        choices=("auto", "single", "normal", "meta", "metagenomic"),
        default="auto",
    )
    parser.add_argument(
        "--allow-empty-cds",
        action="store_true",
        help="Allow successful Prodigal runs that emit no CDS/protein records.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Optional argument vector.

    Returns:
        Process exit code.
    """

    args = build_arg_parser().parse_args(argv)
    return run_prodigal_annotate(
        input_fasta=args.input_fasta,
        output_gff=args.output_gff,
        output_faa=args.output_faa,
        mode=args.mode,
        require_cds=not args.allow_empty_cds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
