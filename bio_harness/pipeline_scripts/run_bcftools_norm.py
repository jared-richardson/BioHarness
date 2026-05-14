"""Helper CLI for one atomic ``bcftools norm`` invocation.

This script keeps ``bcftools norm`` execution to one visible helper-backed
operation while still handling deterministic output-directory creation and
option validation inside the harness.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Sequence


_VALID_MULTIALLELIC_MODES = frozenset({"+any", "-any", "none"})


def build_bcftools_norm_command(
    *,
    input_vcf: Path,
    reference_fasta: Path,
    output_vcf: Path,
    multiallelic_mode: str = "-any",
    atomize: bool = False,
) -> list[str]:
    """Build one ``bcftools norm`` command.

    Args:
        input_vcf: Input VCF or VCF.GZ path.
        reference_fasta: Reference FASTA used by ``bcftools norm``.
        output_vcf: Output VCF or VCF.GZ path.
        multiallelic_mode: ``bcftools norm -m`` mode or ``none`` to omit it.
        atomize: Whether to add ``--atomize``.

    Returns:
        Command parts suitable for ``subprocess.run``.

    Raises:
        ValueError: If one unsupported multiallelic mode is requested.
    """

    normalized_mode = str(multiallelic_mode or "").strip() or "-any"
    if normalized_mode not in _VALID_MULTIALLELIC_MODES:
        allowed = ", ".join(sorted(_VALID_MULTIALLELIC_MODES))
        raise ValueError(f"multiallelic_mode must be one of: {allowed}")

    output_format_flag = "-Oz" if str(output_vcf).endswith(".gz") else "-Ov"
    command = [
        "bcftools",
        "norm",
        "-f",
        str(reference_fasta),
        output_format_flag,
        "-o",
        str(output_vcf),
    ]
    if normalized_mode != "none":
        command.extend(["-m", normalized_mode])
    if atomize:
        command.append("--atomize")
    command.append(str(input_vcf))
    return command


def run_bcftools_norm(
    *,
    input_vcf: Path,
    reference_fasta: Path,
    output_vcf: Path,
    multiallelic_mode: str = "-any",
    atomize: bool = False,
) -> int:
    """Run one atomic ``bcftools norm`` operation.

    Args:
        input_vcf: Input VCF or VCF.GZ path.
        reference_fasta: Reference FASTA used by ``bcftools norm``.
        output_vcf: Output VCF or VCF.GZ path.
        multiallelic_mode: ``bcftools norm -m`` mode or ``none`` to omit it.
        atomize: Whether to add ``--atomize``.

    Returns:
        Process exit code from the ``bcftools`` invocation.
    """

    output_vcf.parent.mkdir(parents=True, exist_ok=True)
    command = build_bcftools_norm_command(
        input_vcf=input_vcf,
        reference_fasta=reference_fasta,
        output_vcf=output_vcf,
        multiallelic_mode=multiallelic_mode,
        atomize=atomize,
    )
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def _splice_multiallelic_mode_value(argv: list[str]) -> list[str]:
    """Rewrite ``--multiallelic-mode VALUE`` into ``--multiallelic-mode=VALUE``.

    Fix #20: the canonical bcftools norm ``-m`` values are ``+any``, ``-any``,
    and ``none``. argparse treats the ``-any`` / ``+any`` tokens as new option
    flags when they follow a space, producing
    ``error: argument --multiallelic-mode: expected one argument``. Rewriting
    the pair to the equals form lets argparse consume the value literally
    regardless of its leading character, so any planner that emits the
    space-separated form (including the Qwen 3.6 stepwise planner) still runs
    successfully.
    """

    fixed: list[str] = []
    skip_next = False
    for idx, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token == "--multiallelic-mode" and idx + 1 < len(argv):
            next_token = str(argv[idx + 1])
            if next_token in _VALID_MULTIALLELIC_MODES:
                fixed.append(f"--multiallelic-mode={next_token}")
                skip_next = True
                continue
        fixed.append(token)
    return fixed


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint for one ``bcftools norm`` helper call.

    Args:
        argv: Optional argv override for tests.

    Returns:
        Process exit code.
    """

    import sys

    parser = argparse.ArgumentParser(description="Run one atomic bcftools norm command.")
    parser.add_argument("--input-vcf", required=True)
    parser.add_argument("--reference-fasta", required=True)
    parser.add_argument("--output-vcf", required=True)
    parser.add_argument("--multiallelic-mode", default="-any")
    parser.add_argument("--atomize", action="store_true")
    raw = list(argv) if argv is not None else list(sys.argv[1:])
    normalized = _splice_multiallelic_mode_value(raw)
    args = parser.parse_args(normalized)
    return run_bcftools_norm(
        input_vcf=Path(args.input_vcf),
        reference_fasta=Path(args.reference_fasta),
        output_vcf=Path(args.output_vcf),
        multiallelic_mode=args.multiallelic_mode,
        atomize=bool(args.atomize),
    )


if __name__ == "__main__":
    raise SystemExit(main())
