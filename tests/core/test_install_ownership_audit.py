from __future__ import annotations

import json
import tomllib
from pathlib import Path

from bio_harness.core import environment_bootstrap
from bio_harness.core.isolated_tool_recipes import load_isolated_tool_recipes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIXI_TOML_PATH = PROJECT_ROOT / "pixi.toml"
SKILL_INDEX_PATH = PROJECT_ROOT / "bio_harness" / "skills" / "definitions" / "index.json"
DEFAULT_PIXI_TOOL_EQUIVALENTS: dict[str, set[str]] = {
    "blast": {
        "blast_formatter",
        "blastdb_aliastool",
        "blastdbcheck",
        "blastdbcmd",
        "blastn",
        "blastp",
        "blastx",
        "deltablast",
        "makeblastdb",
        "makeprofiledb",
        "psiblast",
        "rpsblast",
        "rpstblastn",
        "tblastn",
        "tblastx",
    },
    "bwa-mem2": {"bwa"},
    "gatk4": {"gatk"},
    "openjdk": {"java"},
    "python": {"python3"},
    "spades": {"spades.py"},
    "subread": {"featurecounts"},
}
EXPLICIT_ALLOWED_INTERNAL_EXCEPTIONS: dict[str, str] = {
    "deseq2": "default environment ships pydeseq2 for the deterministic DE fallback path",
    "rscript": "R-backed Pixi feature environments provide Rscript transitively rather than as a standalone mapped token",
}


def _default_pixi_dependency_names() -> set[str]:
    payload = tomllib.loads(PIXI_TOML_PATH.read_text(encoding="utf-8"))
    dependencies = payload.get("dependencies", {})
    return {str(name).strip() for name in dependencies if str(name).strip()}


def _required_tool_tokens() -> set[str]:
    payload = json.loads(SKILL_INDEX_PATH.read_text(encoding="utf-8"))
    rows = payload.get("skills", [])
    tools: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for token in row.get("tools_required", []) or []:
            cleaned = str(token).strip()
            if cleaned:
                tools.add(cleaned)
    return tools


def test_freebayes_skill_metadata_declares_samtools() -> None:
    payload = json.loads(SKILL_INDEX_PATH.read_text(encoding="utf-8"))
    freebayes_row = next(
        row
        for row in payload.get("skills", [])
        if isinstance(row, dict) and str(row.get("name", "")).strip() == "freebayes_call"
    )

    assert freebayes_row["tools_required"] == ["freebayes", "samtools"]


def test_all_skill_tools_have_declared_install_ownership() -> None:
    default_tools = _default_pixi_dependency_names()
    optional_tools = {
        str(tool).strip()
        for tools in environment_bootstrap.PIXI_ENVIRONMENT_TOOLS.values()
        for tool in tools
        if str(tool).strip()
    }
    isolated_tools = {str(tool).strip() for tool in load_isolated_tool_recipes().keys() if str(tool).strip()}
    manual_tools = {str(tool).strip() for tool in environment_bootstrap.MANUAL_TOOL_NOTES if str(tool).strip()}

    covered_tools = set(default_tools) | optional_tools | isolated_tools | manual_tools
    for owner, aliases in DEFAULT_PIXI_TOOL_EQUIVALENTS.items():
        if owner in covered_tools:
            covered_tools.update(aliases)

    unresolved = sorted(
        token
        for token in _required_tool_tokens()
        if token not in covered_tools and token not in EXPLICIT_ALLOWED_INTERNAL_EXCEPTIONS
    )

    assert unresolved == []
    assert "freebayes" in optional_tools
    assert "samtools" in _required_tool_tokens()
    assert "blast" in default_tools
    assert "blastn" in covered_tools
