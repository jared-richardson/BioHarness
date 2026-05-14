from __future__ import annotations

import shlex


def spades_assemble(**kwargs) -> str:
    """Render a SPAdes paired-end assembly command.

    Args:
        **kwargs: SPAdes wrapper arguments including ``reads_1``,
            ``reads_2``, ``threads``, ``memory_gb``, ``output_dir``, optional
            mode flags, and optional ``phred_offset``.

    Returns:
        Shell command string ready for execution.

    Raises:
        ValueError: If required arguments are missing or ``phred_offset`` is
            not one of ``33``, ``64``, or an auto-detection sentinel.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    reads_1 = str(kwargs.get("reads_1", "")).strip()
    reads_2 = str(kwargs.get("reads_2", "")).strip()
    threads = str(kwargs.get("threads", "")).strip()
    memory_gb = str(kwargs.get("memory_gb", "")).strip()
    output_dir = str(kwargs.get("output_dir", "")).strip()
    phred_offset = _normalize_phred_offset(kwargs.get("phred_offset", 33))
    missing = [
        name
        for name, value in (
            ("reads_1", reads_1),
            ("reads_2", reads_2),
            ("threads", threads),
            ("memory_gb", memory_gb),
            ("output_dir", output_dir),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required parameter(s) for template: {', '.join(missing)}")

    parts = [
        "spades.py",
        "-1",
        shlex.quote(reads_1),
        "-2",
        shlex.quote(reads_2),
        "-t",
        shlex.quote(threads),
        "-m",
        shlex.quote(memory_gb),
    ]
    if phred_offset:
        parts.extend(["--phred-offset", phred_offset])
    meta_mode = bool(kwargs.get("meta_mode", False))
    careful = bool(kwargs.get("careful", False))
    isolate = bool(kwargs.get("isolate_mode", False))
    if meta_mode:
        parts.append("--meta")
    elif careful and isolate:
        # --careful and --isolate are mutually exclusive in SPAdes;
        # prefer --careful (standard for variant calling pipelines).
        parts.append("--careful")
    elif careful:
        parts.append("--careful")
    elif isolate:
        parts.append("--isolate")
    parts.extend(["-o", shlex.quote(output_dir)])
    return " ".join(parts)


def _normalize_phred_offset(value: object) -> str:
    raw = str(value if value is not None else "").strip().lower()
    if raw in {"", "auto", "detect", "none"}:
        return ""
    if raw in {"33", "64"}:
        return raw
    raise ValueError("Unsupported SPAdes phred_offset; expected 33, 64, or auto")
