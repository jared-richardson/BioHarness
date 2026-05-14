import logging
import json
from pathlib import Path
from typing import Dict, Optional, List, Tuple
import yaml

from bio_harness.core.skill_retrieval import (
    build_skill_retrieval_record,
    render_retrieval_record,
    search_skill_records,
)

logger = logging.getLogger(__name__)

try:
    import frontmatter  # type: ignore
except Exception:  # pragma: no cover - optional dependency path
    frontmatter = None


class SkillRegistry:
    """
    Manages the loading and retrieval of skills defined in Markdown files with YAML frontmatter.
    """
    _skills: Dict[str, Dict]
    REQUIRED_FIELDS = ("name", "description", "risk_level", "parameters")
    REQUIRED_ENRICHED_LIST_FIELDS = ("analysis_categories", "capabilities")
    REQUIRED_ENRICHED_MAPPING_FIELDS = ("system_requirements",)
    REQUIRED_SYSTEM_REQUIREMENT_KEYS = ("min_ram_gb", "min_cores")
    ENRICHED_METADATA_ALLOWLIST = {
        "bash_run": frozenset({"capabilities"}),
    }
    ALLOWED_RISK_LEVELS = {"low", "medium", "high"}

    def __init__(self, skills_dir: Path):
        """
        Initializes the SkillRegistry with the path to the skills definition directory.

        Args:
            skills_dir: The pathlib.Path object pointing to the directory
                        containing skill definition Markdown files.
        """
        if not skills_dir.is_dir():
            raise ValueError(f"Skills directory does not exist: {skills_dir}")
        self.skills_dir = skills_dir
        self._skills = {}  # Initialize an empty dictionary to store skills
        self.load_skills() # Load skills upon initialization

    def load_skills(self) -> None:
        """
        Scans the skills directory for Markdown files, parses their YAML frontmatter,
        and stores the skill definitions. Invalid YAML in a file will be logged
        as a warning, but will not stop the loading process.
        """
        self._skills = {}  # Clear existing skills before reloading
        for skill_file in self.skills_dir.glob("*.md"):
            if skill_file.name == "template.md":
                logger.info(f"Skipping template file: {skill_file.name}")
                continue
            
            try:
                metadata = self._load_frontmatter(skill_file)
                valid, errors = self.validate_skill_metadata(metadata)
                if valid and "name" in metadata:
                    skill_name = metadata["name"]
                    self._skills[skill_name] = {**metadata, "file_path": str(skill_file)}
                    logger.info(f"Loaded skill: {skill_name} from {skill_file.name}")
                else:
                    logger.warning(
                        f"Skipping invalid skill file {skill_file.name}: {'; '.join(errors)}"
                    )
            except yaml.YAMLError as e:
                logger.warning(
                    f"Skipping skill file {skill_file.name} due to invalid YAML frontmatter: {e}"
                )
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred while loading skill file {skill_file.name}: {e}"
                )

    def _repo_root_for_output(self, output_file: Path) -> Path | None:
        """Return the nearest repo-like root for path normalization when available."""
        for candidate in output_file.resolve().parents:
            if (
                (candidate / "bio_harness").is_dir()
                and (candidate / "scripts").is_dir()
                and (
                    (candidate / "pixi.toml").is_file()
                    or (candidate / "app.py").is_file()
                )
            ):
                return candidate
        return None

    def _index_file_path(self, skill_file: Path, output_file: Path) -> str:
        """Return a repo-neutral path for the generated skill index entry."""
        resolved_skill = skill_file.resolve()
        repo_root = self._repo_root_for_output(output_file)
        if repo_root is not None:
            try:
                return str(resolved_skill.relative_to(repo_root))
            except ValueError:
                pass
        try:
            return str(resolved_skill.relative_to(output_file.parent))
        except ValueError:
            return str(Path(resolved_skill.name))

    def _load_frontmatter(self, skill_file: Path) -> Dict:
        """
        Load YAML frontmatter from a markdown file.
        Uses python-frontmatter when available, and falls back to local parsing.
        """
        if frontmatter is not None:
            with open(skill_file, "r", encoding="utf-8") as handle:
                post = frontmatter.load(handle)
                return dict(post.metadata)

        raw = skill_file.read_text(encoding="utf-8")
        lines = raw.splitlines()
        if len(lines) < 3 or lines[0].strip() != "---":
            return {}

        end_idx = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break
        if end_idx is None:
            return {}

        yaml_text = "\n".join(lines[1:end_idx])
        parsed = yaml.safe_load(yaml_text) or {}
        if not isinstance(parsed, dict):
            return {}
        return parsed

    def validate_skill_metadata(self, metadata: Dict) -> Tuple[bool, List[str]]:
        """
        Validates required metadata fields for a SKILL.md file.

        Returns:
            (is_valid, errors)
        """
        errors: List[str] = []
        for field in self.REQUIRED_FIELDS:
            if field not in metadata:
                errors.append(f"missing required field '{field}'")

        risk_level = str(metadata.get("risk_level", "")).lower()
        if risk_level and risk_level not in self.ALLOWED_RISK_LEVELS:
            errors.append(
                f"invalid risk_level '{metadata.get('risk_level')}', expected one of {sorted(self.ALLOWED_RISK_LEVELS)}"
            )

        parameters = metadata.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            errors.append("field 'parameters' must be a mapping/object")

        return len(errors) == 0, errors

    def validate_enriched_metadata(
        self,
        metadata: Dict,
        *,
        allowlist: Optional[Dict[str, set[str] | frozenset[str]]] = None,
    ) -> Tuple[bool, List[str]]:
        """Validate metadata fields relied on by deterministic routing/install paths."""

        errors: List[str] = []
        skill_name = str(metadata.get("name", "")).strip()
        allowed_fields = set((allowlist or self.ENRICHED_METADATA_ALLOWLIST).get(skill_name, set()))

        for field in self.REQUIRED_ENRICHED_LIST_FIELDS:
            if field in allowed_fields:
                continue
            value = metadata.get(field)
            if not isinstance(value, list) or not value or not all(str(item).strip() for item in value):
                errors.append(f"field '{field}' must be a non-empty list")

        for field in self.REQUIRED_ENRICHED_MAPPING_FIELDS:
            if field in allowed_fields:
                continue
            value = metadata.get(field)
            if not isinstance(value, dict) or not value:
                errors.append(f"field '{field}' must be a non-empty mapping/object")
                continue
            missing_keys = [key for key in self.REQUIRED_SYSTEM_REQUIREMENT_KEYS if key not in value]
            if missing_keys:
                errors.append(f"field '{field}' is missing required keys {missing_keys}")

        return len(errors) == 0, errors

    def find_incomplete_skills(
        self,
        *,
        allowlist: Optional[Dict[str, set[str] | frozenset[str]]] = None,
    ) -> Dict[str, List[str]]:
        """Return any loaded skills missing required enriched metadata."""

        issues: Dict[str, List[str]] = {}
        for name, data in sorted(self._skills.items()):
            valid, errors = self.validate_enriched_metadata(data, allowlist=allowlist)
            if not valid:
                issues[name] = errors
        return issues

    def generate_index(self, output_file: Optional[Path] = None) -> Path:
        """
        Generates a machine-readable index of loaded skills.
        """
        if output_file is None:
            output_file = self.skills_dir / "index.json"
        output_file = output_file.resolve()

        entries = []
        for name, data in sorted(self._skills.items()):
            skill_file = Path(str(data.get("file_path", ""))).expanduser()
            entry: dict = {
                "name": name,
                "description": data.get("description", ""),
                "risk_level": data.get("risk_level", "unknown"),
                "tools_required": data.get("tools_required", []),
                "system_requirements": data.get("system_requirements", {}),
                "parameters": data.get("parameters", {}),
                "file_path": self._index_file_path(skill_file, output_file),
            }
            # Include enriched fields when present
            for extra_field in (
                "when_to_use",
                "when_not_to_use",
                "input_types",
                "output_types",
                "canonical_output_filenames",
                "analysis_categories",
                "capabilities",
            ):
                val = data.get(extra_field)
                if val is not None:
                    entry[extra_field] = val
            entries.append(entry)

        payload = {
            "version": 1,
            "skills_count": len(entries),
            "skills": entries,
        }

        output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("Wrote skills index to %s", output_file)
        return output_file

    def generate_retrieval_index(
        self,
        output_file: Optional[Path] = None,
        *,
        tool_cards_dir: Optional[Path] = None,
    ) -> Path:
        """Generate a retrieval-focused index for hybrid skill search.

        Args:
            output_file: Optional output path. Defaults to
                ``skills_dir / "retrieval_index.json"``.
            tool_cards_dir: Optional directory containing persisted tool cards.

        Returns:
            Path to the written retrieval index JSON.
        """

        if output_file is None:
            output_file = self.skills_dir / "retrieval_index.json"
        output_file = output_file.resolve()
        records = self._build_retrieval_records(tool_cards_dir=tool_cards_dir)
        payload = {
            "version": 1,
            "skills_count": len(records),
            "skills": [render_retrieval_record(record) for record in records],
        }
        output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("Wrote skill retrieval index to %s", output_file)
        return output_file

    def search_skills(
        self,
        query: str,
        *,
        limit: int = 5,
        tool_cards_dir: Optional[Path] = None,
    ) -> List[Dict]:
        """Search loaded skills with hybrid lexical and semantic scoring.

        Args:
            query: Natural-language search query.
            limit: Maximum number of results to return.
            tool_cards_dir: Optional directory of tool-card JSON files.

        Returns:
            Ranked skill metadata entries enriched with retrieval scores.
        """

        records = self._build_retrieval_records(tool_cards_dir=tool_cards_dir)
        matches = search_skill_records(query, records, limit=limit)
        results: List[Dict] = []
        for match in matches:
            metadata = dict(self._skills.get(match.name, {}))
            metadata["retrieval_score"] = match.score
            metadata["semantic_score"] = match.semantic_score
            metadata["lexical_score"] = match.lexical_score
            metadata["matched_terms"] = list(match.matched_terms)
            results.append(metadata)
        return results

    def get_skill(self, name: str) -> Optional[Dict]:
        """
        Retrieves a skill definition by its name.

        Args:
            name: The name of the skill to retrieve.

        Returns:
            A dictionary containing the skill's metadata, or None if the skill is not found.
        """
        return self._skills.get(name)

    def _build_retrieval_records(
        self,
        *,
        tool_cards_dir: Optional[Path] = None,
    ) -> List:
        """Build retrieval records from loaded skills and optional tool cards."""

        records = []
        for name, data in sorted(self._skills.items()):
            tool_card = self._load_tool_card(name, tool_cards_dir=tool_cards_dir)
            records.append(build_skill_retrieval_record(data, tool_card=tool_card))
        return records

    def _load_tool_card(self, name: str, *, tool_cards_dir: Optional[Path]) -> Optional[object]:
        """Load a persisted tool card for one skill when available."""

        if tool_cards_dir is None:
            return None
        candidate = Path(tool_cards_dir) / f"{str(name).strip()}.json"
        if not candidate.is_file():
            return None
        try:
            from bio_harness.core.tool_cards import read_tool_card

            return read_tool_card(candidate)
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning("Failed to load tool card for %s: %s", name, exc)
            return None
