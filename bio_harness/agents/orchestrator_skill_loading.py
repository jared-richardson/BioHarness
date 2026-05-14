from __future__ import annotations

import importlib
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from bio_harness.core.harness_help_context import build_harness_help_context

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_skill_markdown_path(file_path: str | Path) -> Path:
    """Resolve an indexed skill markdown path against the repo root when needed."""
    candidate = Path(file_path).expanduser()
    if candidate.is_absolute():
        return candidate
    repo_candidate = PROJECT_ROOT / candidate
    if repo_candidate.exists():
        return repo_candidate
    return candidate


def load_tools_context(skills: dict[str, dict[str, Any]], *, logger: logging.Logger | None = None) -> str:
    """Load deterministic help plus full SKILL.md contents into one context string."""

    active_logger = logger or logging.getLogger(__name__)
    context_parts: list[str] = []
    try:
        context_parts.append(build_harness_help_context(skills, compact=True))
    except Exception as exc:
        active_logger.error("Failed to build deterministic harness help context: %s", exc)
    for skill_name, skill_data in skills.items():
        file_path = _resolve_skill_markdown_path(skill_data["file_path"])
        try:
            full_content = file_path.read_text()
            context_parts.append(
                f"""--- Skill: {skill_name} ---
{full_content}
--- End Skill: {skill_name} ---
"""
            )
        except Exception as exc:
            active_logger.error("Failed to read content for skill '%s' from %s: %s", skill_name, file_path, exc)
    return "\n".join(context_parts)


def load_skill_functions(
    skill_names: list[str],
    skill_library_dir: Path,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Discover skill functions from the skill library directory."""

    active_logger = logger or logging.getLogger(__name__)
    inserted_path = False
    library_path = str(skill_library_dir)
    if library_path not in sys.path:
        sys.path.insert(0, library_path)
        inserted_path = True

    discovered_funcs: dict[str, Any] = {}
    try:
        for module_file in skill_library_dir.glob("*.py"):
            if module_file.name.startswith("_"):
                continue
            module_name = module_file.stem
            try:
                module = importlib.import_module(module_name)
                for attr_name, attr_value in inspect.getmembers(module, inspect.isfunction):
                    if attr_name.startswith("_"):
                        continue
                    discovered_funcs[attr_name] = attr_value
            except Exception as exc:
                active_logger.error("Could not import skill module '%s': %s", module_name, exc)
    finally:
        if inserted_path and library_path in sys.path:
            sys.path.remove(library_path)

    loaded: dict[str, Any] = {}
    for skill_name in skill_names:
        func = discovered_funcs.get(skill_name)
        if func is not None:
            loaded[skill_name] = func
            active_logger.info("Loaded skill function '%s'.", skill_name)
        else:
            active_logger.warning("No implementation mapping found for skill '%s'.", skill_name)
    return loaded
