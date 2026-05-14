#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def normalize(in_path: Path, out_path: Path) -> int:
    rows_written = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with in_path.open("r", encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip() or line.startswith("#"):
                dst.write(line)
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 9:
                dst.write(line)
                continue
            if fields[2].strip().lower() == "pseudogene":
                fields[2] = "gene"
                line = "\t".join(fields) + "\n"
            dst.write(line)
            rows_written += 1
    return 0 if rows_written else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input.gff> <output.gff>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(normalize(Path(sys.argv[1]), Path(sys.argv[2])))
