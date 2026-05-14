"""Helpers for reading small delimited text tables consistently.

This module centralizes delimiter inference for lightweight metadata-style
tables so validation and execution paths do not diverge when a file extension
does not match the actual on-disk delimiter.
"""

from __future__ import annotations

import csv
from pathlib import Path

_SUPPORTED_DELIMITERS = ("\t", ",")


def sniff_table_delimiter(
    text: str,
    *,
    filename: str = "",
    fallback: str = "\t",
) -> str:
    """Infer the delimiter for one delimited text sample.

    Args:
        text: Sample text from the table, typically the header and a few rows.
        filename: Optional filename used for suffix-based fallback only.
        fallback: Delimiter to use when the sample is ambiguous.

    Returns:
        The inferred delimiter, restricted to tab or comma.
    """

    sample = str(text or "")
    if sample:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters="".join(_SUPPORTED_DELIMITERS))
            delimiter = str(getattr(dialect, "delimiter", "") or "")
            if delimiter in _SUPPORTED_DELIMITERS:
                return delimiter
        except csv.Error:
            pass

        for line in sample.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            tab_count = stripped.count("\t")
            comma_count = stripped.count(",")
            if tab_count > comma_count:
                return "\t"
            if comma_count > tab_count:
                return ","
            break

    suffix = Path(str(filename or "")).suffix.lower()
    if suffix == ".csv":
        return ","
    if suffix == ".tsv":
        return "\t"
    return fallback if fallback in _SUPPORTED_DELIMITERS else "\t"


def detect_table_delimiter(
    path: Path,
    *,
    fallback: str = "\t",
    sample_chars: int = 4096,
) -> str:
    """Infer the delimiter for one on-disk table.

    Args:
        path: Table path to inspect.
        fallback: Delimiter to use when the file cannot be read or is ambiguous.
        sample_chars: Maximum number of characters to inspect from the file.

    Returns:
        The inferred delimiter, restricted to tab or comma.
    """

    try:
        sample = path.read_text(encoding="utf-8", errors="replace")[:sample_chars]
    except OSError:
        sample = ""
    return sniff_table_delimiter(sample, filename=path.name, fallback=fallback)


def load_delimited_dict_rows(path: Path) -> tuple[list[str], list[dict[str, str]], str]:
    """Load a small delimited table into normalized dictionary rows.

    Args:
        path: Table path to load.

    Returns:
        A tuple of `(columns, rows, delimiter)` where `columns` preserves the
        normalized header order and `rows` contains stripped string values.

    Raises:
        ValueError: If the file is missing a usable header row.
    """

    delimiter = detect_table_delimiter(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("missing_header")
        columns = [str(name or "").strip() for name in reader.fieldnames if str(name or "").strip()]
        if not columns:
            raise ValueError("missing_header")
        rows: list[dict[str, str]] = []
        for raw_row in reader:
            if not isinstance(raw_row, dict):
                continue
            row = {
                str(key).strip(): str(value or "").strip()
                for key, value in raw_row.items()
                if str(key or "").strip()
            }
            if any(str(value).strip() for value in row.values()):
                rows.append(row)
    return columns, rows, delimiter
