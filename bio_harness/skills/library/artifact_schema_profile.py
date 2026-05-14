from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable


_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "profile_artifact_schema.py"


def artifact_schema_profile(
    input_path: str,
    output_json: str | None = None,
    sample_rows: int = 25,
) -> str:
    """Render a shell command that profiles a completed artifact schema."""
    artifact_path = str(input_path or "").strip()
    if not artifact_path:
        raise ValueError("Missing required parameter(s) for template: input_path")

    command = [str(preferred_helper_python_executable()), str(_SCRIPT_PATH), artifact_path]
    if output_json:
        command.extend(["--output-json", str(output_json)])
    command.extend(["--sample-rows", str(int(sample_rows))])
    return " ".join(shlex.quote(part) for part in command)
