"""Shared schemas and exceptions for the BioLLM planner."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

_DEFAULT_FALLBACK_MODEL = "qwen3-coder-next:latest"


class BioHarnessError(Exception):
    """Custom exception for BioHarness-related planner failures."""


class ToolStep(BaseModel):
    """Represents a single step in the execution plan."""

    tool_name: str = Field(description="The name of the tool to be executed.")
    arguments: dict[str, Any] = Field(description="A dictionary of arguments for the tool.")
    step_id: int = Field(description="A unique identifier for this step in the plan.")
    deliverables: list[str] = Field(
        default_factory=list,
        description="Optional deliverables associated with this step.",
    )
    expected_files: list[str] = Field(
        default_factory=list,
        description="Optional files that should exist after this step.",
    )
    validation_method: str = Field(
        default="",
        description="Optional validation mode for the declared expected files.",
    )


class LLMOutputSchema(BaseModel):
    """Schema for the expected executable-plan JSON output from the LLM."""

    thought_process: str = Field(
        default="No thought process provided by model.",
        description="Brief reasoning for the generated plan.",
    )
    plan: list[ToolStep] = Field(description="A list of tool execution steps.")
    final_deliverables: list[str] = Field(
        default_factory=list,
        description="Final published deliverables requested for the run.",
    )


class AbstractToolStep(BaseModel):
    """Compact planning step used for the outline-first planner pass."""

    tool_name: str = Field(description="The selected tool name.")
    objective: str = Field(description="A short description of what this step should do.")
    step_id: int = Field(description="A unique identifier for this step in the outline.")


class AbstractPlanSchema(BaseModel):
    """Compact planning schema for the first planner pass."""

    thought_process: str = Field(
        default="No thought process provided by model.",
        description="Brief reasoning for the generated outline.",
    )
    plan_outline: list[AbstractToolStep] = Field(description="A compact execution outline.")


__all__ = [
    "AbstractPlanSchema",
    "AbstractToolStep",
    "BioHarnessError",
    "LLMOutputSchema",
    "ToolStep",
    "_DEFAULT_FALLBACK_MODEL",
]
