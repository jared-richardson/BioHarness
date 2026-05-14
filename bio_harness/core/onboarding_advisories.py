"""Repair-advisory helpers for onboarding refinement outcomes.

This module translates repeated onboarding refinement failures into the shared
repo-versioned repair-advisory format so recurring onboarding lessons can feed
back into broader repair guidance.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.tool_cards import ToolCard
from bio_harness.harness.repair_context import (
    load_repair_advisories,
    save_repair_advisories,
    upsert_repair_advisory,
)


def build_tool_advisory_proposal(
    tool_name: str,
    card: ToolCard,
    *,
    min_repeats: int = 2,
    source: str = "tool_onboarding_refinement",
) -> dict[str, Any] | None:
    """Build a tool-scoped advisory proposal from repeated refinement patterns.

    Args:
        tool_name: Tool or wrapper name to tag in the advisory catalog.
        card: Tool card containing smoke-test evidence and common errors.
        min_repeats: Minimum repeated focus/fix count before proposing advice.
        source: Provenance label for the advisory entry.

    Returns:
        Advisory proposal mapping, or `None` when no repeated pattern meets the
        threshold.
    """

    name = str(tool_name or card.name or card.canonical_tool_name).strip()
    if not name:
        return None

    focus_fix_counts: Counter[tuple[str, str]] = Counter()
    for entry in card.smoke_test_results:
        if not isinstance(entry, Mapping):
            continue
        focus = str(entry.get("refinement_focus", "") or "").strip()
        progress = entry.get("progress_assessment", {})
        if not isinstance(progress, Mapping):
            progress = {}
        # Only retain repeated failure patterns, not successful terminal states.
        if bool(progress.get("passed", False)):
            continue
        fix = _fix_hint_for_focus(card, focus)
        if focus and fix:
            focus_fix_counts[(focus, fix)] += 1

    repeated = [(focus, fix, count) for (focus, fix), count in focus_fix_counts.items() if count >= min_repeats]
    if not repeated:
        return None

    repeated.sort(key=lambda item: (-item[2], item[0], item[1]))
    top_focuses = [focus for focus, _, _ in repeated]
    repair_hints = [fix for _, fix, _ in repeated]
    avoid_patterns = [_avoid_pattern_for_focus(focus) for focus in top_focuses]
    summary_focuses = ", ".join(top_focuses[:2])
    return {
        "scope": "tool",
        "name": name,
        "summary": (
            f"Repeated onboarding refinement for `{name}` clusters around {summary_focuses}; "
            "preserve the narrowed diagnostic focus before broad reruns."
        ),
        "repair_hints": _dedupe_strings(repair_hints),
        "avoid_patterns": _dedupe_strings(avoid_patterns),
        "source": source,
    }


def persist_tool_advisory_proposal(
    proposal: Mapping[str, Any] | None,
    *,
    catalog_path: Path,
) -> Path | None:
    """Persist one onboarding-generated advisory proposal.

    Args:
        proposal: Advisory proposal built by `build_tool_advisory_proposal()`.
        catalog_path: Advisory catalog path to update.

    Returns:
        The path written, or `None` if no proposal was supplied.
    """

    if not isinstance(proposal, Mapping):
        return None
    catalog = load_repair_advisories(catalog_path)
    updated = upsert_repair_advisory(
        catalog,
        scope=str(proposal.get("scope", "")),
        name=str(proposal.get("name", "")),
        summary=str(proposal.get("summary", "")),
        repair_hints=list(proposal.get("repair_hints", []) or []),
        avoid_patterns=list(proposal.get("avoid_patterns", []) or []),
        source=str(proposal.get("source", "tool_onboarding_refinement")),
    )
    return save_repair_advisories(updated, catalog_path)


def _fix_hint_for_focus(card: ToolCard, focus: str) -> str:
    """Return the latest fix hint associated with one focus, if any."""

    for entry in reversed(card.common_errors):
        if str(entry.get("focus", "") or "").strip() == focus:
            return str(entry.get("fix", "") or "").strip()
    return ""


def _avoid_pattern_for_focus(focus: str) -> str:
    """Return a stable avoid-pattern sentence for one refinement focus."""

    normalized = str(focus or "").strip()
    mapping = {
        "output_paths": "Repeating broad smoke tests without first narrowing to the missing output paths.",
        "output_completeness": "Re-running the full recipe when only a subset of outputs is still missing.",
        "output_markers": "Treating satisfied output files as a reason to ignore missing expected summary markers.",
        "command_flags": "Keeping unsupported optional flags in the command template after a diagnostic flag probe fails.",
        "input_prerequisites": "Retrying broad execution before verifying required indices, references, or staged inputs.",
        "return_code_behavior": "Assuming a partial artifact write means the non-zero exit can be ignored.",
        "wrapper_rendering": "Continuing smoke execution when wrapper placeholders or parameter bindings are still invalid.",
        "timeout_budget": "Increasing smoke scope instead of shrinking fixtures after timeout-focused failures.",
        "forbidden_output": "Ignoring forbidden output markers after the diagnostic recipe already isolated them.",
    }
    return mapping.get(normalized, "Repeating the same non-improving onboarding pattern without narrowing the next repair step.")


def _dedupe_strings(values: list[str]) -> list[str]:
    """Return stable-order unique non-empty strings."""

    seen: set[str] = set()
    deduped: list[str] = []
    for raw in values:
        token = str(raw or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


__all__ = [
    "build_tool_advisory_proposal",
    "persist_tool_advisory_proposal",
]
