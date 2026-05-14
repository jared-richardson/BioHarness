from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.input_staging import stage_inputs, write_stage_receipt


def test_stage_inputs_symlink_and_receipt(tmp_path: Path):
    src = tmp_path / "sample.fastq.gz"
    src.write_text("reads\n", encoding="utf-8")
    dest_root = tmp_path / "workspace" / "inputs_readonly"

    receipt = stage_inputs([src], dest_root=dest_root, link_mode="symlink")
    staged = dest_root / src.name

    assert staged.is_symlink()
    assert receipt == [
        {
            "source": str(src.resolve()),
            "destination": str(dest_root.resolve() / src.name),
            "link_mode": "symlink",
            "kind": "file",
        }
    ]

    receipt_path = write_stage_receipt(receipt, tmp_path / "receipt.json")
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["staged"][0]["destination"] == str(dest_root.resolve() / src.name)


def test_stage_inputs_copy_directory(tmp_path: Path):
    src_dir = tmp_path / "dataset"
    src_dir.mkdir()
    (src_dir / "a.txt").write_text("x\n", encoding="utf-8")
    dest_root = tmp_path / "workspace" / "inputs_readonly"

    receipt = stage_inputs([src_dir], dest_root=dest_root, link_mode="copy")
    copied = dest_root / src_dir.name

    assert copied.is_dir()
    assert (copied / "a.txt").read_text(encoding="utf-8") == "x\n"
    assert receipt[0]["kind"] == "directory"
