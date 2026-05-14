from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", dest="primary_input", type=Path, required=True)
    parser.add_argument(
        "--fallback",
        dest="fallback_inputs",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _choose_input(primary_input: Path, fallback_inputs: list[Path]) -> Path:
    for candidate in [primary_input, *fallback_inputs]:
        if candidate.exists() and candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"No CNVkit summary input found. Checked: {[str(primary_input), *[str(path) for path in fallback_inputs]]}"
    )


def _write_summary(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with input_path.open("r", encoding="utf-8", errors="replace", newline="") as src:
        reader = csv.DictReader(src, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"CNVkit summary input has no header: {input_path}")
        with output_path.open("w", encoding="utf-8", newline="") as dst:
            writer = csv.writer(dst, delimiter="\t")
            writer.writerow(
                [
                    "chromosome",
                    "start",
                    "end",
                    "segment",
                    "log2",
                    "copy_number",
                    "probes",
                    "source_file",
                ]
            )
            for row in reader:
                chromosome = str(row.get("chromosome", "")).strip()
                start = str(row.get("start", "")).strip()
                end = str(row.get("end", "")).strip()
                log2 = str(row.get("log2", "")).strip()
                cn = str(row.get("cn", "")).strip()
                probes = str(row.get("probes", "")).strip()
                if not chromosome or not start or not end:
                    continue
                writer.writerow(
                    [
                        chromosome,
                        start,
                        end,
                        f"{chromosome}:{start}-{end}",
                        log2,
                        cn,
                        probes,
                        input_path.name,
                    ]
                )


def main() -> int:
    args = _parse_args()
    input_path = _choose_input(
        args.primary_input.expanduser().resolve(),
        [path.expanduser().resolve() for path in args.fallback_inputs],
    )
    _write_summary(input_path, args.output.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
