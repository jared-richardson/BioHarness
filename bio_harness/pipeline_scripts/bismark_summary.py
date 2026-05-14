from __future__ import annotations

import argparse
import csv
from pathlib import Path


_REPORT_KEYS = {
    "Sequence pairs analysed in total": "sequence_pairs_total",
    "Number of paired-end alignments with a unique best hit": "unique_best_hit_pairs",
    "Mapping efficiency": "mapping_efficiency",
    "Sequence pairs with no alignments under any condition": "pairs_without_alignment",
    "Sequence pairs did not map uniquely": "pairs_not_unique",
    "Sequence pairs which were discarded because genomic sequence could not be extracted": "pairs_discarded_missing_genomic_sequence",
    "Total number of C's analysed": "total_c_analysed",
    "Total methylated C's in CpG context": "methylated_cpg",
    "Total methylated C's in CHG context": "methylated_chg",
    "Total methylated C's in CHH context": "methylated_chh",
    "Total methylated C's in Unknown context": "methylated_unknown",
    "Total unmethylated C's in CpG context": "unmethylated_cpg",
    "Total unmethylated C's in CHG context": "unmethylated_chg",
    "Total unmethylated C's in CHH context": "unmethylated_chh",
    "Total unmethylated C's in Unknown context": "unmethylated_unknown",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--bam", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-name", type=str, default="")
    return parser.parse_args()


def _extract_metrics(report_path: Path) -> dict[str, str]:
    metrics: dict[str, str] = {}
    for raw_line in report_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = _REPORT_KEYS.get(key.strip())
        if normalized_key is None:
            continue
        metrics[normalized_key] = value.strip()
    return metrics


def _write_summary(
    output_path: Path,
    *,
    sample_name: str,
    bam_path: Path,
    report_path: Path,
    metrics: dict[str, str],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as dst:
        writer = csv.writer(dst, delimiter="\t")
        writer.writerow(["metric", "value"])
        writer.writerow(["sample_name", sample_name])
        writer.writerow(["bam_path", str(bam_path)])
        writer.writerow(["report_path", str(report_path)])
        for key in sorted(metrics):
            writer.writerow([key, metrics[key]])


def main() -> int:
    args = _parse_args()
    report_path = args.report.expanduser().resolve()
    bam_path = args.bam.expanduser().resolve()
    metrics = _extract_metrics(report_path)
    sample_name = args.sample_name.strip() or bam_path.stem
    _write_summary(
        args.output.expanduser().resolve(),
        sample_name=sample_name,
        bam_path=bam_path,
        report_path=report_path,
        metrics=metrics,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
