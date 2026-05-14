#!/usr/bin/env python3
"""Write a simple sample/condition metadata table.

This helper avoids shell-redirection metadata fabrication in deterministic
compiled workflows. It writes a two-column TSV with the header
``sample\tcondition``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Optional CLI arguments.

    Returns:
        Parsed argument namespace.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Output TSV path.")
    parser.add_argument(
        "--sample-condition",
        action="append",
        default=[],
        help="Repeated SAMPLE=CONDITION entry.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Write the metadata table.

    Args:
        argv: Optional CLI arguments.

    Returns:
        Process exit code.
    """

    args = _parse_args(argv)
    output_path = Path(args.output).expanduser().resolve(strict=False)
    rows: list[tuple[str, str]] = []
    for raw_entry in args.sample_condition:
        normalized = str(raw_entry or "").replace("\\t", "\t")
        sample, sep, condition = normalized.partition("=")
        if not sep:
            sample, sep, condition = normalized.partition("\t")
        sample = sample.strip()
        condition = condition.strip()
        if not sep or not sample or not condition:
            raise ValueError(f"Invalid --sample-condition entry: {raw_entry!r}")
        rows.append((sample, condition))
    if not rows:
        raise ValueError("At least one --sample-condition entry is required.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("sample\tcondition\n")
        for sample, condition in rows:
            handle.write(f"{sample}\t{condition}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
