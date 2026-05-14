"""Pre-execution plan validation helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from bio_harness.core.tool_registry import ToolRegistry, render_expected_output_path

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """Severity of one validation finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationFinding:
    """One validation issue discovered while checking a plan."""

    severity: Severity
    code: str
    message: str
    step_id: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "step_id": self.step_id,
        }


@dataclass
class PlanValidationResult:
    """Aggregated pre-execution validation result."""

    findings: List[ValidationFinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return whether the plan has no error-level findings."""

        return not any(item.severity == Severity.ERROR for item in self.findings)

    @property
    def errors(self) -> List[ValidationFinding]:
        """Return error-level findings."""

        return [item for item in self.findings if item.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[ValidationFinding]:
        """Return warning-level findings."""

        return [item for item in self.findings if item.severity == Severity.WARNING]

    def summary(self) -> str:
        """Return a compact user-facing summary."""

        err_count = len(self.errors)
        warn_count = len(self.warnings)
        if err_count == 0 and warn_count == 0:
            return "Plan validation passed."
        parts: list[str] = []
        if err_count:
            parts.append(f"{err_count} error(s)")
        if warn_count:
            parts.append(f"{warn_count} warning(s)")
        return f"Plan validation: {', '.join(parts)}."


def _normalize_plan_dict(plan_dict: Any) -> tuple[dict[str, Any], PlanValidationResult]:
    """Normalize raw plan-like input into a plain dictionary."""

    result = PlanValidationResult()
    if hasattr(plan_dict, "model_dump"):
        plan_dict = plan_dict.model_dump()
    if not isinstance(plan_dict, dict):
        result.findings.append(
            ValidationFinding(
                severity=Severity.ERROR,
                code="INVALID_PLAN_TYPE",
                message=f"Expected dict, got {type(plan_dict).__name__}.",
            )
        )
        return {}, result

    raw_steps = plan_dict.get("plan", [])
    if isinstance(raw_steps, list):
        normalized_steps: list[dict[str, Any]] = []
        for step in raw_steps:
            if hasattr(step, "model_dump"):
                normalized_steps.append(step.model_dump())
            elif isinstance(step, dict):
                normalized_steps.append(step)
        plan_dict = {**plan_dict, "plan": normalized_steps}
    return plan_dict, result


def _check_empty_plan(plan_dict: Dict[str, Any]) -> List[ValidationFinding]:
    """Reject plans with zero executable steps."""

    steps = plan_dict.get("plan", [])
    if not isinstance(steps, list) or not steps:
        return [
            ValidationFinding(
                severity=Severity.ERROR,
                code="EMPTY_PLAN",
                message="Plan contains no executable steps.",
            )
        ]
    return []


def _check_duplicate_step_ids(plan_dict: Dict[str, Any]) -> List[ValidationFinding]:
    """Reject duplicate step identifiers."""

    findings: List[ValidationFinding] = []
    seen: dict[int, int] = {}
    for step in plan_dict.get("plan", []):
        if not isinstance(step, dict):
            continue
        try:
            step_id = int(step.get("step_id", -1))
        except (TypeError, ValueError):
            continue
        if step_id in seen:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    code="DUPLICATE_STEP_ID",
                    message=f"Step ID {step_id} appears more than once in the plan.",
                    step_id=step_id,
                )
            )
        seen[step_id] = seen.get(step_id, 0) + 1
    return findings


def _check_tool_names(
    plan_dict: Dict[str, Any],
    known_skill_names: Set[str],
    *,
    strict: bool,
) -> List[ValidationFinding]:
    """Verify every step references a known tool."""

    findings: List[ValidationFinding] = []
    for step in plan_dict.get("plan", []):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip()
        step_id = step.get("step_id")
        if not tool_name:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    code="MISSING_TOOL_NAME",
                    message=f"Step {step_id} has no tool_name.",
                    step_id=step_id,
                )
            )
            continue
        if tool_name not in known_skill_names:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR if strict else Severity.WARNING,
                    code="UNKNOWN_TOOL",
                    message=f"Step {step_id} uses unknown tool '{tool_name}'.",
                    step_id=step_id,
                )
            )
    return findings


def _argument_missing(value: Any) -> bool:
    """Return whether one argument value should count as missing."""

    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _check_required_arguments(
    plan_dict: Dict[str, Any],
    registry: ToolRegistry,
) -> List[ValidationFinding]:
    """Verify each step carries every required argument."""

    findings: List[ValidationFinding] = []
    for step in plan_dict.get("plan", []):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip()
        step_id = step.get("step_id")
        if not tool_name:
            continue
        args = step.get("arguments", {})
        arguments = args if isinstance(args, dict) else {}
        required_parameters = registry.required_parameters_for(tool_name)
        harness_managed = set(registry.harness_managed_parameters_for(tool_name))
        missing = [
            name
            for name in required_parameters
            if name not in harness_managed
            and (name not in arguments or _argument_missing(arguments.get(name)))
        ]
        if missing:
            findings.append(
                ValidationFinding(
                    severity=Severity.ERROR,
                    code="MISSING_REQUIRED_ARGS",
                    message=(
                        f"Step {step_id} ({tool_name}) is missing required argument(s): "
                        f"{', '.join(sorted(missing))}."
                    ),
                    step_id=step_id,
                )
            )
    return findings


def _check_legacy_required_inputs(
    plan_dict: Dict[str, Any],
    input_path_keys: Dict[str, List[str]],
) -> List[ValidationFinding]:
    """Preserve the legacy input-key validation behavior for compatibility."""

    findings: List[ValidationFinding] = []
    for step in plan_dict.get("plan", []):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip()
        step_id = step.get("step_id")
        arguments = step.get("arguments", {})
        args = arguments if isinstance(arguments, dict) else {}
        required_keys = input_path_keys.get(tool_name, [])
        if not required_keys:
            continue
        missing = [key for key in required_keys if key not in args or _argument_missing(args.get(key))]
        if missing:
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    code="MISSING_INPUT_ARGS",
                    message=(
                        f"Step {step_id} ({tool_name}) is missing input argument(s): "
                        f"{', '.join(missing)}."
                    ),
                    step_id=step_id,
                )
            )
    return findings


def _check_argument_names(
    plan_dict: Dict[str, Any],
    registry: ToolRegistry,
) -> List[ValidationFinding]:
    """Warn on undeclared argument names when tool metadata is available."""

    findings: List[ValidationFinding] = []
    for step in plan_dict.get("plan", []):
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip()
        step_id = step.get("step_id")
        if not tool_name:
            continue
        args = step.get("arguments", {})
        arguments = args if isinstance(args, dict) else {}
        declared = set(registry.parameter_schema_for(tool_name).keys())
        if not declared:
            continue
        explicit_wrapper_params = registry.wrapper_parameter_names_for(tool_name)
        wrapper_accepts_kwargs = registry.wrapper_accepts_var_keyword(tool_name)
        for arg_name in sorted(arguments.keys()):
            if arg_name in declared:
                continue
            if explicit_wrapper_params and arg_name in explicit_wrapper_params:
                continue
            if explicit_wrapper_params and not wrapper_accepts_kwargs:
                findings.append(
                    ValidationFinding(
                        severity=Severity.ERROR,
                        code="UNDECLARED_ARGUMENT",
                        message=(
                            f"Step {step_id} ({tool_name}) passes unexpected argument '{arg_name}' "
                            "that is not declared by the skill definition or wrapper."
                        ),
                        step_id=step_id,
                    )
                )
                continue
            findings.append(
                ValidationFinding(
                    severity=Severity.WARNING,
                    code="UNDECLARED_ARGUMENT",
                    message=(
                        f"Step {step_id} ({tool_name}) passes undeclared argument '{arg_name}'. "
                        "Planner output may have drifted from the tool contract."
                    ),
                    step_id=step_id,
                )
            )
    return findings


def _check_step_connectivity(
    plan_dict: Dict[str, Any],
    registry: ToolRegistry,
) -> List[ValidationFinding]:
    """Warn when a declared input does not resolve to an earlier output or path."""

    findings: List[ValidationFinding] = []
    steps = plan_dict.get("plan", [])
    if not isinstance(steps, list):
        return findings

    known_outputs: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "") or "").strip()
        step_id = step.get("step_id")
        args = step.get("arguments", {})
        arguments = args if isinstance(args, dict) else {}
        for key in registry.input_keys_for(tool_name):
            value = str(arguments.get(key, "") or "").strip()
            if not value:
                continue
            if value.startswith("/") or value.startswith("./") or value.startswith(".."):
                continue
            if value not in known_outputs and not Path(value).suffix:
                findings.append(
                    ValidationFinding(
                        severity=Severity.INFO,
                        code="UNRESOLVED_INPUT",
                        message=(
                            f"Step {step_id} ({tool_name}) argument '{key}' = '{value}' "
                            "does not match an earlier step output or a filesystem path."
                        ),
                        step_id=step_id,
                    )
                )
        for key in registry.output_argument_keys_for(tool_name):
            value = arguments.get(key)
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    rendered = str(item or "").strip()
                    if rendered:
                        known_outputs.add(rendered)
            else:
                rendered = str(value or "").strip()
                if rendered:
                    known_outputs.add(rendered)
        expected_output_files_by_key = registry.expected_output_files_by_key_for(tool_name)
        if expected_output_files_by_key:
            for key, relative_names in expected_output_files_by_key.items():
                output_root = str(arguments.get(key, "") or "").strip()
                if not output_root:
                    continue
                for relative_name in relative_names:
                    rendered = render_expected_output_path(
                        key=key,
                        output_root=output_root,
                        relative_name=relative_name,
                    )
                    if rendered:
                        known_outputs.add(rendered)
            continue
        for relative_name in registry.expected_output_files_for(tool_name):
            output_roots = []
            for key in registry.output_argument_keys_for(tool_name):
                value = str(arguments.get(key, "") or "").strip()
                if value:
                    output_roots.append(value)
            for output_root in output_roots:
                known_outputs.add(str(Path(output_root) / relative_name))
    return findings


def validate_plan(
    plan_dict: Dict[str, Any],
    *,
    registry: Optional[ToolRegistry] = None,
    known_skill_names: Optional[Set[str]] = None,
    input_path_keys: Optional[Dict[str, List[str]]] = None,
) -> PlanValidationResult:
    """Run pre-execution validation on one plan payload."""

    normalized_plan, result = _normalize_plan_dict(plan_dict)
    if result.errors:
        return result
    result.findings.extend(_check_empty_plan(normalized_plan))
    if result.errors:
        return result

    active_registry = registry
    strict_mode = active_registry is not None
    normalized_known_skills = (
        set(known_skill_names)
        if known_skill_names is not None
        else (set(active_registry.known_tool_names()) if active_registry is not None else None)
    )

    duplicate_findings = _check_duplicate_step_ids(normalized_plan)
    if not strict_mode:
        for finding in duplicate_findings:
            finding.severity = Severity.WARNING
    result.findings.extend(duplicate_findings)
    if normalized_known_skills is not None:
        result.findings.extend(
            _check_tool_names(
                normalized_plan,
                normalized_known_skills,
                strict=strict_mode,
            )
        )
    if result.errors:
        return result
    if active_registry is not None:
        result.findings.extend(_check_required_arguments(normalized_plan, active_registry))
        result.findings.extend(_check_argument_names(normalized_plan, active_registry))
        result.findings.extend(_check_step_connectivity(normalized_plan, active_registry))
    elif input_path_keys is not None:
        compatibility_registry = ToolRegistry()
        for tool_name, keys in input_path_keys.items():
            meta = compatibility_registry._ensure(tool_name)
            meta.input_path_keys = list(keys)
        result.findings.extend(_check_legacy_required_inputs(normalized_plan, input_path_keys))
        result.findings.extend(_check_step_connectivity(normalized_plan, compatibility_registry))

    if result.findings:
        logger.info(
            "Plan validation: %d error(s), %d warning(s).",
            len(result.errors),
            len(result.warnings),
        )
        for finding in result.findings:
            level = logging.INFO
            if finding.severity == Severity.ERROR:
                level = logging.ERROR
            elif finding.severity == Severity.WARNING:
                level = logging.WARNING
            logger.log(level, "[%s] %s", finding.code, finding.message)
    return result
