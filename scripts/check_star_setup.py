#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bio_harness.core.tool_env import ensure_pixi_tooling_on_path, which_with_pixi
from bio_harness.skills.library.star_align import star_align


def _run_checked(cmd: list[str], *, cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=str(cwd), check=False, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify STAR is installed for BioHarness and optionally run a smoke alignment.")
    parser.add_argument("--genome-fasta", help="Reference FASTA for STAR smoke test.")
    parser.add_argument("--annotation-gtf", help="Optional GTF for STAR smoke test.")
    parser.add_argument("--reads-1", help="FASTQ read 1 for STAR smoke test.")
    parser.add_argument("--reads-2", help="FASTQ read 2 for STAR smoke test.")
    parser.add_argument("--index-dir", help="Genome index directory for smoke test.")
    parser.add_argument("--out-prefix", help="STAR output prefix for smoke test.")
    parser.add_argument("--threads", type=int, default=4, help="Thread count for smoke test.")
    parser.add_argument(
        "--out-sam-type",
        default="BAM SortedByCoordinate",
        help="Value for STAR --outSAMtype in smoke test.",
    )
    parser.add_argument(
        "--print-install-help",
        action="store_true",
        help="Print Linux/macOS install guidance when STAR is missing.",
    )
    args = parser.parse_args()

    ensure_pixi_tooling_on_path()
    resolved_star = which_with_pixi("STAR") or which_with_pixi("star")
    print(f"[star-check] project_root={PROJECT_ROOT}")
    print(f"[star-check] path_has_pixi={str((PROJECT_ROOT / '.pixi' / 'envs' / 'default' / 'bin')) in os.environ.get('PATH', '')}")
    print(f"[star-check] star_bin={resolved_star or ''}")

    if not resolved_star:
        print("[star-check] status=missing")
        if args.print_install_help:
            print("[star-check] install_hint=run `pixi install` in the repo root; pixi.toml already declares STAR for osx-arm64 and linux-64.")
            print("[star-check] override_hint=or set BIO_HARNESS_STAR_BIN=/absolute/path/to/STAR if you want a custom STAR binary.")
        return 1

    _run_checked([resolved_star, "--version"], cwd=PROJECT_ROOT)

    smoke_requested = any(
        str(value or "").strip()
        for value in (args.genome_fasta, args.reads_1, args.reads_2, args.index_dir, args.out_prefix)
    )
    if not smoke_requested:
        print("[star-check] status=ok")
        return 0

    required = {
        "reads_1": args.reads_1,
        "reads_2": args.reads_2,
        "index_dir": args.index_dir,
        "out_prefix": args.out_prefix,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        print(f"[star-check] error=missing_smoke_args:{','.join(missing)}", file=sys.stderr)
        return 2
    if args.annotation_gtf and not str(args.genome_fasta or "").strip():
        print("[star-check] error=genome_fasta_required_when_annotation_gtf_is_set", file=sys.stderr)
        return 2

    fasta = str(Path(args.genome_fasta).expanduser().resolve(strict=False)) if args.genome_fasta else ""
    reads_1 = str(Path(args.reads_1).expanduser().resolve(strict=False))
    reads_2 = str(Path(args.reads_2).expanduser().resolve(strict=False))
    index_dir = Path(args.index_dir).expanduser().resolve(strict=False)
    out_prefix = str(Path(args.out_prefix).expanduser().resolve(strict=False))
    annotation_gtf = str(Path(args.annotation_gtf).expanduser().resolve(strict=False)) if args.annotation_gtf else ""

    build_script = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "build_star_index.sh"
    if annotation_gtf:
        build_cmd = [
            "bash",
            str(build_script),
            str(index_dir),
            fasta,
            annotation_gtf,
            str(args.threads),
        ]
        _run_checked(build_cmd, cwd=PROJECT_ROOT)
    else:
        required_index_files = ("chrName.txt", "Genome", "SA", "SAindex")
        missing_index = [name for name in required_index_files if not (index_dir / name).exists()]
        if missing_index:
            print(
                "[star-check] error=missing_index_files_without_annotation_gtf:"
                + ",".join(missing_index),
                file=sys.stderr,
            )
            return 2

    star_cmd = star_align(
        star_bin=resolved_star,
        threads=args.threads,
        genome_dir=str(index_dir),
        reads_1=reads_1,
        reads_2=reads_2,
        annotation_gtf=annotation_gtf,
        output_prefix=out_prefix,
        outSAMtype=args.out_sam_type,
    )
    print(f"[star-check] smoke_command={star_cmd}")
    _run_checked(["bash", "-lc", star_cmd], cwd=PROJECT_ROOT)
    print("[star-check] smoke_status=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
