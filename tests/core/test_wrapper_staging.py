from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bio_harness.core.wrapper_staging import idempotent_stage_copy_command


def test_idempotent_stage_copy_command_skips_same_file_for_relative_and_absolute_paths(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    source = selected_dir / "calls.vcf.gz"
    source.write_text("##fileformat=VCFv4.2\n", encoding="utf-8")

    command = idempotent_stage_copy_command("./calls.vcf.gz", str(source))
    completed = subprocess.run(
        ["bash", "-lc", command],
        cwd=selected_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert source.read_text(encoding="utf-8") == "##fileformat=VCFv4.2\n"


def test_idempotent_stage_copy_command_copies_missing_destination(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    source = selected_dir / "calls.vcf.gz"
    destination = selected_dir / "_staging" / "snpeff" / "calls.vcf.gz"
    source.write_text("##fileformat=VCFv4.2\n", encoding="utf-8")

    command = idempotent_stage_copy_command(str(source), str(destination))
    completed = subprocess.run(
        ["bash", "-lc", command],
        cwd=selected_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert destination.read_text(encoding="utf-8") == "##fileformat=VCFv4.2\n"


def test_wrapper_shells_do_not_reintroduce_string_inequality_copy_guards() -> None:
    library_root = Path(__file__).resolve().parents[2] / "bio_harness" / "skills" / "library"
    pattern = re.compile(r"if\s+\[\s+.*!=.*\];\s+then\s+(?:cp|mv|ln)\b")

    offenders: list[str] = []
    for path in sorted(library_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if not pattern.search(text):
            continue
        offenders.append(str(path))

    assert offenders == []
