"""Helper CLI for one atomic ``freebayes`` wrapper invocation.

This helper:

* Ensures reference ``.fai`` and BAM ``.bai`` indexes exist.
* Splits the reference into roughly equal-length region chunks and runs
  multiple ``freebayes`` workers in parallel, then concatenates their VCFs
  with ``bcftools``. This is a standard freebayes speed-up and keeps one
  user-visible variant-calling operation atomic from the planner's
  perspective.
* Monitors wall-clock duration and VCF progress; if no output grows for
  ``--stall-timeout-seconds`` while workers are running, or total runtime
  exceeds ``--wall-timeout-seconds``, the workers are terminated and a
  structured stderr diagnostic is emitted.

If region-based parallelism cannot be used (missing ``bcftools``, no
``.fai`` with usable contigs, or requested workers <= 1), the helper falls
back to a single ``freebayes`` subprocess with the same watchdogs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from bio_harness.core.tool_env import which_with_pixi


# Exit codes (distinct from process-level errors so the repair loop can
# classify failures).
EXIT_STALL_WATCHDOG = 72  # No VCF growth for --stall-timeout-seconds.
EXIT_WALL_TIMEOUT = 73  # Total runtime exceeded --wall-timeout-seconds.
EXIT_WORKER_FAILURE = 74  # One parallel worker returned non-zero.

# Default watchdog thresholds (seconds). Generous enough to accommodate
# real calling workloads on mid-sized genomes, while still bounding total
# runtime so a hung freebayes does not stall the whole benchmark.
DEFAULT_WALL_TIMEOUT_SECONDS = 1500
DEFAULT_STALL_TIMEOUT_SECONDS = 420


def _resolve_binary(tool_name: str, fallback: str) -> str:
    """Return the preferred executable path for one tool."""

    return str(which_with_pixi(tool_name) or shutil.which(tool_name) or fallback)


def ensure_reference_and_bam_indexes(*, reference_fasta: Path, input_bam: Path) -> None:
    """Ensure reference ``.fai`` and BAM ``.bai`` indexes exist.

    Args:
        reference_fasta: Reference FASTA path.
        input_bam: BAM path to index when needed.
    """

    samtools = _resolve_binary("samtools", "samtools")
    if not reference_fasta.with_suffix(reference_fasta.suffix + ".fai").exists():
        subprocess.run([samtools, "faidx", str(reference_fasta)], check=True)
    bai_path = Path(f"{input_bam}.bai")
    if not bai_path.exists():
        subprocess.run([samtools, "index", str(input_bam)], check=True)


def _parse_fai_contigs(fai_path: Path) -> list[tuple[str, int]]:
    """Return ``(contig_name, length)`` tuples from a ``.fai`` file.

    Args:
        fai_path: Path to ``<ref>.fai``.

    Returns:
        Ordered list of contigs with positive length.
    """

    contigs: list[tuple[str, int]] = []
    if not fai_path.exists():
        return contigs
    for line in fai_path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        try:
            length = int(parts[1])
        except ValueError:
            continue
        if name and length > 0:
            contigs.append((name, length))
    return contigs


def plan_regions(
    contigs: list[tuple[str, int]],
    *,
    target_workers: int,
    max_chunk_bp: int = 2_000_000,
) -> list[list[str]]:
    """Split contigs into roughly equal-length region batches.

    Args:
        contigs: Ordered ``(name, length)`` contig list from the ``.fai``.
        target_workers: Desired number of worker processes (>= 1).
        max_chunk_bp: Upper bound on base pairs per individual region. Large
            contigs are subdivided so one worker does not monopolise runtime.

    Returns:
        A list of region batches. Each batch is a list of ``chr:start-end``
        strings (1-based, closed). Workers run ``freebayes --region`` per
        region in their batch.
    """

    workers = max(1, int(target_workers))
    # First fan every contig out into chunks of at most max_chunk_bp.
    chunks: list[tuple[str, int]] = []
    for name, length in contigs:
        start = 1
        while start <= length:
            end = min(length, start + max_chunk_bp - 1)
            chunks.append((f"{name}:{start}-{end}", end - start + 1))
            start = end + 1
    if not chunks:
        return []
    if workers <= 1 or len(chunks) == 1:
        return [[region for region, _ in chunks]]

    # Greedily distribute chunks across worker bins by descending size to
    # balance total bp per worker.
    chunks_sorted = sorted(chunks, key=lambda item: item[1], reverse=True)
    bins: list[tuple[int, list[str]]] = [(0, []) for _ in range(workers)]
    for region, length in chunks_sorted:
        # Find the bin with the smallest current total.
        idx = min(range(len(bins)), key=lambda i: bins[i][0])
        current_total, current_regions = bins[idx]
        bins[idx] = (current_total + length, current_regions + [region])
    # Drop empty bins (happens when #chunks < workers).
    return [regions for _, regions in bins if regions]


def _build_freebayes_command(
    *,
    freebayes: str,
    reference_fasta: Path,
    input_bam: Path,
    ploidy: int | None,
    regions: list[str] | None,
) -> list[str]:
    command = [freebayes]
    if ploidy is not None:
        command.extend(["-p", str(int(ploidy))])
    if regions:
        for region in regions:
            command.extend(["--region", region])
    command.extend(["-f", str(reference_fasta), str(input_bam)])
    return command


def _run_with_watchdogs(
    *,
    command: list[str],
    stdout_path: Path,
    wall_timeout_seconds: int,
    stall_timeout_seconds: int,
    watchdog_interval_seconds: int,
    log_prefix: str,
) -> int:
    """Run one ``subprocess.Popen`` with stall + wall-clock watchdogs.

    Args:
        command: The argv for the subprocess.
        stdout_path: File to stream subprocess stdout into.
        wall_timeout_seconds: Total runtime budget.
        stall_timeout_seconds: Max allowed seconds with no output growth.
        watchdog_interval_seconds: How often to poll process state and size.
        log_prefix: Short tag printed before diagnostic messages.

    Returns:
        Process exit code. Watchdog-triggered terminations return
        :data:`EXIT_STALL_WATCHDOG` or :data:`EXIT_WALL_TIMEOUT`.
    """

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w") as fh:
        proc = subprocess.Popen(command, stdout=fh, stderr=subprocess.PIPE)

    start = time.monotonic()
    last_size = -1
    last_growth = start
    stderr_tail: list[str] = []
    stall_seconds = max(1, int(stall_timeout_seconds))
    wall_seconds = max(1, int(wall_timeout_seconds))
    poll_seconds = max(1, int(watchdog_interval_seconds))

    try:
        while True:
            try:
                rc = proc.wait(timeout=poll_seconds)
                break
            except subprocess.TimeoutExpired:
                pass
            # Grow-check
            try:
                size = stdout_path.stat().st_size if stdout_path.exists() else 0
            except OSError:
                size = 0
            now = time.monotonic()
            if size > last_size:
                last_size = size
                last_growth = now
            elapsed = now - start
            if elapsed > wall_seconds:
                _emit_watchdog_failure(
                    log_prefix=log_prefix,
                    reason="wall_timeout",
                    elapsed_seconds=elapsed,
                    last_size_bytes=last_size,
                    stdout_path=stdout_path,
                )
                _terminate(proc)
                return EXIT_WALL_TIMEOUT
            if now - last_growth > stall_seconds and last_size >= 0:
                _emit_watchdog_failure(
                    log_prefix=log_prefix,
                    reason="stall_no_output_growth",
                    elapsed_seconds=elapsed,
                    last_size_bytes=last_size,
                    stdout_path=stdout_path,
                )
                _terminate(proc)
                return EXIT_STALL_WATCHDOG
    finally:
        if proc.stderr is not None:
            try:
                stderr_text = proc.stderr.read().decode("utf-8", errors="replace")
            except Exception:
                stderr_text = ""
            if stderr_text:
                stderr_tail.append(stderr_text)
                sys.stderr.write(stderr_text)

    return int(rc)


def _terminate(proc: subprocess.Popen) -> None:
    """Send SIGTERM then SIGKILL as needed to ensure the child exits."""

    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _emit_watchdog_failure(
    *,
    log_prefix: str,
    reason: str,
    elapsed_seconds: float,
    last_size_bytes: int,
    stdout_path: Path,
) -> None:
    """Print a structured watchdog diagnostic to stderr."""

    record = {
        "failure_class": "freebayes_watchdog",
        "reason": reason,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "last_output_size_bytes": int(last_size_bytes),
        "output_path": str(stdout_path),
    }
    sys.stderr.write(
        f"ERROR: {log_prefix}: freebayes watchdog triggered "
        f"(reason={reason}, elapsed={elapsed_seconds:.1f}s, "
        f"output_size={last_size_bytes} bytes, path={stdout_path}).\n"
    )
    sys.stderr.write("FREEBAYES_CALL_DIAGNOSTIC_JSON=" + json.dumps(record) + "\n")


def _choose_worker_count(requested: int | None) -> int:
    """Pick the effective worker count.

    Args:
        requested: Value from ``--workers``; ``None`` or ``<= 0`` selects
            an environment-guided default.

    Returns:
        Integer worker count in ``[1, 8]``.
    """

    if requested is not None and requested > 0:
        return int(min(requested, 16))
    try:
        cpu_count = os.cpu_count() or 1
    except Exception:
        cpu_count = 1
    # Keep a couple of cores free for other harness subprocesses and ollama.
    return max(1, min(8, cpu_count - 2))


def _concat_vcfs(
    *,
    part_paths: list[Path],
    destination_vcf: Path,
    log_prefix: str,
) -> int:
    """Concatenate per-worker VCF parts with ``bcftools concat``.

    Falls back to a pure-Python header-aware concat if ``bcftools`` is
    unavailable.
    """

    bcftools = _resolve_binary("bcftools", "")
    if bcftools and Path(bcftools).exists() and part_paths:
        command = [bcftools, "concat", "-a", "-O", "v", "-o", str(destination_vcf)]
        command.extend(str(p) for p in part_paths)
        completed = subprocess.run(command, check=False)
        if completed.returncode == 0:
            return 0
        sys.stderr.write(
            f"WARN: {log_prefix}: bcftools concat exit {completed.returncode}; "
            "falling back to Python VCF concat.\n"
        )
    # Pure-Python fallback: keep the first header; append data lines.
    destination_vcf.parent.mkdir(parents=True, exist_ok=True)
    with destination_vcf.open("w", encoding="utf-8") as out_fh:
        header_emitted = False
        for part in part_paths:
            if not part.exists():
                continue
            with part.open("r", encoding="utf-8", errors="replace") as in_fh:
                for line in in_fh:
                    if line.startswith("#"):
                        if not header_emitted:
                            out_fh.write(line)
                    else:
                        out_fh.write(line)
            header_emitted = True
    return 0


def run_freebayes_call(
    *,
    reference_fasta: Path,
    input_bam: Path,
    output_vcf: Path | None = None,
    output_vcf_gz: Path | None = None,
    ploidy: int | None = None,
    workers: int | None = None,
    wall_timeout_seconds: int = DEFAULT_WALL_TIMEOUT_SECONDS,
    stall_timeout_seconds: int = DEFAULT_STALL_TIMEOUT_SECONDS,
    watchdog_interval_seconds: int = 15,
) -> int:
    """Run one atomic FreeBayes wrapper operation with optional region parallelism.

    Args:
        reference_fasta: Reference FASTA path.
        input_bam: BAM path to call against.
        output_vcf: Optional uncompressed output VCF path.
        output_vcf_gz: Optional compressed output VCF path.
        ploidy: Optional ploidy override.
        workers: Worker-process count; ``None`` or ``<=0`` uses a CPU-based default.
        wall_timeout_seconds: Total per-worker wall-clock budget.
        stall_timeout_seconds: Per-worker max seconds without output growth.
        watchdog_interval_seconds: Watchdog poll interval.

    Returns:
        Process exit code. ``EXIT_WORKER_FAILURE`` when any worker returns
        non-zero, ``EXIT_STALL_WATCHDOG`` / ``EXIT_WALL_TIMEOUT`` on
        watchdog-triggered termination.

    Raises:
        ValueError: If neither ``output_vcf`` nor ``output_vcf_gz`` is set.
    """

    if output_vcf is None and output_vcf_gz is None:
        raise ValueError("Either output_vcf or output_vcf_gz is required")

    destination = output_vcf or Path(str(output_vcf_gz)[:-3])
    destination.parent.mkdir(parents=True, exist_ok=True)
    ensure_reference_and_bam_indexes(reference_fasta=reference_fasta, input_bam=input_bam)

    freebayes = _resolve_binary("freebayes", "freebayes")
    contigs = _parse_fai_contigs(reference_fasta.with_suffix(reference_fasta.suffix + ".fai"))
    worker_count = _choose_worker_count(workers)
    region_batches = plan_regions(contigs, target_workers=worker_count)

    # Single-process path: used when we cannot or should not parallelise.
    if worker_count == 1 or not region_batches or len(region_batches) == 1:
        regions = region_batches[0] if region_batches else None
        command = _build_freebayes_command(
            freebayes=freebayes,
            reference_fasta=reference_fasta,
            input_bam=input_bam,
            ploidy=ploidy,
            regions=regions,
        )
        rc = _run_with_watchdogs(
            command=command,
            stdout_path=destination,
            wall_timeout_seconds=wall_timeout_seconds,
            stall_timeout_seconds=stall_timeout_seconds,
            watchdog_interval_seconds=watchdog_interval_seconds,
            log_prefix="freebayes_call[serial]",
        )
        if rc != 0:
            return rc
        return _post_process(destination, output_vcf_gz)

    # Parallel path: one freebayes per region batch, streaming to a .part file.
    tmp_dir = destination.parent / (destination.name + ".parts")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    procs: list[tuple[subprocess.Popen, Path]] = []
    start = time.monotonic()
    for idx, regions in enumerate(region_batches):
        part = tmp_dir / f"part-{idx:03d}.vcf"
        part_paths.append(part)
        command = _build_freebayes_command(
            freebayes=freebayes,
            reference_fasta=reference_fasta,
            input_bam=input_bam,
            ploidy=ploidy,
            regions=regions,
        )
        stdout_fh = part.open("w")
        proc = subprocess.Popen(command, stdout=stdout_fh, stderr=subprocess.PIPE)
        procs.append((proc, part))

    stall_seconds = max(1, int(stall_timeout_seconds))
    wall_seconds = max(1, int(wall_timeout_seconds))
    last_sizes = {part: -1 for _, part in procs}
    last_growth = {part: start for _, part in procs}
    poll_seconds = max(1, int(watchdog_interval_seconds))
    overall_rc = 0

    while procs:
        now = time.monotonic()
        remaining: list[tuple[subprocess.Popen, Path]] = []
        for proc, part in procs:
            rc = proc.poll()
            if rc is not None:
                if rc != 0:
                    overall_rc = EXIT_WORKER_FAILURE
                continue
            # Still running — apply watchdogs.
            try:
                size = part.stat().st_size if part.exists() else 0
            except OSError:
                size = 0
            if size > last_sizes[part]:
                last_sizes[part] = size
                last_growth[part] = now
            elapsed = now - start
            if elapsed > wall_seconds:
                _emit_watchdog_failure(
                    log_prefix="freebayes_call[parallel]",
                    reason="wall_timeout",
                    elapsed_seconds=elapsed,
                    last_size_bytes=last_sizes[part],
                    stdout_path=part,
                )
                _terminate(proc)
                overall_rc = EXIT_WALL_TIMEOUT
                continue
            if now - last_growth[part] > stall_seconds:
                _emit_watchdog_failure(
                    log_prefix="freebayes_call[parallel]",
                    reason="stall_no_output_growth",
                    elapsed_seconds=elapsed,
                    last_size_bytes=last_sizes[part],
                    stdout_path=part,
                )
                _terminate(proc)
                overall_rc = EXIT_STALL_WATCHDOG
                continue
            remaining.append((proc, part))
        procs = remaining
        if procs:
            time.sleep(poll_seconds)

    if overall_rc != 0:
        return overall_rc

    concat_rc = _concat_vcfs(
        part_paths=part_paths,
        destination_vcf=destination,
        log_prefix="freebayes_call[parallel]",
    )
    if concat_rc != 0:
        return concat_rc

    return _post_process(destination, output_vcf_gz)


def _post_process(destination: Path, output_vcf_gz: Path | None) -> int:
    """Optionally bgzip + tabix the final VCF."""

    if output_vcf_gz is None:
        return 0
    bgzip = _resolve_binary("bgzip", "bgzip")
    tabix = _resolve_binary("tabix", "tabix")
    with output_vcf_gz.open("wb") as handle:
        bgzip_result = subprocess.run(
            [bgzip, "-f", "-c", str(destination)],
            stdout=handle,
            check=False,
        )
    if bgzip_result.returncode != 0:
        return int(bgzip_result.returncode)
    tabix_result = subprocess.run(
        [tabix, "-f", "-p", "vcf", str(output_vcf_gz)],
        check=False,
    )
    return int(tabix_result.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint for one FreeBayes helper call."""

    parser = argparse.ArgumentParser(description="Run one atomic FreeBayes wrapper operation.")
    parser.add_argument("--reference-fasta", required=True)
    parser.add_argument("--input-bam", required=True)
    parser.add_argument("--output-vcf")
    parser.add_argument("--output-vcf-gz")
    parser.add_argument("--ploidy", type=int)
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel worker count (default: CPU-1, capped at 8).",
    )
    parser.add_argument(
        "--wall-timeout-seconds",
        type=int,
        default=DEFAULT_WALL_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--stall-timeout-seconds",
        type=int,
        default=DEFAULT_STALL_TIMEOUT_SECONDS,
    )
    parser.add_argument("--watchdog-interval-seconds", type=int, default=15)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run_freebayes_call(
        reference_fasta=Path(args.reference_fasta),
        input_bam=Path(args.input_bam),
        output_vcf=Path(args.output_vcf) if args.output_vcf else None,
        output_vcf_gz=Path(args.output_vcf_gz) if args.output_vcf_gz else None,
        ploidy=args.ploidy,
        workers=args.workers,
        wall_timeout_seconds=args.wall_timeout_seconds,
        stall_timeout_seconds=args.stall_timeout_seconds,
        watchdog_interval_seconds=args.watchdog_interval_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
