#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
import tarfile
import urllib.request
from pathlib import Path

from bio_harness.core.benchmark_asset_integrity import (
    render_bioagentbench_deseq_sample_metadata,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = PROJECT_ROOT / "workspace" / "benchmarks" / "bioagent-bench" / "tasks" / "deseq"
DATA_ROOT = TASK_DIR / "data"
REFERENCE_ROOT = TASK_DIR / "references"
RESULTS_ROOT = TASK_DIR / "results"

REFERENCE_URL = "https://osf.io/download/6846a111dfcfcd74e2ab813b/"
RESULTS_URL = "https://osf.io/download/68b0b0fba826cb07ed161e64/"
MISSING_SAMPLE_URLS = {
    "SRR1278971.tar.gz": "https://osf.io/download/684694513564c1f16c85b2b5/",
    "SRR1278972.tar.gz": "https://osf.io/download/6846941a3564c1f16c85b2a7/",
    "SRR1278973.tar.gz": "https://osf.io/download/68b0afa67f881b4d577da9f7/",
}
ENA_SAMPLE_FASTQS = {
    "SRR1278968": [
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/008/SRR1278968/SRR1278968_1.fastq.gz",
            "756e6f1cc378417c2f5243365d9a0b80",
        ),
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/008/SRR1278968/SRR1278968_2.fastq.gz",
            "95f3e699b7154e8dd112885416173255",
        ),
    ],
    "SRR1278969": [
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/009/SRR1278969/SRR1278969_1.fastq.gz",
            "38f70cf087d1e206bc1de738883a7dca",
        ),
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/009/SRR1278969/SRR1278969_2.fastq.gz",
            "50eb7afa6a7d6c2a79bdd369b04ecd0f",
        ),
    ],
    "SRR1278970": [
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/000/SRR1278970/SRR1278970_1.fastq.gz",
            "dcb827f2914803fee6e07d82421a2a77",
        ),
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/000/SRR1278970/SRR1278970_2.fastq.gz",
            "5e454a31b832336e404baebaed5bb0ba",
        ),
    ],
    "SRR1278971": [
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/001/SRR1278971/SRR1278971_1.fastq.gz",
            "6e29963361536e277e7a77ca60e22b3a",
        ),
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/001/SRR1278971/SRR1278971_2.fastq.gz",
            "88a9dfc492c19f66a8a92c1452e014c4",
        ),
    ],
    "SRR1278972": [
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/002/SRR1278972/SRR1278972_1.fastq.gz",
            "e49b5c6af6e06f382440e6e70b57c066",
        ),
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/002/SRR1278972/SRR1278972_2.fastq.gz",
            "f0e5209ce926ed8e88d61ba5888910a8",
        ),
    ],
    "SRR1278973": [
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/003/SRR1278973/SRR1278973_1.fastq.gz",
            "2d474a1f1370f34e5382f14641e2dc72",
        ),
        (
            "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR127/003/SRR1278973/SRR1278973_2.fastq.gz",
            "ca81b99cdbcab5332a345e9219b98e93",
        ),
    ],
}


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    with urllib.request.urlopen(url) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _extract_tar_gz(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(destination)


def _flatten_fastq_subdirs(data_root: Path) -> None:
    for subdir in sorted(data_root.iterdir()):
        if not subdir.is_dir():
            continue
        for fastq_path in sorted(subdir.glob("*.fastq")):
            target = data_root / fastq_path.name
            if target.exists():
                continue
            fastq_path.replace(target)
        try:
            subdir.rmdir()
        except OSError:
            continue


def _write_sample_metadata() -> Path:
    metadata_path = DATA_ROOT / "sample_metadata.tsv"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        render_bioagentbench_deseq_sample_metadata(),
        encoding="utf-8",
    )
    return metadata_path


def _cleanup_existing_reads() -> list[str]:
    removed: list[str] = []
    sample_prefixes = tuple(ENA_SAMPLE_FASTQS.keys())
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for path in sorted(DATA_ROOT.iterdir()):
        if not path.is_file():
            continue
        if not path.name.startswith(sample_prefixes):
            continue
        if path.suffix not in {".gz", ".fastq", ".fq"} and not path.name.endswith(".tar.gz"):
            continue
        path.unlink()
        removed.append(str(path))
    output_root = TASK_DIR / "output"
    if output_root.exists():
        for path in sorted(output_root.iterdir()):
            if not path.is_file():
                continue
            if path.name.startswith(sample_prefixes):
                path.unlink()
                removed.append(str(path))
    return removed


def _download_ena_fastqs() -> list[str]:
    downloaded: list[str] = []
    for sample, entries in ENA_SAMPLE_FASTQS.items():
        for url, expected_md5 in entries:
            destination = DATA_ROOT / Path(url).name
            _download(url, destination)
            actual_md5 = _md5(destination)
            if actual_md5 != expected_md5:
                raise RuntimeError(
                    f"MD5 mismatch for {destination.name}: expected {expected_md5}, got {actual_md5}"
                )
            downloaded.append(str(destination))
    return downloaded


def _decompress_fastqs_in_place() -> list[str]:
    decompressed: list[str] = []
    for path in sorted(DATA_ROOT.glob("*.fastq.gz")):
        target = path.with_suffix("")
        with gzip.open(path, "rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        path.unlink()
        decompressed.append(str(target))
    return decompressed


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage local assets for the BioAgentBench deseq task.")
    parser.add_argument(
        "--download-missing-samples",
        action="store_true",
        help="Download and extract the missing biofilm sample archives for local end-to-end runs.",
    )
    parser.add_argument(
        "--clean-existing-reads",
        action="store_true",
        help="Remove staged DESeq read files and stale task-local STAR outputs before restaging.",
    )
    parser.add_argument(
        "--download-all-samples-from-ena",
        action="store_true",
        help="Download all six DESeq FASTQ pairs from ENA as .fastq.gz and verify MD5s.",
    )
    parser.add_argument(
        "--decompress-fastqs",
        action="store_true",
        help="Expand staged .fastq.gz files to .fastq and remove the compressed copies.",
    )
    args = parser.parse_args()

    reference_archive = TASK_DIR / "_downloads" / "reference.tar.gz"
    results_archive = TASK_DIR / "_downloads" / "results.tar.gz"
    _download(REFERENCE_URL, reference_archive)
    _download(RESULTS_URL, results_archive)
    _extract_tar_gz(reference_archive, REFERENCE_ROOT)
    _extract_tar_gz(results_archive, RESULTS_ROOT)
    metadata_path = _write_sample_metadata()

    removed_paths: list[str] = []
    if args.clean_existing_reads:
        removed_paths = _cleanup_existing_reads()

    downloaded_archives: list[str] = []
    if args.download_missing_samples:
        for filename, url in MISSING_SAMPLE_URLS.items():
            archive_path = DATA_ROOT / filename
            _download(url, archive_path)
            _extract_tar_gz(archive_path, DATA_ROOT)
            downloaded_archives.append(str(archive_path))
        _flatten_fastq_subdirs(DATA_ROOT)

    downloaded_fastqs: list[str] = []
    if args.download_all_samples_from_ena:
        downloaded_fastqs = _download_ena_fastqs()

    decompressed_fastqs: list[str] = []
    if args.decompress_fastqs:
        decompressed_fastqs = _decompress_fastqs_in_place()

    print(f"[prepare-deseq] reference_dir={REFERENCE_ROOT}")
    print(f"[prepare-deseq] results_dir={RESULTS_ROOT}")
    print(f"[prepare-deseq] sample_metadata={metadata_path}")
    if removed_paths:
        print(f"[prepare-deseq] removed_existing_paths={len(removed_paths)}")
    if downloaded_archives:
        print(f"[prepare-deseq] downloaded_sample_archives={len(downloaded_archives)}")
    if downloaded_fastqs:
        print(f"[prepare-deseq] downloaded_fastqs={len(downloaded_fastqs)}")
    if decompressed_fastqs:
        print(f"[prepare-deseq] decompressed_fastqs={len(decompressed_fastqs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
