from __future__ import annotations

import json
import shlex
from pathlib import Path


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]

    raw = str(value).strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    try:
        tokens = [token for token in shlex.split(raw, posix=True) if token]
    except Exception:
        tokens = [raw]
    return tokens or [raw]


def _required_path(kwargs: dict[str, object], key: str) -> str:
    value = str(kwargs.get(key, "") or "").strip()
    if not value:
        raise ValueError(f"Missing required parameter(s) for template: {key}")
    return value


def _append_repeated_flags(parts: list[str], flag: str, values: list[str]) -> None:
    for value in values:
        parts.extend([flag, value])


def _render_prep_dirs(*paths: str) -> str:
    parents: list[str] = []
    for raw in paths:
        if not str(raw or "").strip():
            continue
        parent = str(Path(raw).expanduser().parent)
        if parent and parent not in parents and parent != ".":
            parents.append(parent)
    if not parents:
        return ""
    quoted = " ".join(shlex.quote(path) for path in parents)
    return f"mkdir -p {quoted} && "


def cutadapt_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    reads_1 = _required_path(kwargs, "reads_1")
    output_reads_1 = _required_path(kwargs, "output_reads_1")
    reads_2 = str(kwargs.get("reads_2", "") or "").strip()
    output_reads_2 = str(kwargs.get("output_reads_2", "") or "").strip()

    if bool(reads_2) != bool(output_reads_2):
        raise ValueError(
            "Paired-end cutadapt requires both reads_2 and output_reads_2, or neither."
        )

    adapter_3prime_r1 = _normalize_values(kwargs.get("adapter_3prime_r1"))
    adapter_3prime_r2 = _normalize_values(kwargs.get("adapter_3prime_r2"))
    front_adapter_r1 = _normalize_values(kwargs.get("front_adapter_r1"))
    front_adapter_r2 = _normalize_values(kwargs.get("front_adapter_r2"))

    if not reads_2 and (adapter_3prime_r2 or front_adapter_r2):
        raise ValueError(
            "Single-end cutadapt cannot accept R2 adapter parameters without reads_2."
        )

    if not (adapter_3prime_r1 or adapter_3prime_r2 or front_adapter_r1 or front_adapter_r2):
        raise ValueError(
            "At least one adapter parameter must be provided for cutadapt trimming."
        )

    parts = ["cutadapt"]
    _append_repeated_flags(parts, "-a", adapter_3prime_r1)
    _append_repeated_flags(parts, "-A", adapter_3prime_r2)
    _append_repeated_flags(parts, "-g", front_adapter_r1)
    _append_repeated_flags(parts, "-G", front_adapter_r2)

    quality_cutoff = str(kwargs.get("quality_cutoff", "") or "").strip()
    if quality_cutoff:
        parts.extend(["-q", quality_cutoff])

    minimum_length = str(kwargs.get("minimum_length", "") or "").strip()
    if minimum_length:
        parts.extend(["-m", minimum_length])

    threads = str(kwargs.get("threads", "") or "").strip()
    if threads:
        parts.extend(["--cores", threads])

    if _is_truthy(kwargs.get("discard_untrimmed")):
        parts.append("--discard-untrimmed")

    json_report = str(kwargs.get("json_report", "") or "").strip()

    parts.extend(["-o", output_reads_1])
    if reads_2:
        parts.extend(["-p", output_reads_2])
    parts.append(reads_1)
    if reads_2:
        parts.append(reads_2)

    prep = _render_prep_dirs(output_reads_1, output_reads_2, json_report)
    if not json_report:
        command = " ".join(shlex.quote(part) for part in parts)
        return f"{prep}{command}"

    parts_with_json = list(parts)
    insert_at = len(parts_with_json) - (2 if reads_2 else 1)
    parts_with_json[insert_at:insert_at] = ["--json", json_report]
    command_with_json = " ".join(shlex.quote(part) for part in parts_with_json)
    command_without_json = " ".join(shlex.quote(part) for part in parts)
    report_dir = _render_prep_dirs(output_reads_1, output_reads_2, json_report).removesuffix(" && ")
    fallback_json = shlex.quote('{"report_status":"json_not_supported","tool":"cutadapt"}')
    report_fallback = (
        f"printf '%s\\n' "
        f"{fallback_json}"
        f" > {shlex.quote(json_report)}"
    )
    script = (
        "set -euo pipefail; "
        + (f"{report_dir}; " if report_dir else "")
        + "if cutadapt --help 2>&1 | grep -q -- --json; then "
        + f"{command_with_json}; "
        + "else "
        + f"{command_without_json}; {report_fallback}; "
        + "fi"
    )
    return f"bash -c {shlex.quote(script)}"
