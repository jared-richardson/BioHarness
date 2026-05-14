"""Helper CLI for one atomic ``featureCounts`` invocation."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Sequence

from bio_harness.core.tool_env import which_with_pixi


def _resolve_featurecounts_binary() -> str:
    """Return the preferred ``featureCounts`` executable."""

    return str(which_with_pixi("featurecounts") or "featureCounts")


def _resolve_samtools_binary() -> str | None:
    """Return the preferred ``samtools`` executable when available."""

    resolved = which_with_pixi("samtools")
    return str(resolved) if resolved else None


def _detect_paired_end(input_bam: Path) -> bool:
    """Return whether one BAM appears to contain paired-end reads.

    Args:
        input_bam: BAM path to inspect.

    Returns:
        ``True`` when paired-end evidence is present, else ``False``.
    """

    samtools = _resolve_samtools_binary()
    if not samtools:
        return False
    completed = subprocess.run(
        [samtools, "view", "-c", "-f", "1", str(input_bam)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return False
    try:
        return int((completed.stdout or "0").strip() or 0) > 0
    except ValueError:
        return False


def build_featurecounts_command(
    *,
    threads: int,
    annotation_gtf: Path,
    output_counts: Path,
    input_bams: Sequence[Path],
    annotation_format: str = "",
    feature_type: str = "",
    attribute_type: str = "",
    is_paired_end: bool | None = None,
    count_read_pairs: bool = True,
    strand_specificity: int | None = None,
) -> list[str]:
    """Build one ``featureCounts`` command.

    Args:
        threads: Number of worker threads.
        annotation_gtf: Annotation file path.
        output_counts: Output counts file path.
        input_bams: Input BAM paths.
        annotation_format: Optional explicit annotation format.
        feature_type: Optional feature type.
        attribute_type: Optional attribute type.
        is_paired_end: Optional paired-end override.
        count_read_pairs: Whether to count read pairs when paired-end mode is on.
        strand_specificity: Optional strandedness flag.

    Returns:
        Command parts suitable for ``subprocess.run``.

    Raises:
        ValueError: If one required argument is missing.
    """

    if not input_bams:
        raise ValueError("input_bams must contain at least one BAM path")

    normalized_format = str(annotation_format or "").strip()
    annotation_name = str(annotation_gtf).lower()
    if not normalized_format and annotation_name.endswith((".gff", ".gff3", ".gff.gz", ".gff3.gz")):
        normalized_format = "GFF"

    command = [
        _resolve_featurecounts_binary(),
        "-T",
        str(int(threads)),
        "-a",
        str(annotation_gtf),
        "-o",
        str(output_counts),
    ]
    if normalized_format:
        command.extend(["-F", normalized_format])
    if feature_type:
        command.extend(["-t", str(feature_type)])
    elif normalized_format.upper() == "GFF":
        command.extend(["-t", "gene"])
    if attribute_type:
        command.extend(["-g", str(attribute_type)])
    elif normalized_format.upper() == "GFF":
        command.extend(["-g", "ID"])

    paired_end = is_paired_end
    if paired_end is None and len(input_bams) == 1:
        paired_end = _detect_paired_end(input_bams[0])
    if bool(paired_end):
        command.append("-p")
        if count_read_pairs:
            command.append("--countReadPairs")

    if strand_specificity is not None:
        command.extend(["-s", str(int(strand_specificity))])

    command.extend(str(path) for path in input_bams)
    return command


def run_featurecounts(
    *,
    threads: int,
    annotation_gtf: Path,
    output_counts: Path,
    input_bams: Sequence[Path],
    annotation_format: str = "",
    feature_type: str = "",
    attribute_type: str = "",
    is_paired_end: bool | None = None,
    count_read_pairs: bool = True,
    strand_specificity: int | None = None,
) -> int:
    """Run one atomic ``featureCounts`` operation.

    Args:
        threads: Number of worker threads.
        annotation_gtf: Annotation file path.
        output_counts: Output counts file path.
        input_bams: Input BAM paths.
        annotation_format: Optional explicit annotation format.
        feature_type: Optional feature type.
        attribute_type: Optional attribute type.
        is_paired_end: Optional paired-end override.
        count_read_pairs: Whether to count read pairs when paired-end mode is on.
        strand_specificity: Optional strandedness flag.

    Returns:
        Process exit code from the ``featureCounts`` invocation.
    """

    output_counts.parent.mkdir(parents=True, exist_ok=True)
    command = build_featurecounts_command(
        threads=threads,
        annotation_gtf=annotation_gtf,
        output_counts=output_counts,
        input_bams=input_bams,
        annotation_format=annotation_format,
        feature_type=feature_type,
        attribute_type=attribute_type,
        is_paired_end=is_paired_end,
        count_read_pairs=count_read_pairs,
        strand_specificity=strand_specificity,
    )
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint for one ``featureCounts`` helper call.

    Args:
        argv: Optional argv override for tests.

    Returns:
        Process exit code.
    """

    parser = argparse.ArgumentParser(description="Run one atomic featureCounts command.")
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--annotation-gtf", required=True)
    parser.add_argument("--output-counts", required=True)
    parser.add_argument("--input-bam", action="append", dest="input_bams", required=True)
    parser.add_argument("--annotation-format", default="")
    parser.add_argument("--feature-type", default="")
    parser.add_argument("--attribute-type", default="")
    paired_group = parser.add_mutually_exclusive_group()
    paired_group.add_argument("--paired-end", dest="paired_end", action="store_true")
    paired_group.add_argument("--single-end", dest="paired_end", action="store_false")
    parser.set_defaults(paired_end=None)
    parser.add_argument("--count-read-pairs", action="store_true")
    parser.add_argument("--strand-specificity", type=int)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run_featurecounts(
        threads=int(args.threads),
        annotation_gtf=Path(args.annotation_gtf),
        output_counts=Path(args.output_counts),
        input_bams=[Path(value) for value in args.input_bams],
        annotation_format=str(args.annotation_format or ""),
        feature_type=str(args.feature_type or ""),
        attribute_type=str(args.attribute_type or ""),
        is_paired_end=args.paired_end,
        count_read_pairs=bool(args.count_read_pairs),
        strand_specificity=args.strand_specificity,
    )


if __name__ == "__main__":
    raise SystemExit(main())
