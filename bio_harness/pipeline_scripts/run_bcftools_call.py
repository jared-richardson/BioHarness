"""Helper CLI for one atomic ``bcftools mpileup|call`` wrapper invocation."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Sequence

from bio_harness.core.tool_env import which_with_pixi


def _resolve_bcftools_binary() -> str:
    """Return the preferred ``bcftools`` executable."""

    return str(which_with_pixi("bcftools") or "bcftools")


def run_bcftools_call(
    *,
    reference_fasta: Path,
    input_bam: Path,
    output_vcf_gz: Path,
) -> int:
    """Run one atomic bcftools germline-calling wrapper operation.

    Args:
        reference_fasta: Reference FASTA path.
        input_bam: BAM path to call against.
        output_vcf_gz: Compressed output VCF path.

    Returns:
        Process exit code from the final invoked tool.
    """

    output_vcf_gz.parent.mkdir(parents=True, exist_ok=True)
    bcftools = _resolve_bcftools_binary()
    mpileup = subprocess.Popen(
        [bcftools, "mpileup", "-f", str(reference_fasta), str(input_bam)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=False,
    )
    assert mpileup.stdout is not None
    call = subprocess.Popen(
        [bcftools, "call", "-mv", "-Oz", "-o", str(output_vcf_gz)],
        stdin=mpileup.stdout,
        stderr=subprocess.DEVNULL,
        text=False,
    )
    mpileup.stdout.close()
    mpileup.wait()
    call.wait()
    if mpileup.returncode != 0:
        return int(mpileup.returncode)
    if call.returncode != 0:
        return int(call.returncode)
    index_result = subprocess.run(
        [bcftools, "index", "-t", "-f", str(output_vcf_gz)],
        check=False,
    )
    return int(index_result.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint for one bcftools-call helper invocation.

    Args:
        argv: Optional argv override for tests.

    Returns:
        Process exit code.
    """

    parser = argparse.ArgumentParser(description="Run one atomic bcftools call wrapper operation.")
    parser.add_argument("--reference-fasta", required=True)
    parser.add_argument("--input-bam", required=True)
    parser.add_argument("--output-vcf-gz", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run_bcftools_call(
        reference_fasta=Path(args.reference_fasta),
        input_bam=Path(args.input_bam),
        output_vcf_gz=Path(args.output_vcf_gz),
    )


if __name__ == "__main__":
    raise SystemExit(main())
