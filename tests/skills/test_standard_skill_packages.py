"""Tests for generated standard skill packages."""

from pathlib import Path
from typing import Any

import yaml

from scripts.generate_standard_skill_packages import generate_packages, load_definition

DEFINITIONS_DIR = Path("bio_harness/skills/definitions")
CATALOG_DIR = Path("bio_harness/skills/catalog")
REQUIRED_SKILL_SECTIONS = (
    "## What This Does",
    "## Use This Skill When",
    "## Do Not Use This Skill When",
    "## Required Inputs",
    "## Expected Outputs",
    "## Execution Contract",
)
REQUIRED_CONTRACT_FIELDS = ("name", "description", "risk_level", "parameters")


def _definition_paths() -> list[Path]:
    """Return source tool-contract definitions that should have skill packages."""

    return [
        path
        for path in sorted(DEFINITIONS_DIR.glob("*.md"))
        if path.name != "template.md"
    ]


def _load_skill_frontmatter(skill_md: Path) -> dict[str, Any]:
    """Load the YAML frontmatter from a generated ``SKILL.md`` file."""

    lines = skill_md.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", f"{skill_md} must start with YAML frontmatter"
    end_idx = lines[1:].index("---") + 1
    loaded = yaml.safe_load("\n".join(lines[1:end_idx])) or {}
    assert isinstance(loaded, dict)
    return loaded


def test_every_tool_contract_has_standard_skill_package() -> None:
    """Every executable tool contract should have a standard skill package."""

    expected_names = {load_definition(path)[0]["name"] for path in _definition_paths()}
    package_names = {path.name for path in CATALOG_DIR.iterdir() if path.is_dir()}

    assert package_names == expected_names


def test_standard_skill_packages_have_required_files_and_sections() -> None:
    """Generated packages should be readable as standard agent skills."""

    for definition_path in _definition_paths():
        metadata, _body = load_definition(definition_path)
        package_dir = CATALOG_DIR / str(metadata["name"])
        skill_md = package_dir / "SKILL.md"
        contract_yaml = package_dir / "contract.yaml"

        assert skill_md.is_file(), f"missing {skill_md}"
        assert contract_yaml.is_file(), f"missing {contract_yaml}"

        skill_metadata = _load_skill_frontmatter(skill_md)
        assert skill_metadata["name"] == metadata["name"]
        assert str(skill_metadata["description"]).strip() == str(metadata["description"]).strip()

        body = skill_md.read_text(encoding="utf-8")
        for section in REQUIRED_SKILL_SECTIONS:
            assert section in body, f"{skill_md} missing {section}"
        assert "The harness executes the typed contract in `contract.yaml`." in body


def test_generated_contracts_round_trip_required_source_metadata() -> None:
    """Generated contract YAML must preserve required executable metadata."""

    for definition_path in _definition_paths():
        metadata, _body = load_definition(definition_path)
        package_dir = CATALOG_DIR / str(metadata["name"])
        contract = yaml.safe_load((package_dir / "contract.yaml").read_text(encoding="utf-8"))

        assert isinstance(contract, dict)
        for field in REQUIRED_CONTRACT_FIELDS:
            assert contract[field] == metadata[field], (
                f"{package_dir / 'contract.yaml'} field {field!r} drifted from "
                f"{definition_path}"
            )
        assert contract["source_definition"] == definition_path.as_posix()
        assert contract["generated_by"] == "scripts/generate_standard_skill_packages.py"


def test_standard_skill_catalog_count_matches_definition_count() -> None:
    """The generated catalog should not silently drop or add packages."""

    package_dirs = [path for path in CATALOG_DIR.iterdir() if path.is_dir()]

    assert len(package_dirs) == len(_definition_paths())
    assert len(package_dirs) == 82


def test_generator_creates_frontmatter_first_standard_skill_package(tmp_path: Path) -> None:
    """Generator should create parseable standard ``SKILL.md`` packages."""

    definitions_dir = tmp_path / "definitions"
    catalog_dir = tmp_path / "catalog"
    definitions_dir.mkdir()
    source = definitions_dir / "toy_tool.md"
    source.write_text(
        """---
name: toy_tool
description: Run a toy analysis.
when_to_use: Use when testing package generation
when_not_to_use: Do not use for real science
risk_level: low
parameters:
  input_table:
    type: path
    description: Input table.
    required: true
output_types:
  - tsv
tools_required:
  - toy
system_requirements:
  min_ram_gb: 1
  min_cores: 1
command_template: toy --input {input_table}
---
Body note.
""",
        encoding="utf-8",
    )

    generated = generate_packages(definitions_dir, catalog_dir, tmp_path)

    assert generated == [catalog_dir / "toy_tool"]
    skill_md = catalog_dir / "toy_tool" / "SKILL.md"
    contract_yaml = catalog_dir / "toy_tool" / "contract.yaml"
    assert skill_md.read_text(encoding="utf-8").splitlines()[0] == "---"
    assert _load_skill_frontmatter(skill_md) == {
        "name": "toy_tool",
        "description": "Run a toy analysis.",
    }
    contract = yaml.safe_load(contract_yaml.read_text(encoding="utf-8"))
    assert contract["source_definition"] == "definitions/toy_tool.md"
    assert contract["parameters"]["input_table"]["required"] is True
