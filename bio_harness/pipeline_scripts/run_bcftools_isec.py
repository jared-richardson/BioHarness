"""Helper CLI for one atomic ``bcftools isec`` invocation.

The helper keeps intersection/complement/private set operations visible as one
planner step while still handling deterministic directory creation and mode
validation in a benchmark-blind way.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Sequence


_VALID_ISEC_MODES = frozenset({"intersection", "complement", "private"})


def build_bcftools_isec_command(
    *,
    input_vcfs: Sequence[Path],
    output_dir: Path,
    mode: str = "intersection",
    min_matches: int = 2,
) -> list[str]:
    """Build one ``bcftools isec`` command.

    Args:
        input_vcfs: Ordered VCF inputs.
        output_dir: Output directory for numbered ``bcftools isec`` results.
        mode: Set-operation mode.
        min_matches: Minimum number of matching inputs for ``intersection``.

    Returns:
        Command parts suitable for ``subprocess.run``.

    Raises:
        ValueError: If the mode is unsupported, too few inputs are provided,
            or ``min_matches`` is invalid for ``intersection`` mode.
    """

    normalized_mode = str(mode or "").strip().lower() or "intersection"
    if normalized_mode not in _VALID_ISEC_MODES:
        allowed = ", ".join(sorted(_VALID_ISEC_MODES))
        raise ValueError(f"mode must be one of: {allowed}")
    if len(input_vcfs) < 2:
        raise ValueError("input_vcfs must contain at least two VCF paths")
    if normalized_mode == "intersection" and int(min_matches) < 1:
        raise ValueError("min_matches must be at least 1 for intersection mode")

    command = ["bcftools", "isec", "-p", str(output_dir)]
    if normalized_mode == "intersection":
        command.extend(["-n", f"+{int(min_matches)}"])
    elif normalized_mode == "complement":
        command.extend(["-C", "-w1"])
    else:
        command.extend(["-n", "=1"])
    command.extend(str(path) for path in input_vcfs)
    return command


def run_bcftools_isec(
    *,
    input_vcfs: Sequence[Path],
    output_dir: Path,
    output_vcf: Path | None = None,
    mode: str = "intersection",
    min_matches: int = 2,
) -> int:
    """Run one atomic ``bcftools isec`` operation.

    Args:
        input_vcfs: Ordered VCF inputs.
        output_dir: Output directory for numbered ``bcftools isec`` results.
        output_vcf: Optional named VCF copied from the first numbered result.
        mode: Set-operation mode.
        min_matches: Minimum number of matching inputs for ``intersection``.

    Returns:
        Process exit code from the ``bcftools`` invocation.
    """

    output_dir.mkdir(parents=True, exist_ok=True)

    # Fix #27 (consumer-side defensive auto-index): ``bcftools isec`` requires
    # every bgzipped (``.vcf.gz``) or BCF (``.bcf``) input to have a tabix
    # (``.tbi``) or CSI (``.csi``) index next to it. Without the index the
    # tool fails with ``[E::idx_find_and_load] Could not retrieve index file``
    # and exit code 255 — the failure mode exp43 hit for every isec retry
    # after the evolution filter step produced unindexed ``.vcf.gz`` outputs.
    #
    # This defensive loop covers the case where inputs were produced outside
    # our toolchain (e.g. by a planner-emitted ``bash_run`` using raw
    # ``bcftools filter`` without ``--write-index``). The producer-side fix
    # lives in ``run_bcftools_filter.py`` so this is belt-and-suspenders, but
    # the cost is negligible (indexing a ~kB VCF is milliseconds) and the
    # pattern applies to ANY bcftools set-operation consumer, not just the
    # evolution pipeline.
    for vcf in input_vcfs:
        vcf_str = str(vcf)
        lowered = vcf_str.lower()
        if not (lowered.endswith(".vcf.gz") or lowered.endswith(".bcf")):
            continue
        tbi = Path(vcf_str + ".tbi")
        csi = Path(vcf_str + ".csi")
        if tbi.exists() or csi.exists():
            continue
        if not Path(vcf_str).exists():
            # Input missing entirely; let bcftools emit its own structured
            # error rather than masking it with a tabix failure.
            continue
        try:
            subprocess.run(
                ["tabix", "-p", "vcf", "-f", vcf_str],
                check=False,
            )
        except FileNotFoundError:
            # tabix binary unavailable; bcftools will emit the original
            # "could not load index" error with full input context.
            pass

    command = build_bcftools_isec_command(
        input_vcfs=input_vcfs,
        output_dir=output_dir,
        mode=mode,
        min_matches=min_matches,
    )
    completed = subprocess.run(command, check=False)
    returncode = int(completed.returncode)
    if returncode != 0 or output_vcf is None:
        return returncode
    return _materialize_named_output(output_dir=output_dir, output_vcf=output_vcf)


def _materialize_named_output(*, output_dir: Path, output_vcf: Path) -> int:
    """Write a stable named VCF from bcftools' numbered isec output.

    Args:
        output_dir: Directory passed to ``bcftools isec -p``.
        output_vcf: Desired branch-named VCF path.

    Returns:
        Zero on success, otherwise the failing subprocess return code or ``1``
        when no numbered VCF exists.
    """

    source = _first_numbered_vcf(output_dir)
    if source is None:
        return 1
    output_vcf.parent.mkdir(parents=True, exist_ok=True)
    if _is_bgzipped_vcf_path(output_vcf):
        if output_vcf.exists():
            output_vcf.unlink()
        if _is_bgzipped_vcf_path(source):
            shutil.copyfile(source, output_vcf)
        else:
            completed = subprocess.run(
                ["bcftools", "view", "-Oz", "-o", str(output_vcf), str(source)],
                check=False,
            )
            if int(completed.returncode) != 0:
                return int(completed.returncode)
        return _ensure_tabix_index(output_vcf)

    if output_vcf.exists():
        output_vcf.unlink()
    if _is_bgzipped_vcf_path(source):
        completed = subprocess.run(
            ["bcftools", "view", "-Ov", "-o", str(output_vcf), str(source)],
            check=False,
        )
        return int(completed.returncode)
    shutil.copyfile(source, output_vcf)
    return 0


def _first_numbered_vcf(output_dir: Path) -> Path | None:
    for name in ("0000.vcf.gz", "0000.vcf", "0000.bcf"):
        candidate = output_dir / name
        if candidate.is_file():
            return candidate
    for candidate in sorted(output_dir.glob("*.vcf*")):
        if candidate.is_file() and candidate.name[:4].isdigit():
            return candidate
    return None


def _ensure_tabix_index(vcf_path: Path) -> int:
    try:
        completed = subprocess.run(
            ["tabix", "-p", "vcf", "-f", str(vcf_path)],
            check=False,
        )
        if int(completed.returncode) == 0:
            return 0
    except FileNotFoundError:
        pass
    try:
        completed = subprocess.run(
            ["bcftools", "index", "-t", "-f", str(vcf_path)],
            check=False,
        )
    except FileNotFoundError:
        return 1
    return int(completed.returncode)


def _is_bgzipped_vcf_path(path: Path) -> bool:
    return str(path).lower().endswith(".vcf.gz")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint for one ``bcftools isec`` helper call.

    Args:
        argv: Optional argv override for tests.

    Returns:
        Process exit code.
    """

    parser = argparse.ArgumentParser(description="Run one atomic bcftools isec command.")
    parser.add_argument("--input-vcf", action="append", dest="input_vcfs", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--output-vcf", default="")
    parser.add_argument("--mode", choices=tuple(sorted(_VALID_ISEC_MODES)), default="intersection")
    parser.add_argument("--min-matches", type=int, default=2)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run_bcftools_isec(
        input_vcfs=[Path(value) for value in args.input_vcfs],
        output_dir=Path(args.output_dir),
        output_vcf=Path(args.output_vcf) if str(args.output_vcf or "").strip() else None,
        mode=args.mode,
        min_matches=int(args.min_matches),
    )


if __name__ == "__main__":
    raise SystemExit(main())
