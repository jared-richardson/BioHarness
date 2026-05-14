"""Legacy simple planner/executor agent.

This module is kept for compatibility. New product paths should use
``bio_harness.agents.orchestrator.Orchestrator``, which carries the newer
execution-policy and workspace-guard integration.
"""

import importlib
import inspect
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Dict

from pydantic import ValidationError

from bio_harness.core.llm import BioHarnessError, BioLLM, LLMOutputSchema
from bio_harness.core.runner import CommandRunner
from bio_harness.core.skill_argument_policy import (
    normalize_execution_arguments,
    resolve_execution_working_directory,
)
from bio_harness.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class Agent:
    """Legacy planner/executor agent kept for compatibility only."""

    def __init__(self, skills_dir: Path, skill_library_dir: Path, llm_backend: str | None = None, host: str | None = None):
        self.skill_registry = SkillRegistry(skills_dir)
        self.biollm = BioLLM(host=host, llm_backend=llm_backend)
        self.command_runner = CommandRunner()
        self.skill_library_dir = skill_library_dir
        self._loaded_skill_functions: Dict[str, Any] = {}
        self._load_skill_functions()

    def _load_skill_functions(self) -> None:
        import sys

        if str(self.skill_library_dir) not in sys.path:
            sys.path.insert(0, str(self.skill_library_dir))

        try:
            discovered_funcs: Dict[str, Any] = {}
            for module_file in self.skill_library_dir.glob("*.py"):
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
                    logger.error("Could not import skill module '%s': %s", module_name, exc)

            for skill_name in self.skill_registry._skills.keys():
                if skill_name in discovered_funcs:
                    self._loaded_skill_functions[skill_name] = discovered_funcs[skill_name]
                    logger.info("Loaded skill function '%s'", skill_name)
                else:
                    logger.warning("No implementation mapping for skill '%s'", skill_name)
        finally:
            if str(self.skill_library_dir) in sys.path:
                sys.path.remove(str(self.skill_library_dir))

    def think(self, user_query: str) -> Dict[str, Any]:
        return self.biollm.think(user_query, list(self.skill_registry._skills.values()))

    def execute_plan(self, plan_json: Dict[str, Any], log_queue: queue.Queue, cwd: str | None = None) -> None:
        log_queue.put("Starting plan execution...\n")
        try:
            validated_plan = LLMOutputSchema(**plan_json)
        except ValidationError as exc:
            log_queue.put(f"Error: Invalid plan JSON received: {exc}\n")
            log_queue.put(None)
            return

        for step in validated_plan.plan:
            tool_name = step.tool_name
            arguments = step.arguments
            step_id = step.step_id

            log_queue.put(f"--- Executing Step {step_id}: {tool_name} ---\n")
            if tool_name not in self._loaded_skill_functions:
                log_queue.put(f"Error: Unknown tool '{tool_name}'.\n")
                log_queue.put(None)
                return

            try:
                skill_arguments = normalize_execution_arguments(tool_name, arguments, cwd=cwd)
                command_cwd = resolve_execution_working_directory(tool_name, skill_arguments, cwd=cwd)
                command_to_execute = self._loaded_skill_functions[tool_name](
                    **skill_arguments
                )
                temp_log_queue = queue.Queue()
                command_thread = threading.Thread(
                    target=self.command_runner.run_command,
                    args=(command_to_execute, temp_log_queue, command_cwd),
                    daemon=True,
                )
                command_thread.start()

                step_exit_code = None
                while True:
                    log_line = temp_log_queue.get()
                    if log_line is None:
                        break
                    stripped = log_line.strip()
                    if stripped.startswith("[exit_code=") and stripped.endswith("]"):
                        try:
                            step_exit_code = int(stripped.removeprefix("[exit_code=").removesuffix("]"))
                        except ValueError:
                            step_exit_code = None
                    log_queue.put(f"[Step {step_id}] {log_line}")
                command_thread.join()
                if step_exit_code is not None and step_exit_code != 0:
                    log_queue.put(f"Error: Step {step_id} ({tool_name}) failed with exit code {step_exit_code}.\n")
                    log_queue.put(None)
                    return
                log_queue.put(f"--- Step {step_id} ({tool_name}) finished ---\n")
            except Exception as exc:
                log_queue.put(f"Error executing step {step_id} ({tool_name}): {exc}\n")
                log_queue.put(None)
                return

        log_queue.put("Plan execution completed.\n")
        log_queue.put(None)


__all__ = ["Agent", "BioHarnessError"]
