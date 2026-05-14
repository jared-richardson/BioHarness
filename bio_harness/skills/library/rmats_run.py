from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.tool_env import which_with_pixi

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RMATS_WRAPPER = PROJECT_ROOT / "pipeline_scripts" / "run_rmats_if_needed.sh"


def _normalize_bam_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def _resolve_rmats_launch_env() -> dict[str, str]:
    rmats_candidate = str(which_with_pixi("rmats") or "").strip()
    if not rmats_candidate:
        return {}

    rmats_path = Path(rmats_candidate).expanduser()
    launch_env = {"RMATS_BIN": str(rmats_path)}
    if rmats_path.parent.name != "bin":
        return launch_env

    env_root = rmats_path.parent.parent
    packaged_dir = env_root / "rMATS"
    packaged_script = packaged_dir / "rmats.py"
    python_bin = env_root / "bin" / "python3"
    if packaged_script.is_file() and packaged_dir.is_dir() and python_bin.is_file():
        launch_env["RMATS_BIN"] = str(packaged_script)
        launch_env["RMATS_PYTHON_BIN"] = str(python_bin)
        launch_env["RMATS_PYTHONPATH"] = str(packaged_dir)
    return launch_env


def rmats_run(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    group1_bams = _normalize_bam_items(kwargs.get("group1_bams"))
    group2_bams = _normalize_bam_items(kwargs.get("group2_bams"))
    if not group1_bams or not group2_bams:
        raise ValueError("rmats_run requires non-empty group1_bams and group2_bams.")

    annotation_gtf = str(kwargs.get("annotation_gtf", "")).strip()
    output_dir = str(kwargs.get("output_dir", "")).strip()
    if not annotation_gtf or not output_dir:
        raise ValueError("rmats_run requires annotation_gtf and output_dir.")

    out_path = Path(output_dir).expanduser()
    tmp_dir = str(kwargs.get("tmp_dir", "")).strip() or str(out_path.parent / "rmats_tmp")
    try:
        read_length = int(kwargs.get("read_length", 100))
    except Exception:
        read_length = 100
    try:
        threads = int(kwargs.get("threads", 2))
    except Exception:
        threads = 2
    threads = max(1, threads)
    read_length = max(1, read_length)

    group1_file = out_path / "group1_bams.txt"
    group2_file = out_path / "group2_bams.txt"

    quoted_group1 = shlex.quote(",".join(group1_bams))
    quoted_group2 = shlex.quote(",".join(group2_bams))
    quoted_group1_file = shlex.quote(str(group1_file))
    quoted_group2_file = shlex.quote(str(group2_file))
    quoted_out_dir = shlex.quote(str(out_path))
    quoted_tmp_dir = shlex.quote(str(tmp_dir))
    quoted_gtf = shlex.quote(annotation_gtf)
    quoted_wrapper = shlex.quote(str(RMATS_WRAPPER))
    rmats_env_prefix = "".join(
        f"{key}={shlex.quote(value)} "
        for key, value in _resolve_rmats_launch_env().items()
        if value
    )

    return (
        f"mkdir -p {quoted_out_dir} {quoted_tmp_dir} && "
        f"printf '%s\\n' {quoted_group1} > {quoted_group1_file} && "
        f"printf '%s\\n' {quoted_group2} > {quoted_group2_file} && "
        f"{rmats_env_prefix}bash {quoted_wrapper} {quoted_group1_file} {quoted_group2_file} "
        f"{quoted_gtf} {quoted_out_dir} {quoted_tmp_dir} {read_length} {threads}"
    )
