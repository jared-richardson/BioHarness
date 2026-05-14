from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "bio_harness" / "pipeline_scripts" / "build_star_index.sh"


def _write_fake_star(bin_dir: Path, call_log: Path) -> None:
    star_path = bin_dir / "STAR"
    star_path.write_text(
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [ \"${1:-}\" = \"--version\" ]; then\n"
            "  echo \"STAR_2.7.99a\"\n"
            "  exit 0\n"
            "fi\n"
            "mode=\"\"\n"
            "genome_dir=\"\"\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    --runMode) mode=\"$2\"; shift 2 ;;\n"
            "    --genomeDir) genome_dir=\"$2\"; shift 2 ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "if [ \"$mode\" != \"genomeGenerate\" ]; then\n"
            "  exit 3\n"
            "fi\n"
            "mkdir -p \"$genome_dir\"\n"
            "for f in Genome SA SAindex chrLength.txt chrName.txt chrNameLength.txt chrStart.txt genomeParameters.txt; do\n"
            "  printf '%s\\n' \"$f\" > \"$genome_dir/$f\"\n"
            "done\n"
            "count=0\n"
            "if [ -f \"$STAR_CALL_LOG\" ]; then count=\"$(cat \"$STAR_CALL_LOG\")\"; fi\n"
            "printf '%s\\n' \"$((count + 1))\" > \"$STAR_CALL_LOG\"\n"
        ),
        encoding="utf-8",
    )
    star_path.chmod(0o755)


def _base_env(bin_dir: Path, call_log: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["STAR_CALL_LOG"] = str(call_log)
    return env


def _run_build(
    *,
    index_dir: Path,
    fasta: Path,
    gtf: Path,
    cache_root: Path,
    overhang: str,
    env: dict[str, str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(BUILD_SCRIPT),
            str(index_dir),
            str(fasta),
            str(gtf),
            "2",
            str(cache_root),
            overhang,
        ],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        check=False,
    )


def test_star_cache_reuse_on_matching_key(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "star_calls.txt"
    _write_fake_star(bin_dir, call_log)
    env = _base_env(bin_dir, call_log)

    fasta = tmp_path / "ref.fa"
    gtf = tmp_path / "ref.gtf"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf.write_text("chr1\tsrc\texon\t1\t4\t.\t+\t.\tgene_id \"g1\"; transcript_id \"t1\";\n", encoding="utf-8")

    cache_root = tmp_path / "cache"
    first = _run_build(
        index_dir=tmp_path / "idx_a",
        fasta=fasta,
        gtf=gtf,
        cache_root=cache_root,
        overhang="149",
        env=env,
        cwd=tmp_path,
    )
    second = _run_build(
        index_dir=tmp_path / "idx_b",
        fasta=fasta,
        gtf=gtf,
        cache_root=cache_root,
        overhang="149",
        env=env,
        cwd=tmp_path,
    )

    assert first.returncode == 0, first.stderr
    assert "__STAR_INDEX_REBUILT__" in first.stdout
    assert second.returncode == 0, second.stderr
    assert "__STAR_INDEX_CACHE_HIT__" in second.stdout
    assert call_log.read_text(encoding="utf-8").strip() == "1"


def test_star_rebuild_on_key_mismatch(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "star_calls.txt"
    _write_fake_star(bin_dir, call_log)
    env = _base_env(bin_dir, call_log)

    fasta = tmp_path / "ref.fa"
    gtf = tmp_path / "ref.gtf"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf.write_text("chr1\tsrc\texon\t1\t4\t.\t+\t.\tgene_id \"g1\"; transcript_id \"t1\";\n", encoding="utf-8")

    cache_root = tmp_path / "cache"
    first = _run_build(
        index_dir=tmp_path / "idx_a",
        fasta=fasta,
        gtf=gtf,
        cache_root=cache_root,
        overhang="149",
        env=env,
        cwd=tmp_path,
    )
    second = _run_build(
        index_dir=tmp_path / "idx_b",
        fasta=fasta,
        gtf=gtf,
        cache_root=cache_root,
        overhang="151",
        env=env,
        cwd=tmp_path,
    )

    assert first.returncode == 0
    assert second.returncode == 0
    assert "__STAR_INDEX_REBUILT__" in second.stdout
    assert call_log.read_text(encoding="utf-8").strip() == "2"


def test_stale_sentinel_file_does_not_cause_false_cache_hit(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "star_calls.txt"
    _write_fake_star(bin_dir, call_log)
    env = _base_env(bin_dir, call_log)

    fasta = tmp_path / "ref.fa"
    gtf = tmp_path / "ref.gtf"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf.write_text("chr1\tsrc\texon\t1\t4\t.\t+\t.\tgene_id \"g1\"; transcript_id \"t1\";\n", encoding="utf-8")

    stale_index = tmp_path / "idx_stale"
    stale_index.mkdir(parents=True, exist_ok=True)
    (stale_index / "SA").write_text("stale", encoding="utf-8")

    cache_root = tmp_path / "cache"
    result = _run_build(
        index_dir=stale_index,
        fasta=fasta,
        gtf=gtf,
        cache_root=cache_root,
        overhang="149",
        env=env,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "__STAR_INDEX_SKIPPED__:manifest_match" not in result.stdout
    assert "__STAR_INDEX_REBUILT__" in result.stdout
    assert call_log.read_text(encoding="utf-8").strip() == "1"
