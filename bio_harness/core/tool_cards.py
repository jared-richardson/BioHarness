"""Persistent tool-card helpers for tool onboarding.

Tool cards are compact, reusable summaries of how a bioinformatics tool should
be used. They are designed to bridge onboarding and later planning by storing
progressively disclosed metadata, usage guidance, and probe evidence in a
stable JSON format.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.tool_onboarding import slugify_skill_name

_DETAIL_LEVELS = {"l1", "l2", "full"}


@dataclass(frozen=True)
class ToolArgumentCard:
    """One argument entry within a tool card.

    Attributes:
        name: Stable parameter name.
        arg_type: Normalized parameter type.
        description: Short argument description.
        required: Whether the argument is required.
        default: Optional default value.
        file_role: Optional semantic file-role label.
    """

    name: str
    arg_type: str
    description: str
    required: bool
    default: Any | None = None
    file_role: str = ""


@dataclass(frozen=True)
class ToolCard:
    """Persistent onboarding summary for one tool or skill.

    Attributes:
        name: Canonical skill or tool identifier.
        version: Optional version string discovered during probing.
        canonical_tool_name: Runtime binary or wrapper name.
        capabilities: Capability identifiers linked to this tool.
        when_to_use: Short statement of intended use.
        when_not_to_use: Short avoidance or caution statement.
        support_tier: Stability tier such as `catalog_only`.
        validated: Whether the card came from a validated install path.
        required_args: Required argument summaries.
        optional_args: Optional argument summaries.
        canonical_outputs: Expected output formats or files.
        safe_example: Validated or representative invocation template.
        dangerous_flags: Known mutating or destructive flags.
        common_errors: Structured error dictionaries.
        probe_observations: Structured probe evidence.
        smoke_test_results: Structured smoke-test evidence.
        refinement_history: Human-readable refinement notes.
        source_documents: Trusted source identifiers used to build the card.
        runner_up_wrappers: Alternative wrapper candidates kept for audit.
    """

    name: str
    version: str
    canonical_tool_name: str
    capabilities: tuple[str, ...]
    when_to_use: str
    when_not_to_use: str
    support_tier: str
    validated: bool
    required_args: tuple[ToolArgumentCard, ...]
    optional_args: tuple[ToolArgumentCard, ...]
    canonical_outputs: tuple[str, ...]
    safe_example: str
    dangerous_flags: tuple[str, ...]
    common_errors: tuple[dict[str, Any], ...]
    probe_observations: tuple[dict[str, Any], ...]
    smoke_test_results: tuple[dict[str, Any], ...]
    refinement_history: tuple[str, ...]
    source_documents: tuple[str, ...]
    runner_up_wrappers: tuple[str, ...]


def tool_card_from_draft(
    draft: Mapping[str, Any],
    *,
    source_meta: Mapping[str, Any] | None = None,
    manual_summary: Mapping[str, Any] | None = None,
    version: str = "",
    support_tier: str = "catalog_only",
    validated: bool = True,
) -> ToolCard:
    """Build a tool card from an onboarding draft.

    Args:
        draft: Onboarding draft dictionary.
        source_meta: Optional source metadata used during onboarding.
        manual_summary: Optional structured documentation summary.
        version: Optional discovered tool version string.
        support_tier: Stability tier label for the card.
        validated: Whether the card originated from a validated install path.

    Returns:
        Structured tool card.
    """

    raw_name = str(draft.get("skill_name", "") or draft.get("name", "")).strip()
    name = slugify_skill_name(raw_name)
    parameters = draft.get("parameters", {})
    required_args: list[ToolArgumentCard] = []
    optional_args: list[ToolArgumentCard] = []
    if isinstance(parameters, Mapping):
        for raw_param_name, raw_spec in parameters.items():
            param_name = str(raw_param_name).strip()
            if not param_name:
                continue
            spec = raw_spec if isinstance(raw_spec, Mapping) else {}
            card = ToolArgumentCard(
                name=param_name,
                arg_type=str(spec.get("type", "string")).strip().lower() or "string",
                description=str(spec.get("description", "")).strip()
                or f"Parameter `{param_name}`.",
                required=bool(spec.get("required", False)),
                default=spec.get("default"),
                file_role=str(spec.get("file_role", "") or "").strip(),
            )
            if card.required:
                required_args.append(card)
            else:
                optional_args.append(card)

    raw_source = ""
    raw_mode = ""
    if isinstance(source_meta, Mapping):
        raw_source = str(source_meta.get("source", "")).strip()
        raw_mode = str(source_meta.get("mode", "")).strip()

    source_documents: list[str] = []
    if raw_source:
        source_documents.append(raw_source)
    if raw_mode:
        source_documents.append(f"source_mode:{raw_mode}")

    manual_data = manual_summary if isinstance(manual_summary, Mapping) else {}
    for value in manual_data.get("source_documents", []) or []:
        text = str(value).strip()
        if text:
            source_documents.append(text)

    canonical_tool_name = str(
        (
            list(draft.get("tools_required", []) or [None])[0]
            if isinstance(draft.get("tools_required", []), list)
            else None
        )
        or name
    ).strip()

    output_types = draft.get("output_types", [])
    canonical_outputs = tuple(
        str(value).strip()
        for value in list(output_types if isinstance(output_types, list) else [])
        + list(manual_data.get("canonical_outputs", []) or [])
        if str(value).strip()
    )

    safe_example = str(draft.get("command_template", "")).strip()
    if not safe_example:
        safe_example = str(list(manual_data.get("example_invocations", []) or [""])[0]).strip()

    dangerous_flags = tuple(
        str(value).strip()
        for value in (manual_data.get("dangerous_flags", []) or [])
        if str(value).strip()
    )
    common_errors = tuple(
        dict(value)
        for value in (manual_data.get("common_errors", []) or [])
        if isinstance(value, Mapping)
    )

    return ToolCard(
        name=name,
        version=str(version or draft.get("version", "")).strip(),
        canonical_tool_name=canonical_tool_name,
        capabilities=tuple(
            str(value).strip()
            for value in (draft.get("capabilities", []) if isinstance(draft.get("capabilities", []), list) else [])
            if str(value).strip()
        ),
        when_to_use=(
            str(draft.get("when_to_use", "")).strip()
            or str(manual_data.get("when_to_use", "")).strip()
        ),
        when_not_to_use=(
            str(draft.get("when_not_to_use", "")).strip()
            or str(manual_data.get("when_not_to_use", "")).strip()
        ),
        support_tier=str(support_tier or "catalog_only").strip() or "catalog_only",
        validated=bool(validated),
        required_args=tuple(required_args),
        optional_args=tuple(optional_args),
        canonical_outputs=tuple(dict.fromkeys(canonical_outputs)),
        safe_example=safe_example,
        dangerous_flags=dangerous_flags,
        common_errors=common_errors,
        probe_observations=(),
        smoke_test_results=(),
        refinement_history=(),
        source_documents=tuple(dict.fromkeys(source_documents)),
        runner_up_wrappers=(),
    )


def write_tool_card(
    card: ToolCard,
    *,
    tool_cards_dir: Path,
) -> Path:
    """Write one tool card to disk.

    Args:
        card: Tool card to persist.
        tool_cards_dir: Directory receiving JSON tool-card files.

    Returns:
        Path to the written JSON file.
    """

    target_dir = Path(tool_cards_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{slugify_skill_name(card.name)}.json"
    payload = asdict(card)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def read_tool_card(path: Path) -> ToolCard:
    """Read one persisted tool card from disk.

    Args:
        path: JSON tool-card path.

    Returns:
        Reconstructed tool card instance.
    """

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ToolCard(
        name=str(payload.get("name", "")),
        version=str(payload.get("version", "")),
        canonical_tool_name=str(payload.get("canonical_tool_name", "")),
        capabilities=tuple(str(value) for value in payload.get("capabilities", []) or []),
        when_to_use=str(payload.get("when_to_use", "")),
        when_not_to_use=str(payload.get("when_not_to_use", "")),
        support_tier=str(payload.get("support_tier", "catalog_only")),
        validated=bool(payload.get("validated", False)),
        required_args=tuple(_argument_card_from_mapping(value) for value in payload.get("required_args", []) or []),
        optional_args=tuple(_argument_card_from_mapping(value) for value in payload.get("optional_args", []) or []),
        canonical_outputs=tuple(str(value) for value in payload.get("canonical_outputs", []) or []),
        safe_example=str(payload.get("safe_example", "")),
        dangerous_flags=tuple(str(value) for value in payload.get("dangerous_flags", []) or []),
        common_errors=tuple(dict(value) for value in payload.get("common_errors", []) or []),
        probe_observations=tuple(dict(value) for value in payload.get("probe_observations", []) or []),
        smoke_test_results=tuple(dict(value) for value in payload.get("smoke_test_results", []) or []),
        refinement_history=tuple(str(value) for value in payload.get("refinement_history", []) or []),
        source_documents=tuple(str(value) for value in payload.get("source_documents", []) or []),
        runner_up_wrappers=tuple(str(value) for value in payload.get("runner_up_wrappers", []) or []),
    )


def render_tool_card(
    card: ToolCard,
    *,
    detail_level: str = "full",
) -> dict[str, Any]:
    """Render a progressively disclosed view of a tool card.

    Args:
        card: Tool card to render.
        detail_level: One of `l1`, `l2`, or `full`.

    Returns:
        Dictionary containing only the requested fields.

    Raises:
        ValueError: If the detail level is not recognized.
    """

    level = str(detail_level or "full").strip().lower()
    if level not in _DETAIL_LEVELS:
        raise ValueError(f"Unknown tool-card detail level: {detail_level}")

    l1 = {
        "name": card.name,
        "version": card.version,
        "canonical_tool_name": card.canonical_tool_name,
        "capabilities": list(card.capabilities),
        "when_to_use": card.when_to_use,
        "when_not_to_use": card.when_not_to_use,
        "support_tier": card.support_tier,
        "validated": card.validated,
    }
    if level == "l1":
        return l1

    l2 = {
        **l1,
        "required_args": [asdict(arg) for arg in card.required_args],
        "optional_args": [asdict(arg) for arg in card.optional_args],
        "canonical_outputs": list(card.canonical_outputs),
        "safe_example": card.safe_example,
        "dangerous_flags": list(card.dangerous_flags),
        "common_errors": [dict(entry) for entry in card.common_errors],
    }
    if level == "l2":
        return l2

    return {
        **l2,
        "probe_observations": [dict(entry) for entry in card.probe_observations],
        "smoke_test_results": [dict(entry) for entry in card.smoke_test_results],
        "refinement_history": list(card.refinement_history),
        "source_documents": list(card.source_documents),
        "runner_up_wrappers": list(card.runner_up_wrappers),
    }


def _argument_card_from_mapping(raw: Mapping[str, Any]) -> ToolArgumentCard:
    """Build a tool-argument card from a JSON mapping."""

    return ToolArgumentCard(
        name=str(raw.get("name", "")),
        arg_type=str(raw.get("arg_type", "string")),
        description=str(raw.get("description", "")),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        file_role=str(raw.get("file_role", "")),
    )


__all__ = [
    "ToolArgumentCard",
    "ToolCard",
    "read_tool_card",
    "render_tool_card",
    "tool_card_from_draft",
    "write_tool_card",
]
