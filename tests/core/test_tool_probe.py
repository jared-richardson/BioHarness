from __future__ import annotations

import os
from pathlib import Path

import pytest

from bio_harness.core.tool_probe import (
    SafeProbePolicy,
    discover_cli_metadata,
    extract_subcommands_from_help,
    is_safe_probe_command,
    run_safe_probe_command,
)


def _write_fake_tool(script_path: Path) -> None:
    script_path.write_text(
        """#!/usr/bin/env python3
import sys

args = sys.argv[1:]
if not args or args[0] in ("--help", "-h", "help"):
    print(\"\"\"FakeTool 2.4.1

Commands:
  align     Align reads to a reference
  index     Build an index from a reference

Options:
  --dry-run   Validate arguments without writing outputs
  --example   Print an example invocation
\"\"\")
    raise SystemExit(0)
if args[0] in ("--version", "-V", "version"):
    print("FakeTool version 2.4.1")
    raise SystemExit(0)
if len(args) >= 2 and args[0] == "align" and args[1] in ("--help", "-h", "help"):
    print("align --help\\n  --reads FASTQ\\n  --reference FASTA")
    raise SystemExit(0)
if len(args) >= 2 and args[0] == "index" and args[1] in ("--help", "-h", "help"):
    print("index --help\\n  --reference FASTA")
    raise SystemExit(0)
print("unsupported invocation", file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def test_is_safe_probe_command_accepts_read_only_help_and_version() -> None:
    assert is_safe_probe_command(("samtools", "--help")) is True
    assert is_safe_probe_command(("samtools", "sort", "--help")) is True
    assert is_safe_probe_command(("samtools", "sort", "version")) is True


def test_is_safe_probe_command_rejects_workflow_like_or_deep_commands() -> None:
    assert is_safe_probe_command(("samtools", "sort", "input.bam")) is False
    assert is_safe_probe_command(("samtools", "sort", "-o", "out.bam")) is False
    assert is_safe_probe_command(("tool", "one", "two", "three", "--help")) is False


def test_extract_subcommands_from_help_uses_command_sections() -> None:
    help_text = """
ExampleTool v1.0

Commands:
  align      Align reads
  index      Build index
  stats      Report stats

Options:
  --help     Show help
"""
    assert extract_subcommands_from_help(help_text) == ["align", "index", "stats"]


def test_run_safe_probe_command_rejects_unsafe_invocations() -> None:
    with pytest.raises(ValueError, match="Unsafe onboarding probe command"):
        run_safe_probe_command(("samtools", "sort", "input.bam"))


def test_discover_cli_metadata_collects_help_version_and_subcommands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool_path = tmp_path / "faketool"
    _write_fake_tool(tool_path)
    existing_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{tmp_path}:{existing_path}")

    info = discover_cli_metadata(
        "faketool",
        timeout=5,
        policy=SafeProbePolicy(max_subcommands=5),
    )

    assert info["tool_name"] == "faketool"
    assert info["executable"] == str(tool_path)
    assert "FakeTool 2.4.1" in info["help_text"]
    assert info["version"] == "2.4.1"
    assert info["subcommands"] == ["align", "index"]
    assert info["supports_dry_run"] is True
    assert info["supports_examples"] is True
    assert "--help" in info["observed_help_flags"]
    assert "--version" in info["safe_probe_flags"]
