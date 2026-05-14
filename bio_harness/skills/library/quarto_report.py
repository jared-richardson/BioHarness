from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable


_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "build_run_report_bundle.py"


def quarto_report(run_input: str, output_dir: str | None = None) -> str:
    """Render a shell command that builds a report bundle with Quarto rendering enabled."""
    selected = str(run_input or "").strip()
    if not selected:
        raise ValueError("Missing required parameter(s) for template: run_input")
    command = [str(preferred_helper_python_executable()), str(_SCRIPT_PATH), selected, "--render-quarto"]
    if output_dir:
        command.extend(["--output", str(output_dir)])
    return " ".join(shlex.quote(part) for part in command)
