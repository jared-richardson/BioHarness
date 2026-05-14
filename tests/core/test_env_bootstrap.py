from __future__ import annotations

from pathlib import Path

from bio_harness.core.env_bootstrap import (
    bootstrap_environment,
    format_bootstrap_for_prompt,
)


def test_bootstrap_environment_returns_required_keys() -> None:
    snapshot = bootstrap_environment()

    assert "available_tools" in snapshot
    assert "tool_groups" in snapshot
    assert "tool_versions" in snapshot
    assert "pixi_bin_dirs" in snapshot
    assert "pixi_jvm_bin_dirs" in snapshot
    assert "jvm_available" in snapshot
    assert "data_inventory" in snapshot
    assert "system_resources" in snapshot
    assert "known_workarounds" in snapshot


def test_bootstrap_environment_uses_relative_inventory_paths(tmp_path: Path) -> None:
    data_root = tmp_path / "inputs"
    nested = data_root / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "reads.fastq.gz").write_text("placeholder", encoding="utf-8")
    (data_root / "ref.fa").write_text(">chr1\nACGT\n", encoding="utf-8")

    snapshot = bootstrap_environment(data_root=data_root)

    inventory = snapshot["data_inventory"]
    assert {"name": "reads.fastq.gz", "relative_path": "nested/reads.fastq.gz"} in inventory
    assert {"name": "ref.fa", "relative_path": "ref.fa"} in inventory


def test_format_bootstrap_for_prompt_uses_relative_inventory_paths(tmp_path: Path) -> None:
    data_root = tmp_path / "inputs"
    (data_root / "lane1.fastq.gz").parent.mkdir(parents=True, exist_ok=True)
    (data_root / "lane1.fastq.gz").write_text("placeholder", encoding="utf-8")

    snapshot = bootstrap_environment(data_root=data_root)
    rendered = format_bootstrap_for_prompt(snapshot)

    assert "## Environment Snapshot" in rendered
    assert "lane1.fastq.gz" in rendered
    assert str(data_root.resolve()) not in rendered


def test_format_bootstrap_for_prompt_includes_workarounds_when_present() -> None:
    snapshot = {
        "available_tools": {"spades.py": "/usr/local/bin/spades.py"},
        "tool_groups": {"assembly": ["spades.py"]},
        "tool_versions": {},
        "pixi_bin_dirs": [],
        "pixi_jvm_bin_dirs": [],
        "jvm_available": False,
        "data_root": "inputs_readonly",
        "data_inventory": [],
        "system_resources": {
            "platform": "Darwin",
            "machine": "arm64",
            "cpu_count": 8,
            "ram_gb": 32.0,
            "disk_free_gb": 200.0,
        },
        "known_workarounds": [
            {
                "tool": "spades.py",
                "issue": "--careful and --isolate are mutually exclusive",
                "workaround": "Use --careful only.",
            }
        ],
    }

    rendered = format_bootstrap_for_prompt(snapshot)

    assert "Known tool issues and workarounds:" in rendered
    assert "spades.py" in rendered
    assert "Use --careful only." in rendered
