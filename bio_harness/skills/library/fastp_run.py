from __future__ import annotations

import shlex
from pathlib import Path


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _required_path(kwargs: dict[str, object], key: str) -> str:
    value = str(kwargs.get(key, "") or "").strip()
    if not value:
        raise ValueError(f"Missing required parameter(s) for template: {key}")
    return value


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


def fastp_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    reads_1 = _required_path(kwargs, "reads_1")
    output_reads_1 = _required_path(kwargs, "output_reads_1")
    reads_2 = str(kwargs.get("reads_2", "") or "").strip()
    output_reads_2 = str(kwargs.get("output_reads_2", "") or "").strip()

    if bool(reads_2) != bool(output_reads_2):
        raise ValueError(
            "Paired-end fastp requires both reads_2 and output_reads_2, or neither."
        )

    adapter_sequence = str(kwargs.get("adapter_sequence", "") or "").strip()
    adapter_sequence_r2 = str(kwargs.get("adapter_sequence_r2", "") or "").strip()
    if not reads_2 and adapter_sequence_r2:
        raise ValueError(
            "Single-end fastp cannot accept adapter_sequence_r2 without reads_2."
        )

    parts = ["fastp", "-i", reads_1, "-o", output_reads_1]
    if reads_2:
        parts.extend(["-I", reads_2, "-O", output_reads_2])

    if _is_truthy(kwargs.get("detect_adapter_for_pe")):
        parts.append("--detect_adapter_for_pe")
    if adapter_sequence:
        parts.extend(["--adapter_sequence", adapter_sequence])
    if adapter_sequence_r2:
        parts.extend(["--adapter_sequence_r2", adapter_sequence_r2])
    if _is_truthy(kwargs.get("cut_front")):
        parts.append("--cut_front")
    if _is_truthy(kwargs.get("cut_tail")):
        parts.append("--cut_tail")
    if _is_truthy(kwargs.get("cut_right")):
        parts.append("--cut_right")
    if _is_truthy(kwargs.get("correction")):
        parts.append("--correction")

    for key, flag in (
        ("cut_mean_quality", "--cut_mean_quality"),
        ("length_required", "--length_required"),
        ("threads", "--thread"),
    ):
        value = str(kwargs.get(key, "") or "").strip()
        if value:
            parts.extend([flag, value])

    json_report = str(kwargs.get("json_report", "") or "").strip()
    html_report = str(kwargs.get("html_report", "") or "").strip()
    if json_report:
        parts.extend(["--json", json_report])
    if html_report:
        parts.extend(["--html", html_report])

    prep = _render_prep_dirs(output_reads_1, output_reads_2, json_report, html_report)
    command = " ".join(shlex.quote(part) for part in parts)
    return f"{prep}{command}"
